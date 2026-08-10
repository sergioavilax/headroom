"""The cost ledger's records and its storage contract.

``core/`` holds storage *interfaces* (BUILD_PLAN §0.5); the implementations live in
``headroom/db/``. This file is the ledger's half of that split, kept separate from
``core/storage.py`` because the control plane and the ledger have nothing to do with
each other beyond a foreign key — one is read on every request's hot path, the other
is written after every request has finished.

**A ledger row is an invoice line, so it is self-contained.** It carries the rates it
was priced at, not a pointer to them. ``config/models.yaml`` can be edited, a vendor
can publish new prices, a model can be retired — and every row already written still
says what it cost and why. That is the D-017 guarantee expressed as a schema decision
rather than as a promise (docs/DECISIONS.md H-024).

**Nothing is deleted, and the rows point at things that also are not.** ``tenant_id``
and ``key_id`` are foreign keys onto the Phase 2 tables, which revoke and deactivate
rather than delete precisely so these references stay valid forever (H-022). There are
no cascades: a row that vanished would turn a historical invoice into an orphan.

**Every request that reached a tenant gets a row — including the failures.** Phase 8's
H2 publishes overhead percentiles and error accounting from this table, and an
error-free table would make both meaningless. What a failure costs is decided in
``headroom/metering/cost.py``; what is *not* here is the unauthenticated 401, which has
no tenant to attribute and would be an unattributable row (H-025).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

__all__ = ["LedgerEntry", "LedgerQuery", "LedgerStore", "UsageTotals", "format_usd"]


def format_usd(value: Decimal | None) -> str | None:
    """The one way money becomes a string, everywhere it leaves the process.

    Plain decimal notation, always. ``str(Decimal("0.000000000000"))`` is ``"0E-12"``
    — correct, parseable, and a surprise in a JSON field a dashboard is about to
    render; ``format(…, "f")`` gives ``"0.000000000000"`` and never switches to
    exponent notation at either end of the range.

    A string rather than a number because JSON has exactly one numeric type and it is
    a double. Serializing a ``NUMERIC`` as a JSON number would undo, at the last step,
    the exactness the price file, the Decimal arithmetic, and the column type all
    exist to preserve.
    """
    return None if value is None else format(value, "f")


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    """One completed request, priced and attributed.

    Field order mirrors the migration so a reader can hold both open side by side.
    """

    # --- identity and attribution -------------------------------------------------
    request_id: str
    tenant_id: str
    key_id: str
    route: str
    dialect: str
    model: str
    started_at: datetime

    provider: str | None = None
    streamed: bool = False

    # --- how it went ---------------------------------------------------------------
    outcome: str = "ok"
    status_code: int | None = None
    upstream_status: int | None = None
    error_source: str | None = None
    error_reason: str | None = None
    #: Anthropic's ``stop_reason`` / OpenAI's ``finish_reason``. Phase 5 consults it
    #: before caching: a truncated answer is never a cacheable one (invariant 6).
    stop_reason: str | None = None

    # --- what the provider said (never what the text implied) ----------------------
    input_tokens: int | None = None
    #: Reasoning-inclusive. The Phase 1 live smoke is why this is stated: 11 visible
    #: characters, 63 billed tokens.
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None

    # --- the price that was applied, copied in ------------------------------------
    price_effective_from: date | None = None
    usd_per_mtok_in: Decimal | None = None
    usd_per_mtok_out: Decimal | None = None
    usd_cost: Decimal | None = None
    #: ``priced`` | ``partial`` | ``unpriced_model`` | ``usage_unknown`` | ``not_billable``
    #: — see ``headroom/metering/cost.py``. NULL cost and zero cost are different facts.
    cost_status: str = "usage_unknown"

    # --- what the budget gate did (Phase 4) ----------------------------------------
    #: ``no_budget`` | ``reserved`` | ``exceeded``. On a refusal this row is the *only*
    #: record that the request happened at all — no provider was called, so there is
    #: nothing else to correlate against.
    budget_status: str | None = None
    #: The conservative bound held before the provider ran (``headroom/policy/budgets``).
    budget_reserved_usd: Decimal | None = None
    #: What the hold became. Equals ``usd_cost`` when the cost is known, ``0`` when
    #: nothing was billable, and the *reservation* when the cost is unknown — the one
    #: place the budget and the invoice deliberately disagree (H-031).
    budget_settled_usd: Decimal | None = None

    # --- timings, from the Phase 1 RequestContext ----------------------------------
    upstream_latency_ms: float | None = None
    ttft_ms: float | None = None
    #: The gateway's own cost between first upstream byte and first byte out. Phase
    #: 8's H2 reports the p50 of this column against a pre-registered <50 ms target.
    passthrough_overhead_ms: float | None = None
    total_ms: float | None = None

    # --- what the cache did (Phase 5) ------------------------------------------------
    #: ``cache_hit_exact`` | ``cache_hit_semantic`` | ``cache_miss`` | ``cache_bypass``
    #: | ``cache_disabled``. Every proxied request that reached the gate has one.
    #:
    #: On a hit the row is deliberately *unlike* an upstream call in every way that
    #: matters: ``upstream_status`` and ``provider`` are NULL because no upstream was
    #: involved, the token counts are NULL because nothing was generated, ``usd_cost``
    #: is a measured ``0`` with ``not_billable`` beside it, and the saving lives in its
    #: own column below rather than borrowing one that means spend.
    cache_disposition: str | None = None
    #: What this hit would have cost, from the entry's own recorded cost. NULL when that
    #: cost was never known, so a savings total can never quietly add a zero (H-025).
    cache_avoided_usd: Decimal | None = None
    #: Cosine similarity of a semantic hit; NULL for exact. §P8.H1 reads this column.
    cache_similarity: Decimal | None = None
    #: The request that populated the entry — the provenance that makes a semantic hit
    #: auditable after the fact, and the answer key for cache correctness.
    cache_source_request_id: str | None = None

    # --- what failover did (Phase 6) -------------------------------------------------
    #: Chain slots that did not serve. Zero means the primary served it. A candidate the
    #: circuit breaker skipped counts the same as one that was tried and failed: the
    #: question is whether the request went where it was routed.
    failover_hops: int = 0
    #: The first candidate passed over, and why. NULL when ``failover_hops`` is 0.
    #: Complementary to ``error_reason`` / ``upstream_status``, which carry the *last*
    #: failure — the one the caller was handed when a chain ran out (H-051).
    failover_from: str | None = None
    failover_error: str | None = None

    # --- assigned by the store -----------------------------------------------------
    id: str | None = None
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class LedgerQuery:
    """Filters for reading the ledger back. Every field is optional and ANDed.

    Deliberately boring and closed: the admin surface is read-only and the filters are
    exactly the ones the Phase 7 dashboard needs. No free-text predicate reaches SQL.
    """

    tenant_id: str | None = None
    key_id: str | None = None
    model: str | None = None
    provider: str | None = None
    outcome: str | None = None
    #: Half-open on the request's own ``started_at``: ``since <= started_at < until``.
    since: datetime | None = None
    until: datetime | None = None
    limit: int = 100
    offset: int = 0


@dataclass(frozen=True, slots=True)
class UsageTotals:
    """Aggregated spend for one group — a tenant, or a tenant and model pair.

    ``usd_cost`` sums only the rows that have one. ``unpriced_requests`` counts the
    rows it could not include, so the total is never quietly understated: a caller can
    always see how much of the picture is missing, which a bare sum would hide.
    """

    tenant_id: str
    model: str | None = None
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    usd_cost: Decimal = field(default_factory=lambda: Decimal(0))
    unpriced_requests: int = 0
    errored_requests: int = 0


class LedgerStore(ABC):
    """Where priced requests go, and how they come back.

    Two implementations, one contract suite over both — the Phase 2 shape
    (docs/DECISIONS.md H-021), for the same reason: the reporting logic is worth
    testing keylessly, and a second implementation that quietly disagrees with the
    first turns a green suite into a claim about nothing.
    """

    @abstractmethod
    async def record(self, entry: LedgerEntry) -> None:
        """Persist one row.

        **Idempotent on ``request_id``.** A retried write must not double-bill, and a
        gateway that crashes between the response and the row has to be free to try
        again. Re-recording an existing request id is a no-op, not an error and not a
        second row.
        """

    @abstractmethod
    async def list_entries(self, query: LedgerQuery) -> list[LedgerEntry]:
        """Matching rows, newest request first."""

    @abstractmethod
    async def totals(self, query: LedgerQuery, *, by_model: bool = False) -> list[UsageTotals]:
        """Aggregate matching rows per tenant, or per tenant and model."""

    @abstractmethod
    async def get(self, request_id: str) -> LedgerEntry | None:
        """One row by request id — the path from a caller's screenshot to its cost."""

    async def aclose(self) -> None:
        """Release resources. A no-op for stores that hold none."""
        return None
