"""``TenantStore`` on Postgres — the one the gateway actually runs.

Hand-written SQL against ``migrations/0001_tenants_and_virtual_keys.sql``, as H-003
committed to. Three habits run through every statement here:

* **``RETURNING *`` on every write**, so the row a caller gets back is the row the
  database holds, including the defaults it filled in. An admin API that echoes its own
  input has not proved anything happened.
* **Partial updates are expressed with ``COALESCE($n, column)``**, so "leave this alone"
  is a NULL parameter rather than a query built by string concatenation. One prepared
  statement per operation, no dynamic SQL.
* **``updated_at`` is set by the statement that changes the row.** A trigger would be
  tidier and would also be invisible in a file review; the plan's schema is small
  enough that explicit is better.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

import asyncpg

from headroom.core.storage import (
    KeyRecord,
    Tenant,
    TenantNameConflict,
    TenantStore,
    VirtualKey,
)
from headroom.db.pool import DatabasePool

__all__ = ["PostgresTenantStore"]

_TENANT_COLUMNS = "id, name, active, created_at, updated_at"
_KEY_COLUMNS = (
    "id, tenant_id, name, key_prefix, allowed_models, allowed_providers, "
    "created_at, updated_at, revoked_at"
)


def _tenant(row: asyncpg.Record) -> Tenant:
    return Tenant(
        id=str(row["id"]),
        name=row["name"],
        active=row["active"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _key(row: asyncpg.Record) -> VirtualKey:
    return VirtualKey(
        id=str(row["id"]),
        tenant_id=str(row["tenant_id"]),
        name=row["name"],
        key_prefix=row["key_prefix"],
        allowed_models=tuple(row["allowed_models"] or ()),
        allowed_providers=tuple(row["allowed_providers"] or ()),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        revoked_at=row["revoked_at"],
    )


class PostgresTenantStore(TenantStore):
    """The control plane's tables, behind the interface everything else uses."""

    __slots__ = ("_owns_pool", "pool")

    def __init__(self, pool: DatabasePool | None = None, *, url: str | None = None) -> None:
        self._owns_pool = pool is None
        self.pool = pool if pool is not None else DatabasePool(url)

    # --- tenants ------------------------------------------------------------------

    async def create_tenant(self, name: str) -> Tenant:
        async with self.pool.connection() as conn:
            try:
                row = await conn.fetchrow(
                    f"INSERT INTO tenants (name) VALUES ($1) RETURNING {_TENANT_COLUMNS}",
                    name,
                )
            except asyncpg.UniqueViolationError as exc:
                raise TenantNameConflict(f"a tenant named {name!r} already exists") from exc
        assert row is not None  # RETURNING on a successful INSERT always yields a row
        return _tenant(row)

    async def get_tenant(self, tenant_id: str) -> Tenant | None:
        row = await self._fetch_by_id(
            f"SELECT {_TENANT_COLUMNS} FROM tenants WHERE id = $1", tenant_id
        )
        return None if row is None else _tenant(row)

    async def list_tenants(self) -> list[Tenant]:
        async with self.pool.connection() as conn:
            rows = await conn.fetch(
                f"SELECT {_TENANT_COLUMNS} FROM tenants ORDER BY created_at, id"
            )
        return [_tenant(row) for row in rows]

    async def update_tenant(
        self, tenant_id: str, *, name: str | None = None, active: bool | None = None
    ) -> Tenant | None:
        try:
            row = await self._fetch_by_id(
                f"""
                UPDATE tenants
                   SET name       = COALESCE($2, name),
                       active     = COALESCE($3, active),
                       updated_at = now()
                 WHERE id = $1
             RETURNING {_TENANT_COLUMNS}
                """,
                tenant_id,
                name,
                active,
            )
        except asyncpg.UniqueViolationError as exc:
            raise TenantNameConflict(f"a tenant named {name!r} already exists") from exc
        return None if row is None else _tenant(row)

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
        if not _is_uuid(tenant_id):
            return None
        async with self.pool.connection() as conn:
            try:
                row = await conn.fetchrow(
                    f"""
                    INSERT INTO virtual_keys
                        (tenant_id, name, key_hash, key_prefix,
                         allowed_models, allowed_providers)
                    VALUES ($1, $2, $3, $4, $5, $6)
                 RETURNING {_KEY_COLUMNS}
                    """,
                    tenant_id,
                    name,
                    key_hash,
                    key_prefix,
                    list(allowed_models),
                    list(allowed_providers),
                )
            except asyncpg.ForeignKeyViolationError:
                # No such tenant. `None` rather than an exception, because the admin
                # API's answer to it is a 404 about the tenant, not a 500 about us.
                return None
        return None if row is None else _key(row)

    async def get_key(self, key_id: str) -> VirtualKey | None:
        row = await self._fetch_by_id(
            f"SELECT {_KEY_COLUMNS} FROM virtual_keys WHERE id = $1", key_id
        )
        return None if row is None else _key(row)

    async def list_keys(self, tenant_id: str | None = None) -> list[VirtualKey]:
        if tenant_id is not None and not _is_uuid(tenant_id):
            return []
        async with self.pool.connection() as conn:
            rows = await conn.fetch(
                f"""
                SELECT {_KEY_COLUMNS} FROM virtual_keys
                 WHERE $1::uuid IS NULL OR tenant_id = $1::uuid
              ORDER BY created_at, id
                """,
                tenant_id,
            )
        return [_key(row) for row in rows]

    async def update_key(
        self,
        key_id: str,
        *,
        name: str | None = None,
        allowed_models: Sequence[str] | None = None,
        allowed_providers: Sequence[str] | None = None,
    ) -> VirtualKey | None:
        row = await self._fetch_by_id(
            f"""
            UPDATE virtual_keys
               SET name              = COALESCE($2, name),
                   allowed_models    = COALESCE($3, allowed_models),
                   allowed_providers = COALESCE($4, allowed_providers),
                   updated_at        = now()
             WHERE id = $1
         RETURNING {_KEY_COLUMNS}
            """,
            key_id,
            name,
            None if allowed_models is None else list(allowed_models),
            None if allowed_providers is None else list(allowed_providers),
        )
        return None if row is None else _key(row)

    async def revoke_key(self, key_id: str) -> VirtualKey | None:
        # COALESCE keeps the first revocation's timestamp: revoking twice during an
        # incident must not rewrite when the key actually died.
        row = await self._fetch_by_id(
            f"""
            UPDATE virtual_keys
               SET revoked_at = COALESCE(revoked_at, now()),
                   updated_at = now()
             WHERE id = $1
         RETURNING {_KEY_COLUMNS}
            """,
            key_id,
        )
        return None if row is None else _key(row)

    async def find_by_hash(self, key_hash: str) -> KeyRecord | None:
        async with self.pool.connection() as conn:
            row = await conn.fetchrow(
                """
                SELECT k.id, k.tenant_id, k.name, k.key_prefix, k.allowed_models,
                       k.allowed_providers, k.created_at, k.updated_at, k.revoked_at,
                       t.name       AS tenant_name,
                       t.active     AS tenant_active,
                       t.created_at AS tenant_created_at,
                       t.updated_at AS tenant_updated_at
                  FROM virtual_keys k
                  JOIN tenants t ON t.id = k.tenant_id
                 WHERE k.key_hash = $1
                """,
                key_hash,
            )
        if row is None:
            return None
        tenant = Tenant(
            id=str(row["tenant_id"]),
            name=row["tenant_name"],
            active=row["tenant_active"],
            created_at=row["tenant_created_at"],
            updated_at=row["tenant_updated_at"],
        )
        return KeyRecord(key=_key(row), tenant=tenant)

    # --- plumbing -----------------------------------------------------------------

    async def _fetch_by_id(self, sql: str, entity_id: str, *args: Any) -> asyncpg.Record | None:
        """Run a statement whose first parameter is a UUID primary key.

        A non-UUID id is "not found", not a 500: the admin API's path parameters are
        strings from the outside world, and ``/admin/keys/banana`` deserves a 404
        rather than a Postgres cast error.
        """
        if not _is_uuid(entity_id):
            return None
        async with self.pool.connection() as conn:
            return await conn.fetchrow(sql, entity_id, *args)

    async def aclose(self) -> None:
        if self._owns_pool:
            await self.pool.aclose()


def _is_uuid(value: str) -> bool:
    try:
        UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return True
