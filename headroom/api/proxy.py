"""The two proxy routes and the passthrough engine behind them.

``POST /v1/messages`` and ``POST /v1/chat/completions``. Both run the same pipeline —
read, route, open upstream, forward — because every behaviour the gate checks
(streaming fidelity, tool-block fidelity, error honesty, timings) should be one
implementation that two dialects parameterise, not two implementations that drift.

Three properties are load-bearing, and each is a decision the code is arranged around.

**The request body is never re-serialized.** It is read as bytes and sent as bytes.
Only a shallow JSON parse happens, to read ``model`` and ``stream`` for routing, and
its result is discarded. That is what makes assumption **A5** — tool_use and
tool_result blocks round-trip untouched — hold *structurally* rather than by careful
copying: there is no code that could reorder a key or re-escape a character, because
there is no code that rebuilds the body at all.

**Nothing buffers a stream.** The upstream response is opened headers-first and its
chunks are forwarded as they arrive. ``tests/test_no_buffering.py`` proves this
causally rather than by timing: it holds the upstream open mid-stream and checks the
client already has bytes.

**A stream that ends early says so.** The passthrough watches the event sequence go by
and knows whether the dialect's terminal marker appeared. If the upstream dies, or
simply stops without one, the caller gets a terminal error event — never a silent
truncation. ``tests/test_mid_stream_cut.py``, written before any of this, is the
specification.

Phase 2 adds one more, and its **order** is the interesting part:

**Identify, then read, then scope.** Authentication runs before the body is even
parsed, so an anonymous caller gets 401 rather than a 400 that tells them their JSON is
malformed — a gateway should not debug requests for strangers. The model scope is
checked before routing, so an out-of-scope model answers 403 whether or not this
deployment routes it, and a key cannot be used to enumerate the routing table. The
provider scope is checked last, because it is the only one that needs the route
resolved. Each of the three raises, and the existing ``HeadroomError`` handler below
turns it into the caller's own dialect (docs/DECISIONS.md H-020).

Phase 3 adds metering, at the three points where a request can end:

**Every exit meters exactly once.** ``_buffered_response`` reads the usage block out of
a complete body, ``_passthrough`` reads it out of the stream it is already observing,
and ``_error_response`` meters a request that never got that far. The call is
synchronous and the row goes to a queue (``headroom/metering/writer.py``), so no
caller ever waits on a database — and it happens *after* ``ctx.complete``, so a row
carries the same outcome and timings the log line does.

The streaming loop changed shape for this in one visible way: it now feeds the SSE
observer for the *whole* response rather than stopping once the terminal marker is
seen. That is not tidying — in the OpenAI dialect the chunk bearing ``finish_reason``
arrives **before** the usage-only chunk, so a loop that stopped at the terminal event
metered nothing at all. Nothing about the forwarded bytes changed; the tap simply keeps
tapping (H-007).

Phase 4 adds the budget gate, at the seam Phase 2 and Phase 3 left for it:

**Admission is the last thing before the upstream is opened, and settlement is bolted
to metering.** ``gateway.budgets.admit`` runs after the scope checks and before
``provider.open``, so a refused request provably never reaches a provider — there is no
code path between the raise and the call. Settlement then rides the metering exits,
because the amount a hold settles at is decided from the cost the meter just computed;
:func:`_finish` is the three-line sequence *measure, settle, commit* that keeps the
ledger row carrying both figures.

Nothing else in this file moved. Metering did not become asynchronous — ``measure`` and
``commit`` are the same synchronous calls ``record`` always made — and the one ``await``
added per exit is the budget's own conditional write, which H-027 forbids from riding
the fire-and-forget queue: a lost ledger row is a reporting gap, a lost settlement is
D-019 growing back.

Phase 4b adds the other half of the plan's Phase 4, one line above the budget gate:

**The rate limiter sheds load before the cap counts it.** Token buckets are consumed
after the scope checks and *before* the budget reservation, so a request that is going to
be refused for rate is refused before it takes a hold it would have to hand straight back
— and so a burst is shed one step before it reaches the single DynamoDB item every
request for a tenant serialises on (docs/DECISIONS.md H-039). Nothing settles on the way
out: a bucket refills by itself, which is the entire difference between a flow and a
stock, and the reason this half of the phase adds no code to :func:`_finish`.

Phase 5 adds the cache, in the one slot left between them:

**Looked up after the limiter, before the cap; stored after the response is delivered.**
A hit is served without opening an upstream and without taking a budget reservation — it
spends nothing, so there is nothing to bound — while still costing its rate-limit units,
because a hit is not free to *this* process and a tenant that could serve unlimited
traffic by repeating itself would have a denial of service for the asking
(docs/DECISIONS.md H-046).

**The store side is where the streaming path grew its only new line.** A response can
only be cached if its bytes are still around, so ``_passthrough`` keeps a copy — and
keeps it **only when there is a live cache plan**, which there is not for a tenant with
caching disabled, for an ineligible request, or for one that already hit. The copy is
appended beside the existing observer feed, on the same side of the ``yield``, so the
client's first byte is not waiting on it; and the write itself happens after the last
byte has left, so a slow database delays nothing at all on the path that streams.

Phase 6 changes **one line** of this file, and the fact that it is one line is the
design:

**``provider.open`` became ``gateway.failover.open``.** A route now resolves to a chain
rather than to a name, and the executor walks it — retrying on transport faults and on
the two status families BUILD_PLAN names (429 / 5xx), skipping providers whose breaker
has tripped, backing off before it comes back to one that already failed *this* request.
Everything upstream of that line is unchanged: one authentication, one estimate, one
bucket consumption, one cache lookup, one budget reservation, one ledger row — a request
that hops three times still spends exactly once, because admission happens before the
executor exists in the call path and settlement happens after it has finished
(docs/DECISIONS.md H-053).

**And nothing downstream of it changed either, which is the point.** The executor may
only run while nothing has been committed to the client, so by the time ``_passthrough``
yields its first byte there is no retry left to attempt. A stream that dies after that
byte is still H-008's problem and still gets H-008's answer — a terminal error event in
the caller's own dialect — because splicing a second provider's answer onto the first
one's fragment is how gateways serve Frankenstein responses (H-048). The only thing the
streaming path gained is one call telling provider health how the delivery *ended*, which
is the signal that trips a breaker when a GPU is killed mid-answer.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Request
from starlette.responses import Response, StreamingResponse

from headroom.api.deps import GatewayDep
from headroom.api.gateway import Gateway
from headroom.api.headers import (
    control_headers,
    failover_headers,
    forward_request_headers,
    forward_response_headers,
)
from headroom.api.middleware import context_of
from headroom.cache.eligibility import MAX_CACHEABLE_BODY_BYTES, REASON_BODY_TOO_LARGE
from headroom.cache.replay import replay
from headroom.core.cache import CacheHit
from headroom.core.context import (
    OUTCOME_CLIENT_DISCONNECT,
    OUTCOME_OK,
    OUTCOME_UPSTREAM_ERROR,
    RequestContext,
)
from headroom.core.errors import (
    REASON_STREAM_INCOMPLETE,
    SOURCE_GATEWAY,
    SOURCE_UPSTREAM,
    HeadroomError,
    InvalidRequestBody,
    ProviderError,
)
from headroom.core.sse import SSEEvent, SSEObserver
from headroom.dialects.anthropic import ANTHROPIC
from headroom.dialects.base import Dialect
from headroom.dialects.openai import OPENAI
from headroom.metering.usage import Usage
from headroom.policy.budgets import estimate_usd
from headroom.providers.base import UpstreamRequest, UpstreamResponse

__all__ = ["router"]

router = APIRouter(tags=["proxy"])

#: Set on every error response so an operator can tell "the provider rejected this"
#: from "we broke" without parsing a body.
ERROR_SOURCE_HEADER = "x-headroom-error-source"

_SSE_CONTENT_TYPE = "text/event-stream"


@router.post(ANTHROPIC.route_path)
async def anthropic_messages(request: Request, gateway: GatewayDep) -> Response:
    """Anthropic dialect in, Anthropic dialect out (BUILD_PLAN L4: no translation)."""
    return await proxy(request, ANTHROPIC, gateway)


@router.post(OPENAI.route_path)
async def openai_chat_completions(request: Request, gateway: GatewayDep) -> Response:
    """OpenAI dialect in, any OpenAI-compatible backend out — the vLLM path."""
    return await proxy(request, OPENAI, gateway)


async def proxy(request: Request, dialect: Dialect, gateway: Gateway) -> Response:
    """Run one request through the gateway, turning failures into honest responses."""
    ctx = context_of(request)
    ctx.dialect = dialect.name
    try:
        return await _proxy(request, dialect, gateway, ctx)
    except HeadroomError as exc:
        return await _error_response(dialect, ctx, exc, gateway)


async def _proxy(
    request: Request, dialect: Dialect, gateway: Gateway, ctx: RequestContext
) -> Response:
    # First, and before the body is even read: who is this? Everything after this line
    # is attributed to a tenant, which is what Phase 3's ledger and Phase 4's budgets
    # are built on.
    principal = await gateway.authenticator.authenticate(request.headers, ctx)

    raw_body = await request.body()
    body = _parse_body(raw_body)

    model = dialect.model_of(body)
    if model is None:
        raise InvalidRequestBody("request body must name a model")
    ctx.model = model
    ctx.stream = dialect.wants_stream(body)
    # Before routing, so a key cannot learn which models this deployment serves by
    # reading 403 against 404.
    principal.require_model(model)

    # The whole routing decision, chain included. `primary` is what the scope check and
    # the 403 are decided against — unchanged from Phase 2 — and `permitted` then drops
    # any fallback this key may not reach, because a scope is not something an outage is
    # allowed to widen (docs/DECISIONS.md H-049).
    route = gateway.routing.resolve_route(dialect.name, model).permitted(principal.permits_provider)
    ctx.provider = route.primary
    principal.require_provider(route.primary)

    # One bound, two gates. The rate limiter needs this request's size in tokens and the
    # budget gate needs it in dollars, and they are the same number arrived at by the
    # same formula (H-034) — so it is computed once, here, and handed to both. Two
    # independent estimates of one request would be two things to keep in sync.
    estimate = estimate_usd(
        dialect, body, raw_body, model=model, when=ctx.started_at, prices=gateway.budgets.prices
    )

    # The load shedder first (429), the cap second (402), and both before anything is
    # spent. Neither returns a refusal — they raise — so there is no path from either to
    # `provider.open`, which is the property `tests/test_rate_limit_gate.py` and
    # `tests/test_budget_gate.py` assert by checking the mock was never called. The
    # ordering is argued in docs/DECISIONS.md H-039 and in `headroom/policy/limits.py`.
    await gateway.limits.admit(
        ctx,
        tenant_limits=principal.tenant_limits,
        key_limits=principal.key_limits,
        estimate=estimate,
    )

    # Between the two gates, and the position is argued in H-046: after the limiter
    # because a hit costs this process real work (a search, and on the semantic path a
    # CPU embedding), before the cap because a hit costs the *tenant* nothing and a
    # reservation it would settle to zero is two DynamoDB round trips on the one path
    # whose entire product is that the first token is already there.
    hit = await gateway.cache.lookup(ctx, dialect, body, settings=principal.tenant_cache)
    if hit is not None:
        return await replay(hit, ctx, on_complete=lambda: _finish_cached(gateway, ctx, hit))

    await gateway.budgets.admit(ctx, dialect, body, raw_body, estimate=estimate)

    upstream_request = UpstreamRequest(
        dialect=dialect.name,
        path=dialect.upstream_path,
        model=model,
        body=raw_body,  # the caller's bytes, verbatim — see the module docstring
        stream=ctx.stream,
        headers=forward_request_headers(request.headers),
        control=control_headers(request.headers),
    )

    # The line that used to read `await provider.open(...)`. Everything above it is
    # untouched, which is the phase in one sentence: failover is a widening of *how* an
    # upstream is obtained, not of what a request is. The executor is also the only code
    # that may call a provider twice, and it refuses to once a byte has gone downstream
    # — the splice guard (H-048), which is why nothing below this line changed either.
    upstream = await gateway.failover.open(route.attempts, upstream_request, ctx)
    ctx.upstream_status = upstream.status_code

    # An error status means there is no stream to forward, whatever the caller asked
    # for — both providers answer a failed streaming request with a plain JSON error.
    if upstream.status_code >= 400 or not ctx.stream:
        return await _buffered_response(dialect, ctx, upstream, gateway)
    return _streaming_response(dialect, ctx, upstream, gateway)


def _parse_body(raw: bytes) -> dict[str, Any]:
    """Read just enough of the body to route it. The result is never sent anywhere.

    Deliberately shallow (BUILD_PLAN L4, passthrough-first): the gateway does not
    validate a request beyond the two fields it must read. Everything else is the
    provider's business, and the provider's rejection is more accurate — and more
    current — than any schema Headroom could keep in sync.
    """
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise InvalidRequestBody(f"request body is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise InvalidRequestBody("request body must be a JSON object")
    return parsed


async def _buffered_response(
    dialect: Dialect, ctx: RequestContext, upstream: UpstreamResponse, gateway: Gateway
) -> Response:
    """Read the whole upstream body and forward it — status, headers, and bytes.

    This is the non-streaming path *and* the upstream-error path. In both cases the
    body is forwarded exactly as received: an upstream 400 explaining which field was
    malformed is far more useful to the caller than anything the gateway could compose,
    and reshaping it would be the "generic 500" failure in a politer costume.

    The usage block is read out of the same bytes, *after* they have been forwarded in
    full — reading is not rewriting, and the ``Response`` is built from the identical
    object either way.
    """
    try:
        body = await upstream.aread()
    finally:
        await upstream.aclose()

    headers = forward_response_headers(upstream.headers)
    headers.update(failover_headers(ctx.failover_hops, ctx.failover_from))
    failed = upstream.status_code >= 400
    if failed:
        headers[ERROR_SOURCE_HEADER] = SOURCE_UPSTREAM

    ctx.mark_first_token_out()
    ctx.complete(
        OUTCOME_UPSTREAM_ERROR if failed else OUTCOME_OK,
        status_code=upstream.status_code,
        error_source=SOURCE_UPSTREAM if failed else None,
        error_reason=f"upstream_status_{upstream.status_code}" if failed else None,
    )
    # An error body has no usage to read, and the meter is told nothing rather than
    # asked to find something: it prices the request from the upstream status.
    usage = Usage() if failed else dialect.usage_from_body(body)
    await _finish(gateway, ctx, usage)
    # Deliberately reached on the *error* path too, with the error body in hand. It is
    # refused there — that is what `may_store_response` is for — and driving the real
    # refusal through the real path is what makes the D-021 poison attempts a test of
    # the gateway rather than of a helper function.
    #
    # One honest cost, stated: on this path the write precedes the response, so a miss
    # pays one INSERT before its last byte. The streamed path below pays nothing, since
    # its bytes are already gone. A second fire-and-forget queue would erase the
    # difference and add another thing to drain at shutdown, for a few milliseconds on
    # the slower of two paths that has just waited on a model.
    await gateway.cache.store_response(
        ctx, body=body, content_type=headers.get("content-type", "application/json"), usage=usage
    )
    return Response(
        content=body,
        status_code=upstream.status_code,
        headers=headers,
        media_type=headers.get("content-type", "application/json"),
    )


def _streaming_response(
    dialect: Dialect, ctx: RequestContext, upstream: UpstreamResponse, gateway: Gateway
) -> StreamingResponse:
    headers = forward_response_headers(upstream.headers)
    headers.update(failover_headers(ctx.failover_hops, ctx.failover_from))
    return StreamingResponse(
        _passthrough(dialect, ctx, upstream, gateway),
        status_code=upstream.status_code,
        headers=headers,
        media_type=headers.get("content-type", _SSE_CONTENT_TYPE),
    )


async def _passthrough(
    dialect: Dialect, ctx: RequestContext, upstream: UpstreamResponse, gateway: Gateway
) -> AsyncIterator[bytes]:
    """Forward upstream bytes unchanged while watching whether the stream completes.

    The parser is a **tap, not a filter**: every chunk is fed to an observer *and*
    yielded untouched, so knowing the event sequence costs the client nothing. Chunk
    boundaries are therefore whatever the upstream produced (assumption A4 — the gate
    asserts content and event equality, never identical chunking), and a frame split
    across two chunks, or a multi-byte character split down the middle, passes through
    intact because nothing here decodes anything.
    """
    observer = SSEObserver()
    usage = dialect.usage_observer()
    content_type = upstream.headers.get("content-type", _SSE_CONTENT_TYPE)
    is_sse = _SSE_CONTENT_TYPE in content_type
    saw_terminal = False
    failure: str | None = None
    failure_message = ""
    # Only when there is something to cache. No plan — a disabled tenant, an ineligible
    # request, a hit — and not one byte is copied, which is what makes "caching off
    # costs nothing" a property of the code rather than a claim about it.
    recorder: list[bytes] | None = [] if ctx.cache_plan is not None else None
    recorded = 0

    def watch(events: list[SSEEvent]) -> None:
        """Steer completion detection and metering off the same events.

        Note what this does *not* short-circuit: the usage observer is fed after the
        terminal marker too. In the OpenAI dialect the usage-only chunk arrives after
        the frame carrying ``finish_reason``, so stopping early would meter nothing.
        """
        nonlocal saw_terminal
        for event in events:
            if not saw_terminal and dialect.is_terminal(event):
                saw_terminal = True
            usage.feed(event)

    try:
        async for chunk in upstream.aiter_bytes():
            if not chunk:
                continue
            if is_sse:
                watch(observer.feed(chunk))
            if recorder is not None:
                # Beside the observer feed, on the same side of the yield: a list append
                # is strictly cheaper than the parse that already happens here, and
                # keeping both before the yield keeps the loop obviously correct.
                recorded += len(chunk)
                if recorded > MAX_CACHEABLE_BODY_BYTES:
                    # Stop paying to remember a response that will never be stored. The
                    # request itself is unaffected; only the copy is abandoned.
                    recorder = None
                    ctx.cache_plan = None
                    ctx.cache_reason = REASON_BODY_TOO_LARGE
                else:
                    recorder.append(chunk)
            ctx.mark_first_token_out()
            yield chunk
        if is_sse:
            watch(observer.close())
    except ProviderError as exc:
        # The connection died with bytes already on the wire. The status line is spent,
        # so this cannot be an HTTP error — it has to be said inside the stream.
        failure, failure_message = exc.reason, exc.message
    except asyncio.CancelledError:
        # The caller hung up. Recorded before the close below, because a cancelled
        # close may not get the chance to finish — and this is where Phase 4 settles.
        ctx.complete(OUTCOME_CLIENT_DISCONNECT, error_source=SOURCE_GATEWAY)
        # The upstream generated whatever it generated before we stopped reading, so
        # the row is written with whatever usage arrived — usually none, which is
        # recorded as unknown rather than as free. The budget treats that "unknown"
        # differently and deliberately: a model ran, so the hold stands (H-031).
        #
        # `shielded` because an `await` inside a cancellation handler can be cancelled
        # again before it lands. The settlement is stamped on the context either way,
        # so the row and the log line are complete even if the write is not; and if it
        # never lands, the hold expires and the sweeper releases it.
        await _finish(gateway, ctx, usage.usage, shielded=True)
        raise
    finally:
        with contextlib.suppress(Exception):
            await upstream.aclose()

    if failure is None and is_sse and not saw_terminal:
        # Nothing raised; the bytes just stopped. This is the quiet half of the
        # truncation problem and the reason completion is tracked rather than assumed.
        failure = REASON_STREAM_INCOMPLETE
        failure_message = (
            f"upstream ended the stream without a terminal event; the {dialect.name} "
            f"response is incomplete and must not be treated as an answer"
        )

    if failure is None:
        ctx.complete(OUTCOME_OK)
        # Phase 6 scores a streamed provider *here*, not when its headers arrived: an
        # upstream that answers and then dies mid-answer is not a healthy upstream, and
        # that is precisely what a `docker kill` on a live vLLM produces. One
        # observation per attempt — the executor deliberately records nothing for a live
        # stream (docs/DECISIONS.md H-052).
        _score(gateway, ctx, ok=True)
        await _finish(gateway, ctx, usage.usage)
        # After the last byte: the client already has the whole answer, so the write
        # costs the request nothing at all on this path.
        await _store_stream(gateway, ctx, recorder, content_type, usage.usage)
        return

    # Marked before `complete`, not after: the terminal event is itself a byte the
    # client receives, and a completion stamped ahead of it would put the request's end
    # before its first output. On a cut this is the only path that ever sets the mark.
    ctx.mark_first_token_out()
    ctx.complete(failure, error_source=SOURCE_UPSTREAM, error_reason=failure)
    # A cut or an unterminated stream is a provider failure and is counted as one — it
    # is the signal that trips the breaker during the two-GPU demo. What it is *not* is
    # a reason to retry: bytes have already gone downstream, and the next lines say so
    # in the caller's own dialect instead (H-008, H-048).
    _score(gateway, ctx, ok=False, reason=failure)
    # Metered with whatever the provider managed to report. A stream cut before its
    # totals leaves the output count unknown, and the row says so — it is not billed
    # as a complete answer, and it is not billed as a free one either (invariant 6,
    # one layer down: an amputated answer must not look finished to the invoice).
    await _finish(gateway, ctx, usage.usage)
    # The truncation path, and it calls the cache on purpose with the amputated bytes in
    # hand. Nothing lands — `ctx.outcome` is not ``ok`` — and driving the refusal through
    # the real code is what makes the D-021 attempt a test of this gateway rather than of
    # a predicate in isolation. Invariant 6 is enforced *here*, at the one place a
    # truncated answer could otherwise become permanent.
    await _store_stream(gateway, ctx, recorder, content_type, usage.usage)
    yield dialect.terminal_error_event(
        reason=failure, message=failure_message, request_id=ctx.request_id
    )


def _score(gateway: Gateway, ctx: RequestContext, *, ok: bool, reason: str | None = None) -> None:
    """Record how a streamed response ended, against the provider that served it.

    Only the streaming path calls this. The failover executor scores every attempt it
    can judge on its own — a transport failure, a retryable status, a body it read to the
    end — and deliberately says nothing about a live stream, because at hand-off time
    there is nothing yet to judge. One attempt, one observation, never two.

    A client disconnect is scored by neither: the caller hung up, which is not evidence
    about the provider and must not be allowed to trip a breaker for everybody else.
    """
    if ctx.provider is None:
        return
    gateway.health.record(
        ctx.provider, ok=ok, reason=reason, latency_ms=ctx.upstream_latency_ms if ok else None
    )


async def _store_stream(
    gateway: Gateway,
    ctx: RequestContext,
    recorder: list[bytes] | None,
    content_type: str,
    usage: Usage,
) -> None:
    """Offer a streamed response to the cache, whatever happened to it.

    ``recorder is None`` means there was nothing to offer in the first place — caching
    off, request ineligible, or a body that outgrew the bound — and in that case the plan
    has already been cleared, so this is a no-op rather than a silent skip.
    """
    if recorder is None:
        return
    await gateway.cache.store_response(
        ctx, body=b"".join(recorder), content_type=content_type, usage=usage
    )


async def _finish_cached(gateway: Gateway, ctx: RequestContext, hit: CacheHit) -> None:
    """Close the books on a request the cache served.

    The same ``measure``/``commit`` pair every other exit runs, with two differences that
    are the whole point of the row it writes. The usage carries **only** the stop reason:
    the answer really did end that way, and it is the one fact about the response that is
    still true on a replay — while the token counts stay ``None``, because nothing was
    generated and a row claiming otherwise would corrupt every ``SUM(output_tokens)`` in
    the dashboard and in the Phase 9 rollup.

    The cost that falls out is ``$0`` with ``not_billable`` beside it, from
    ``Meter._is_billable``'s existing rule (no upstream status, no timeout → no model
    ran) — the same status an unroutable model gets, and for the same reason. The saving
    is on the row too, in ``cache_avoided_usd``, where it cannot be mistaken for spend.

    There is no settlement step: a hit never took a reservation, so
    ``budget_reservation`` is ``None`` and ``_finish``'s middle line would be a no-op.
    Calling ``record`` directly says that, rather than implying a hold that never existed.
    """
    usage = Usage(stop_reason=hit.entry.stop_reason)
    gateway.meter.record(ctx, usage)


async def _finish(
    gateway: Gateway, ctx: RequestContext, usage: Usage, *, shielded: bool = False
) -> None:
    """Close a request's books: measure, settle, commit — in that order, always.

    The order is the whole point and is not interchangeable. ``measure`` stamps
    ``cost_status`` and ``usd_cost`` on the context but writes nothing; the budget's
    settlement is *decided* from ``cost_status`` (H-031) and stamps its own two figures;
    only then is the ledger row built, so one row carries the cost, the hold, and what
    the hold became. Settling first would settle against a cost nobody had computed;
    committing first would write a row missing the column the phase exists to add.

    A request with no live reservation — an unbudgeted tenant, or one refused before a
    hold was taken — passes through the middle step untouched.
    """
    breakdown = gateway.meter.measure(ctx, usage)
    await gateway.budgets.settle(ctx, shielded=shielded)
    gateway.meter.commit(ctx, usage, breakdown)


async def _error_response(
    dialect: Dialect, ctx: RequestContext, exc: HeadroomError, gateway: Gateway
) -> Response:
    """A failure with no upstream body to forward, said in the caller's dialect.

    The status is chosen by the error class (see ``headroom.core.errors``), the body is
    the dialect's own error shape so the caller's SDK raises the right exception type,
    and ``headroom.reason`` carries the exact cause that an HTTP status is too coarse
    to express.

    Phase 4b adds ``exc.headers``, which is empty for every error class but one: a rate
    limit refusal carries its ``retry-after`` and the bucket that produced it. They are
    merged *under* ``x-headroom-error-source``, so no error class can overwrite the one
    header that says who refused.
    """
    ctx.mark_first_token_out()
    ctx.complete(
        exc.reason,
        status_code=exc.status_code,
        error_source=exc.source,
        error_reason=exc.reason,
    )
    # No provider ran, so there is no usage to read. The meter decides what that costs
    # from the upstream status — zero for a refusal or an unroutable model, unknown for
    # a timeout, which was sent and may have been billed by someone we cannot ask. A
    # request that never authenticated has no tenant and gets no row at all (H-025).
    #
    # This path also releases a hold taken moments earlier: a request that reserved and
    # then hit a timeout, an unroutable model, or a transport failure has its budget
    # handed straight back, because the settlement follows the cost and that cost is 0.
    await _finish(gateway, ctx, Usage())
    # And it offers nothing to the cache, through the same call every other exit makes.
    # A timeout is the interesting one: the provider may well have generated an answer,
    # and there is not one byte of it here — which is precisely why an empty body and a
    # non-``ok`` outcome must both refuse, rather than one of them.
    await gateway.cache.store_response(ctx, body=b"", content_type="", usage=Usage())
    return Response(
        content=dialect.error_body(
            status_code=exc.status_code,
            reason=exc.reason,
            message=exc.message,
            request_id=ctx.request_id,
        ),
        status_code=exc.status_code,
        media_type="application/json",
        headers={
            **exc.headers,
            # An exhausted chain leaves its story here too: the caller gets the last
            # failure's honest status (H-009, unchanged) *and* the fact that Headroom
            # tried elsewhere first, which is otherwise invisible from outside.
            **failover_headers(ctx.failover_hops, ctx.failover_from),
            ERROR_SOURCE_HEADER: exc.source,
        },
    )
