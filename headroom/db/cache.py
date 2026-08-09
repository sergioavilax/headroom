"""``ResponseCacheStore`` on Postgres + pgvector — the one the gateway actually runs.

Hand-written SQL against ``migrations/0005_response_cache.sql``, with the habits
``headroom/db/tenants.py`` and ``headroom/db/ledger.py`` already established: one
prepared statement per operation, ``RETURNING`` on writes, nothing built by
concatenation.

Two things here are specific to this table and worth reading before the code.

**Every statement names a tenant, and the planner is not trusted to remember that.**
There is no ``WHERE request_hash = $1`` anywhere: the exact lookup is
``WHERE tenant_id = $1 AND request_hash = $2`` against a unique index that leads with
``tenant_id``, and the semantic search filters on the whole namespace before pgvector is
allowed to order anything. The exact key is *also* salted with the namespace
(``headroom/cache/keys.py``), so a hash from one tenant cannot collide with another's
even in a database where the predicate has been removed — which is precisely the state
``tests/test_cache_isolation.py`` puts it in, on purpose.

**Vectors cross the wire as text, and that is a dependency decision.** ``pgvector`` ships
an asyncpg codec; using it would add a runtime dependency for one type. Instead the
vector is rendered as ``'[0.1,0.2,…]'`` and cast with ``$n::vector``, which is pgvector's
own documented input format, and read back with ``embedding::text``. Postgres does the
parsing either way; this way ``uv.lock`` does not grow a package to do what a cast
already does.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg

from headroom.core.cache import (
    CacheEntry,
    CacheNamespace,
    CacheStats,
    ResponseCacheStore,
    SemanticMatch,
)
from headroom.db.pool import DatabasePool

__all__ = ["PostgresResponseCacheStore"]

_COLUMNS = (
    "id, tenant_id, dialect, model, transport, request_hash, context_hash, "
    "body, content_type, stop_reason, input_tokens, output_tokens, reasoning_tokens, "
    "usd_cost, cost_status, source_request_id, embedding_model, embedding::text AS embedding, "
    "probe, expires_at, created_at"
)


def _vector_literal(values: Sequence[float]) -> str:
    """pgvector's own text input format. ``repr`` on a float round-trips exactly."""
    return "[" + ",".join(repr(float(value)) for value in values) + "]"


def _parse_vector(text: str | None) -> tuple[float, ...] | None:
    if text is None:
        return None
    return tuple(float(part) for part in text.strip("[]").split(",") if part)


def _entry(row: asyncpg.Record) -> CacheEntry:
    return CacheEntry(
        tenant_id=str(row["tenant_id"]),
        dialect=row["dialect"],
        model=row["model"],
        transport=row["transport"],
        request_hash=row["request_hash"],
        context_hash=row["context_hash"],
        body=bytes(row["body"]),
        content_type=row["content_type"],
        stop_reason=row["stop_reason"],
        input_tokens=row["input_tokens"],
        output_tokens=row["output_tokens"],
        reasoning_tokens=row["reasoning_tokens"],
        usd_cost=row["usd_cost"],
        cost_status=row["cost_status"],
        source_request_id=row["source_request_id"],
        embedding_model=row["embedding_model"],
        embedding=_parse_vector(row["embedding"]),
        probe=row["probe"],
        expires_at=row["expires_at"],
        created_at=row["created_at"],
        id=str(row["id"]),
    )


class PostgresResponseCacheStore(ResponseCacheStore):
    """The cache table, behind the interface the gate uses."""

    __slots__ = ("_owns_pool", "pool")

    def __init__(self, pool: DatabasePool | None = None, *, url: str | None = None) -> None:
        self._owns_pool = pool is None
        self.pool = pool if pool is not None else DatabasePool(url)

    async def get_exact(
        self, namespace: CacheNamespace, *, request_hash: str, when: datetime
    ) -> CacheEntry | None:
        if not _is_uuid(namespace.tenant_id):
            return None
        async with self.pool.connection() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT {_COLUMNS} FROM response_cache
                 WHERE tenant_id    = $1
                   AND request_hash = $2
                   AND expires_at   > $3
                """,
                namespace.tenant_id,
                request_hash,
                when,
            )
        return None if row is None else _entry(row)

    async def search(
        self,
        namespace: CacheNamespace,
        *,
        embedding: Sequence[float],
        context_hash: str,
        embedding_model: str,
        threshold: float,
        limit: int = 1,
        when: datetime,
    ) -> list[SemanticMatch]:
        if not _is_uuid(namespace.tenant_id):
            return []
        async with self.pool.connection() as conn:
            rows = await conn.fetch(
                f"""
                SELECT {_COLUMNS}, 1 - (embedding <=> $5::vector) AS similarity
                  FROM response_cache
                 WHERE tenant_id       = $1
                   AND dialect         = $2
                   AND model           = $3
                   AND transport       = $4
                   AND context_hash    = $6
                   AND embedding_model = $7
                   AND embedding IS NOT NULL
                   AND expires_at      > $8
                   -- The bar is applied here, in the statement, and not by the index.
                   -- An approximate index can therefore cost a saving (a neighbour it
                   -- did not return) and can never cost a wrong answer (one below the
                   -- threshold that it did).
                   AND 1 - (embedding <=> $5::vector) >= $9
              ORDER BY embedding <=> $5::vector, request_hash
                 LIMIT $10
                """,
                namespace.tenant_id,
                namespace.dialect,
                namespace.model,
                namespace.transport,
                _vector_literal(embedding),
                context_hash,
                embedding_model,
                when,
                threshold,
                limit,
            )
        return [
            SemanticMatch(entry=_entry(row), similarity=float(row["similarity"])) for row in rows
        ]

    async def put(self, entry: CacheEntry) -> CacheEntry:
        async with self.pool.connection() as conn:
            row = await conn.fetchrow(
                f"""
                INSERT INTO response_cache
                    (tenant_id, dialect, model, transport, request_hash, context_hash,
                     body, content_type, stop_reason, input_tokens, output_tokens,
                     reasoning_tokens, usd_cost, cost_status, source_request_id,
                     embedding_model, embedding, probe, expires_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15,
                        $16, $17::vector, $18, $19)
           ON CONFLICT (tenant_id, request_hash) DO UPDATE SET
                     dialect           = EXCLUDED.dialect,
                     model             = EXCLUDED.model,
                     transport         = EXCLUDED.transport,
                     context_hash      = EXCLUDED.context_hash,
                     body              = EXCLUDED.body,
                     content_type      = EXCLUDED.content_type,
                     stop_reason       = EXCLUDED.stop_reason,
                     input_tokens      = EXCLUDED.input_tokens,
                     output_tokens     = EXCLUDED.output_tokens,
                     reasoning_tokens  = EXCLUDED.reasoning_tokens,
                     usd_cost          = EXCLUDED.usd_cost,
                     cost_status       = EXCLUDED.cost_status,
                     source_request_id = EXCLUDED.source_request_id,
                     embedding_model   = EXCLUDED.embedding_model,
                     embedding         = EXCLUDED.embedding,
                     probe             = EXCLUDED.probe,
                     expires_at        = EXCLUDED.expires_at,
                     created_at        = now()
              RETURNING {_COLUMNS}
                """,
                entry.tenant_id,
                entry.dialect,
                entry.model,
                entry.transport,
                entry.request_hash,
                entry.context_hash,
                entry.body,
                entry.content_type,
                entry.stop_reason,
                entry.input_tokens,
                entry.output_tokens,
                entry.reasoning_tokens,
                entry.usd_cost,
                entry.cost_status,
                entry.source_request_id,
                entry.embedding_model,
                None if entry.embedding is None else _vector_literal(entry.embedding),
                entry.probe,
                entry.expires_at,
            )
        assert row is not None  # RETURNING on a successful upsert always yields a row
        return _entry(row)

    async def purge_tenant(self, tenant_id: str) -> int:
        if not _is_uuid(tenant_id):
            return 0
        async with self.pool.connection() as conn:
            status = await conn.execute(
                "DELETE FROM response_cache WHERE tenant_id = $1", tenant_id
            )
        return _deleted(status)

    async def delete_expired(self, *, when: datetime) -> int:
        async with self.pool.connection() as conn:
            status = await conn.execute("DELETE FROM response_cache WHERE expires_at <= $1", when)
        return _deleted(status)

    async def stats(self, tenant_id: str) -> CacheStats:
        if not _is_uuid(tenant_id):
            return CacheStats(tenant_id=tenant_id)
        async with self.pool.connection() as conn:
            row = await conn.fetchrow(
                """
                SELECT count(*)                                        AS entries,
                       count(*) FILTER (WHERE embedding IS NOT NULL)   AS semantic_entries,
                       COALESCE(sum(octet_length(body)), 0)            AS body_bytes,
                       min(created_at)                                 AS oldest,
                       max(created_at)                                 AS newest
                  FROM response_cache
                 WHERE tenant_id = $1
                """,
                tenant_id,
            )
        assert row is not None  # an aggregate without GROUP BY always yields one row
        return CacheStats(
            tenant_id=tenant_id,
            entries=row["entries"],
            semantic_entries=row["semantic_entries"],
            body_bytes=int(row["body_bytes"]),
            oldest=row["oldest"],
            newest=row["newest"],
        )

    async def aclose(self) -> None:
        if self._owns_pool:
            await self.pool.aclose()


def _deleted(status: Any) -> int:
    """``DELETE 3`` -> 3. asyncpg reports the command tag, not a count."""
    parts = str(status).split()
    return int(parts[-1]) if parts and parts[-1].isdigit() else 0


def _is_uuid(value: str) -> bool:
    """A non-UUID tenant id owns nothing, rather than raising a cast error at the caller.

    The same rule ``headroom/db/tenants.py`` applies to its path parameters: the admin
    API's ids come from the outside world, and ``/admin/cache/banana`` deserves an empty
    answer rather than a 500.
    """
    try:
        UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return True
