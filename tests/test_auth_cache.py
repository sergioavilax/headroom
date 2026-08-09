"""The auth cache, and the revocation window it buys.

BUILD_PLAN's Phase 2 gate: *"a revoked key is dead on the very next request (no cache of
auth decisions beyond a short TTL, and the TTL is a documented number)."* The number is
``AUTH_CACHE_TTL_S`` — **5.0 seconds** — and this file is where it is held to it
(docs/DECISIONS.md H-018).

Two guarantees, and they are different because a gateway is more than one process:

* **In the process that revoked the key**, the very next request fails. The admin route
  drops the cache entry, so there is no window at all.
* **In every other process** — Phase 9 runs several Fargate tasks against one RDS —
  the entry expires on its own, so the window is bounded by the TTL and by nothing else.

The second is asserted on an injected clock rather than by sleeping. A suite that waits
out its own TTLs is a suite whose TTLs get shortened until the property stops being
tested, and five seconds of dead air in CI is exactly the pressure that does it.
"""

from __future__ import annotations

import pytest

from headroom.core.context import RequestContext
from headroom.core.errors import RevokedCredential, UnknownCredential
from headroom.core.storage import KeyRecord
from headroom.db.memory import InMemoryTenantStore
from headroom.policy.auth import AUTH_CACHE_TTL_S, AuthCache, Authenticator, Principal
from headroom.policy.keys import display_prefix, hash_key, mint_key
from headroom.providers.mock import MockScript

from .support.fixtures import anthropic_request
from .support.harness import GatewayHarness


class FakeClock:
    """A monotonic clock a test can move. Seconds, starting at zero."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class CountingStore(InMemoryTenantStore):
    """The in-memory store, counting authentication lookups.

    A subclass rather than a patched attribute: the store's ``__slots__`` are there to
    keep a record type from growing a stray field, and a test that has to defeat that
    is a test working against the design.
    """

    def __init__(self) -> None:
        super().__init__()
        self.lookups = 0

    async def find_by_hash(self, key_hash: str) -> KeyRecord | None:
        self.lookups += 1
        return await super().find_by_hash(key_hash)


async def seeded(store: InMemoryTenantStore | None = None) -> tuple[InMemoryTenantStore, str, str]:
    """A store with one tenant and one key; returns ``(store, plaintext, key_id)``."""
    store = store if store is not None else InMemoryTenantStore()
    tenant = await store.create_tenant("acme")
    plaintext = mint_key()
    key = await store.create_key(
        tenant_id=tenant.id,
        name="default",
        key_hash=hash_key(plaintext),
        key_prefix=display_prefix(plaintext),
    )
    assert key is not None
    return store, plaintext, key.id


def headers(plaintext: str) -> dict[str, str]:
    return {"authorization": f"Bearer {plaintext}"}


# --- the documented number ----------------------------------------------------------


def test_the_ttl_is_a_documented_number() -> None:
    """The gate asks for a number, not a policy. It is five seconds."""
    assert AUTH_CACHE_TTL_S == 5.0
    assert AuthCache().ttl_s == AUTH_CACHE_TTL_S


# --- the cross-process window -------------------------------------------------------


async def test_a_cached_decision_survives_until_the_ttl_and_not_one_tick_longer() -> None:
    store, plaintext, key_id = await seeded()
    clock = FakeClock()
    auth = Authenticator(store, AuthCache(clock=clock))

    await auth.authenticate(headers(plaintext), RequestContext())
    # Revoked *behind the authenticator's back* — this is another process's revocation,
    # which is the only case the TTL exists to bound.
    await store.revoke_key(key_id)

    clock.advance(AUTH_CACHE_TTL_S - 0.001)
    still_works = await auth.authenticate(headers(plaintext), RequestContext())
    assert still_works.key_id == key_id, "inside the window, the cached decision stands"

    clock.advance(0.002)
    with pytest.raises(RevokedCredential):
        await auth.authenticate(headers(plaintext), RequestContext())


async def test_the_store_is_consulted_once_per_ttl_window() -> None:
    """The point of the cache: repeated requests do not repeat the lookup."""
    counter = CountingStore()
    _, plaintext, _ = await seeded(counter)
    clock = FakeClock()
    auth = Authenticator(counter, AuthCache(clock=clock))

    for _ in range(5):
        await auth.authenticate(headers(plaintext), RequestContext())
    assert counter.lookups == 1

    clock.advance(AUTH_CACHE_TTL_S + 0.001)
    await auth.authenticate(headers(plaintext), RequestContext())
    assert counter.lookups == 2


async def test_failures_are_never_cached() -> None:
    """A key created a second ago must work *now*.

    Caching "unknown" would make every freshly minted key dead for up to five seconds —
    which is the admin API's first user experience, and a bug report that reads
    "sometimes new keys do not work".
    """
    store, _, _ = await seeded()
    clock = FakeClock()
    auth = Authenticator(store, AuthCache(clock=clock))

    plaintext = mint_key()
    with pytest.raises(UnknownCredential):
        await auth.authenticate(headers(plaintext), RequestContext())

    tenants = await store.list_tenants()
    await store.create_key(
        tenant_id=tenants[0].id,
        name="new",
        key_hash=hash_key(plaintext),
        key_prefix=display_prefix(plaintext),
    )

    principal = await auth.authenticate(headers(plaintext), RequestContext())
    assert principal.tenant_id == tenants[0].id


def test_a_zero_ttl_disables_the_cache_entirely() -> None:
    """The per-request-DB-hit configuration, should a deployment want it."""
    cache = AuthCache(ttl_s=0.0)

    cache.put("hash", Principal("t", "acme", "k", "hk_abc"))

    assert len(cache) == 0
    assert cache.get("hash") is None


# --- the in-process window: none ----------------------------------------------------


async def test_revoking_through_the_admin_api_kills_the_key_on_the_next_request(
    gateway: GatewayHarness,
) -> None:
    """No clock advance anywhere in this test. That is the assertion."""
    gateway.book.set("ok", MockScript.anthropic_message("hi"))

    before = await gateway.post("/v1/messages", anthropic_request(), script="ok")
    assert before.status_code == 200
    assert len(gateway.authenticator.cache) == 1, "the decision really was cached"

    revoked = await gateway.admin("DELETE", f"/admin/keys/{gateway.key.id}")
    assert revoked.status_code == 200

    after = await gateway.post("/v1/messages", anthropic_request(), script="ok")

    assert after.status_code == 401
    assert after.json()["headroom"]["reason"] == "revoked_api_key"


async def test_deactivating_a_tenant_kills_its_keys_on_the_next_request(
    gateway: GatewayHarness,
) -> None:
    gateway.book.set("ok", MockScript.anthropic_message("hi"))
    first = await gateway.post("/v1/messages", anthropic_request(), script="ok")
    assert first.status_code == 200

    await gateway.admin("DELETE", f"/admin/tenants/{gateway.tenant.id}")

    after = await gateway.post("/v1/messages", anthropic_request(), script="ok")

    assert after.status_code == 401
    assert after.json()["headroom"]["reason"] == "inactive_tenant"


async def test_narrowing_a_keys_scope_takes_effect_on_the_next_request(
    gateway: GatewayHarness,
) -> None:
    """Widening a scope late is harmless; narrowing it late is the whole problem."""
    gateway.book.set("ok", MockScript.anthropic_message("hi"))
    first = await gateway.post("/v1/messages", anthropic_request(), script="ok")
    assert first.status_code == 200

    await gateway.admin(
        "PATCH", f"/admin/keys/{gateway.key.id}", json={"allowed_models": ["something-else"]}
    )

    after = await gateway.post("/v1/messages", anthropic_request(), script="ok")

    assert after.status_code == 403
    assert after.json()["headroom"]["reason"] == "model_out_of_scope"


# --- housekeeping -------------------------------------------------------------------


def test_invalidating_an_uncached_key_is_harmless() -> None:
    AuthCache().invalidate_key("no-such-key")
    AuthCache().invalidate_tenant("no-such-tenant")


async def test_an_expired_entry_is_forgotten_rather_than_kept() -> None:
    """Only successes are cached, so the natural bound is "keys in use" — but an entry
    that expires must actually leave, or the bound is "keys ever used"."""
    store, plaintext, _ = await seeded()
    clock = FakeClock()
    cache = AuthCache(clock=clock)
    auth = Authenticator(store, cache)

    await auth.authenticate(headers(plaintext), RequestContext())
    assert len(cache) == 1

    clock.advance(AUTH_CACHE_TTL_S + 1)
    assert cache.get(hash_key(plaintext)) is None
    assert len(cache) == 0
