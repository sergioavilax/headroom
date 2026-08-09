"""The request context: one object per request, threaded through every code path.

BUILD_PLAN's Phase 1 text makes this a day-one requirement rather than a Phase 3
convenience: *"a request context object (request id, tenant placeholder, timings)
threads through everything from day one so P3/P7 don't retrofit tracing."* Retrofitted
tracing is always partial — the paths nobody thought about (the error paths, the
mid-stream cut) are exactly the ones that end up unmeasured, and those are the paths
Headroom's experiments are about.

**Timings are monotonic.** Every mark comes from ``time.perf_counter()``, so the
ordering the gate asserts (received ≤ first upstream byte ≤ first token out ≤ complete)
holds by construction rather than by luck — a wall clock can step backwards under NTP
and turn a latency measurement negative. One wall-clock stamp is kept alongside, for
the Phase 3 ledger rows and the dated price lookup, which need a date rather than an
interval.

**Marks are idempotent.** ``mark_first_upstream_byte`` is called on every upstream
chunk and ``mark_first_token_out`` on every forwarded chunk; only the first call of
each records anything. That keeps the call sites free of "have I done this yet"
bookkeeping in the hot loop.
"""

from __future__ import annotations

import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Final

from headroom.core.budgets import Reservation
from headroom.core.ledger import format_usd

__all__ = ["RequestContext", "current_context", "new_request_id"]

# Outcomes a request can end in. Phase 3 writes this to the ledger, Phase 5 consults it
# before caching (a non-"ok" response is never cacheable — invariant 6), and Phase 6
# adds its own for failover hops.
OUTCOME_IN_FLIGHT: Final = "in_flight"
OUTCOME_OK: Final = "ok"
OUTCOME_UPSTREAM_ERROR: Final = "upstream_error"
OUTCOME_CLIENT_DISCONNECT: Final = "client_disconnect"
#: Last resort, recorded by the middleware when nothing more specific was set.
OUTCOME_ERROR: Final = "error"


def new_request_id() -> str:
    """A request id the operator can grep for across logs, ledger, and dashboard."""
    return f"hr_{uuid.uuid4().hex}"


@dataclass(slots=True)
class RequestContext:
    """Everything known about one request in flight, and how long each stage took."""

    request_id: str = field(default_factory=new_request_id)
    route: str = ""
    method: str = "POST"

    # --- who and what ------------------------------------------------------------
    # Filled by `Principal.stamp` the moment a request authenticates (Phase 2). They
    # stay None on requests that never got that far — an anonymous 401 has no tenant,
    # and inventing one would put unattributable rows in Phase 3's ledger.
    tenant_id: str | None = None
    key_id: str | None = None
    dialect: str | None = None
    model: str | None = None
    provider: str | None = None
    stream: bool = False

    # --- how it went -------------------------------------------------------------
    outcome: str = OUTCOME_IN_FLIGHT
    status_code: int | None = None
    upstream_status: int | None = None
    error_source: str | None = None  # "upstream" | "gateway" | None
    error_reason: str | None = None

    # --- what it cost (Phase 3) ---------------------------------------------------
    # Filled by `apply_metering` once the response is over and the provider's usage
    # block has been read. They stay None on a request nothing metered — which is the
    # honest state for one that never reached a provider, and different from zero.
    #
    # These are duplicated onto the ledger row on purpose rather than left to it: the
    # log line reaches stdout *before* the row is queued, so it is the reconstruction
    # path when a process dies with rows in flight (docs/DECISIONS.md H-027).
    input_tokens: int | None = None
    #: Reasoning-inclusive, straight from the usage block. Never counted from text.
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    stop_reason: str | None = None
    usd_cost: Decimal | None = None
    #: See ``headroom/metering/cost.py``: priced | partial | unpriced_model |
    #: usage_unknown | not_billable. A NULL cost and a zero cost are different facts
    #: and this is the field that says which one this is.
    cost_status: str | None = None

    # --- what the budget said (Phase 4) --------------------------------------------
    # Filled by `headroom/policy/budgets.py` at admission and again at settlement, so
    # one request's whole budget story — held this much, was allowed, ended up costing
    # that much — is on the log line and in the ledger row without a join.
    #: no_budget | reserved | exceeded. ``None`` on a request that never got as far as
    #: the gate (an anonymous 401, a body that named no model).
    budget_status: str | None = None
    budget_reserved_usd: Decimal | None = None
    budget_settled_usd: Decimal | None = None
    #: The live hold, from admission until settlement. Not logged and not stored — it
    #: is a handle, and the two amounts above are what it is worth recording. Cleared
    #: by the first settlement so a second one cannot move the counters again.
    budget_reservation: Reservation | None = None

    # --- what the rate limiter said (Phase 4b) --------------------------------------
    # Two fields, not five: a token bucket has no settlement, so there is no "what it
    # ended up costing" to record here. The live numbers a *caller* needs are on the
    # 429's headers, where a client can act on them; these two are for the operator
    # reading a log line and for the Phase 7 dashboard charting which limit bites.
    #: ok | limited. ``None`` means no limit applied to this request at all — an
    #: uncapped tenant and key, or a request refused before the gate ran.
    rate_limit_status: str | None = None
    #: The bucket that refused, as ``tenant:requests`` / ``key:tokens``. Set only on a
    #: refusal: on the way through, no single bucket is worth naming.
    rate_limit_scope: str | None = None

    # --- when --------------------------------------------------------------------
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    received_at: float = field(default_factory=time.perf_counter)
    first_upstream_byte_at: float | None = None
    first_token_out_at: float | None = None
    completed_at: float | None = None

    # --- marks -------------------------------------------------------------------

    def mark_first_upstream_byte(self) -> None:
        """First byte of the upstream response body arrived. Idempotent."""
        if self.first_upstream_byte_at is None:
            self.first_upstream_byte_at = time.perf_counter()

    def mark_first_token_out(self) -> None:
        """First byte released downstream. Idempotent.

        For a streamed response this is the number the product is about; for a
        non-streamed one it is when the complete body was handed to the server.
        """
        if self.first_token_out_at is None:
            self.first_token_out_at = time.perf_counter()

    def complete(
        self,
        outcome: str,
        *,
        status_code: int | None = None,
        error_source: str | None = None,
        error_reason: str | None = None,
    ) -> None:
        """Close the context. The first call wins — a later one cannot rewrite history.

        First-call-wins matters on the failure paths: the proxy records the honest
        outcome (say, ``upstream_stream_cut``) and the middleware's ``finally`` then
        calls ``complete`` as a backstop. Without this rule the backstop would
        overwrite the diagnosis with a generic one.
        """
        if self.completed_at is not None:
            return
        self.completed_at = time.perf_counter()
        self.outcome = outcome
        if status_code is not None:
            self.status_code = status_code
        if error_source is not None:
            self.error_source = error_source
        if error_reason is not None:
            self.error_reason = error_reason

    def apply_metering(
        self,
        *,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        reasoning_tokens: int | None = None,
        cache_read_tokens: int | None = None,
        cache_write_tokens: int | None = None,
        stop_reason: str | None = None,
        usd_cost: Decimal | None = None,
        cost_status: str | None = None,
    ) -> None:
        """Record what the request cost. Called once, by the meter, after completion.

        Unlike :meth:`complete` this is *not* first-call-wins: it is called from
        exactly one place, and a second call would mean a request was metered twice —
        a bug worth seeing in the log rather than swallowing.
        """
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.reasoning_tokens = reasoning_tokens
        self.cache_read_tokens = cache_read_tokens
        self.cache_write_tokens = cache_write_tokens
        self.stop_reason = stop_reason
        self.usd_cost = usd_cost
        self.cost_status = cost_status

    # --- derived durations (milliseconds; None until the mark exists) -------------

    @property
    def upstream_latency_ms(self) -> float | None:
        """Received → first upstream byte. Dominated by the provider, not by us."""
        return self._delta(self.received_at, self.first_upstream_byte_at)

    @property
    def time_to_first_token_ms(self) -> float | None:
        """Received → first byte out. What a streaming caller actually experiences."""
        return self._delta(self.received_at, self.first_token_out_at)

    @property
    def passthrough_overhead_ms(self) -> float | None:
        """First upstream byte → first byte out: the gateway's own cost on the hot path.

        This is *not* the whole answer to "what does the gateway cost" — Phase 8's H2
        gets that by running the same suite with and without the hop. It is the part
        Headroom can measure about itself, per request, and it belongs in the ledger.
        """
        return self._delta(self.first_upstream_byte_at, self.first_token_out_at)

    @property
    def total_ms(self) -> float | None:
        """Received → complete."""
        return self._delta(self.received_at, self.completed_at)

    @staticmethod
    def _delta(start: float | None, end: float | None) -> float | None:
        if start is None or end is None:
            return None
        return (end - start) * 1000.0

    def as_log_fields(self) -> dict[str, Any]:
        """The structured shape written at completion and read by Phase 7."""
        return {
            "request_id": self.request_id,
            "route": self.route,
            "dialect": self.dialect,
            "tenant_id": self.tenant_id,
            # Phase 3 attributes cost per key, not only per tenant: a tenant with one
            # runaway service and four well-behaved ones is a bill nobody can explain
            # from a tenant total alone.
            "key_id": self.key_id,
            "model": self.model,
            "provider": self.provider,
            "stream": self.stream,
            "outcome": self.outcome,
            "status": self.status_code,
            "upstream_status": self.upstream_status,
            "error_source": self.error_source,
            "error_reason": self.error_reason,
            "stop_reason": self.stop_reason,
            # Phase 3. The token counts come from the provider's usage block and never
            # from the response text — a reasoning model bills for tokens that appear
            # nowhere in the content stream. `usd_cost` is a *string*: this line is
            # JSON, and JSON numbers are floats, which is the one representation of
            # money the whole metering path is arranged to avoid.
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "usd_cost": format_usd(self.usd_cost),
            "cost_status": self.cost_status,
            # Phase 4. The budget's whole account of this request: whether a cap
            # applied, what was held before it ran, and what was taken when it
            # finished. Strings for the same reason `usd_cost` is one.
            "budget_status": self.budget_status,
            "budget_reserved_usd": format_usd(self.budget_reserved_usd),
            "budget_settled_usd": format_usd(self.budget_settled_usd),
            # Phase 4b. Which limit applied and, on a refusal, which bucket said no.
            "rate_limit_status": self.rate_limit_status,
            "rate_limit_scope": self.rate_limit_scope,
            "upstream_latency_ms": _round(self.upstream_latency_ms),
            "ttft_ms": _round(self.time_to_first_token_ms),
            "passthrough_overhead_ms": _round(self.passthrough_overhead_ms),
            "total_ms": _round(self.total_ms),
        }


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 3)


# Ambient access for code too deep to be handed the context explicitly (logging,
# future policy hooks). Explicit passing stays the rule — this is the escape hatch,
# not the interface.
_current: ContextVar[RequestContext | None] = ContextVar("headroom_request_context", default=None)


def current_context() -> RequestContext | None:
    """The context for the request being served on this task, if there is one."""
    return _current.get()


def bind_context(ctx: RequestContext) -> None:
    """Make ``ctx`` the ambient context for the current task."""
    _current.set(ctx)
