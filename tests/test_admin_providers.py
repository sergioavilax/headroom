"""``/admin/providers`` — the surface the kill demo is watched through.

Behind the same root token as the rest of ``/admin`` (H-019), and reporting the two
things an operator has to hold in their head at once during an incident: *is this
provider healthy* and *where does traffic go when it is not*. Answering only the first
would send them back to the YAML file for the second, at the worst possible moment.

The listing is deliberately per process (H-052). A Fargate deployment with four tasks has
four independent opinions about a provider, and that is correct rather than a limitation:
a breaker is a record of what **this** gateway has been able to reach, and a task whose
own network is broken must trip its own breaker without convincing the other three that
Anthropic is down.
"""

from __future__ import annotations

from typing import Any

from headroom.policy.health import BREAKER_CLOSED, BREAKER_OPEN
from headroom.providers.mock import MockScript

from .support.fixtures import anthropic_request
from .support.harness import GatewayHarness


def _by_name(payload: Any) -> dict[str, Any]:
    return {entry["name"]: entry for entry in payload}


async def test_the_listing_shows_every_configured_provider_including_idle_ones(
    chain: GatewayHarness,
) -> None:
    """A configured-and-idle provider and an absent one are different facts.

    A provider that has never served a request appears with zero samples and a closed
    breaker rather than not appearing at all — which is what makes a missing entry a
    signal rather than a shrug.
    """
    response = await chain.admin("GET", "/admin/providers")

    entries = _by_name(response.json())
    assert response.status_code == 200
    assert sorted(entries) == ["mock_a", "mock_b"]
    assert entries["mock_b"]["state"] == BREAKER_CLOSED
    assert entries["mock_b"]["samples"] == 0
    assert entries["mock_b"]["p50_latency_ms"] is None


async def test_a_provider_reports_the_routes_that_can_reach_it(chain: GatewayHarness) -> None:
    """The chain, beside the health, so one GET answers both halves of the question."""
    response = await chain.admin("GET", "/admin/providers/mock_a")

    payload = response.json()
    assert payload["name"] == "mock_a"
    assert payload["kind"] == "mock"
    dialects = {route["dialect"]: route for route in payload["routes"]}
    assert sorted(dialects) == ["anthropic", "openai"]
    assert dialects["openai"]["chain"] == ["mock_a", "mock_b"]
    assert dialects["openai"]["attempts"] == ["mock_a", "mock_b"]


async def test_a_fallback_reports_the_same_chain_it_appears_in(chain: GatewayHarness) -> None:
    """Reading ``mock_b``'s page tells you it is somebody's fallback, not a lone provider."""
    payload = (await chain.admin("GET", "/admin/providers/mock_b")).json()

    assert [route["chain"] for route in payload["routes"]] == [
        ["mock_a", "mock_b"],
        ["mock_a", "mock_b"],
    ]


async def test_the_page_shows_a_breaker_tripping_and_what_it_cost(
    chain: GatewayHarness,
) -> None:
    """The demo, read off the admin surface: the failure count, the state, the cooldown."""
    chain.book.set("fault@mock_a", MockScript.timeout())
    chain.book.set("fault@mock_b", MockScript.anthropic_message("served by mock_b"))

    for _ in range(5):
        await chain.post("/v1/messages", anthropic_request(), script="fault")

    entries = _by_name((await chain.admin("GET", "/admin/providers")).json())
    assert entries["mock_a"]["state"] == BREAKER_OPEN
    assert entries["mock_a"]["consecutive_failures"] == 5
    assert entries["mock_a"]["failure_ratio"] == 1.0
    assert entries["mock_a"]["last_error"] == "upstream_timeout"
    assert 0 < entries["mock_a"]["reopen_in_s"] <= 10.0
    # And the fallback is visibly the one doing the work.
    assert entries["mock_b"]["total_successes"] == 5
    assert entries["mock_b"]["state"] == BREAKER_CLOSED
    assert entries["mock_b"]["p50_latency_ms"] is not None


async def test_delete_closes_the_breaker_without_erasing_the_record(
    chain: GatewayHarness,
) -> None:
    """Incident response: "I fixed it, stop skipping it" — without waiting out a cooldown.

    The lifetime counters survive on purpose. Closing a breaker is a statement about the
    present; deleting the evidence would make the post-mortem harder for no benefit.
    """
    chain.book.set("fault@mock_a", MockScript.timeout())
    chain.book.set("fault@mock_b", MockScript.anthropic_message("served"))
    for _ in range(5):
        await chain.post("/v1/messages", anthropic_request(), script="fault")

    response = await chain.admin("DELETE", "/admin/providers/mock_a/health")

    payload = response.json()
    assert response.status_code == 200
    assert payload["state"] == BREAKER_CLOSED
    assert payload["samples"] == 0
    assert payload["consecutive_failures"] == 0
    assert payload["total_failures"] == 5
    assert payload["reopen_in_s"] is None
    # And the primary is genuinely back in rotation on the very next request.
    chain.book.set("fault@mock_a", MockScript.anthropic_message("primary is back"))
    served = await chain.post("/v1/messages", anthropic_request(), script="fault")
    assert "primary is back" in served.text
    assert chain.last_context().failover_hops == 0


async def test_an_unknown_provider_is_a_404_that_lists_the_real_ones(
    chain: GatewayHarness,
) -> None:
    response = await chain.admin("GET", "/admin/providers/vllm_z")

    assert response.status_code == 404
    assert response.json()["error"]["type"] == "provider_not_found"
    assert "mock_a, mock_b" in response.json()["error"]["message"]


async def test_the_routes_are_the_admin_credentials_not_a_tenants(
    chain: GatewayHarness,
) -> None:
    """A virtual key is never an admin token, and provider topology is not tenant data."""
    anonymous = await chain.admin("GET", "/admin/providers", authenticate=False)
    with_virtual_key = await chain.admin("GET", "/admin/providers", token=chain.api_key)

    assert anonymous.status_code == 401
    assert with_virtual_key.status_code == 401


async def test_deleting_health_on_an_unknown_provider_is_a_404(chain: GatewayHarness) -> None:
    """Not a silent success: an operator typing a name at 3 a.m. deserves to be told."""
    response = await chain.admin("DELETE", "/admin/providers/nope/health")

    assert response.status_code == 404
