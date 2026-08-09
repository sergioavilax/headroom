"""``TenantStore`` in a dict — the keyless one, and a real implementation.

Not a mock. It implements the same interface as the Postgres store, and
``tests/test_tenant_store.py`` runs one contract suite over both, so a behaviour that
holds here holds there or the suite goes red (docs/DECISIONS.md H-021). That is the
only thing that makes a second implementation safe to have.

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
from datetime import UTC, datetime

from headroom.core.storage import (
    KeyRecord,
    Tenant,
    TenantNameConflict,
    TenantStore,
    VirtualKey,
)

__all__ = ["InMemoryTenantStore"]


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
