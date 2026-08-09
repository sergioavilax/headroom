"""``/admin/usage``: the ledger as the Phase 7 dashboard will read it.

Three things are worth asserting about a reporting surface, and they are all about
what it *refuses* to do: it will not let a caller in without the root token, it will
not write, and it will not turn exact money into a JSON float on the way out.
"""

from __future__ import annotations

import json
from decimal import Decimal

from headroom.providers.mock import MockScript
from headroom.providers.mock_scripts import REASONING_MODEL, openai_reasoning_stream_chunks

from .support.fixtures import anthropic_request, openai_request
from .support.harness import GatewayHarness


async def seed(gateway: GatewayHarness, *, count: int = 1) -> None:
    """Drive real requests through the gateway, then let the writer catch up."""
    gateway.book.set("ok", MockScript.anthropic_message("hi"))
    for _ in range(count):
        await gateway.post("/v1/messages", anthropic_request(), script="ok")
    await gateway.writer.drain()


# --- the credential ---------------------------------------------------------------------


async def test_the_usage_api_needs_the_root_admin_token(gateway: GatewayHarness) -> None:
    for path in ("/admin/usage", "/admin/usage/totals"):
        response = await gateway.admin("GET", path, authenticate=False)
        assert response.status_code == 401, path


async def test_a_virtual_key_is_not_an_admin_token_here_either(
    gateway: GatewayHarness,
) -> None:
    """The Phase 2 rule, extended to the surface that shows everyone's spend."""
    response = await gateway.admin("GET", "/admin/usage", token=gateway.api_key)

    assert response.status_code == 401


async def test_the_ledger_is_read_only(gateway: GatewayHarness) -> None:
    """No verb writes. A cost ledger with a write endpoint eventually gets written to."""
    for method in ("POST", "PATCH", "DELETE", "PUT"):
        response = await gateway.admin(method, "/admin/usage")
        assert response.status_code == 405, method


# --- listing -----------------------------------------------------------------------------


async def test_a_metered_request_appears_in_the_list(gateway: GatewayHarness) -> None:
    await seed(gateway)

    response = await gateway.admin("GET", "/admin/usage")

    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["model"] == "mock-model-1"
    assert rows[0]["tenant_id"] == gateway.tenant.id
    assert rows[0]["cost_status"] == "priced"


async def test_money_leaves_the_api_as_a_string(gateway: GatewayHarness) -> None:
    """**JSON has one number type and it is a double.**

    Serializing a NUMERIC as a JSON number would undo, at the last step, the exactness
    the price file, the Decimal arithmetic, and the column type all exist to preserve.
    Asserted against the raw bytes, because ``response.json()`` would hide a float that
    happened to round-trip today.
    """
    await seed(gateway)

    response = await gateway.admin("GET", "/admin/usage")

    assert b'"usd_cost":"0.000011500000"' in response.content.replace(b", ", b",")
    row = response.json()[0]
    assert isinstance(row["usd_cost"], str)
    assert Decimal(row["usd_cost"]) == Decimal("0.0000115")


async def test_a_row_reports_the_rates_it_was_billed_at(gateway: GatewayHarness) -> None:
    """Not today's rates. "Why did this cost that" is the explorer's whole question."""
    await seed(gateway)

    row = (await gateway.admin("GET", "/admin/usage")).json()[0]

    assert row["usd_per_mtok_in"] == "0.25"
    assert row["usd_per_mtok_out"] == "1.25"
    assert row["price_effective_from"] == "1970-01-01"


async def test_rows_filter_by_tenant_and_model(gateway: GatewayHarness) -> None:
    await seed(gateway)

    matching = await gateway.admin("GET", "/admin/usage", params={"tenant_id": gateway.tenant.id})
    other = await gateway.admin(
        "GET", "/admin/usage", params={"tenant_id": "00000000-0000-4000-8000-000000000000"}
    )
    wrong_model = await gateway.admin("GET", "/admin/usage", params={"model": "gpt-5"})

    assert len(matching.json()) == 1
    assert other.json() == []
    assert wrong_model.json() == []


async def test_the_list_pages(gateway: GatewayHarness) -> None:
    await seed(gateway, count=3)

    page = await gateway.admin("GET", "/admin/usage", params={"limit": "2"})
    rest = await gateway.admin("GET", "/admin/usage", params={"limit": "2", "offset": "2"})

    assert len(page.json()) == 2
    assert len(rest.json()) == 1


async def test_one_row_by_request_id(gateway: GatewayHarness) -> None:
    """The path from a caller's ``x-headroom-request-id`` to what they were charged."""
    gateway.book.set("ok", MockScript.anthropic_message("hi"))
    proxied = await gateway.post("/v1/messages", anthropic_request(), script="ok")
    await gateway.writer.drain()
    request_id = proxied.headers["x-headroom-request-id"]

    response = await gateway.admin("GET", f"/admin/usage/{request_id}")

    assert response.status_code == 200
    assert response.json()["request_id"] == request_id


async def test_an_unknown_request_id_is_a_404_in_headrooms_own_envelope(
    gateway: GatewayHarness,
) -> None:
    response = await gateway.admin("GET", "/admin/usage/hr_nope")

    assert response.status_code == 404
    assert response.json()["headroom"]["reason"] == "ledger_row_not_found"


# --- totals -------------------------------------------------------------------------------


async def test_totals_aggregate_a_tenants_spend(gateway: GatewayHarness) -> None:
    await seed(gateway, count=3)

    totals = (await gateway.admin("GET", "/admin/usage/totals")).json()

    assert len(totals) == 1
    assert totals[0]["tenant_id"] == gateway.tenant.id
    assert totals[0]["requests"] == 3
    assert Decimal(totals[0]["usd_cost"]) == Decimal("0.0000345")
    assert totals[0]["input_tokens"] == 33
    assert totals[0]["output_tokens"] == 21


async def test_totals_split_by_model_on_request(gateway: GatewayHarness) -> None:
    await seed(gateway)
    gateway.book.set("reasoning", MockScript(chunks=openai_reasoning_stream_chunks()))
    await gateway.post(
        "/v1/chat/completions",
        openai_request(model=REASONING_MODEL, stream=True),
        script="reasoning",
    )
    await gateway.writer.drain()

    totals = (await gateway.admin("GET", "/admin/usage/totals", params={"by_model": "true"})).json()

    by_model = {total["model"]: total for total in totals}
    assert set(by_model) == {"mock-model-1", REASONING_MODEL}
    assert by_model[REASONING_MODEL]["reasoning_tokens"] == 57


async def test_a_total_publishes_what_it_could_not_price(gateway: GatewayHarness) -> None:
    """The dashboard is told how much of the picture is missing, not just the sum."""
    gateway.book.set("no_usage", MockScript.openai_stream("hi", include_usage=False))
    await gateway.post("/v1/chat/completions", openai_request(stream=True), script="no_usage")
    await gateway.writer.drain()

    totals = (await gateway.admin("GET", "/admin/usage/totals")).json()

    assert totals[0]["requests"] == 1
    assert totals[0]["unpriced_requests"] == 1
    assert Decimal(totals[0]["usd_cost"]) == Decimal(0)


async def test_a_zero_cost_is_digits_not_exponent_notation(gateway: GatewayHarness) -> None:
    """``"0E-12"`` is what ``str(Decimal)`` gives and not what a dashboard should get."""
    await gateway.post("/v1/messages", anthropic_request(model="gpt-5"))
    await gateway.writer.drain()

    row = (await gateway.admin("GET", "/admin/usage")).json()[0]

    assert row["usd_cost"] == "0.000000000000"
    assert "E" not in row["usd_cost"]


async def test_a_total_counts_errors_separately(gateway: GatewayHarness) -> None:
    gateway.book.set("boom", MockScript.error(429))
    await gateway.post("/v1/messages", anthropic_request(), script="boom")
    await gateway.writer.drain()

    totals = (await gateway.admin("GET", "/admin/usage/totals")).json()

    assert totals[0]["errored_requests"] == 1


async def test_totals_of_nothing_are_empty(gateway: GatewayHarness) -> None:
    response = await gateway.admin("GET", "/admin/usage/totals")

    assert response.status_code == 200
    assert response.json() == []


async def test_the_response_is_json_a_dashboard_can_parse(gateway: GatewayHarness) -> None:
    await seed(gateway)

    response = await gateway.admin("GET", "/admin/usage")

    assert response.headers["content-type"].startswith("application/json")
    assert isinstance(json.loads(response.content), list)
