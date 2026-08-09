"""The admin surface: CRUD over tenants and keys, and the token that guards it.

Two things are being checked here at once. The boring one is that the CRUD works. The
one worth the file is that the *failure* modes are the right ones — because an admin API
is the piece of a gateway that, when it fails open, hands over every tenant at once.

The sharpest of those is ``test_the_admin_api_is_off_when_no_token_is_configured``: a
deployment that forgot ``HEADROOM_ADMIN_TOKEN`` must get 503 on every route, never an
open CRUD (docs/DECISIONS.md H-019).
"""

from __future__ import annotations

from typing import Any

import pytest

from headroom.api.gateway import Gateway
from headroom.core.storage import TenantStore

from .support.fixtures import anthropic_request
from .support.harness import GatewayHarness


def gateway_of(harness: GatewayHarness) -> Gateway:
    instance: Gateway = harness.app.state.gateway
    return instance


async def make_tenant(harness: GatewayHarness, name: str) -> dict[str, Any]:
    response = await harness.admin("POST", "/admin/tenants", json={"name": name})
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def make_key(harness: GatewayHarness, tenant_id: str, **extra: Any) -> dict[str, Any]:
    response = await harness.admin(
        "POST", "/admin/keys", json={"tenant_id": tenant_id, "name": "svc", **extra}
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


# --- the guard ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/admin/tenants"),
        ("POST", "/admin/tenants"),
        ("GET", "/admin/keys"),
        ("POST", "/admin/keys"),
    ],
)
async def test_every_admin_route_needs_the_root_token(
    gateway: GatewayHarness, method: str, path: str
) -> None:
    response = await gateway.admin(method, path, json={"name": "x"}, authenticate=False)

    assert response.status_code == 401
    assert response.json()["headroom"]["reason"] == "admin_unauthorized"


async def test_a_wrong_root_token_is_401(gateway: GatewayHarness) -> None:
    response = await gateway.admin("GET", "/admin/tenants", token="not-the-token")

    assert response.status_code == 401


async def test_a_virtual_key_is_not_an_admin_token(gateway: GatewayHarness) -> None:
    """The two credentials live in the same header and are never interchangeable."""
    response = await gateway.admin("GET", "/admin/tenants", token=gateway.api_key)

    assert response.status_code == 401


async def test_the_admin_api_is_off_when_no_token_is_configured(
    gateway: GatewayHarness,
) -> None:
    """Unset means **off**, not open.

    The alternative — "no token configured, so no check" — is a fully open tenant-and-key
    CRUD on the first deployment that forgets one environment variable, and it fails
    silently, which is the worst combination available.
    """
    gateway_of(gateway).admin_token = None

    response = await gateway.admin("GET", "/admin/tenants")

    assert response.status_code == 503
    body = response.json()
    assert body["headroom"]["reason"] == "admin_api_disabled"
    assert "HEADROOM_ADMIN_TOKEN" in body["error"]["message"]


async def test_an_admin_error_carries_the_request_id(gateway: GatewayHarness) -> None:
    response = await gateway.admin("GET", "/admin/tenants", authenticate=False)

    assert response.json()["headroom"]["request_id"] == response.headers["x-headroom-request-id"]


# --- tenants ------------------------------------------------------------------------


async def test_create_and_read_a_tenant(gateway: GatewayHarness) -> None:
    created = await make_tenant(gateway, "globex")

    assert created["name"] == "globex"
    assert created["active"] is True
    assert created["created_at"] and created["updated_at"]

    fetched = await gateway.admin("GET", f"/admin/tenants/{created['id']}")
    assert fetched.status_code == 200
    assert fetched.json() == created


async def test_list_tenants_includes_the_seeded_one(gateway: GatewayHarness) -> None:
    await make_tenant(gateway, "globex")

    response = await gateway.admin("GET", "/admin/tenants")

    names = {tenant["name"] for tenant in response.json()}
    assert names == {"acme", "globex"}


async def test_a_duplicate_tenant_name_is_409(gateway: GatewayHarness) -> None:
    await make_tenant(gateway, "globex")

    response = await gateway.admin("POST", "/admin/tenants", json={"name": "globex"})

    assert response.status_code == 409
    assert response.json()["headroom"]["reason"] == "tenant_name_conflict"


async def test_patching_a_tenant_leaves_unmentioned_fields_alone(
    gateway: GatewayHarness,
) -> None:
    created = await make_tenant(gateway, "globex")

    response = await gateway.admin(
        "PATCH", f"/admin/tenants/{created['id']}", json={"active": False}
    )

    assert response.status_code == 200
    assert response.json()["name"] == "globex", "a PATCH is not a replace"
    assert response.json()["active"] is False


async def test_deleting_a_tenant_deactivates_it_rather_than_removing_it(
    gateway: GatewayHarness,
) -> None:
    """Phase 3's ledger will point at this id forever; it must not vanish."""
    created = await make_tenant(gateway, "globex")

    deleted = await gateway.admin("DELETE", f"/admin/tenants/{created['id']}")
    still_there = await gateway.admin("GET", f"/admin/tenants/{created['id']}")

    assert deleted.status_code == 200
    assert deleted.json()["active"] is False
    assert still_there.status_code == 200


async def test_an_unknown_tenant_is_404(gateway: GatewayHarness) -> None:
    response = await gateway.admin("GET", "/admin/tenants/00000000-0000-4000-8000-000000000000")

    assert response.status_code == 404
    assert response.json()["headroom"]["reason"] == "tenant_not_found"


async def test_a_tenant_id_that_is_not_even_an_id_is_404(gateway: GatewayHarness) -> None:
    """``/admin/tenants/banana`` is "not found", not a database cast error."""
    response = await gateway.admin("GET", "/admin/tenants/banana")

    assert response.status_code == 404


async def test_an_unknown_field_is_rejected(gateway: GatewayHarness) -> None:
    """``extra="forbid"``: a typo in an admin payload fails loudly, it is not ignored."""
    response = await gateway.admin("POST", "/admin/tenants", json={"name": "globex", "activ": True})

    assert response.status_code == 422


# --- keys ---------------------------------------------------------------------------


async def test_create_a_key_and_get_the_plaintext_once(gateway: GatewayHarness) -> None:
    created = await make_key(gateway, gateway.tenant.id)

    assert created["key"].startswith("hk_")
    assert created["key_prefix"] == created["key"][:11]
    assert created["status"] == "active"
    assert created["revoked_at"] is None
    assert created["allowed_models"] == []
    assert created["allowed_providers"] == []


async def test_a_created_key_authenticates_immediately(gateway: GatewayHarness) -> None:
    """No negative caching: a key minted a millisecond ago has to work now."""
    created = await make_key(gateway, gateway.tenant.id)

    response = await gateway.post("/v1/messages", anthropic_request(), api_key=created["key"])

    assert response.status_code == 200


async def test_a_key_for_an_unknown_tenant_is_404(gateway: GatewayHarness) -> None:
    response = await gateway.admin(
        "POST",
        "/admin/keys",
        json={"tenant_id": "00000000-0000-4000-8000-000000000000", "name": "svc"},
    )

    assert response.status_code == 404
    assert response.json()["headroom"]["reason"] == "tenant_not_found"


async def test_keys_can_be_listed_and_narrowed_to_a_tenant(gateway: GatewayHarness) -> None:
    other = await make_tenant(gateway, "globex")
    mine = await make_key(gateway, gateway.tenant.id)
    theirs = await make_key(gateway, other["id"])

    everything = await gateway.admin("GET", "/admin/keys")
    narrowed = await gateway.admin("GET", "/admin/keys", params={"tenant_id": other["id"]})

    assert {key["id"] for key in everything.json()} >= {mine["id"], theirs["id"]}
    assert [key["id"] for key in narrowed.json()] == [theirs["id"]]


async def test_patching_a_key_replaces_scope_wholesale(gateway: GatewayHarness) -> None:
    created = await make_key(gateway, gateway.tenant.id, allowed_models=["a", "b"])

    response = await gateway.admin(
        "PATCH", f"/admin/keys/{created['id']}", json={"allowed_models": ["c"]}
    )

    assert response.status_code == 200
    assert response.json()["allowed_models"] == ["c"], "scope is replaced, never merged"
    assert response.json()["name"] == "svc"


async def test_deleting_a_key_revokes_it(gateway: GatewayHarness) -> None:
    created = await make_key(gateway, gateway.tenant.id)

    revoked = await gateway.admin("DELETE", f"/admin/keys/{created['id']}")
    fetched = await gateway.admin("GET", f"/admin/keys/{created['id']}")

    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"
    assert revoked.json()["revoked_at"] is not None
    assert fetched.json()["status"] == "revoked", "the row survives; only the key dies"


async def test_revoking_twice_keeps_the_first_timestamp(gateway: GatewayHarness) -> None:
    """Operators revoke twice during an incident. The review needs the first time."""
    created = await make_key(gateway, gateway.tenant.id)

    first = await gateway.admin("DELETE", f"/admin/keys/{created['id']}")
    second = await gateway.admin("DELETE", f"/admin/keys/{created['id']}")

    assert second.status_code == 200
    assert second.json()["revoked_at"] == first.json()["revoked_at"]


async def test_an_unknown_key_is_404(gateway: GatewayHarness) -> None:
    response = await gateway.admin("GET", "/admin/keys/00000000-0000-4000-8000-000000000000")

    assert response.status_code == 404
    assert response.json()["headroom"]["reason"] == "key_not_found"


async def test_the_admin_api_never_needs_a_virtual_key(gateway: GatewayHarness) -> None:
    """The proxy's 401 must not bleed onto ``/admin`` — different credential, different
    router, and the control plane has to stay reachable when every key is revoked."""
    store: TenantStore = gateway.store
    key = await store.get_key(gateway.key.id)
    assert key is not None
    await store.revoke_key(key.id)

    response = await gateway.admin("GET", "/admin/tenants")

    assert response.status_code == 200
