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
    forward_request_headers,
    forward_response_headers,
)
from headroom.api.middleware import context_of
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
from headroom.core.sse import SSEObserver
from headroom.dialects.anthropic import ANTHROPIC
from headroom.dialects.base import Dialect
from headroom.dialects.openai import OPENAI
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
        return _error_response(dialect, ctx, exc)


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

    provider_name = gateway.routing.resolve(dialect.name, model)
    ctx.provider = provider_name
    principal.require_provider(provider_name)
    provider = gateway.registry.get(provider_name)

    upstream_request = UpstreamRequest(
        dialect=dialect.name,
        path=dialect.upstream_path,
        model=model,
        body=raw_body,  # the caller's bytes, verbatim — see the module docstring
        stream=ctx.stream,
        headers=forward_request_headers(request.headers),
        control=control_headers(request.headers),
    )

    upstream = await provider.open(upstream_request, ctx)
    ctx.upstream_status = upstream.status_code

    # An error status means there is no stream to forward, whatever the caller asked
    # for — both providers answer a failed streaming request with a plain JSON error.
    if upstream.status_code >= 400 or not ctx.stream:
        return await _buffered_response(ctx, upstream)
    return _streaming_response(dialect, ctx, upstream)


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


async def _buffered_response(ctx: RequestContext, upstream: UpstreamResponse) -> Response:
    """Read the whole upstream body and forward it — status, headers, and bytes.

    This is the non-streaming path *and* the upstream-error path. In both cases the
    body is forwarded exactly as received: an upstream 400 explaining which field was
    malformed is far more useful to the caller than anything the gateway could compose,
    and reshaping it would be the "generic 500" failure in a politer costume.
    """
    try:
        body = await upstream.aread()
    finally:
        await upstream.aclose()

    headers = forward_response_headers(upstream.headers)
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
    return Response(
        content=body,
        status_code=upstream.status_code,
        headers=headers,
        media_type=headers.get("content-type", "application/json"),
    )


def _streaming_response(
    dialect: Dialect, ctx: RequestContext, upstream: UpstreamResponse
) -> StreamingResponse:
    headers = forward_response_headers(upstream.headers)
    return StreamingResponse(
        _passthrough(dialect, ctx, upstream),
        status_code=upstream.status_code,
        headers=headers,
        media_type=headers.get("content-type", _SSE_CONTENT_TYPE),
    )


async def _passthrough(
    dialect: Dialect, ctx: RequestContext, upstream: UpstreamResponse
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
    is_sse = _SSE_CONTENT_TYPE in upstream.headers.get("content-type", "")
    saw_terminal = False
    failure: str | None = None
    failure_message = ""

    try:
        async for chunk in upstream.aiter_bytes():
            if not chunk:
                continue
            if is_sse and not saw_terminal:
                saw_terminal = any(dialect.is_terminal(event) for event in observer.feed(chunk))
            ctx.mark_first_token_out()
            yield chunk
        if is_sse and not saw_terminal:
            saw_terminal = any(dialect.is_terminal(event) for event in observer.close())
    except ProviderError as exc:
        # The connection died with bytes already on the wire. The status line is spent,
        # so this cannot be an HTTP error — it has to be said inside the stream.
        failure, failure_message = exc.reason, exc.message
    except asyncio.CancelledError:
        # The caller hung up. Recorded before the close below, because a cancelled
        # close may not get the chance to finish — and Phase 4 settles reservations on
        # exactly this path.
        ctx.complete(OUTCOME_CLIENT_DISCONNECT, error_source=SOURCE_GATEWAY)
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
        return

    # Marked before `complete`, not after: the terminal event is itself a byte the
    # client receives, and a completion stamped ahead of it would put the request's end
    # before its first output. On a cut this is the only path that ever sets the mark.
    ctx.mark_first_token_out()
    ctx.complete(failure, error_source=SOURCE_UPSTREAM, error_reason=failure)
    yield dialect.terminal_error_event(
        reason=failure, message=failure_message, request_id=ctx.request_id
    )


def _error_response(dialect: Dialect, ctx: RequestContext, exc: HeadroomError) -> Response:
    """A failure with no upstream body to forward, said in the caller's dialect.

    The status is chosen by the error class (see ``headroom.core.errors``), the body is
    the dialect's own error shape so the caller's SDK raises the right exception type,
    and ``headroom.reason`` carries the exact cause that an HTTP status is too coarse
    to express.
    """
    ctx.mark_first_token_out()
    ctx.complete(
        exc.reason,
        status_code=exc.status_code,
        error_source=exc.source,
        error_reason=exc.reason,
    )
    return Response(
        content=dialect.error_body(
            status_code=exc.status_code,
            reason=exc.reason,
            message=exc.message,
            request_id=ctx.request_id,
        ),
        status_code=exc.status_code,
        media_type="application/json",
        headers={ERROR_SOURCE_HEADER: exc.source},
    )
