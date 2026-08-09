"""One contract, two implementations — the test that makes a second store safe to have.

``InMemoryTenantStore`` exists so the auth matrix, the admin CRUD, and the revocation
window can be tested keylessly and without a container (docs/DECISIONS.md H-021). The
obvious hazard is drift: an in-memory store that quietly disagrees with the SQL one
turns every green test above into a claim about nothing.

So every behaviour is asserted here, once, parametrised over **both** stores. The
Postgres parameter follows H-012's rule exactly: it skips when the compose stack is not
up and ``DATABASE_URL`` was merely *inferred*, and it **fails** when someone stated the
database is there — which CI does — so this file cannot silently stop testing SQL.

The Postgres fixture applies ``migrations/`` and then truncates the two control-plane
tables before each test. That is deliberate and worth stating plainly: **the compose
database is a test fixture**, and the contract assertions have to be exact (``==``, not
``in``) or they are not the same assertions the in-memory store faces.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

import asyncpg
import pytest

from headroom.core.errors import ConfigurationError, ControlPlaneUnavailable
from headroom.core.storage import TenantNameConflict, TenantStore
from headroom.db.memory import InMemoryTenantStore
from headroom.db.migrate import discover_migrations, run_migrations
from headroom.db.pool import translate_db_error
from headroom.db.tenants import PostgresTenantStore, _is_uuid
from headroom.policy.keys import display_prefix, hash_key, mint_key

from .support.services import COMPOSE_DATABASE_URL, resolve_endpoint

DATABASE = resolve_endpoint("DATABASE_URL", COMPOSE_DATABASE_URL)

MISSING_ID = "00000000-0000-4000-8000-000000000000"


@asynccontextmanager
async def open_postgres_store() -> AsyncIterator[TenantStore]:
    """A migrated, empty control plane on the compose (or configured) Postgres."""
    if DATABASE.skip_reason is not None:
        pytest.skip(DATABASE.skip_reason)
    if not DATABASE.reachable:
        # Explicit and unreachable: H-012 says fail rather than skip. Fail *here*,
        # rather than letting the migration runner's ten one-second connect retries
        # run once per test — a suite that takes ten minutes to report a wrong
        # DATABASE_URL is a suite people learn to interrupt.
        pytest.fail(f"DATABASE_URL was set to {DATABASE.url} and nothing is listening there")
    await run_migrations(DATABASE.url)
    conn = await asyncpg.connect(DATABASE.url, timeout=10)
    try:
        await conn.execute("TRUNCATE virtual_keys, tenants CASCADE")
    finally:
        await conn.close()
    store = PostgresTenantStore(url=DATABASE.url)
    try:
        yield store
    finally:
        await store.aclose()


@pytest.fixture
async def postgres_store() -> AsyncIterator[TenantStore]:
    async with open_postgres_store() as store:
        yield store


@pytest.fixture(params=["memory", "postgres"])
async def store(request: pytest.FixtureRequest) -> AsyncIterator[TenantStore]:
    """The store under test — the same tests, both implementations."""
    if request.param == "memory":
        yield InMemoryTenantStore()
        return
    async with open_postgres_store() as resolved:
        yield resolved


async def add_key(
    store: TenantStore,
    tenant_id: str,
    name: str = "svc",
    *,
    allowed_models: Sequence[str] = (),
    allowed_providers: Sequence[str] = (),
) -> str:
    plaintext = mint_key()
    key = await store.create_key(
        tenant_id=tenant_id,
        name=name,
        key_hash=hash_key(plaintext),
        key_prefix=display_prefix(plaintext),
        allowed_models=allowed_models,
        allowed_providers=allowed_providers,
    )
    assert key is not None
    return plaintext


# --- tenants ------------------------------------------------------------------------


async def test_a_created_tenant_comes_back_whole(store: TenantStore) -> None:
    tenant = await store.create_tenant("acme")

    assert tenant.id
    assert tenant.name == "acme"
    assert tenant.active is True
    assert tenant.created_at.tzinfo is not None, "timestamps are timezone-aware"
    assert await store.get_tenant(tenant.id) == tenant


async def test_tenant_names_are_unique(store: TenantStore) -> None:
    await store.create_tenant("acme")

    with pytest.raises(TenantNameConflict):
        await store.create_tenant("acme")


async def test_renaming_onto_another_tenants_name_conflicts(store: TenantStore) -> None:
    await store.create_tenant("acme")
    other = await store.create_tenant("globex")

    with pytest.raises(TenantNameConflict):
        await store.update_tenant(other.id, name="acme")


async def test_renaming_a_tenant_to_its_own_name_is_fine(store: TenantStore) -> None:
    tenant = await store.create_tenant("acme")

    updated = await store.update_tenant(tenant.id, name="acme")

    assert updated is not None
    assert updated.name == "acme"


async def test_tenants_list_oldest_first(store: TenantStore) -> None:
    first = await store.create_tenant("a")
    second = await store.create_tenant("b")

    assert [tenant.id for tenant in await store.list_tenants()] == [first.id, second.id]


async def test_a_partial_tenant_update_leaves_the_rest_alone(store: TenantStore) -> None:
    tenant = await store.create_tenant("acme")

    updated = await store.update_tenant(tenant.id, active=False)

    assert updated is not None
    assert updated.name == "acme"
    assert updated.active is False
    assert updated.created_at == tenant.created_at


async def test_unknown_tenants_are_none_not_errors(store: TenantStore) -> None:
    assert await store.get_tenant(MISSING_ID) is None
    assert await store.update_tenant(MISSING_ID, active=False) is None
    assert await store.get_tenant("not-even-a-uuid") is None


# --- keys ---------------------------------------------------------------------------


async def test_a_created_key_comes_back_whole(store: TenantStore) -> None:
    tenant = await store.create_tenant("acme")
    plaintext = mint_key()

    key = await store.create_key(
        tenant_id=tenant.id,
        name="svc",
        key_hash=hash_key(plaintext),
        key_prefix=display_prefix(plaintext),
        allowed_models=("mock-model-1",),
        allowed_providers=("mock",),
    )

    assert key is not None
    assert key.tenant_id == tenant.id
    assert key.key_prefix == display_prefix(plaintext)
    assert key.allowed_models == ("mock-model-1",)
    assert key.allowed_providers == ("mock",)
    assert key.revoked_at is None
    assert key.revoked is False
    assert await store.get_key(key.id) == key


async def test_a_key_for_a_missing_tenant_is_none(store: TenantStore) -> None:
    result = await store.create_key(
        tenant_id=MISSING_ID, name="svc", key_hash="h", key_prefix="hk_00000000"
    )

    assert result is None


async def test_keys_can_be_narrowed_to_a_tenant(store: TenantStore) -> None:
    one = await store.create_tenant("acme")
    two = await store.create_tenant("globex")
    await add_key(store, one.id, "one")
    await add_key(store, two.id, "two")

    assert [key.name for key in await store.list_keys()] == ["one", "two"]
    assert [key.name for key in await store.list_keys(two.id)] == ["two"]
    assert await store.list_keys(MISSING_ID) == []


async def test_scope_is_replaced_not_merged(store: TenantStore) -> None:
    tenant = await store.create_tenant("acme")
    plaintext = await add_key(store, tenant.id, allowed_models=("a", "b"))
    record = await store.find_by_hash(hash_key(plaintext))
    assert record is not None

    updated = await store.update_key(record.key.id, allowed_models=["c"])

    assert updated is not None
    assert updated.allowed_models == ("c",)
    assert updated.name == "svc", "an unmentioned field is untouched"


async def test_an_empty_scope_can_be_set_explicitly(store: TenantStore) -> None:
    """Widening back to unrestricted has to be expressible — ``[]`` is not ``None``."""
    tenant = await store.create_tenant("acme")
    plaintext = await add_key(store, tenant.id, allowed_models=("a",))
    record = await store.find_by_hash(hash_key(plaintext))
    assert record is not None

    updated = await store.update_key(record.key.id, allowed_models=[])

    assert updated is not None
    assert updated.allowed_models == ()


async def test_revocation_is_idempotent_and_keeps_the_first_timestamp(
    store: TenantStore,
) -> None:
    tenant = await store.create_tenant("acme")
    plaintext = await add_key(store, tenant.id)
    record = await store.find_by_hash(hash_key(plaintext))
    assert record is not None

    first = await store.revoke_key(record.key.id)
    second = await store.revoke_key(record.key.id)

    assert first is not None and second is not None
    assert first.revoked is True
    assert second.revoked_at == first.revoked_at


async def test_unknown_keys_are_none_not_errors(store: TenantStore) -> None:
    assert await store.get_key(MISSING_ID) is None
    assert await store.update_key(MISSING_ID, name="x") is None
    assert await store.revoke_key(MISSING_ID) is None
    assert await store.get_key("not-even-a-uuid") is None


# --- the authentication lookup ------------------------------------------------------


async def test_find_by_hash_returns_the_key_and_its_tenant(store: TenantStore) -> None:
    tenant = await store.create_tenant("acme")
    plaintext = await add_key(store, tenant.id)

    record = await store.find_by_hash(hash_key(plaintext))

    assert record is not None
    assert record.tenant == tenant
    assert record.key.tenant_id == tenant.id
    assert record.key.key_prefix == display_prefix(plaintext)


async def test_find_by_hash_of_an_unknown_key_is_none(store: TenantStore) -> None:
    assert await store.find_by_hash(hash_key(mint_key())) is None


async def test_find_by_hash_reports_revocation_and_tenant_state(store: TenantStore) -> None:
    """The two facts authentication turns into a 401, read in one round trip."""
    tenant = await store.create_tenant("acme")
    plaintext = await add_key(store, tenant.id)
    record = await store.find_by_hash(hash_key(plaintext))
    assert record is not None

    await store.revoke_key(record.key.id)
    await store.update_tenant(tenant.id, active=False)

    after = await store.find_by_hash(hash_key(plaintext))
    assert after is not None
    assert after.key.revoked is True
    assert after.tenant.active is False


async def test_the_plaintext_never_reaches_the_stored_row(store: TenantStore) -> None:
    """The store half of "the key exists once". The Postgres parameter of this test is
    the literal "a test greps the stored form" item from the Phase 2 gate."""
    tenant = await store.create_tenant("acme")
    plaintext = await add_key(store, tenant.id)

    record = await store.find_by_hash(hash_key(plaintext))

    assert record is not None
    rendered = repr(record)
    assert plaintext not in rendered
    assert plaintext[len(display_prefix(plaintext)) :] not in rendered


# --- Postgres only: what the database itself holds -----------------------------------


@pytest.mark.skipif(DATABASE.skip_reason is not None, reason=str(DATABASE.skip_reason))
async def test_no_column_of_the_database_contains_a_plaintext_key(
    postgres_store: TenantStore,
) -> None:
    """Grep the stored form, for real — every text column of both control-plane tables.

    Not "check the columns we remember": the query below discovers the text-ish columns
    from ``information_schema``, so a future migration that adds a place to leak a key
    is covered the day it lands.
    """
    tenant = await postgres_store.create_tenant("acme")
    plaintext = await add_key(postgres_store, tenant.id)
    secret_tail = plaintext[len(display_prefix(plaintext)) :]

    conn = await asyncpg.connect(DATABASE.url, timeout=10)
    try:
        columns = await conn.fetch(
            """
            SELECT table_name, column_name
              FROM information_schema.columns
             WHERE table_schema = 'public'
               AND table_name IN ('tenants', 'virtual_keys')
               AND data_type IN ('text', 'character varying', 'ARRAY', 'uuid')
            """
        )
        assert columns, "the control-plane tables must exist for this to prove anything"
        for row in columns:
            # Identifiers come from information_schema on this very database, not from
            # anything a caller can influence.
            query = f'SELECT "{row["column_name"]}"::text AS value FROM "{row["table_name"]}"'
            values = await conn.fetch(query)
            for value in values:
                stored = value["value"]
                if stored is None:
                    continue
                assert plaintext not in stored, f"{row['table_name']}.{row['column_name']}"
                assert secret_tail not in stored, f"{row['table_name']}.{row['column_name']}"
    finally:
        await conn.close()


@pytest.mark.skipif(DATABASE.skip_reason is not None, reason=str(DATABASE.skip_reason))
async def test_the_migration_applies_once_and_is_recorded(postgres_store: TenantStore) -> None:
    """The runner's exactly-once ledger, now that there is something to apply (H-003)."""
    applied_again = await run_migrations(DATABASE.url)

    assert applied_again == [], "an applied migration never runs twice"

    conn = await asyncpg.connect(DATABASE.url, timeout=10)
    try:
        versions = await conn.fetch("SELECT version FROM schema_migrations ORDER BY version")
    finally:
        await conn.close()

    assert "0001_tenants_and_virtual_keys" in {row["version"] for row in versions}


@pytest.mark.skipif(DATABASE.skip_reason is not None, reason=str(DATABASE.skip_reason))
async def test_a_tenant_cannot_be_deleted_out_from_under_its_keys(
    postgres_store: TenantStore,
) -> None:
    """``ON DELETE RESTRICT``: Phase 3's ledger points at these ids forever."""
    tenant = await postgres_store.create_tenant("acme")
    await add_key(postgres_store, tenant.id)

    conn = await asyncpg.connect(DATABASE.url, timeout=10)
    try:
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await conn.execute("DELETE FROM tenants WHERE id = $1", tenant.id)
    finally:
        await conn.close()


@pytest.mark.skipif(DATABASE.skip_reason is not None, reason=str(DATABASE.skip_reason))
async def test_two_keys_cannot_share_a_hash(postgres_store: TenantStore) -> None:
    """The UNIQUE index that makes authentication one indexed lookup."""
    tenant = await postgres_store.create_tenant("acme")
    shared = hash_key(mint_key())
    await postgres_store.create_key(
        tenant_id=tenant.id, name="one", key_hash=shared, key_prefix="hk_00000000"
    )

    with pytest.raises(asyncpg.UniqueViolationError):
        await postgres_store.create_key(
            tenant_id=tenant.id, name="two", key_hash=shared, key_prefix="hk_00000000"
        )


async def test_a_gateway_survives_a_control_plane_it_cannot_reach() -> None:
    """A store pointed at nothing answers 503, not 500 — and does not crash the process.

    The pool is lazy, so this failure is a *request-time* one: constructing the store,
    and therefore starting the gateway, must not require a database (H-020's 503). No
    database is needed to run this: port 1 refuses the connection.
    """
    unreachable = PostgresTenantStore(url="postgresql://nobody@127.0.0.1:1/nowhere")
    try:
        with pytest.raises(ControlPlaneUnavailable):
            await unreachable.find_by_hash("whatever")
    finally:
        await unreachable.aclose()


def test_an_unmigrated_database_names_the_fix() -> None:
    """A missing table is a misconfiguration, and the message says which command."""
    translated = translate_db_error(asyncpg.UndefinedTableError("relation does not exist"))

    assert isinstance(translated, ConfigurationError)
    assert "make migrate" in translated.message


def test_the_first_migration_is_named_by_the_convention() -> None:
    names = [path.name for path in discover_migrations()]

    assert names[0] == "0001_tenants_and_virtual_keys.sql"
    assert all(name[:4].isdigit() for name in names)


def test_uuid_lookups_do_not_reach_the_database() -> None:
    """``/admin/keys/banana`` is a 404, not a Postgres cast error — and the check is
    local, so a malformed id costs no connection."""
    assert _is_uuid(MISSING_ID)
    assert not _is_uuid("banana")
    assert not _is_uuid("")
