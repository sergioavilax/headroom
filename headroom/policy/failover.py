"""Same-dialect failover: which upstream serves a request when the first one will not.

``headroom/policy/routing.py`` says *where* a request is meant to go. This file decides
what happens when it cannot get there — and it is deliberately the **only** place in the
codebase that may call a provider twice for one request.

Five decisions, each argued in docs/DECISIONS.md and each testable on its own.

**The splice guard (H-048) is the load-bearing one.** A retry after a byte has gone
downstream would join two providers' answers into one response, and every SDK on the far
end would parse the result as a single, complete, plausible message. So the executor's
whole contract is: *it may only run while nothing about this response has been committed
to the client.* In the shipped proxy that holds structurally — the executor is called
before any code that can yield or return a body, and there is no path from a forwarded
byte back into this loop — and it is **checked anyway**, on every retry, because
"structurally unreachable" is a property of today's call sites and not of the design.
Once ``ctx.first_token_out_at`` is set, the last failure is raised instead. What happens
after that line is H-008's business: a terminal error event inside the stream, never a
second provider's prose.

**What triggers a hop, and what must never (H-049).** Transport faults
(``ProviderTimeout``, ``ProviderUnavailable``, a body that died mid-read) and the two
status families BUILD_PLAN names — **429 and 5xx**. Nothing else. Two exclusions matter
more than the inclusions:

* **The gateway's own refusals never reach here.** A 402 (budget) and a 429 (rate limit)
  are raised by ``gateway.budgets`` / ``gateway.limits`` *before* the executor is
  entered, and neither is a ``ProviderError``, so there is no ``except`` clause in this
  file that could catch one. Failing over on our own 429 would move a burst to another
  provider instead of shedding it — the precise inversion of what a limiter is for — and
  H-038 exists so the distinction is a property of the proxy rather than of today's
  providers.
* **An upstream 4xx other than 429 is the caller's problem**, and the next provider will
  say the same thing one round trip later. Forwarding it immediately is both faster and
  more honest.

**Backoff is paid to a provider that already failed, not to a fresh one (H-050).**
Sleeping before trying a *different* upstream is pure added latency: nothing about A
being down suggests B needs a moment. Sleeping before re-trying one that has already
failed *this request* is exactly what jittered exponential backoff is for. So the delay
is a function of how many times **this** provider has failed here, and moving down the
chain costs nothing.

**A hop is counted even when the breaker skipped it.** ``failover_hops`` answers "did the
primary serve this request", so a candidate passed over because its breaker is open
counts the same as one that was tried and failed. The trail on the log line says which
it was (H-051).

**The chain is fail-closed.** When every candidate is spent, the caller gets the *last*
failure — an upstream's own error body forwarded verbatim if there was one, otherwise the
transport error's honest status (504 / 502) with the trail appended to its message. No
new status is invented and no vocabulary is coined; H-009's rule is unchanged.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Final

from headroom.core.context import RequestContext
from headroom.core.errors import ProviderError
from headroom.policy.health import HealthTracker
from headroom.providers.base import (
    BufferedUpstreamResponse,
    UpstreamRequest,
    UpstreamResponse,
)
from headroom.providers.registry import ProviderRegistry

__all__ = [
    "ATTEMPT_BREAKER_OPEN",
    "Attempt",
    "BackoffPolicy",
    "Failover",
    "is_retryable_status",
]

#: The reason recorded in the attempt trail for a candidate the breaker skipped. Not an
#: error class: nothing was sent, so there is nothing to map (H-009).
ATTEMPT_BREAKER_OPEN: Final = "breaker_open"

#: The one status below 500 that means *try again*, per BUILD_PLAN §P6's own wording
#: ("429/5xx"). Anthropic's 529 lands in the 5xx family and needs no special case.
_RETRY_STATUS: Final = 429


def is_retryable_status(status_code: int) -> bool:
    """Whether an upstream's own answer is a reason to try somewhere else.

    Exactly BUILD_PLAN's list: 429 and 5xx. A 4xx that is not 429 describes the
    *request*, and the next provider will describe it identically — so it is forwarded
    rather than retried, which is both faster for the caller and more honest about
    whose fault it was.
    """
    return status_code == _RETRY_STATUS or status_code >= 500


@dataclass(frozen=True, slots=True)
class BackoffPolicy:
    """Jittered exponential backoff, with the parameters published rather than tuned.

    Full jitter — ``uniform(0, ceiling)`` rather than ``ceiling`` — because the failure
    this exists to prevent is a *synchronised* retry: a burst of requests that all failed
    at the same instant and would all come back at the same instant. Sleeping a random
    fraction of the ceiling spreads them; sleeping the ceiling itself merely delays the
    stampede by a fixed amount.

    BUILD_PLAN names the shape and not the numbers, so these are chosen here and asserted
    in ``tests/test_failover_backoff.py``: a gateway whose whole product is first-token
    latency cannot afford a one-second first retry, and a cap keeps the worst case
    arithmetic rather than aspirational.
    """

    base_s: float = 0.05
    multiplier: float = 2.0
    cap_s: float = 2.0

    def ceiling_s(self, retry_index: int) -> float:
        """The most this retry may sleep. ``retry_index`` counts from zero."""
        return min(self.cap_s, self.base_s * (self.multiplier**retry_index))

    def delay_s(self, retry_index: int, jitter: float) -> float:
        """The actual sleep: a uniform sample from ``[0, ceiling]``."""
        return self.ceiling_s(retry_index) * jitter

    def worst_case_s(self, retries: int) -> float:
        """The most a request can spend asleep across ``retries`` retries of one provider.

        Published so the bound is a number rather than a feeling: with the shipped
        parameters, three attempts against one provider sleep at most 150 ms in total.
        """
        return sum(self.ceiling_s(index) for index in range(max(0, retries)))


@dataclass(frozen=True, slots=True)
class Attempt:
    """One slot in the attempt sequence, after the fact. Feeds the trail and the ledger."""

    provider: str
    #: ``None`` when the slot served the request; otherwise the stable reason it did not
    #: — an ``error_reason`` from the taxonomy, ``upstream_status_529``, or
    #: :data:`ATTEMPT_BREAKER_OPEN`.
    failure: str | None

    @property
    def label(self) -> str:
        """``vllm_a:upstream_status_529`` — what the log line carries, one per slot."""
        return f"{self.provider}:{self.failure or 'ok'}"


@dataclass(frozen=True, slots=True)
class _Outcome:
    """What one attempt produced: a servable response, or the reason it is not one.

    A value rather than scratch state on the executor, because the exhausted-chain path
    needs both *why* the last slot failed and *what artefact it left behind* — an
    upstream that answered 529 leaves a body to forward; a timeout leaves an exception
    to raise; and telling those apart from a bare string is exactly the ambiguity that
    would forward a stale answer for the wrong failure.
    """

    response: UpstreamResponse | None = None
    failure: str | None = None
    #: Set when the failure was a transport fault. Re-raised when the chain is spent.
    error: ProviderError | None = None
    #: Set when the failure was an upstream's own 429/5xx. Forwarded when spent.
    answer: BufferedUpstreamResponse | None = None


@dataclass(slots=True)
class Failover:
    """Opens an upstream, retrying across a same-dialect chain until one serves.

    Held on :class:`~headroom.api.gateway.Gateway` and called from exactly one place in
    ``headroom/api/proxy.py``, on the line that used to read ``await provider.open(...)``.
    Everything above it — auth, scopes, the limiter, the cache, the budget gate, the
    meter — is unchanged and unaware, which is what makes the phase additive.

    ``sleep`` and ``jitter`` are injected for the reason every clock in this repo is:
    a backoff tested by actually waiting is a test that gets deleted when the suite gets
    slow, and a jittered one tested against a real RNG is a test that flakes.
    """

    registry: ProviderRegistry
    health: HealthTracker
    backoff: BackoffPolicy = BackoffPolicy()
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    #: Returns a float in ``[0, 1)``. ``random.random`` in production, a fixture in tests.
    jitter: Callable[[], float] = random.random

    async def open(
        self, chain: Sequence[str], request: UpstreamRequest, ctx: RequestContext
    ) -> UpstreamResponse:
        """The upstream that will serve this request, having tried as hard as configured.

        Returns a **live** response only for a streamed request that got a status under
        400 — the one case where bytes must flow as they arrive. Everything else comes
        back as a :class:`BufferedUpstreamResponse` whose body has already been read,
        because reading is what let the executor decide, and because reading a
        non-streamed body *inside* the loop is what allows a connection that dies
        mid-body to still fail over (nothing has gone downstream, so nothing can splice).

        Raises the last transport failure when the chain is exhausted without an upstream
        answer, and returns the last upstream *answer* when there was one — fail-closed
        and honest either way.
        """
        slots = list(chain)
        if not slots:  # pragma: no cover - the routing table cannot produce this
            raise ValueError("failover chain is empty")

        attempts: list[Attempt] = []
        failures_by_provider: dict[str, int] = {}
        last: _Outcome | None = None

        for index, name in enumerate(slots):
            is_final = index == len(slots) - 1
            # A breaker may take a candidate out of rotation, but never the last one:
            # refusing the only remaining upstream would convert a provider's outage
            # into the gateway's, which is strictly worse than trying and failing.
            if not is_final and not self.health.admit(name):
                attempts.append(Attempt(provider=name, failure=ATTEMPT_BREAKER_OPEN))
                continue

            if index > 0:
                # The splice guard. Structurally unreachable in the shipped proxy — the
                # executor returns before any byte can be forwarded — and checked anyway,
                # because "unreachable" is a property of the call sites and those change.
                if ctx.first_token_out_at is not None:
                    break
                await self._backoff(name, failures_by_provider)

            ctx.provider = name
            # Whatever a failed attempt stamped is not this response's first upstream
            # byte. Left in place it would fold the entire failover sequence into
            # `passthrough_overhead_ms` — the column §P8.H2 publishes.
            ctx.restart_upstream_timing()

            outcome = await self._attempt(name, request, ctx)
            if outcome.response is not None:
                attempts.append(Attempt(provider=name, failure=None))
                _stamp(ctx, attempts)
                return outcome.response

            attempts.append(Attempt(provider=name, failure=outcome.failure))
            failures_by_provider[name] = failures_by_provider.get(name, 0) + 1
            self.health.record(name, ok=False, reason=outcome.failure)
            last = outcome

        _stamp(ctx, attempts)
        # Fail closed, carrying the last failure. An upstream that answered gets its
        # answer forwarded verbatim (H-009: the gateway composes nothing); a transport
        # failure is re-raised in its own class, so the invented status and the stable
        # `headroom.reason` are exactly the ones Phase 1 fixed.
        if last is not None and last.answer is not None:
            return last.answer
        if last is not None and last.error is not None:
            raise _with_trail(last.error, attempts)
        # Reachable only if every slot was skipped, which the `is_final` guard prevents.
        # Kept because a silent fall-through here would be a 500 with nothing in it.
        raise RuntimeError(f"failover chain {list(chain)!r} produced no attempt")

    # --- one attempt -------------------------------------------------------------

    async def _attempt(self, name: str, request: UpstreamRequest, ctx: RequestContext) -> _Outcome:
        """Try one provider. Returns a servable response, or the reason it is not one."""
        provider = self.registry.get(name)
        started = time.perf_counter()
        try:
            response = await provider.open(request, ctx)
        except ProviderError as exc:
            return _Outcome(failure=exc.reason, error=exc)

        buffered = response.status_code >= 400 or not request.stream
        if not buffered:
            # A live stream. It is scored when it *finishes*, by `headroom/api/proxy.py`
            # — an upstream that answers headers and then dies is not a healthy upstream,
            # and that is exactly what a `docker kill` on a live vLLM produces.
            return _Outcome(response=response)

        try:
            body = await response.aread()
        except ProviderError as exc:
            # The connection died mid-body. Nothing has gone downstream on this path, so
            # this is still a safe place to fail over — which is what makes a `kill`
            # during an in-flight non-streamed request survivable.
            await _release(response)
            return _Outcome(failure=exc.reason, error=exc)
        answer = BufferedUpstreamResponse(response.status_code, response.headers, body, ctx)
        await _release(response)

        if is_retryable_status(response.status_code):
            return _Outcome(failure=f"upstream_status_{response.status_code}", answer=answer)

        # A complete response from the provider, whatever its status: reachable,
        # responsive, and finished. A 400 is a healthy provider correctly refusing a bad
        # request, and counting it as ill health would let one client's malformed
        # payloads trip a breaker for every other tenant.
        self.health.record(name, ok=True, latency_ms=(time.perf_counter() - started) * 1000.0)
        return _Outcome(response=answer)

    async def _backoff(self, name: str, failures_by_provider: dict[str, int]) -> None:
        """Sleep only when coming back to a provider that already failed this request."""
        already_failed = failures_by_provider.get(name, 0)
        if already_failed == 0:
            return
        await self.sleep(self.backoff.delay_s(already_failed - 1, self.jitter()))


async def _release(response: UpstreamResponse) -> None:
    """Give the connection back. A provider that raises on close must not mask the fault."""
    try:
        await response.aclose()
    except Exception:
        # Closing is best effort. The interesting failure is the one upstream, and a
        # provider that raises on the way out must not be allowed to replace it.
        return


def _stamp(ctx: RequestContext, attempts: list[Attempt]) -> None:
    """Write the attempt trail onto the context, for the log line and the ledger row.

    ``failover_hops`` is the number of slots that did **not** serve — a candidate skipped
    by an open breaker counts the same as one that was tried and failed, because the
    question the column answers is "did the primary serve this request" (H-051).
    """
    ctx.failover_attempts = tuple(attempt.label for attempt in attempts)
    ctx.failover_hops = max(0, len(attempts) - 1)
    first_failure = next((attempt for attempt in attempts if attempt.failure is not None), None)
    if first_failure is not None and ctx.failover_hops:
        ctx.failover_from = first_failure.provider
        ctx.failover_error = first_failure.failure


def _with_trail(error: ProviderError, attempts: list[Attempt]) -> ProviderError:
    """The same error class, with the chain's story appended to its message.

    The class is preserved deliberately: the invented status (504 / 502) and the stable
    ``headroom.reason`` both come from it, and Phase 1 fixed those as a compatibility
    surface. Only the human-readable half grows.
    """
    if len(attempts) < 2:
        return error
    trail = " -> ".join(attempt.label for attempt in attempts)
    return type(error)(f"{error.message}; failover chain exhausted: {trail}")
