"""``/admin/cache``: the config surface §P8.H1 needs, and the off switch an incident needs.

Two things are worth more attention than the CRUD. The **threshold round trip** — what an
operator PUTs is what they GET back *and* what the gate actually compares against — because
a sweep whose knob is off by a rounding error measures the wrong curve. And **DELETE**,
which is the only operation here that has to be more than a configuration change.
"""

from __future__ import annotations

import builtins
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from headroom.cache.embedding import LazyEmbedder
from headroom.core.cache import (
    CACHE_EXACT,
    CACHE_SEMANTIC,
    DEFAULT_SIMILARITY_THRESHOLD,
    DEFAULT_TTL_S,
    DISPOSITION_DISABLED,
    DISPOSITION_MISS,
)
from headroom.providers.mock import MockScript
from tests.support.fixtures import anthropic_request
from tests.support.harness import GatewayHarness


def path(gateway: GatewayHarness) -> str:
    return f"/admin/cache/{gateway.tenant.id}"


# --- reading ---------------------------------------------------------------------------


async def test_a_new_tenant_reads_as_disabled_rather_than_missing(
    gateway: GatewayHarness,
) -> None:
    """A 200 with ``mode: disabled``, not a 404.

    The distinction ``/admin/limits`` already draws against ``/admin/budgets``: a budget is
    a record that exists or does not, while a cache policy is a property every tenant has
    and most tenants have switched off.
    """
    response = await gateway.admin("GET", path(gateway))

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "disabled"
    assert body["entries"] == 0
    assert body["tenant_name"] == gateway.tenant.name


async def test_the_view_reports_the_pin_and_the_effective_value_separately(
    gateway: GatewayHarness,
) -> None:
    """``None`` means "follow the default wherever it goes", and an operator has to be
    able to tell that from a pin that happens to equal today's default."""
    body = (await gateway.admin("GET", path(gateway))).json()

    assert body["ttl_s"] is None
    assert body["similarity_threshold"] is None
    assert body["effective_ttl_s"] == DEFAULT_TTL_S
    assert body["effective_similarity_threshold"] == DEFAULT_SIMILARITY_THRESHOLD


async def test_the_listing_shows_only_tenants_that_cache(gateway: GatewayHarness) -> None:
    assert (await gateway.admin("GET", "/admin/cache")).json() == []

    await gateway.admin("PUT", path(gateway), json={"mode": CACHE_EXACT})

    listed = (await gateway.admin("GET", "/admin/cache")).json()
    assert [row["tenant_id"] for row in listed] == [gateway.tenant.id]


async def test_the_view_names_the_embedding_model(gateway: GatewayHarness) -> None:
    """Which vector space this deployment's entries live in. An operator who changes it
    needs to see that it changed — every existing entry becomes unreachable by similarity
    the moment it does, because the model id is part of the query."""
    body = (await gateway.admin("GET", path(gateway))).json()
    assert body["embedding_model"] == "BAAI/bge-small-en-v1.5"


# --- writing ---------------------------------------------------------------------------


async def test_put_switches_caching_on_and_it_bites_immediately(
    gateway: GatewayHarness,
) -> None:
    """The invalidation is the assertion: the policy rides the ``Principal`` (H-037), so a
    route that changed the row without dropping its own cached principal would leave the
    very next request using the old mode for up to five seconds."""
    gateway.book.set("ok", MockScript.anthropic_message("hello"))
    await gateway.post("/v1/messages", anthropic_request(), script="ok")
    assert gateway.last_context().cache_disposition == DISPOSITION_DISABLED

    await gateway.admin("PUT", path(gateway), json={"mode": CACHE_EXACT})

    await gateway.post("/v1/messages", anthropic_request(), script="ok")
    assert gateway.last_context().cache_disposition == DISPOSITION_MISS


async def test_the_threshold_round_trips_exactly(gateway: GatewayHarness) -> None:
    """``NUMERIC(5,4)`` rather than a float column, so a sweep's knob is the knob it set."""
    for value in (0.7, 0.85, 0.9123, 0.99):
        response = await gateway.admin(
            "PUT", path(gateway), json={"mode": CACHE_SEMANTIC, "similarity_threshold": value}
        )
        assert response.status_code == 200
        assert response.json()["similarity_threshold"] == value
        assert (await gateway.admin("GET", path(gateway))).json()["similarity_threshold"] == value


async def test_the_threshold_that_round_trips_is_the_one_the_gate_uses(
    gateway: GatewayHarness,
) -> None:
    """The half of a round trip that a GET cannot show: the value reaching the comparison.

    A config surface that stores a number faithfully and compares against a different one
    would make §P8.H1 measure a curve nobody can reproduce.
    """
    from tests.support.corpus import load_corpus

    corpus = load_corpus()
    question = corpus.question("streaming_revenue:radiohead")
    other = corpus.question("streaming_revenue:coldplay")
    # The two score ~0.82 against each other, so a threshold either side of that is the
    # difference between a hit and a miss — and nothing else about the request changes.
    gateway.book.set("seed", MockScript.anthropic_message(question.answer or ""))
    gateway.book.set("other", MockScript.anthropic_message(other.answer or ""))

    await gateway.admin(
        "PUT", path(gateway), json={"mode": CACHE_SEMANTIC, "similarity_threshold": 0.8}
    )
    await gateway.post("/v1/messages", anthropic_request(text=question.text), script="seed")

    # Below the pair's similarity: admitted, and wrongly so — which is the point of the
    # knob and the subject of §P8.H1.
    await gateway.post("/v1/messages", anthropic_request(text=other.text), script="other")
    assert gateway.last_context().cache_disposition == "cache_hit_semantic"
    # A hit stores nothing, so the identical probe below still has no exact entry to find
    # and the threshold really is the only thing that moved.
    assert await gateway.cache_entries() == 1

    await gateway.admin(
        "PUT", path(gateway), json={"mode": CACHE_SEMANTIC, "similarity_threshold": 0.9}
    )
    await gateway.post("/v1/messages", anthropic_request(text=other.text), script="other")
    assert gateway.last_context().cache_disposition == DISPOSITION_MISS


async def test_put_replaces_rather_than_patches(gateway: GatewayHarness) -> None:
    """An absent field means *the documented default*, not *unchanged* — the only reading
    under which the API can express "stop pinning this"."""
    await gateway.admin(
        "PUT",
        path(gateway),
        json={"mode": CACHE_SEMANTIC, "ttl_s": 60, "similarity_threshold": 0.8},
    )

    body = (await gateway.admin("PUT", path(gateway), json={"mode": CACHE_SEMANTIC})).json()

    assert body["ttl_s"] is None
    assert body["similarity_threshold"] is None
    assert body["effective_ttl_s"] == DEFAULT_TTL_S


async def test_put_keeps_existing_entries(gateway: GatewayHarness) -> None:
    """Every stored entry was eligible when written and is still a complete answer to the
    request that keyed it. Lowering a threshold makes more of them reachable and raising
    it fewer; neither changes what any single one of them *says*."""
    gateway.book.set("ok", MockScript.anthropic_message("hello"))
    await gateway.admin("PUT", path(gateway), json={"mode": CACHE_EXACT})
    await gateway.post("/v1/messages", anthropic_request(), script="ok")
    assert await gateway.cache_entries() == 1

    await gateway.admin("PUT", path(gateway), json={"mode": CACHE_SEMANTIC})

    assert await gateway.cache_entries() == 1


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"mode": "on"}, id="unknown_mode"),
        pytest.param({"mode": CACHE_SEMANTIC, "similarity_threshold": 0.0}, id="threshold_zero"),
        pytest.param({"mode": CACHE_SEMANTIC, "similarity_threshold": 1.0}, id="threshold_one"),
        pytest.param({"mode": CACHE_SEMANTIC, "similarity_threshold": 1.5}, id="threshold_over"),
        pytest.param({"mode": CACHE_EXACT, "ttl_s": 0}, id="zero_ttl"),
        pytest.param({"mode": CACHE_EXACT, "nonsense": 1}, id="unknown_field"),
        pytest.param({}, id="no_mode"),
    ],
)
async def test_a_nonsense_policy_is_refused(
    gateway: GatewayHarness, payload: dict[str, object]
) -> None:
    """0 would admit anything as "similar" and 1 is the exact layer with extra steps —
    both refused by the API *and* by a CHECK constraint in migration 0005, so a
    hand-written UPDATE cannot install a cache that answers every question with the first
    answer it ever stored."""
    response = await gateway.admin("PUT", path(gateway), json=payload)
    assert response.status_code == 422


async def test_enabling_semantic_without_an_embedder_is_a_503_naming_the_extra(
    gateway: GatewayHarness,
) -> None:
    """The probe, and the reason it has to actually load the model.

    Found by the end-to-end container run: building a ``BGEEmbedder`` touches no weight
    file, so the first version of this route answered **200** on an image with no
    ``sentence-transformers`` installed. An operator would have believed semantic caching
    was on and seen every request bypass silently.

    503 rather than 500: the gateway is not misconfigured for anything *else* it is doing,
    and the condition clears the moment the model is available (H-020's table, extended).
    """
    gateway.cache.embedder = LazyEmbedder("BAAI/bge-small-en-v1.5")
    with _no_sentence_transformers():
        response = await gateway.admin("PUT", path(gateway), json={"mode": CACHE_SEMANTIC})

    assert response.status_code == 503
    body = response.json()
    assert body["headroom"]["reason"] == "embedder_unavailable"
    assert "uv sync --extra embed" in body["error"]["message"]
    # And nothing was switched on: the tenant is exactly as it was.
    tenant = await gateway.store.get_tenant(gateway.tenant.id)
    assert tenant is not None and tenant.cache.mode == "disabled"


async def test_enabling_exact_needs_no_embedder_at_all(gateway: GatewayHarness) -> None:
    """``exact`` is a genuinely cheaper mode, all the way down to its dependencies."""
    gateway.cache.embedder = LazyEmbedder("BAAI/bge-small-en-v1.5")
    with _no_sentence_transformers():
        response = await gateway.admin("PUT", path(gateway), json={"mode": CACHE_EXACT})

    assert response.status_code == 200


@contextmanager
def _no_sentence_transformers() -> Iterator[None]:
    """An environment where the ``embed`` extra is not installed."""
    real_import = builtins.__import__

    def refuse(name: str, *args: object, **kwargs: object) -> object:
        if name == "sentence_transformers":
            raise ImportError("no module named sentence_transformers")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    builtins.__import__ = refuse  # type: ignore[assignment]
    try:
        yield
    finally:
        builtins.__import__ = real_import


async def test_an_unknown_tenant_is_a_404(gateway: GatewayHarness) -> None:
    missing = "00000000-0000-4000-8000-000000000000"
    assert (await gateway.admin("GET", f"/admin/cache/{missing}")).status_code == 404
    assert (
        await gateway.admin("PUT", f"/admin/cache/{missing}", json={"mode": CACHE_EXACT})
    ).status_code == 404


# --- the off switch ---------------------------------------------------------------------


async def test_delete_disables_and_purges(gateway: GatewayHarness) -> None:
    """Both halves, and the order matters (see the module docstring in
    ``headroom/api/cache.py``): flipping the mode alone leaves entries reachable for up to
    the auth cache's five seconds in *other* processes, so the purge closes the window
    from the data side."""
    gateway.book.set("ok", MockScript.anthropic_message("hello"))
    await gateway.admin("PUT", path(gateway), json={"mode": CACHE_EXACT})
    await gateway.post("/v1/messages", anthropic_request(), script="ok")
    assert await gateway.cache_entries() == 1

    body = (await gateway.admin("DELETE", path(gateway))).json()

    assert body["mode"] == "disabled"
    assert body["entries"] == 0
    assert await gateway.cache_entries() == 0
    await gateway.post("/v1/messages", anthropic_request(), script="ok")
    assert gateway.last_context().cache_disposition == DISPOSITION_DISABLED


async def test_delete_leaves_other_tenants_alone(gateway: GatewayHarness) -> None:
    from headroom.policy.keys import display_prefix, hash_key, mint_key

    other = await gateway.store.create_tenant("globex")
    plaintext = mint_key()
    await gateway.store.create_key(
        tenant_id=other.id,
        name="k",
        key_hash=hash_key(plaintext),
        key_prefix=display_prefix(plaintext),
    )
    await gateway.set_cache(CACHE_EXACT, tenant_id=other.id)
    await gateway.set_cache(CACHE_EXACT)
    gateway.book.set("ok", MockScript.anthropic_message("hello"))
    await gateway.post("/v1/messages", anthropic_request(), script="ok")
    await gateway.post("/v1/messages", anthropic_request(), script="ok", api_key=plaintext)
    assert await gateway.cache_entries(other.id) == 1

    await gateway.admin("DELETE", path(gateway))

    assert await gateway.cache_entries() == 0
    assert await gateway.cache_entries(other.id) == 1


# --- the credential ----------------------------------------------------------------------


async def test_every_route_needs_the_root_token(gateway: GatewayHarness) -> None:
    for method, target in (
        ("GET", "/admin/cache"),
        ("GET", path(gateway)),
        ("PUT", path(gateway)),
        ("DELETE", path(gateway)),
    ):
        response = await gateway.admin(
            method, target, json={"mode": CACHE_EXACT}, authenticate=False
        )
        assert response.status_code == 401, f"{method} {target} was reachable anonymously"


async def test_a_virtual_key_is_not_an_admin_token(gateway: GatewayHarness) -> None:
    response = await gateway.admin("GET", path(gateway), token=gateway.api_key)
    assert response.status_code == 401
