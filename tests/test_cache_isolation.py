"""Tenant isolation, and the sabotage that proves these tests can see it fail.

**No cache entry ever serves across tenants — exact or semantic.** That is the flat
requirement, and the positive tests below assert it through the whole gateway with two
real tenants asking the identical question.

Green on the first run is when a suite deserves the most suspicion, so the second half of
this file deliberately breaks the thing being protected. The sabotage is a realistic one
rather than a strawman: it patches :func:`headroom.cache.keys.namespace_for` so the
namespace no longer carries the tenant — the "the hash is unique anyway, drop the
predicate" optimisation, in the one place that would make it true. That single patch
removes **both** mechanisms at once (the salt inside the exact key and the ``tenant_id``
the store filters on), which is the point: a leak test that only defeats one of two
defences proves nothing about the other.

Under the sabotage, tenant B is served tenant A's answer. Restored, it is not.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from headroom.cache import gate as gate_module
from headroom.cache.keys import namespace_for
from headroom.core.cache import (
    CACHE_EXACT,
    CACHE_SEMANTIC,
    DISPOSITION_HIT_EXACT,
    DISPOSITION_HIT_SEMANTIC,
    DISPOSITION_MISS,
    CacheNamespace,
)
from headroom.db.memory import InMemoryResponseCacheStore
from headroom.policy.keys import display_prefix, hash_key, mint_key
from headroom.providers.mock import MockScript
from tests.support.corpus import load_corpus
from tests.support.fixtures import anthropic_request
from tests.support.harness import GatewayHarness

A_ANSWER = "acme's rate is 18.5 percent"
B_ANSWER = "globex's rate is 22.0 percent"


@pytest.fixture
async def other_tenant(gateway: GatewayHarness) -> AsyncIterator[str]:
    """A second tenant on the same gateway, with its own key. Yields the plaintext key."""
    tenant = await gateway.store.create_tenant("globex")
    plaintext = mint_key()
    key = await gateway.store.create_key(
        tenant_id=tenant.id,
        name="globex-key",
        key_hash=hash_key(plaintext),
        key_prefix=display_prefix(plaintext),
    )
    assert key is not None
    await gateway.set_cache(CACHE_EXACT, tenant_id=tenant.id)
    yield plaintext


# --- the property -----------------------------------------------------------------------


async def test_an_exact_entry_never_crosses_a_tenant_boundary(
    gateway: GatewayHarness, other_tenant: str
) -> None:
    await gateway.set_cache(CACHE_EXACT)
    body = anthropic_request(text="what royalty rate was negotiated?")
    gateway.book.set("a", MockScript.anthropic_message(A_ANSWER))
    gateway.book.set("b", MockScript.anthropic_message(B_ANSWER))

    first = await gateway.post("/v1/messages", body, script="a")
    second = await gateway.post("/v1/messages", body, script="b", api_key=other_tenant)

    assert A_ANSWER.encode() in first.content
    # Tenant B asked the byte-identical question and got its *own* upstream answer.
    assert B_ANSWER.encode() in second.content
    assert gateway.last_context().cache_disposition == DISPOSITION_MISS
    assert len(gateway.provider.received) == 2


async def test_a_semantic_entry_never_crosses_a_tenant_boundary(
    gateway: GatewayHarness, other_tenant: str
) -> None:
    """The dangerous one: a paraphrase that *would* match, refused by the namespace alone.

    The two texts score 0.98 against each other on the real model, so nothing about
    similarity is keeping them apart here — only the tenant is.
    """
    corpus = load_corpus()
    question = corpus.questions[0]
    paraphrase = next(row for row in corpus.probes if row.source == question.id)

    await gateway.set_cache(CACHE_SEMANTIC)
    await gateway.set_cache(CACHE_SEMANTIC, tenant_id=await _tenant_id(gateway, "globex"))
    gateway.book.set("a", MockScript.anthropic_message(A_ANSWER))
    gateway.book.set("b", MockScript.anthropic_message(B_ANSWER))

    await gateway.post("/v1/messages", anthropic_request(text=question.text), script="a")
    second = await gateway.post(
        "/v1/messages", anthropic_request(text=paraphrase.text), script="b", api_key=other_tenant
    )

    assert B_ANSWER.encode() in second.content
    assert gateway.last_context().cache_disposition == DISPOSITION_MISS


async def test_the_namespace_is_what_separates_them(gateway: GatewayHarness) -> None:
    """A unit-level statement of the same fact, so the sabotage below has a target."""
    one = namespace_for(tenant_id="t1", dialect="anthropic", model="m", stream=False)
    two = namespace_for(tenant_id="t2", dialect="anthropic", model="m", stream=False)
    assert one.salt != two.salt
    assert one.tenant_id != two.tenant_id


# --- the sabotage -------------------------------------------------------------------------


@pytest.fixture
def blind_namespace(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove the tenant from the cache namespace, everywhere at once.

    This is the realistic version of the bug: not "somebody deleted a WHERE clause" but
    "somebody decided the namespace did not need the tenant in it". Patching the single
    constructor takes out the hash salt *and* the store predicate together, because both
    are downstream of it — which is exactly the property that makes the real design hard
    to break by halves.
    """

    def blind(*, tenant_id: str, dialect: str, model: str, stream: bool) -> CacheNamespace:
        return CacheNamespace(
            tenant_id="00000000-0000-4000-8000-000000000000",
            dialect=dialect,
            model=model,
            transport="stream" if stream else "body",
        )

    monkeypatch.setattr(gate_module, "namespace_for", blind)


async def test_sabotage_removing_the_tenant_scoping_leaks_an_exact_entry(
    gateway: GatewayHarness, other_tenant: str, blind_namespace: None
) -> None:
    """With the scoping gone, tenant B is served tenant A's answer. The leak is real.

    Note what this proves and what it does not. It does not prove the shipped code is
    correct — the tests above do that. It proves those tests are *capable of failing*,
    which is the only thing that makes them worth having (the Backline
    proven-to-fail-against-old-code discipline).
    """
    await gateway.set_cache(CACHE_EXACT)
    body = anthropic_request(text="what royalty rate was negotiated?")
    gateway.book.set("a", MockScript.anthropic_message(A_ANSWER))
    gateway.book.set("b", MockScript.anthropic_message(B_ANSWER))

    await gateway.post("/v1/messages", body, script="a")
    second = await gateway.post("/v1/messages", body, script="b", api_key=other_tenant)

    assert gateway.last_context().cache_disposition == DISPOSITION_HIT_EXACT
    # Tenant B receives tenant A's answer. This is the failure the design exists to
    # prevent, reproduced deliberately so the assertions above are known to be load-bearing.
    assert A_ANSWER.encode() in second.content
    assert B_ANSWER.encode() not in second.content
    assert len(gateway.provider.received) == 1


async def test_sabotage_removing_the_tenant_scoping_leaks_a_semantic_entry(
    gateway: GatewayHarness, other_tenant: str, blind_namespace: None
) -> None:
    """And the semantic layer leaks on a *paraphrase*, which is worse: no request tenant B
    ever sent is byte-identical to anything tenant A sent, and it is still served A's
    answer."""
    corpus = load_corpus()
    question = corpus.questions[0]
    paraphrase = next(row for row in corpus.probes if row.source == question.id)

    await gateway.set_cache(CACHE_SEMANTIC)
    await gateway.set_cache(CACHE_SEMANTIC, tenant_id=await _tenant_id(gateway, "globex"))
    gateway.book.set("a", MockScript.anthropic_message(A_ANSWER))
    gateway.book.set("b", MockScript.anthropic_message(B_ANSWER))

    await gateway.post("/v1/messages", anthropic_request(text=question.text), script="a")
    second = await gateway.post(
        "/v1/messages", anthropic_request(text=paraphrase.text), script="b", api_key=other_tenant
    )

    assert gateway.last_context().cache_disposition == DISPOSITION_HIT_SEMANTIC
    assert A_ANSWER.encode() in second.content


async def test_the_store_itself_still_refuses_when_asked_correctly() -> None:
    """The sabotage above patches the *namespace*, not the store.

    Asked with an honest namespace, the store keeps tenants apart on its own — which is
    what makes the two mechanisms genuinely independent rather than one mechanism written
    down twice.
    """
    from datetime import UTC, datetime

    from headroom.core.cache import CacheEntry

    store = InMemoryResponseCacheStore()
    when = datetime(2026, 6, 1, tzinfo=UTC)
    a = CacheNamespace(tenant_id="t1", dialect="anthropic", model="m", transport="body")
    b = CacheNamespace(tenant_id="t2", dialect="anthropic", model="m", transport="body")
    await store.put(
        CacheEntry(
            tenant_id=a.tenant_id,
            dialect=a.dialect,
            model=a.model,
            transport=a.transport,
            request_hash="shared",
            context_hash="ctx",
            body=b"a's answer",
            content_type="application/json",
            expires_at=datetime(2027, 1, 1, tzinfo=UTC),
        )
    )

    assert await store.get_exact(b, request_hash="shared", when=when) is None
    assert await store.get_exact(a, request_hash="shared", when=when) is not None


async def _tenant_id(gateway: GatewayHarness, name: str) -> str:
    for tenant in await gateway.store.list_tenants():
        if tenant.name == name:
            return tenant.id
    raise AssertionError(f"no tenant named {name!r}")
