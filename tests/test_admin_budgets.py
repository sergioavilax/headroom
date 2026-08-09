"""``/admin/budgets`` — the control plane for caps.

Same shape as ``tests/test_admin_api.py``: the root token gates everything, unknown ids
are 404, unknown fields are 422, and money crosses the wire as a string. What is
specific here is the money rule — a JSON *number* for a budget is refused by name,
because a double is the one representation of money the whole pipeline is arranged to
avoid, and the admin API is the last place it could sneak in.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from .support.harness import GatewayHarness


async def create_tenant(gateway: GatewayHarness, name: str) -> str:
    response = await gateway.admin("POST", "/admin/tenants", json={"name": name})
    assert response.status_code == 201
    return str(response.json()["id"])


# --- authentication ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/admin/budgets"),
        ("GET", "/admin/budgets/whatever"),
        ("PUT", "/admin/budgets/whatever"),
        ("DELETE", "/admin/budgets/whatever"),
    ],
)
async def test_every_route_needs_the_root_token(
    gateway: GatewayHarness, method: str, path: str
) -> None:
    response = await gateway.admin(method, path, json={"usd": "1"}, authenticate=False)
    assert response.status_code == 401
    assert response.json()["headroom"]["reason"] == "admin_unauthorized"


async def test_a_virtual_key_is_not_an_admin_token(gateway: GatewayHarness) -> None:
    """The two credentials arrive in the same header and are never interchangeable."""
    response = await gateway.admin("GET", "/admin/budgets", token=gateway.api_key)
    assert response.status_code == 401


# --- setting a cap ------------------------------------------------------------------------


async def test_setting_and_reading_a_budget(gateway: GatewayHarness) -> None:
    response = await gateway.admin(
        "PUT", f"/admin/budgets/{gateway.tenant.id}", json={"usd": "25.00"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["scope"] == f"tenant#{gateway.tenant.id}"
    # Twelve decimal places, always — the same `format_usd` the ledger uses, so a
    # dashboard parses one shape for costs and caps rather than two.
    assert body["usd"] == "25.000000000000"
    assert body["window"] == "monthly"
    assert body["spent"] == "0.000000000000"
    assert body["reserved"] == "0.000000000000"
    assert body["remaining"] == "25.000000000000"
    assert body["committed"] == "0.000000000000"
    assert body["reservations"] == 0

    read = await gateway.admin("GET", f"/admin/budgets/{gateway.tenant.id}")
    assert read.status_code == 200
    assert read.json()["remaining"] == "25.000000000000"


async def test_money_leaves_as_a_string(gateway: GatewayHarness) -> None:
    """Every amount is a JSON string, exactly as ``/admin/usage`` does it. JSON has one
    numeric type and it is a double; rendering a budget through it would undo, at the
    last step, the exactness everything upstream preserves."""
    await gateway.admin("PUT", f"/admin/budgets/{gateway.tenant.id}", json={"usd": "25.10"})

    body = (await gateway.admin("GET", f"/admin/budgets/{gateway.tenant.id}")).json()

    for field in ("usd", "spent", "reserved", "remaining", "committed", "expired_released"):
        assert isinstance(body[field], str), field


async def test_a_json_number_for_the_budget_is_refused_by_name(
    gateway: GatewayHarness,
) -> None:
    """The H-023 rule, applied to the admin API: a float is wrong before anything
    multiplies it, and the refusal says what to send instead."""
    response = await gateway.admin(
        "PUT", f"/admin/budgets/{gateway.tenant.id}", json={"usd": 25.10}
    )

    assert response.status_code == 422
    assert "quoted decimal string" in response.text


async def test_an_unknown_window_is_refused(gateway: GatewayHarness) -> None:
    response = await gateway.admin(
        "PUT", f"/admin/budgets/{gateway.tenant.id}", json={"usd": "1", "window": "weekly"}
    )
    assert response.status_code == 422


async def test_an_unknown_field_is_refused(gateway: GatewayHarness) -> None:
    response = await gateway.admin(
        "PUT", f"/admin/budgets/{gateway.tenant.id}", json={"usd": "1", "hard": True}
    )
    assert response.status_code == 422


async def test_a_negative_budget_is_refused(gateway: GatewayHarness) -> None:
    response = await gateway.admin("PUT", f"/admin/budgets/{gateway.tenant.id}", json={"usd": "-5"})
    assert response.status_code == 422
    assert response.json()["headroom"]["reason"] == "invalid_budget"


async def test_a_budget_for_a_tenant_that_does_not_exist_is_404(
    gateway: GatewayHarness,
) -> None:
    """Checked against the control plane rather than assumed: a typo'd id would
    otherwise create an item that enforces nothing and lives forever."""
    response = await gateway.admin(
        "PUT", "/admin/budgets/00000000-0000-0000-0000-000000000000", json={"usd": "1"}
    )
    assert response.status_code == 404
    assert response.json()["headroom"]["reason"] == "tenant_not_found"


async def test_reading_a_budget_that_was_never_set_is_404(gateway: GatewayHarness) -> None:
    """And says the useful thing: no budget is not an error state, it is uncapped."""
    response = await gateway.admin("GET", f"/admin/budgets/{gateway.tenant.id}")

    assert response.status_code == 404
    assert response.json()["headroom"]["reason"] == "budget_not_found"
    assert "uncapped" in response.json()["error"]["message"]


# --- the numbers move ---------------------------------------------------------------------


async def test_the_view_shows_spend_and_committed_after_real_traffic(
    gateway: GatewayHarness,
) -> None:
    from .support.fixtures import anthropic_request

    await gateway.admin("PUT", f"/admin/budgets/{gateway.tenant.id}", json={"usd": "1.00"})
    await gateway.post("/v1/messages", anthropic_request())

    body = (await gateway.admin("GET", f"/admin/budgets/{gateway.tenant.id}")).json()

    assert body["spent"] == "0.000011500000"
    assert body["committed"] == "0.000011500000"
    assert body["remaining"] == "0.999988500000"
    assert body["reservations"] == 0


async def test_raising_a_cap_preserves_spend(gateway: GatewayHarness) -> None:
    from .support.fixtures import anthropic_request

    await gateway.admin("PUT", f"/admin/budgets/{gateway.tenant.id}", json={"usd": "1.00"})
    await gateway.post("/v1/messages", anthropic_request())

    raised = await gateway.admin("PUT", f"/admin/budgets/{gateway.tenant.id}", json={"usd": "2.00"})

    body = raised.json()
    assert body["usd"] == "2.000000000000"
    assert body["spent"] == "0.000011500000"
    assert body["remaining"] == "1.999988500000"


async def test_a_lifetime_budget_reports_its_window(gateway: GatewayHarness) -> None:
    response = await gateway.admin(
        "PUT", f"/admin/budgets/{gateway.tenant.id}", json={"usd": "5", "window": "total"}
    )

    assert response.json()["window"] == "total"
    assert response.json()["window_id"] == "total"


# --- listing and clearing -------------------------------------------------------------------


async def test_listing_shows_every_capped_tenant(gateway: GatewayHarness) -> None:
    other = await create_tenant(gateway, "beta")
    await gateway.admin("PUT", f"/admin/budgets/{gateway.tenant.id}", json={"usd": "1"})
    await gateway.admin("PUT", f"/admin/budgets/{other}", json={"usd": "2"})

    listed = (await gateway.admin("GET", "/admin/budgets")).json()

    assert {entry["scope_id"] for entry in listed} == {gateway.tenant.id, other}
    assert {entry["usd"] for entry in listed} == {"1.000000000000", "2.000000000000"}


async def test_clearing_a_budget_uncaps_the_tenant(gateway: GatewayHarness) -> None:
    await gateway.admin("PUT", f"/admin/budgets/{gateway.tenant.id}", json={"usd": "0.000001"})
    from .support.fixtures import anthropic_request

    assert (await gateway.post("/v1/messages", anthropic_request())).status_code == 402

    cleared = await gateway.admin("DELETE", f"/admin/budgets/{gateway.tenant.id}")

    assert cleared.status_code == 200
    assert cleared.json()["usd"] == "0.000001000000", "returns the budget as it last stood"
    assert (await gateway.admin("GET", f"/admin/budgets/{gateway.tenant.id}")).status_code == 404
    assert (await gateway.post("/v1/messages", anthropic_request())).status_code == 200


async def test_clearing_a_budget_that_is_not_there_is_404(gateway: GatewayHarness) -> None:
    response = await gateway.admin("DELETE", f"/admin/budgets/{gateway.tenant.id}")
    assert response.status_code == 404


# --- the read that sweeps ---------------------------------------------------------------------


async def test_reading_a_budget_releases_holds_whose_owner_never_returned(
    gateway: GatewayHarness,
) -> None:
    """A GET with a side effect, taken deliberately (see the module docstring in
    ``headroom/api/budgets.py``): an operator asking what is reserved during an incident
    must get the live figure, not one inflated by processes that died."""
    from datetime import UTC, datetime, timedelta

    from headroom.core.budgets import RESERVATION_TTL_S, BudgetScope

    await gateway.admin("PUT", f"/admin/budgets/{gateway.tenant.id}", json={"usd": "1.00"})
    stale = datetime.now(UTC) - timedelta(seconds=RESERVATION_TTL_S + 60)
    await gateway.budgets.store.reserve(
        BudgetScope.tenant(gateway.tenant.id),
        request_id="hr_from_a_process_that_died",
        usd=Decimal("0.75"),
        when=stale,
    )

    body = (await gateway.admin("GET", f"/admin/budgets/{gateway.tenant.id}")).json()

    assert body["reserved"] == "0.000000000000"
    assert body["remaining"] == "1.000000000000"
    assert body["expired_releases"] == 1
    assert body["expired_released"] == "0.750000000000"
