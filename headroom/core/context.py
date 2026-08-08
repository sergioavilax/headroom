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
from typing import Any, Final

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
    # `tenant_id` is a placeholder in Phase 1 and stays None: virtual keys and the
    # tenancy that fills it arrive in Phase 2, which sets this field and nothing else.
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
            "model": self.model,
            "provider": self.provider,
            "stream": self.stream,
            "outcome": self.outcome,
            "status": self.status_code,
            "upstream_status": self.upstream_status,
            "error_source": self.error_source,
            "error_reason": self.error_reason,
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
