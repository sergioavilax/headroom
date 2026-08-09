"""Storage interfaces for the control plane, and the records that cross them.

BUILD_PLAN §0.5 gives ``core/`` the storage *interfaces*; the implementations live in
``headroom/db/``. That split is not ceremony. Two things depend on it:

* **Phase 2's gate has to run keylessly and without a service** for the parts that are
  about authentication rather than about SQL. The auth matrix, the admin CRUD surface,
  and the cache's revocation window are all logic, and logic that can only be tested
  against a running Postgres is logic that gets tested less.
* **Phase 9 swaps the endpoint, not the code.** RDS is the same ``TenantStore``.

The obvious risk of a second implementation is drift: an in-memory store that quietly
disagrees with the SQL one turns a green suite into a lie. That is answered by
``tests/test_tenant_store.py``, which is a single contract suite parametrised over
*both* implementations — every behaviour asserted here is asserted against Postgres
too, or it is not asserted at all (docs/DECISIONS.md H-021).

**What is deliberately absent from these records: the key.** A ``VirtualKey`` carries a
hash and a display prefix and has no field a plaintext could live in, so "the plaintext
is returned exactly once" is a property of the type system rather than of a reviewer's
attention (H-017).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

__all__ = [
    "KeyRecord",
    "Tenant",
    "TenantNameConflict",
    "TenantStore",
    "VirtualKey",
]


class TenantNameConflict(Exception):
    """A tenant with that name already exists. Surfaced by the admin API as 409."""


@dataclass(frozen=True, slots=True)
class Tenant:
    """One customer of the gateway. Deactivated, never deleted."""

    id: str
    name: str
    active: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class VirtualKey:
    """One ``hk_...`` credential — everything about it except the credential.

    ``allowed_models`` / ``allowed_providers`` are scopes: **empty means unrestricted**.
    An empty tuple and "restricted to nothing" would be the same value if this were
    nullable, so it is not; the meaning of empty is fixed here and matched by
    :func:`headroom.policy.keys.scope_allows`.
    """

    id: str
    tenant_id: str
    name: str
    key_prefix: str
    allowed_models: tuple[str, ...]
    allowed_providers: tuple[str, ...]
    created_at: datetime
    updated_at: datetime
    revoked_at: datetime | None = None

    @property
    def revoked(self) -> bool:
        """``revoked_at`` *is* the state — there is no boolean to disagree with it."""
        return self.revoked_at is not None


@dataclass(frozen=True, slots=True)
class KeyRecord:
    """A key together with its tenant: what authenticating a request needs.

    One record rather than two lookups, because it is one indexed join and it happens
    on the hot path of every proxied request.
    """

    key: VirtualKey
    tenant: Tenant


class TenantStore(ABC):
    """The control plane's persistence contract.

    Every method that looks something up returns ``None`` when it is not there rather
    than raising: "no such key" is the single most common answer this store gives, and
    it is not exceptional.
    """

    # --- tenants ------------------------------------------------------------------

    @abstractmethod
    async def create_tenant(self, name: str) -> Tenant:
        """Create a tenant. Raises :class:`TenantNameConflict` on a duplicate name."""

    @abstractmethod
    async def get_tenant(self, tenant_id: str) -> Tenant | None: ...

    @abstractmethod
    async def list_tenants(self) -> list[Tenant]:
        """All tenants, oldest first."""

    @abstractmethod
    async def update_tenant(
        self, tenant_id: str, *, name: str | None = None, active: bool | None = None
    ) -> Tenant | None:
        """Patch a tenant. ``None`` means "leave this field alone", not "set to null"."""

    # --- keys ---------------------------------------------------------------------

    @abstractmethod
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
        """Store a new key, or ``None`` if the tenant does not exist.

        The caller mints the secret and hands over its hash; no implementation of this
        interface ever sees a plaintext key.
        """

    @abstractmethod
    async def get_key(self, key_id: str) -> VirtualKey | None: ...

    @abstractmethod
    async def list_keys(self, tenant_id: str | None = None) -> list[VirtualKey]:
        """All keys, oldest first, optionally narrowed to one tenant."""

    @abstractmethod
    async def update_key(
        self,
        key_id: str,
        *,
        name: str | None = None,
        allowed_models: Sequence[str] | None = None,
        allowed_providers: Sequence[str] | None = None,
    ) -> VirtualKey | None:
        """Patch a key's mutable fields. Scope is replaced wholesale, never merged."""

    @abstractmethod
    async def revoke_key(self, key_id: str) -> VirtualKey | None:
        """Revoke a key. Idempotent: re-revoking keeps the original ``revoked_at``.

        Idempotence matters more than it looks. Revocation is what an operator reaches
        for during an incident, usually twice, and a second call that moved the
        timestamp would rewrite the one fact the incident review needs.
        """

    @abstractmethod
    async def find_by_hash(self, key_hash: str) -> KeyRecord | None:
        """The authentication lookup: a key hash to a key and its tenant, or ``None``.

        Called on every proxied request that misses the auth cache, so it is one
        statement against a unique index.
        """

    async def aclose(self) -> None:
        """Release resources. A no-op for stores that hold none."""
        return None
