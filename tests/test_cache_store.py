"""One contract, two response-cache implementations — the H-021 shape, a fourth time.

The stakes here are different from the ledger's. A wrong sum is a wrong invoice; a wrong
*lookup* is a wrong answer, served to a caller who has no way to tell. So the assertions
that matter most in this file are the negative ones: what a store must **not** return.

The Postgres parameter follows H-012 exactly — it skips when the compose stack is down
and ``DATABASE_URL`` was merely inferred, and it **fails** when someone stated the
database is there, which CI does. It also exercises the pieces that only exist in SQL:
the ``vector(384)`` column, the cosine operator, and the HNSW index behind it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import asyncpg
import pytest

from headroom.core.cache import (
    TRANSPORT_BODY,
    TRANSPORT_STREAM,
    CacheEntry,
    CacheNamespace,
    ResponseCacheStore,
)
from headroom.db.cache import PostgresResponseCacheStore
from headroom.db.memory import InMemoryResponseCacheStore
from headroom.db.migrate import run_migrations
from headroom.policy.keys import display_prefix, hash_key, mint_key

from .support.corpus import load_corpus
from .support.services import COMPOSE_DATABASE_URL, resolve_endpoint

DATABASE = resolve_endpoint("DATABASE_URL", COMPOSE_DATABASE_URL)

T0 = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)

TENANT_A = "aaaaaaaa-0000-4000-8000-000000000001"
TENANT_B = "bbbbbbbb-0000-4000-8000-000000000002"

MODEL = "BAAI/bge-small-en-v1.5"


def ns(
    *,
    tenant: str = TENANT_A,
    dialect: str = "anthropic",
    model: str = "mock-model-1",
    transport: str = TRANSPORT_BODY,
) -> CacheNamespace:
    return CacheNamespace(tenant_id=tenant, dialect=dialect, model=model, transport=transport)


def unit(*values: float) -> tuple[float, ...]:
    """A unit vector of the schema's width from a few leading components."""
    padded = list(values) + [0.0] * (384 - len(values))
    norm = sum(value * value for value in padded) ** 0.5
    return tuple(value / norm for value in padded)


def entry(
    request_hash: str,
    *,
    namespace: CacheNamespace | None = None,
    context_hash: str = "ctx",
    body: bytes = b'{"ok":true}',
    embedding: Sequence[float] | None = None,
    embedding_model: str | None = MODEL,
    minutes_to_live: int = 60,
    usd_cost: Decimal | None = Decimal("0.000011500000"),
    source_request_id: str = "hr_source",
) -> CacheEntry:
    space = namespace if namespace is not None else ns()
    return CacheEntry(
        tenant_id=space.tenant_id,
        dialect=space.dialect,
        model=space.model,
        transport=space.transport,
        request_hash=request_hash,
        context_hash=context_hash,
        body=body,
        content_type="application/json",
        stop_reason="end_turn",
        input_tokens=11,
        output_tokens=7,
        usd_cost=usd_cost,
        cost_status="priced",
        source_request_id=source_request_id,
        embedding_model=None if embedding is None else embedding_model,
        embedding=None if embedding is None else tuple(embedding),
        probe=None if embedding is None else "the question",
        expires_at=T0 + timedelta(minutes=minutes_to_live),
    )


# --- fixtures ---------------------------------------------------------------------------


async def _seed_control_plane() -> None:
    """The two tenants the cache's foreign key points at.

    Fixed ids so both parameters really run the same test: Postgres validates them as
    UUIDs and the in-memory store does not care, which is exactly how a contract suite
    ends up only testing one of its two implementations if the ids are invented per store.
    """
    conn = await asyncpg.connect(DATABASE.url, timeout=10)
    try:
        await conn.execute("TRUNCATE response_cache, virtual_keys, tenants CASCADE")
        for tenant_id, name in ((TENANT_A, "acme"), (TENANT_B, "globex")):
            await conn.execute("INSERT INTO tenants (id, name) VALUES ($1, $2)", tenant_id, name)
            plaintext = mint_key()
            await conn.execute(
                "INSERT INTO virtual_keys (tenant_id, name, key_hash, key_prefix) "
                "VALUES ($1, $2, $3, $4)",
                tenant_id,
                name,
                hash_key(plaintext),
                display_prefix(plaintext),
            )
    finally:
        await conn.close()


@asynccontextmanager
async def open_postgres_cache() -> AsyncIterator[ResponseCacheStore]:
    if DATABASE.skip_reason is not None:
        pytest.skip(DATABASE.skip_reason)
    if not DATABASE.reachable:
        pytest.fail(f"DATABASE_URL was set to {DATABASE.url} and nothing is listening there")
    await run_migrations(DATABASE.url)
    await _seed_control_plane()
    store = PostgresResponseCacheStore(url=DATABASE.url)
    try:
        yield store
    finally:
        await store.aclose()
        conn = await asyncpg.connect(DATABASE.url, timeout=10)
        try:
            await conn.execute("TRUNCATE response_cache, virtual_keys, tenants CASCADE")
        finally:
            await conn.close()


@pytest.fixture(params=["memory", "postgres"])
async def cache(request: pytest.FixtureRequest) -> AsyncIterator[ResponseCacheStore]:
    """The store under test — the same tests, both implementations."""
    if request.param == "memory":
        yield InMemoryResponseCacheStore()
        return
    async with open_postgres_cache() as store:
        yield store


# --- the exact layer ----------------------------------------------------------------------


async def test_a_stored_entry_comes_back_whole(cache: ResponseCacheStore) -> None:
    await cache.put(entry("k1", body=b'{"answer":42}'))

    found = await cache.get_exact(ns(), request_hash="k1", when=T0)

    assert found is not None
    assert found.body == b'{"answer":42}'
    assert found.stop_reason == "end_turn"
    assert found.usd_cost == Decimal("0.000011500000")
    assert found.source_request_id == "hr_source"
    assert found.created_at is not None


async def test_an_unknown_key_is_a_miss(cache: ResponseCacheStore) -> None:
    assert await cache.get_exact(ns(), request_hash="nope", when=T0) is None


async def test_a_second_put_replaces_the_first(cache: ResponseCacheStore) -> None:
    """``ON CONFLICT ... DO UPDATE``: both answer a byte-identical request, and the newer
    one carries the fresher expiry."""
    await cache.put(entry("k1", body=b"first"))
    await cache.put(entry("k1", body=b"second", minutes_to_live=120))

    found = await cache.get_exact(ns(), request_hash="k1", when=T0)
    assert found is not None
    assert found.body == b"second"
    stats = await cache.stats(TENANT_A)
    assert stats.entries == 1


async def test_an_expired_entry_is_never_returned(cache: ResponseCacheStore) -> None:
    await cache.put(entry("k1", minutes_to_live=10))

    async def at(minutes: int) -> object:
        return await cache.get_exact(ns(), request_hash="k1", when=T0 + timedelta(minutes=minutes))

    assert await at(9) is not None
    # Strict on both sides, in both implementations: an entry expiring exactly now is gone.
    assert await at(10) is None
    assert await at(11) is None


async def test_one_tenants_entry_is_invisible_to_another(cache: ResponseCacheStore) -> None:
    """The predicate half of isolation. ``tests/test_cache_isolation.py`` removes it."""
    await cache.put(entry("k1", namespace=ns(tenant=TENANT_A)))

    assert await cache.get_exact(ns(tenant=TENANT_B), request_hash="k1", when=T0) is None
    assert await cache.get_exact(ns(tenant=TENANT_A), request_hash="k1", when=T0) is not None


# --- the semantic layer ---------------------------------------------------------------------


async def test_the_nearest_neighbour_above_the_threshold_wins(cache: ResponseCacheStore) -> None:
    await cache.put(entry("far", embedding=unit(1.0, 0.6), body=b"far"))
    await cache.put(entry("near", embedding=unit(1.0, 0.05), body=b"near"))

    matches = await cache.search(
        ns(),
        embedding=unit(1.0, 0.0),
        context_hash="ctx",
        embedding_model=MODEL,
        threshold=0.5,
        limit=2,
        when=T0,
    )

    assert [match.entry.body for match in matches] == [b"near", b"far"]
    assert matches[0].similarity > matches[1].similarity


async def test_a_neighbour_below_the_threshold_is_not_returned(
    cache: ResponseCacheStore,
) -> None:
    await cache.put(entry("k1", embedding=unit(0.0, 1.0)))

    matches = await cache.search(
        ns(),
        embedding=unit(1.0, 0.0),
        context_hash="ctx",
        embedding_model=MODEL,
        threshold=0.5,
        when=T0,
    )
    assert matches == []


async def test_similarity_is_a_cosine_of_unit_vectors(cache: ResponseCacheStore) -> None:
    """The number means the same thing in SQL and in Python, to five places.

    That equality is what lets the tenant's threshold, the ledger's ``cache_similarity``
    column, and §P8.H1's offline sweep all be about one quantity.
    """
    await cache.put(entry("k1", embedding=unit(1.0, 0.0)))

    matches = await cache.search(
        ns(),
        embedding=unit(1.0, 1.0),
        context_hash="ctx",
        embedding_model=MODEL,
        threshold=0.0,
        when=T0,
    )
    assert matches[0].similarity == pytest.approx(0.7071067, abs=1e-5)


async def test_a_different_context_never_matches(cache: ResponseCacheStore) -> None:
    """Similarity is allowed to move the question and nothing else."""
    await cache.put(entry("k1", embedding=unit(1.0), context_hash="ctx-a"))

    matches = await cache.search(
        ns(),
        embedding=unit(1.0),
        context_hash="ctx-b",
        embedding_model=MODEL,
        threshold=0.0,
        when=T0,
    )
    assert matches == []


async def test_a_different_embedding_model_never_matches(cache: ResponseCacheStore) -> None:
    """Two models are two vector spaces, and a cosine between them is a number with no
    meaning. The identical vector must not match under a different model id."""
    await cache.put(entry("k1", embedding=unit(1.0), embedding_model="some-other-model"))

    matches = await cache.search(
        ns(),
        embedding=unit(1.0),
        context_hash="ctx",
        embedding_model=MODEL,
        threshold=0.0,
        when=T0,
    )
    assert matches == []


async def test_the_semantic_search_is_scoped_to_one_tenant(cache: ResponseCacheStore) -> None:
    await cache.put(entry("k1", namespace=ns(tenant=TENANT_A), embedding=unit(1.0)))

    matches = await cache.search(
        ns(tenant=TENANT_B),
        embedding=unit(1.0),
        context_hash="ctx",
        embedding_model=MODEL,
        threshold=0.0,
        when=T0,
    )
    assert matches == []


@pytest.mark.parametrize(
    ("field", "value"),
    [("dialect", "openai"), ("model", "mock-model-2"), ("transport", TRANSPORT_STREAM)],
)
async def test_the_rest_of_the_namespace_narrows_the_search(
    cache: ResponseCacheStore, field: str, value: str
) -> None:
    await cache.put(entry("k1", embedding=unit(1.0)))

    matches = await cache.search(
        ns(**{field: value}),
        embedding=unit(1.0),
        context_hash="ctx",
        embedding_model=MODEL,
        threshold=0.0,
        when=T0,
    )
    assert matches == []


async def test_an_expired_entry_is_never_a_semantic_match(cache: ResponseCacheStore) -> None:
    await cache.put(entry("k1", embedding=unit(1.0), minutes_to_live=10))

    matches = await cache.search(
        ns(),
        embedding=unit(1.0),
        context_hash="ctx",
        embedding_model=MODEL,
        threshold=0.0,
        when=T0 + timedelta(minutes=11),
    )
    assert matches == []


async def test_an_entry_with_no_embedding_is_exact_only(cache: ResponseCacheStore) -> None:
    """The reasoning-model case (H-044) as the store sees it: reachable by hash, and
    unreachable by similarity."""
    await cache.put(entry("k1", embedding=None))

    assert await cache.get_exact(ns(), request_hash="k1", when=T0) is not None
    matches = await cache.search(
        ns(),
        embedding=unit(1.0),
        context_hash="ctx",
        embedding_model=MODEL,
        threshold=0.0,
        limit=10,
        when=T0,
    )
    assert matches == []


async def test_limit_bounds_the_neighbour_list(cache: ResponseCacheStore) -> None:
    """``threshold=0`` with a larger limit is §P8.H1's offline-sweep primitive: the whole
    neighbour list with its similarities, replayable across the threshold range without
    re-embedding anything."""
    for index in range(5):
        await cache.put(entry(f"k{index}", embedding=unit(1.0, index / 10)))

    matches = await cache.search(
        ns(),
        embedding=unit(1.0),
        context_hash="ctx",
        embedding_model=MODEL,
        threshold=0.0,
        limit=3,
        when=T0,
    )
    assert len(matches) == 3
    assert [match.similarity for match in matches] == sorted(
        (match.similarity for match in matches), reverse=True
    )


async def test_a_real_bge_vector_round_trips_through_the_store(
    cache: ResponseCacheStore,
) -> None:
    """384 real floats through ``vector(384)`` and back, and the cosine still separates.

    The committed corpus rather than a synthetic vector, because the thing being checked
    is that a *real* embedding survives the column, the text encoding, and the cast —
    including its sign pattern, which a naive serializer would be the first to lose.
    """
    corpus = load_corpus()
    question = corpus.questions[0]
    paraphrase = next(row for row in corpus.probes if row.source == question.id)
    stranger = next(row for row in corpus.questions if row.id != question.id)

    await cache.put(entry("k1", embedding=question.vector))

    found = await cache.get_exact(ns(), request_hash="k1", when=T0)
    assert found is not None and found.embedding is not None
    assert len(found.embedding) == 384
    assert found.embedding == pytest.approx(question.vector, abs=1e-6)

    near = await cache.search(
        ns(),
        embedding=paraphrase.vector,
        context_hash="ctx",
        embedding_model=MODEL,
        threshold=0.0,
        when=T0,
    )
    far = await cache.search(
        ns(),
        embedding=stranger.vector,
        context_hash="ctx",
        embedding_model=MODEL,
        threshold=0.0,
        when=T0,
    )
    assert near[0].similarity > 0.92
    assert far[0].similarity < 0.86


# --- housekeeping ----------------------------------------------------------------------------


async def test_purging_a_tenant_leaves_every_other_tenant_alone(
    cache: ResponseCacheStore,
) -> None:
    await cache.put(entry("k1", namespace=ns(tenant=TENANT_A)))
    await cache.put(entry("k2", namespace=ns(tenant=TENANT_B)))

    assert await cache.purge_tenant(TENANT_A) == 1

    assert await cache.get_exact(ns(tenant=TENANT_A), request_hash="k1", when=T0) is None
    assert await cache.get_exact(ns(tenant=TENANT_B), request_hash="k2", when=T0) is not None


async def test_deleting_expired_entries_leaves_the_live_ones(
    cache: ResponseCacheStore,
) -> None:
    await cache.put(entry("dead", minutes_to_live=10))
    await cache.put(entry("alive", minutes_to_live=600))

    assert await cache.delete_expired(when=T0 + timedelta(minutes=60)) == 1

    assert await cache.get_exact(ns(), request_hash="alive", when=T0 + timedelta(minutes=60))
    stats = await cache.stats(TENANT_A)
    assert stats.entries == 1


async def test_stats_count_entries_vectors_and_bytes(cache: ResponseCacheStore) -> None:
    await cache.put(entry("k1", body=b"12345", embedding=unit(1.0)))
    await cache.put(entry("k2", body=b"123", embedding=None))

    stats = await cache.stats(TENANT_A)

    assert stats.entries == 2
    assert stats.semantic_entries == 1
    assert stats.body_bytes == 8
    assert stats.oldest is not None and stats.newest is not None


async def test_stats_for_a_tenant_with_nothing_stored_are_zero(
    cache: ResponseCacheStore,
) -> None:
    stats = await cache.stats(TENANT_B)
    assert stats.entries == 0
    assert stats.body_bytes == 0
    assert stats.oldest is None


async def test_a_non_uuid_tenant_owns_nothing_rather_than_erroring(
    cache: ResponseCacheStore,
) -> None:
    """``/admin/cache/banana`` is an empty answer, not a 500 — the rule
    ``headroom/db/tenants.py`` already applies to every path parameter."""
    assert await cache.get_exact(ns(tenant="banana"), request_hash="k", when=T0) is None
    assert await cache.purge_tenant("banana") == 0
    assert (await cache.stats("banana")).entries == 0
