"""What makes a request hop, and — more importantly — what must never make it hop.

BUILD_PLAN §P6 names the triggers: *"retry with jittered exponential backoff on
429/5xx"*, plus the transport faults the MockProvider can inject. The non-triggers are
not in the plan's sentence and matter at least as much, because each one is a way for a
gateway to make its own problem worse:

* **Our own 429.** A rate-limit refusal exists to *shed* load. Failing over on it would
  move the excess to another provider instead — the exact inversion. H-038 built the
  distinguishability for this phase to use, and the way this file uses it is by
  asserting the shape of the code rather than the shape of a header: a gateway refusal
  is raised before the executor is reachable, so no provider is called at all.
* **Our own 402.** A budget refusal is about money, and a second provider spends the
  same money. Re-spending it elsewhere is the failure the whole of Phase 4 exists to
  prevent, arriving through a new door.
* **An upstream 4xx that is not 429.** The request is malformed, or unauthorised, or
  names a model that does not exist. The next provider will say so identically, one
  round trip later; forwarding immediately is both faster and more honest about whose
  fault it was.

The matrix below is the phase's first gate clause, and every row drives the whole stack
— routes, auth, limiter, cache, budget gate, meter — rather than calling the executor.
"""

from __future__ import annotations

import pytest

from headroom.core.errors import ProviderTimeout, ProviderUnavailable
from headroom.providers.mock import MockScript

from .support.fixtures import DEFAULT_MODEL, anthropic_request, openai_request
from .support.harness import GatewayHarness
from .support.streams import anthropic_text

SERVED_BY_B = "served by mock_b"

#: Every upstream answer BUILD_PLAN says to retry. 529 is Anthropic's overloaded signal
#: and needs no special case — it is in the 5xx family, which is the whole rule.
RETRYABLE_STATUSES = [429, 500, 502, 503, 529]

#: Every upstream answer that describes the *request*. A second provider cannot fix a
#: malformed body or an unknown model, and pretending it might would turn one clear
#: error into two round trips and a misleading one.
CLIENT_ERROR_STATUSES = [400, 401, 403, 404, 422]


def _a_fails_b_serves(chain: GatewayHarness, failure: MockScript) -> None:
    """Script the pair: A does the awful thing, B answers normally."""
    chain.book.set("fault@mock_a", failure)
    chain.book.set("fault@mock_b", MockScript.anthropic_message(SERVED_BY_B))


# --------------------------------------------------------------------------------
# Triggers: the next provider serves
# --------------------------------------------------------------------------------


@pytest.mark.parametrize("status", RETRYABLE_STATUSES)
async def test_a_retryable_upstream_status_moves_to_the_next_provider(
    chain: GatewayHarness, status: int
) -> None:
    _a_fails_b_serves(chain, MockScript.error(status, dialect="anthropic"))

    response = await chain.post("/v1/messages", anthropic_request(), script="fault")

    assert response.status_code == 200
    assert SERVED_BY_B in response.text
    assert len(chain.providers["mock_a"].received) == 1
    assert len(chain.providers["mock_b"].received) == 1


@pytest.mark.parametrize(
    ("script", "reason"),
    [
        (MockScript.timeout(), ProviderTimeout.reason),
        (MockScript.connect_error(), ProviderUnavailable.reason),
    ],
    ids=["timeout", "connect_error"],
)
async def test_a_transport_fault_moves_to_the_next_provider(
    chain: GatewayHarness, script: MockScript, reason: str
) -> None:
    """The faults with no status at all — the ones a `docker kill` actually produces."""
    _a_fails_b_serves(chain, script)

    response = await chain.post("/v1/messages", anthropic_request(), script="fault")

    assert response.status_code == 200
    assert SERVED_BY_B in response.text
    assert chain.last_context().failover_error == reason


async def test_the_response_says_who_it_failed_over_from(chain: GatewayHarness) -> None:
    """In Headroom's own namespace, which no upstream is allowed to write in (H-038).

    The hops header is a number and discloses nothing; the provider name is a small,
    deliberate disclosure of the operator's own naming, and it is what makes the kill
    demo — and every support conversation about it — legible from outside the gateway.
    """
    _a_fails_b_serves(chain, MockScript.error(529, dialect="anthropic"))

    response = await chain.post("/v1/messages", anthropic_request(), script="fault")

    assert response.headers["x-headroom-failover-hops"] == "1"
    assert response.headers["x-headroom-failover-from"] == "mock_a"


async def test_a_request_the_primary_serves_carries_no_failover_headers(
    chain: GatewayHarness,
) -> None:
    """The overwhelmingly common case adds no bytes to the response.

    Worth pinning because the alternative — ``failover-hops: 0`` on every response — is
    what a header helper written without thinking about it would produce, and Phase 8's
    H2 measures a path where every byte on the happy path is somebody's overhead.
    """
    chain.book.set("ok", MockScript.anthropic_message("served by mock_a"))

    response = await chain.post("/v1/messages", anthropic_request(), script="ok")

    assert response.status_code == 200
    assert "x-headroom-failover-hops" not in response.headers
    assert "x-headroom-failover-from" not in response.headers
    assert chain.last_context().failover_hops == 0
    assert chain.providers["mock_b"].received == []


async def test_failover_works_on_the_openai_dialect_too(chain: GatewayHarness) -> None:
    """Both dialects, one implementation — the Phase 1 property, still holding.

    The chain is same-dialect on both sides (BUILD_PLAN L4), and the fixture's rule is
    registered under both dialects, so this is the OpenAI path taking the identical code
    to the identical conclusion.
    """
    chain.book.set("fault@mock_a", MockScript.error(503, dialect="openai"))
    chain.book.set("fault@mock_b", MockScript.openai_completion(SERVED_BY_B))

    response = await chain.post("/v1/chat/completions", openai_request(), script="fault")

    assert response.status_code == 200
    assert SERVED_BY_B in response.text
    assert chain.last_context().provider == "mock_b"


async def test_a_streamed_request_fails_over_before_its_first_byte(
    chain: GatewayHarness,
) -> None:
    """A stream whose *opening* failed is still a pre-first-byte fault.

    This is the case the boundary decision is drawn around from the safe side: the
    upstream answered 529 to a streaming request, so there is no stream, no byte has
    gone downstream, and the fallback may serve the whole answer with nothing spliced.
    ``tests/test_failover_boundary.py`` takes the same fault one byte later.
    """
    chain.book.set("fault@mock_a", MockScript.error(529, dialect="anthropic"))
    chain.book.set("fault@mock_b", MockScript.anthropic_stream(SERVED_BY_B))

    response = await chain.post("/v1/messages", anthropic_request(stream=True), script="fault")

    assert response.status_code == 200
    # Reassembled the way an SDK would, because the text arrives as deltas (A4).
    assert anthropic_text(response.content) == SERVED_BY_B
    assert b"event: error" not in response.content
    assert chain.last_context().failover_hops == 1


# --------------------------------------------------------------------------------
# Non-triggers: nobody else is asked
# --------------------------------------------------------------------------------


@pytest.mark.parametrize("status", CLIENT_ERROR_STATUSES)
async def test_a_client_error_from_upstream_is_forwarded_not_retried(
    chain: GatewayHarness, status: int
) -> None:
    """The fallback is never asked, and the upstream's own body reaches the caller."""
    _a_fails_b_serves(chain, MockScript.error(status, dialect="anthropic"))

    response = await chain.post("/v1/messages", anthropic_request(), script="fault")

    assert response.status_code == status
    assert chain.providers["mock_b"].received == []
    assert response.headers["x-headroom-error-source"] == "upstream"
    assert "x-headroom-failover-hops" not in response.headers
    assert chain.last_context().failover_hops == 0


async def test_the_gateways_own_rate_limit_refusal_never_reaches_a_provider(
    chain: GatewayHarness,
) -> None:
    """H-038's contract, cashed. A 429 that is *ours* is a shed, never a hop.

    Note the assertion: not "the header says gateway" — that is H-038's own test — but
    that **no provider in the chain was called at all**. The limiter raises before the
    executor exists in the call path, so failing over on our own 429 is not a bug that
    was avoided, it is a bug with nowhere to live.
    """
    await chain.set_limits(requests_per_min=1)
    chain.book.set("ok", MockScript.anthropic_message("served"))

    first = await chain.post("/v1/messages", anthropic_request(), script="ok")
    second = await chain.post("/v1/messages", anthropic_request(), script="ok")

    assert (first.status_code, second.status_code) == (200, 429)
    assert second.headers["x-headroom-error-source"] == "gateway"
    assert len(chain.providers["mock_a"].received) == 1
    assert chain.providers["mock_b"].received == []


async def test_the_gateways_own_budget_refusal_never_reaches_a_provider(
    chain: GatewayHarness,
) -> None:
    """A 402 means "out of money", and a second provider spends the same money."""
    await chain.set_budget("0.0000001")
    chain.book.set("ok", MockScript.anthropic_message("served"))

    response = await chain.post("/v1/messages", anthropic_request(), script="ok")

    assert response.status_code == 402
    assert response.headers["x-headroom-error-source"] == "gateway"
    assert chain.providers["mock_a"].received == []
    assert chain.providers["mock_b"].received == []


async def test_an_out_of_scope_provider_is_a_403_not_a_hop(chain: GatewayHarness) -> None:
    """Authorization outranks availability, and it is decided before the chain exists."""
    await chain.store.update_key(chain.key.id, allowed_providers=["mock_b"])
    chain.authenticator.cache.invalidate_key(chain.key.id)

    response = await chain.post("/v1/messages", anthropic_request(), script=None)

    assert response.status_code == 403
    assert chain.providers["mock_a"].received == []
    assert chain.providers["mock_b"].received == []


async def test_a_key_scoped_to_the_primary_alone_gets_no_fallback(
    chain: GatewayHarness,
) -> None:
    """A scope is not something an outage may widen.

    The key may reach ``mock_a`` and not ``mock_b``. ``mock_a`` fails, and the request
    fails with it — carrying ``mock_a``'s own 529 — rather than being quietly served by
    a provider this credential was never allowed to use.
    """
    await chain.store.update_key(chain.key.id, allowed_providers=["mock_a"])
    chain.authenticator.cache.invalidate_key(chain.key.id)
    _a_fails_b_serves(chain, MockScript.error(529, dialect="anthropic"))

    response = await chain.post("/v1/messages", anthropic_request(), script="fault")

    assert response.status_code == 529
    assert chain.providers["mock_b"].received == []


async def test_a_cache_hit_can_never_trigger_a_hop(chain: GatewayHarness) -> None:
    """H-046's closing line, cashed one phase later.

    The cache sits above the executor in the pipeline, so a hit returns before a chain
    is ever walked. Both providers are scripted to fail; the second request is served
    anyway, from the entry the first one populated.
    """
    await chain.set_cache("exact")
    chain.book.set("first@mock_a", MockScript.anthropic_message("cached answer"))
    chain.book.set("first@mock_b", MockScript.anthropic_message("cached answer"))

    body = anthropic_request(text="what gets cached?")
    first = await chain.post("/v1/messages", body, script="first")
    chain.book.set("first@mock_a", MockScript.error(529, dialect="anthropic"))
    chain.book.set("first@mock_b", MockScript.error(529, dialect="anthropic"))
    second = await chain.post("/v1/messages", body, script="first")

    assert (first.status_code, second.status_code) == (200, 200)
    assert second.headers["x-headroom-cache"] == "cache_hit_exact"
    assert chain.last_context().failover_hops == 0
    assert len(chain.providers["mock_a"].received) == 1


# --------------------------------------------------------------------------------
# The faults a running container can be asked for
# --------------------------------------------------------------------------------


async def test_a_built_in_fault_aimed_at_the_primary_fails_over(chain: GatewayHarness) -> None:
    """`make up` plus one header, and the demo works with no test process in sight.

    A script book only exists inside a pytest run, so without built-ins the keyless
    container demo of this phase would be "read the tests". ``fault-529@mock_a`` breaks
    exactly one instance and leaves the rest of the chain answering normally.
    """
    response = await chain.post("/v1/messages", anthropic_request(), script="fault-529@mock_a")

    assert response.status_code == 200
    assert response.headers["x-headroom-failover-hops"] == "1"
    assert response.headers["x-headroom-failover-from"] == "mock_a"
    assert chain.last_context().failover_error == "upstream_status_529"


@pytest.mark.parametrize(
    ("script", "reason"),
    [("fault-timeout", "upstream_timeout"), ("fault-connect", "upstream_unavailable")],
)
async def test_the_built_in_transport_faults_work_the_same_way(
    chain: GatewayHarness, script: str, reason: str
) -> None:
    response = await chain.post("/v1/messages", anthropic_request(), script=f"{script}@mock_a")

    assert response.status_code == 200
    assert chain.last_context().failover_error == reason


async def test_the_built_in_cut_is_still_not_a_hop(chain: GatewayHarness) -> None:
    """The fault worth being able to show from outside: it must *not* fail over."""
    response = await chain.post(
        "/v1/messages", anthropic_request(stream=True), script="fault-cut@mock_a"
    )

    assert b"event: error" in response.content
    assert b"message_stop" not in response.content
    assert chain.providers["mock_b"].received == []


async def test_an_unaimed_built_in_fault_breaks_the_whole_chain(chain: GatewayHarness) -> None:
    """Without the ``@`` suffix every instance applies it, which is the honest reading."""
    response = await chain.post("/v1/messages", anthropic_request(), script="fault-503")

    assert response.status_code == 503
    assert len(chain.providers["mock_b"].received) == 1


async def test_a_mistyped_script_name_is_still_an_error(chain: GatewayHarness) -> None:
    """The guard the built-ins must not weaken.

    ``fault-`` opens a small, closed vocabulary. Anything outside it — including a typo
    inside the prefix — still raises, because a fault-injection suite in which a wrong
    name silently becomes a happy path is a suite that reports success for nothing.
    """
    for name in ("fault-timout", "no-such-script"):
        with pytest.raises(KeyError, match="no mock script named"):
            await chain.post("/v1/messages", anthropic_request(), script=name)


async def test_an_unroutable_model_is_still_a_404(chain: GatewayHarness) -> None:
    """Failover extends what a route means; it does not invent one for a model with none."""
    response = await chain.post(
        "/v1/messages", anthropic_request(model="not-a-mock-model"), script=None
    )

    assert response.status_code == 404
    assert chain.providers["mock_a"].received == []


# --------------------------------------------------------------------------------
# The exhausted chain
# --------------------------------------------------------------------------------


async def test_an_exhausted_chain_forwards_the_last_upstream_answer(
    chain: GatewayHarness,
) -> None:
    """Fail closed, carrying the LAST failure — with the upstream's own body intact.

    Both links 529. The caller gets a 529, which is what the last provider actually
    said, rather than an invented 502 about the gateway's own disappointment (H-009).
    The header still records that Headroom tried elsewhere first, because otherwise the
    attempt would be invisible from outside.
    """
    chain.book.set("down@mock_a", MockScript.error(529, dialect="anthropic", message="A is down"))
    chain.book.set("down@mock_b", MockScript.error(503, dialect="anthropic", message="B is down"))

    response = await chain.post("/v1/messages", anthropic_request(), script="down")

    assert response.status_code == 503
    assert "B is down" in response.text
    assert response.headers["x-headroom-error-source"] == "upstream"
    assert response.headers["x-headroom-failover-hops"] == "1"
    assert response.headers["x-headroom-failover-from"] == "mock_a"


async def test_an_exhausted_chain_raises_the_last_transport_failure(
    chain: GatewayHarness,
) -> None:
    """When the last word was a timeout, the caller gets 504 — not 502, not 500.

    The error *class* is preserved deliberately: Phase 1 fixed the invented statuses and
    the stable ``headroom.reason`` values as a compatibility surface (H-009), and a
    failover phase that quietly re-mapped them would break every client's retry logic to
    describe an implementation detail. Only the human-readable message grows, and what
    it grows is the trail.
    """
    chain.book.set("gone@mock_a", MockScript.connect_error())
    chain.book.set("gone@mock_b", MockScript.timeout())

    response = await chain.post("/v1/messages", anthropic_request(), script="gone")

    assert response.status_code == 504
    payload = response.json()
    assert payload["headroom"]["reason"] == ProviderTimeout.reason
    assert "failover chain exhausted" in payload["error"]["message"]
    assert "mock_a:upstream_unavailable -> mock_b:upstream_timeout" in payload["error"]["message"]
    assert response.headers["x-headroom-failover-hops"] == "1"


async def test_a_single_provider_route_keeps_its_phase_one_error_message(
    gateway: GatewayHarness,
) -> None:
    """No chain, no trail: a one-attempt route's error reads exactly as it did before.

    The `gateway` fixture — one provider, no fallbacks — is what every deployment that
    has not configured failover looks like, and this asserts that Phase 6 left its
    errors alone rather than decorating them with a chain of length one.
    """
    gateway.book.set("gone", MockScript.timeout())

    response = await gateway.post("/v1/messages", anthropic_request(), script="gone")

    assert response.status_code == 504
    assert "failover" not in response.text
    assert gateway.last_context().failover_hops == 0
    assert gateway.last_context().failover_attempts == ("mock:upstream_timeout",)


async def test_every_hop_of_an_exhausted_chain_is_on_the_context(
    chain: GatewayHarness,
) -> None:
    """The trail is what an operator reads when the status alone will not do.

    ``error_reason`` carries the last failure and ``failover_from``/``failover_error``
    the first; between them a two-link chain is described completely, which is the
    argument migration 0006 makes for having exactly those two columns.
    """
    chain.book.set("down@mock_a", MockScript.error(429, dialect="anthropic"))
    chain.book.set("down@mock_b", MockScript.timeout())

    await chain.post("/v1/messages", anthropic_request(model=DEFAULT_MODEL), script="down")

    ctx = chain.last_context()
    assert ctx.failover_attempts == ("mock_a:upstream_status_429", "mock_b:upstream_timeout")
    assert (ctx.failover_from, ctx.failover_error) == ("mock_a", "upstream_status_429")
    assert ctx.error_reason == ProviderTimeout.reason
    assert ctx.provider == "mock_b"
