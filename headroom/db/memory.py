"""The stores in a dict — the keyless ones, and real implementations.

Not mocks. They implement the same interfaces as the Postgres stores, and
``tests/test_tenant_store.py`` and ``tests/test_ledger_store.py`` each run one contract
suite over both, so a behaviour that holds here holds there or the suite goes red
(docs/DECISIONS.md H-021). That is the only thing that makes a second implementation
safe to have.

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
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from headroom.core.ledger import LedgerEntry, LedgerQuery, LedgerStore, UsageTotals
from headroom.core.storage import (
    KeyRecord,
    Tenant,
    TenantNameConflict,
    TenantStore,
    VirtualKey,
)

__all__ = ["InMemoryLedgerStore", "InMemoryTenantStore"]


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
