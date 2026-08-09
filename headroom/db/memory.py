"""The stores in a dict — the keyless ones, and real implementations.

Not mocks. They implement the same interfaces as the Postgres and DynamoDB stores, and
``tests/test_tenant_store.py``, ``tests/test_ledger_store.py``, and
``tests/test_budget_store.py`` each run one contract suite over both implementations, so
a behaviour that holds here holds there or the suite goes red (docs/DECISIONS.md H-021).
That is the only thing that makes a second implementation safe to have.

**One caveat, stated where it cannot be missed:** :class:`InMemoryBudgetStore` reproduces
the *semantics* of the budget gate, not its *concurrency*. Its operations never suspend,
so they cannot interleave, so it can never demonstrate that the design is race-free —
and a stampede test run against it would prove nothing at all. That proof belongs to
DynamoDB Local, and ``tests/test_budget_stampede.py`` is written against it alone.

Why it exists: BUILD_PLAN §0.2 invariant 4 wants the gate keyless, and the parts of
Phase 2 that matter most — the 401/403 matrix, the plaintext-appears-once property, the
revocation window — are about authentication logic, not about SQL. Binding them to a
running container would mean a fresh clone runs a smaller suite than the operator
thinks, which is the exact failure H-012 was written about.

What it is **not**: selectable in production. ``build_gateway`` always constructs the
Postgres store; there is no configuration switch that would let a deployment lose every
tenant on restart.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal

from headroom.core.budgets import (
    ADMIT_EXCEEDED,
    ADMIT_NO_BUDGET,
    ADMIT_RESERVED,
    RESERVATION_TTL_S,
    Budget,
    BudgetScope,
    BudgetStore,
    Reservation,
    ReserveResult,
    SweepResult,
    from_picos,
    to_picos,
    window_for,
)
from headroom.core.ledger import LedgerEntry, LedgerQuery, LedgerStore, UsageTotals
from headroom.core.storage import (
    KeyRecord,
    Tenant,
    TenantNameConflict,
    TenantStore,
    VirtualKey,
)

__all__ = ["InMemoryBudgetStore", "InMemoryLedgerStore", "InMemoryTenantStore"]


def _now() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return str(uuid.uuid4())


class InMemoryTenantStore(TenantStore):
    """Tenants and keys in process memory, insertion-ordered.

    Records are frozen dataclasses, so handing one out cannot let a caller mutate the
    store — the same guarantee the SQL implementation gets for free by copying rows out
    of the database.
    """

    __slots__ = ("_by_hash", "_keys", "_tenants")

    def __init__(self) -> None:
        self._tenants: dict[str, Tenant] = {}
        self._keys: dict[str, VirtualKey] = {}
        #: key hash -> key id. The unique index, by hand.
        self._by_hash: dict[str, str] = {}

    # --- tenants ------------------------------------------------------------------

    async def create_tenant(self, name: str) -> Tenant:
        if any(tenant.name == name for tenant in self._tenants.values()):
            raise TenantNameConflict(f"a tenant named {name!r} already exists")
        now = _now()
        tenant = Tenant(id=_new_id(), name=name, active=True, created_at=now, updated_at=now)
        self._tenants[tenant.id] = tenant
        return tenant

    async def get_tenant(self, tenant_id: str) -> Tenant | None:
        return self._tenants.get(tenant_id)

    async def list_tenants(self) -> list[Tenant]:
        return list(self._tenants.values())

    async def update_tenant(
        self, tenant_id: str, *, name: str | None = None, active: bool | None = None
    ) -> Tenant | None:
        current = self._tenants.get(tenant_id)
        if current is None:
            return None
        taken = any(other.name == name for other in self._tenants.values() if other.id != tenant_id)
        if name is not None and name != current.name and taken:
            raise TenantNameConflict(f"a tenant named {name!r} already exists")
        updated = Tenant(
            id=current.id,
            name=current.name if name is None else name,
            active=current.active if active is None else active,
            created_at=current.created_at,
            updated_at=_now(),
        )
        self._tenants[tenant_id] = updated
        return updated

    # --- keys ---------------------------------------------------------------------

    async def create_key(
        self,
        *,
        tenant_id: str,
        name: str,
        key_hash: str,
        key_prefix: str,
        allowed_models: Sequence[str] = (),
        allowed_providers: Sequence[str] = (),
    ) -> VirtualKey | None:
        if tenant_id not in self._tenants:
            return None
        now = _now()
        key = VirtualKey(
            id=_new_id(),
            tenant_id=tenant_id,
            name=name,
            key_prefix=key_prefix,
            allowed_models=tuple(allowed_models),
            allowed_providers=tuple(allowed_providers),
            created_at=now,
            updated_at=now,
        )
        self._keys[key.id] = key
        self._by_hash[key_hash] = key.id
        return key

    async def get_key(self, key_id: str) -> VirtualKey | None:
        return self._keys.get(key_id)

    async def list_keys(self, tenant_id: str | None = None) -> list[VirtualKey]:
        return [
            key for key in self._keys.values() if tenant_id is None or key.tenant_id == tenant_id
        ]

    async def update_key(
        self,
        key_id: str,
        *,
        name: str | None = None,
        allowed_models: Sequence[str] | None = None,
        allowed_providers: Sequence[str] | None = None,
    ) -> VirtualKey | None:
        current = self._keys.get(key_id)
        if current is None:
            return None
        updated = VirtualKey(
            id=current.id,
            tenant_id=current.tenant_id,
            name=current.name if name is None else name,
            key_prefix=current.key_prefix,
            allowed_models=(
                current.allowed_models if allowed_models is None else tuple(allowed_models)
            ),
            allowed_providers=(
                current.allowed_providers if allowed_providers is None else tuple(allowed_providers)
            ),
            created_at=current.created_at,
            updated_at=_now(),
            revoked_at=current.revoked_at,
        )
        self._keys[key_id] = updated
        return updated

    async def revoke_key(self, key_id: str) -> VirtualKey | None:
        current = self._keys.get(key_id)
        if current is None:
            return None
        if current.revoked:
            # Idempotent, and the original timestamp is the one that matters.
            return current
        revoked = VirtualKey(
            id=current.id,
            tenant_id=current.tenant_id,
            name=current.name,
            key_prefix=current.key_prefix,
            allowed_models=current.allowed_models,
            allowed_providers=current.allowed_providers,
            created_at=current.created_at,
            updated_at=_now(),
            revoked_at=_now(),
        )
        self._keys[key_id] = revoked
        return revoked

    async def find_by_hash(self, key_hash: str) -> KeyRecord | None:
        key_id = self._by_hash.get(key_hash)
        if key_id is None:
            return None
        key = self._keys.get(key_id)
        if key is None:  # pragma: no cover - the two dicts are written together
            return None
        tenant = self._tenants.get(key.tenant_id)
        if tenant is None:  # pragma: no cover - tenants are never deleted
            return None
        return KeyRecord(key=key, tenant=tenant)


class InMemoryLedgerStore(LedgerStore):
    """The cost ledger in a dict, keyed by request id.

    The filtering, ordering, and aggregation below are hand-written to match
    ``headroom/db/ledger.py``'s SQL exactly, because ``tests/test_ledger_store.py``
    asserts the same expectations of both. Where the two could drift — NULL handling in
    a sum, the tiebreak in an ORDER BY — the SQL is the specification and this is the
    implementation of it.
    """

    __slots__ = ("_entries",)

    def __init__(self) -> None:
        #: Insertion-ordered by request id. The unique index, by hand.
        self._entries: dict[str, LedgerEntry] = {}

    async def record(self, entry: LedgerEntry) -> None:
        # `ON CONFLICT (request_id) DO NOTHING`, in Python. A retried write after a
        # crash must not double-bill, so the first row for a request id wins.
        if entry.request_id in self._entries:
            return
        self._entries[entry.request_id] = replace(
            entry, id=entry.id or _new_id(), created_at=entry.created_at or _now()
        )

    async def get(self, request_id: str) -> LedgerEntry | None:
        return self._entries.get(request_id)

    async def list_entries(self, query: LedgerQuery) -> list[LedgerEntry]:
        matched = sorted(
            self._matching(query),
            key=lambda entry: (entry.started_at, entry.request_id),
            reverse=True,
        )
        return matched[query.offset : query.offset + query.limit]

    async def totals(self, query: LedgerQuery, *, by_model: bool = False) -> list[UsageTotals]:
        groups: dict[tuple[str, str | None], list[LedgerEntry]] = {}
        for entry in self._matching(query):
            groups.setdefault((entry.tenant_id, entry.model if by_model else None), []).append(
                entry
            )
        totals = [
            UsageTotals(
                tenant_id=tenant_id,
                model=model,
                requests=len(rows),
                input_tokens=sum(row.input_tokens or 0 for row in rows),
                output_tokens=sum(row.output_tokens or 0 for row in rows),
                reasoning_tokens=sum(row.reasoning_tokens or 0 for row in rows),
                usd_cost=sum(
                    (row.usd_cost for row in rows if row.usd_cost is not None), Decimal(0)
                ),
                unpriced_requests=sum(1 for row in rows if row.usd_cost is None),
                errored_requests=sum(1 for row in rows if row.outcome != "ok"),
            )
            for (tenant_id, model), rows in groups.items()
        ]
        return sorted(
            totals, key=lambda total: (-total.usd_cost, total.tenant_id, total.model or "")
        )

    def _matching(self, query: LedgerQuery) -> list[LedgerEntry]:
        return [entry for entry in self._entries.values() if _matches(entry, query)]


@dataclass(slots=True)
class _BudgetItem:
    """One scope's budget, shaped exactly like the DynamoDB item.

    Counters are integer picodollars here too, and every method below moves
    ``remaining`` in the same statement as its components — not because a dict needs
    the discipline, but because the contract suite asserts the identity
    ``remaining == budget - spent - reserved`` against *both* stores after every
    operation, and an implementation that kept the number some other way would pass
    for the wrong reason.
    """

    scope: BudgetScope
    budget_picos: int
    window: str
    window_id: str
    window_expires_at: int
    spent_picos: int = 0
    reserved_picos: int = 0
    remaining_picos: int = 0
    #: request id -> (picos held, expires_at epoch seconds)
    reservations: dict[str, tuple[int, int]] = field(default_factory=dict)
    expired_releases: int = 0
    expired_released_picos: int = 0
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)

    def view(self) -> Budget:
        return Budget(
            scope=self.scope,
            usd=from_picos(self.budget_picos),
            window=self.window,
            window_id=self.window_id,
            spent=from_picos(self.spent_picos),
            reserved=from_picos(self.reserved_picos),
            remaining=from_picos(self.remaining_picos),
            reservations=len(self.reservations),
            expired_releases=self.expired_releases,
            expired_released=from_picos(self.expired_released_picos),
            created_at=self.created_at,
            updated_at=self.updated_at,
        )

    def rolled_view(self, window_id: str) -> Budget:
        """How this budget reads once its window has ended, without writing anything."""
        return replace(
            self.view(),
            window_id=window_id,
            spent=Decimal(0),
            reserved=Decimal(0),
            remaining=from_picos(self.budget_picos),
            reservations=0,
        )

    def roll(self, window_id: str, expires_at: int) -> None:
        self.window_id = window_id
        self.window_expires_at = expires_at
        self.spent_picos = 0
        self.reserved_picos = 0
        self.remaining_picos = self.budget_picos
        self.reservations.clear()


class InMemoryBudgetStore(BudgetStore):
    """Budgets in a dict, with the DynamoDB store's semantics and none of its races.

    Every rule the conditional writes encode is reimplemented here in the obvious way:
    a hold is refused when ``remaining`` is short, a settlement is a no-op when the hold
    is gone, a stale window rolls on first touch, an expired hold is released and
    counted. What it cannot reproduce is the thing that makes the DynamoDB
    implementation correct — that the check and the deduction are one operation — since
    nothing here can be interleaved. See the module docstring.
    """

    __slots__ = ("_items",)

    def __init__(self) -> None:
        self._items: dict[str, _BudgetItem] = {}

    async def reserve(
        self, scope: BudgetScope, *, request_id: str, usd: Decimal, when: datetime
    ) -> ReserveResult:
        item = self._items.get(scope.key)
        if item is None:
            return ReserveResult(status=ADMIT_NO_BUDGET)

        estimate = to_picos(usd, conservative=True)
        now = int(when.timestamp())

        held = item.reservations.get(request_id)
        if held is not None:
            return ReserveResult(
                status=ADMIT_RESERVED,
                reservation=self._handle(scope, request_id, held, item.window_id),
                budget=item.view(),
            )

        if item.window_expires_at <= now:
            window_id, expires_at = window_for(item.window, when)
            item.roll(window_id, expires_at)

        # The sweep runs before the refusal, not on a timer: a dead process's hold must
        # never be the reason a live request is turned away.
        self._release_expired(item, now)

        if item.remaining_picos < estimate:
            return ReserveResult(status=ADMIT_EXCEEDED, budget=item.view())

        expires = now + RESERVATION_TTL_S
        item.remaining_picos -= estimate
        item.reserved_picos += estimate
        item.reservations[request_id] = (estimate, expires)
        item.updated_at = when
        return ReserveResult(
            status=ADMIT_RESERVED,
            reservation=self._handle(scope, request_id, (estimate, expires), item.window_id),
            budget=item.view(),
        )

    async def settle(self, reservation: Reservation, *, usd: Decimal, when: datetime) -> bool:
        item = self._items.get(reservation.scope.key)
        if item is None:
            return False
        held = item.reservations.get(reservation.request_id)
        if held is None or held[0] != to_picos(reservation.usd):
            return False
        actual = to_picos(usd)
        del item.reservations[reservation.request_id]
        item.reserved_picos -= held[0]
        item.spent_picos += actual
        item.remaining_picos += held[0] - actual
        item.updated_at = when
        return True

    async def sweep_expired(self, scope: BudgetScope, *, when: datetime) -> SweepResult:
        item = self._items.get(scope.key)
        if item is None:
            return SweepResult()
        return self._release_expired(item, int(when.timestamp()))

    async def get(self, scope: BudgetScope, *, when: datetime) -> Budget | None:
        item = self._items.get(scope.key)
        if item is None:
            return None
        # The read sweeps, matching the DynamoDB store: an operator asking what is
        # reserved gets the live figure, not one inflated by processes that died.
        self._release_expired(item, int(when.timestamp()))
        if item.window_expires_at <= int(when.timestamp()):
            return item.rolled_view(window_for(item.window, when)[0])
        return item.view()

    async def set_budget(
        self, scope: BudgetScope, *, usd: Decimal, window: str, when: datetime
    ) -> Budget:
        budget = to_picos(usd)
        window_id, expires_at = window_for(window, when)
        item = self._items.get(scope.key)
        if item is None:
            item = _BudgetItem(
                scope=scope,
                budget_picos=budget,
                window=window,
                window_id=window_id,
                window_expires_at=expires_at,
                remaining_picos=budget,
                created_at=when,
                updated_at=when,
            )
            self._items[scope.key] = item
            return item.view()

        if item.window != window or item.window_expires_at <= int(when.timestamp()):
            # A different window type counts a different thing, so the counters start
            # again rather than carrying a total that would answer neither question.
            item.window = window
            item.budget_picos = budget
            item.roll(window_id, expires_at)
        else:
            # Same window: move `remaining` by the delta so spend and live holds survive.
            item.remaining_picos += budget - item.budget_picos
            item.budget_picos = budget
        item.updated_at = when
        return item.view()

    async def clear_budget(self, scope: BudgetScope) -> bool:
        return self._items.pop(scope.key, None) is not None

    async def list_budgets(self, *, when: datetime) -> list[Budget]:
        now = int(when.timestamp())
        views = [
            (
                item.rolled_view(window_for(item.window, when)[0])
                if item.window_expires_at <= now
                else item.view()
            )
            for item in self._items.values()
        ]
        return sorted(views, key=lambda budget: budget.scope.key)

    def _release_expired(self, item: _BudgetItem, now: int) -> SweepResult:
        expired = [rid for rid, (_, exp) in item.reservations.items() if exp <= now]
        total = 0
        for request_id in expired:
            amount, _ = item.reservations.pop(request_id)
            item.reserved_picos -= amount
            item.remaining_picos += amount
            item.expired_releases += 1
            item.expired_released_picos += amount
            total += amount
        return SweepResult(released=len(expired), usd=from_picos(total))

    @staticmethod
    def _handle(
        scope: BudgetScope, request_id: str, held: tuple[int, int], window_id: str
    ) -> Reservation:
        amount, expires = held
        return Reservation(
            scope=scope,
            request_id=request_id,
            usd=from_picos(amount),
            window_id=window_id,
            expires_at=datetime.fromtimestamp(expires, tz=UTC),
        )


def _matches(entry: LedgerEntry, query: LedgerQuery) -> bool:
    """The ``_WHERE`` clause of ``headroom/db/ledger.py``, term for term."""
    return (
        (query.tenant_id is None or entry.tenant_id == query.tenant_id)
        and (query.key_id is None or entry.key_id == query.key_id)
        and (query.model is None or entry.model == query.model)
        and (query.provider is None or entry.provider == query.provider)
        and (query.outcome is None or entry.outcome == query.outcome)
        # Half-open, so adjacent windows neither overlap nor drop a row between them.
        and (query.since is None or entry.started_at >= query.since)
        and (query.until is None or entry.started_at < query.until)
    )
