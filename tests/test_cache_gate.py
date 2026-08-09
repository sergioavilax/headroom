"""The cache through the whole gateway: hits, misses, ordering, and what the ledger says.

Everything here drives real HTTP against the real app, because the interesting claims are
about *where in the pipeline* the cache sits and *what a hit's row looks like*, and
neither is visible from a unit test of the gate.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from headroom.cache.eligibility import REASON_TEMPERATURE, REASON_TOOLS
from headroom.cache.keys import namespace_for, request_hash
from headroom.cache.replay import (
    CACHE_SIMILARITY_HEADER,
    CACHE_SOURCE_HEADER,
    CACHE_STATUS_HEADER,
)
from headroom.core.cache import (
    CACHE_EXACT,
    CACHE_SEMANTIC,
    DISPOSITION_BYPASS,
    DISPOSITION_DISABLED,
    DISPOSITION_HIT_EXACT,
    DISPOSITION_MISS,
)
from headroom.core.limits import DIM_REQUESTS
from headroom.providers.mock import MockScript
from tests.support.corpus import CorpusEmbedder
from tests.support.fixtures import ANTHROPIC_TOOLS, anthropic_request, openai_request
from tests.support.harness import GatewayHarness

ANSWER = "the streaming rate was 18.5 percent"


def script(harness: GatewayHarness, name: str = "ok", text: str = ANSWER) -> None:
    harness.book.set(name, MockScript.anthropic_message(text))


# --- disabled is a real state, not a threshold turned up -------------------------------------


async def test_a_disabled_tenant_does_no_cache_work_at_all(gateway: GatewayHarness) -> None:
    """The emphasis this phase was asked for, as a measurement.

    Not "the cache returns nothing" — *nothing runs*. No store read, no embedding, and
    (see ``test_a_disabled_tenant_records_no_bytes``) no copy of the response either.
    Read off the store's and the embedder's own counters rather than asserted about.
    """
    script(gateway)
    embedder = gateway.cache.embedder
    assert isinstance(embedder, CorpusEmbedder)
    store = gateway.cache.store

    for _ in range(3):
        response = await gateway.post("/v1/messages", anthropic_request(), script="ok")
        assert response.status_code == 200

    assert embedder.calls == 0
    assert getattr(store, "reads", 0) == 0
    assert await gateway.cache_entries() == 0
    assert gateway.last_context().cache_disposition == DISPOSITION_DISABLED


async def test_caching_is_off_for_a_new_tenant(gateway: GatewayHarness) -> None:
    """The shipped default, asserted where an upgrade could quietly change it."""
    assert gateway.tenant.cache.mode == "disabled"
    assert not gateway.tenant.cache.enabled


# --- the exact layer ------------------------------------------------------------------------


async def test_the_second_identical_request_is_an_exact_hit(gateway: GatewayHarness) -> None:
    await gateway.set_cache(CACHE_EXACT)
    script(gateway)

    first = await gateway.post("/v1/messages", anthropic_request(), script="ok")
    source_id = gateway.last_context().request_id
    second = await gateway.post("/v1/messages", anthropic_request(), script="ok")

    assert first.headers.get(CACHE_STATUS_HEADER) is None
    assert second.headers[CACHE_STATUS_HEADER] == DISPOSITION_HIT_EXACT
    assert second.headers[CACHE_SOURCE_HEADER] == source_id
    assert second.content == first.content
    # One upstream call for two requests. The saving, stated as the provider saw it.
    assert len(gateway.provider.received) == 1


async def test_a_different_question_is_a_miss(gateway: GatewayHarness) -> None:
    await gateway.set_cache(CACHE_EXACT)
    script(gateway)

    await gateway.post("/v1/messages", anthropic_request(text="one"), script="ok")
    await gateway.post("/v1/messages", anthropic_request(text="two"), script="ok")

    assert gateway.last_context().cache_disposition == DISPOSITION_MISS
    assert len(gateway.provider.received) == 2


async def test_an_exact_tenant_never_embeds(gateway: GatewayHarness) -> None:
    """``exact`` is a genuinely cheaper mode, not ``semantic`` with a high threshold."""
    await gateway.set_cache(CACHE_EXACT)
    script(gateway)
    embedder = gateway.cache.embedder
    assert isinstance(embedder, CorpusEmbedder)

    await gateway.post("/v1/messages", anthropic_request(), script="ok")
    await gateway.post("/v1/messages", anthropic_request(), script="ok")

    assert embedder.calls == 0


async def test_an_exact_hit_does_not_embed_even_in_semantic_mode(
    gateway: GatewayHarness,
) -> None:
    """The cheapest path stays cheapest: the vector is only built once the hash has missed."""
    await gateway.set_cache(CACHE_SEMANTIC)
    script(gateway)
    embedder = gateway.cache.embedder
    assert isinstance(embedder, CorpusEmbedder)

    await gateway.post("/v1/messages", anthropic_request(), script="ok")
    after_miss = embedder.calls
    await gateway.post("/v1/messages", anthropic_request(), script="ok")

    assert after_miss == 1
    assert embedder.calls == after_miss
    assert gateway.last_context().cache_disposition == DISPOSITION_HIT_EXACT


async def test_the_two_dialects_have_separate_namespaces(gateway: GatewayHarness) -> None:
    await gateway.set_cache(CACHE_EXACT)
    gateway.book.set("a", MockScript.anthropic_message(ANSWER))
    gateway.book.set("o", MockScript.openai_completion(ANSWER))

    await gateway.post("/v1/messages", anthropic_request(text="same words"), script="a")
    await gateway.post("/v1/chat/completions", openai_request(text="same words"), script="o")

    assert gateway.last_context().cache_disposition == DISPOSITION_MISS
    assert len(gateway.provider.received) == 2


async def test_a_streaming_caller_does_not_hit_a_body_entry(gateway: GatewayHarness) -> None:
    """Transport is part of the key (H-043): an entry is replayed, never converted."""
    await gateway.set_cache(CACHE_EXACT)
    gateway.book.set("body", MockScript.anthropic_message(ANSWER))
    gateway.book.set("stream", MockScript.anthropic_stream(ANSWER))

    await gateway.post("/v1/messages", anthropic_request(), script="body")
    await gateway.post("/v1/messages", anthropic_request(stream=True), script="stream")

    assert gateway.last_context().cache_disposition == DISPOSITION_MISS
    assert await gateway.cache_entries() == 2


# --- bypass ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        pytest.param(anthropic_request(temperature=0.9), REASON_TEMPERATURE, id="temperature"),
        pytest.param(
            anthropic_request(tools=ANTHROPIC_TOOLS), REASON_TOOLS, id="tools_present_but_unused"
        ),
    ],
)
async def test_an_ineligible_request_is_a_bypass_with_a_reason(
    gateway: GatewayHarness, body: dict[str, object], reason: str
) -> None:
    await gateway.set_cache(CACHE_SEMANTIC)
    script(gateway)
    embedder = gateway.cache.embedder
    assert isinstance(embedder, CorpusEmbedder)

    await gateway.post("/v1/messages", body, script="ok")

    ctx = gateway.last_context()
    assert ctx.cache_disposition == DISPOSITION_BYPASS
    assert ctx.cache_reason == reason
    # A bypass is decided before anything is embedded or looked up.
    assert embedder.calls == 0
    assert await gateway.cache_entries() == 0


async def test_a_bypassed_request_is_not_stored_either(gateway: GatewayHarness) -> None:
    """Both directions from one decision: an ineligible request neither reads nor writes."""
    await gateway.set_cache(CACHE_EXACT)
    script(gateway)

    body = anthropic_request(temperature=0.9)
    await gateway.post("/v1/messages", body, script="ok")
    await gateway.post("/v1/messages", body, script="ok")

    assert len(gateway.provider.received) == 2
    assert await gateway.cache_entries() == 0


# --- the ledger row a hit writes --------------------------------------------------------------


async def test_a_hits_row_is_distinguishable_from_an_upstream_call(
    gateway: GatewayHarness,
) -> None:
    """Every column that could imply an upstream call, checked for not implying one."""
    await gateway.set_cache(CACHE_EXACT)
    script(gateway)

    await gateway.post("/v1/messages", anthropic_request(), script="ok")
    source = await gateway.ledger_row()
    await gateway.post("/v1/messages", anthropic_request(), script="ok")
    hit = await gateway.ledger_row()

    assert hit.cache_disposition == DISPOSITION_HIT_EXACT
    assert hit.outcome == "ok"
    assert hit.status_code == 200
    # No fake upstream, anywhere.
    assert hit.upstream_status is None
    assert hit.provider is None
    assert hit.upstream_latency_ms is None
    assert hit.passthrough_overhead_ms is None
    # Nothing was generated, so nothing is counted. Every SUM(output_tokens) in the
    # dashboard and in the Phase 9 rollup keeps meaning "tokens a model produced".
    assert hit.input_tokens is None
    assert hit.output_tokens is None
    # Zero as a *measurement*, with the status that says so — the same pair an
    # unroutable model gets (H-025), and for the same reason: no model ran.
    assert hit.usd_cost == Decimal(0)
    assert hit.cost_status == "not_billable"
    # The saving is a column of its own, where it cannot be mistaken for spend.
    assert hit.cache_avoided_usd == source.usd_cost == Decimal("0.000011500000")
    assert hit.cache_source_request_id == source.request_id
    assert hit.cache_similarity is None
    # Honest timings: the ones that happened are real, the ones that did not are NULL.
    assert hit.ttft_ms is not None and hit.ttft_ms >= 0
    assert hit.total_ms is not None
    # The stop reason survives, because the answer really did end that way.
    assert hit.stop_reason == "end_turn"


async def test_a_miss_records_the_disposition_too(gateway: GatewayHarness) -> None:
    await gateway.set_cache(CACHE_EXACT)
    script(gateway)

    await gateway.post("/v1/messages", anthropic_request(), script="ok")

    row = await gateway.ledger_row()
    assert row.cache_disposition == DISPOSITION_MISS
    assert row.cache_avoided_usd is None
    assert row.provider == "mock"
    assert row.upstream_status == 200


async def test_a_disabled_tenants_row_says_so(gateway: GatewayHarness) -> None:
    script(gateway)
    await gateway.post("/v1/messages", anthropic_request(), script="ok")

    row = await gateway.ledger_row()
    assert row.cache_disposition == DISPOSITION_DISABLED


async def test_the_entry_records_what_the_source_request_cost(
    gateway: GatewayHarness,
) -> None:
    await gateway.set_cache(CACHE_EXACT)
    script(gateway)

    await gateway.post("/v1/messages", anthropic_request(), script="ok")
    source = await gateway.ledger_row()

    assert (await gateway.cache_entries()) == 1
    namespace = namespace_for(
        tenant_id=gateway.tenant.id, dialect="anthropic", model="mock-model-1", stream=False
    )
    entry = await gateway.cache.store.get_exact(
        namespace,
        request_hash=request_hash(namespace, anthropic_request()),
        when=gateway.last_context().started_at,
    )
    assert entry is not None
    assert entry.usd_cost == source.usd_cost
    assert entry.cost_status == "priced"
    assert entry.source_request_id == source.request_id


# --- where it sits in the pipeline -------------------------------------------------------------


async def test_a_hit_still_consumes_its_rate_limit(gateway: GatewayHarness) -> None:
    """H-046's first half. A hit is not free to *this* process — it costs a connection, a
    search, and on the semantic path a CPU embedding — so a tenant that could serve
    unlimited traffic by repeating itself would have a denial of service for the asking.
    H-036's no-refund rule follows: the units are not handed back when the lookup hits.
    """
    await gateway.set_cache(CACHE_EXACT)
    await gateway.set_limits(requests_per_min=10)
    script(gateway)

    await gateway.post("/v1/messages", anthropic_request(), script="ok")
    await gateway.post("/v1/messages", anthropic_request(), script="ok")

    assert gateway.last_context().cache_disposition == DISPOSITION_HIT_EXACT
    bucket = await gateway.bucket(DIM_REQUESTS, limit_per_min=10)
    assert bucket.available == 8


async def test_a_rate_limited_request_never_reaches_the_cache(
    gateway: GatewayHarness,
) -> None:
    """429 before the lookup: the limiter is the load shedder, and what it sheds includes
    the embedding that would otherwise be the most expensive thing on this path."""
    await gateway.set_cache(CACHE_SEMANTIC)
    await gateway.set_limits(requests_per_min=1)
    script(gateway)
    embedder = gateway.cache.embedder
    assert isinstance(embedder, CorpusEmbedder)

    await gateway.post("/v1/messages", anthropic_request(), script="ok")
    calls_after_first = embedder.calls
    refused = await gateway.post("/v1/messages", anthropic_request(text="other"), script="ok")

    assert refused.status_code == 429
    assert embedder.calls == calls_after_first
    assert gateway.last_context().cache_disposition is None


async def test_a_hit_takes_no_budget_reservation(gateway: GatewayHarness) -> None:
    """H-046's second half, and the sharper decision: a hit spends nothing, so it holds
    nothing and settles nothing — which is what keeps two DynamoDB round trips off the one
    path whose entire product is that the first token is already there."""
    await gateway.set_cache(CACHE_EXACT)
    await gateway.set_budget("1.00")
    script(gateway)

    await gateway.post("/v1/messages", anthropic_request(), script="ok")
    after_miss = await gateway.budget()
    await gateway.post("/v1/messages", anthropic_request(), script="ok")
    after_hit = await gateway.budget()

    assert after_hit.spent == after_miss.spent
    assert after_hit.reserved == Decimal(0)
    ctx = gateway.last_context()
    assert ctx.budget_status is None
    assert ctx.budget_reserved_usd is None


async def test_a_tenant_over_its_cap_still_gets_its_cached_answers(
    gateway: GatewayHarness,
) -> None:
    """The consequence, stated rather than discovered.

    A budget bounds *spend*, and a hit does not spend. Abuse is bounded by the rate
    limiter, which ran one step earlier. So an exhausted tenant is degraded — every miss
    is a 402 — rather than dead.
    """
    await gateway.set_cache(CACHE_EXACT)
    script(gateway)
    await gateway.set_budget("1.00")
    await gateway.post("/v1/messages", anthropic_request(), script="ok")

    # Now take the cap away entirely.
    await gateway.set_budget("0.000001")

    hit = await gateway.post("/v1/messages", anthropic_request(), script="ok")
    miss = await gateway.post("/v1/messages", anthropic_request(text="new"), script="ok")

    assert hit.status_code == 200
    assert hit.headers[CACHE_STATUS_HEADER] == DISPOSITION_HIT_EXACT
    assert miss.status_code == 402


async def test_an_out_of_scope_model_never_reaches_the_cache(
    gateway: GatewayHarness,
) -> None:
    """403 before 429 before the cache: a key reaching past its permissions is told that,
    not handed somebody's cached answer."""
    await gateway.set_cache(CACHE_EXACT)
    await gateway.store.update_key(gateway.key.id, allowed_models=["nothing-*"])
    gateway.authenticator.cache.invalidate_key(gateway.key.id)

    response = await gateway.post("/v1/messages", anthropic_request())

    assert response.status_code == 403
    assert gateway.last_context().cache_disposition is None


async def test_a_hit_carries_no_similarity_header(gateway: GatewayHarness) -> None:
    """An exact hit's question was not similar but identical, and the header says nothing
    rather than saying 1.0."""
    await gateway.set_cache(CACHE_EXACT)
    script(gateway)
    await gateway.post("/v1/messages", anthropic_request(), script="ok")
    response = await gateway.post("/v1/messages", anthropic_request(), script="ok")

    assert CACHE_SIMILARITY_HEADER not in response.headers
