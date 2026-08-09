"""``/admin/limits``: setting a limit, reading a bucket, and turning it off in a hurry.

The shape mirrors ``tests/test_admin_budgets.py`` — same credential, same 404 discipline,
same "the view is the thing an operator is going to act on" standard — with one behaviour
of its own worth the file: a GET here joins two datastores, the Postgres row that says
what the limit *is* and the DynamoDB item that says what is *left of it*.
"""

from __future__ import annotations

from typing import Any

import pytest

from headroom.core.limits import DIM_REQUESTS, SCOPE_KEY, SCOPE_TENANT
from headroom.providers.mock import MockScript

from .support.fixtures import anthropic_request
from .support.harness import GatewayHarness


def path(harness: GatewayHarness, scope: str = SCOPE_TENANT) -> str:
    scope_id = harness.tenant.id if scope == SCOPE_TENANT else harness.key.id
    return f"/admin/limits/{scope}/{scope_id}"


async def put(harness: GatewayHarness, body: dict[str, Any], scope: str = SCOPE_TENANT) -> Any:
    return await harness.admin("PUT", path(harness, scope), json=body)


# --- the credential -------------------------------------------------------------------


@pytest.mark.parametrize("method", ["GET", "PUT", "DELETE"])
async def test_every_route_needs_the_root_token(gateway: GatewayHarness, method: str) -> None:
    """H-019, unchanged: `/admin/*` is one credential and `/v1/*` is another."""
    response = await gateway.admin(method, path(gateway), json={}, authenticate=False)
    assert response.status_code == 401

    wrong = await gateway.admin(method, path(gateway), json={}, token="not-the-token")
    assert wrong.status_code == 401


async def test_a_virtual_key_is_not_an_admin_credential(gateway: GatewayHarness) -> None:
    """The tenant's own key must not be able to raise the tenant's own limit."""
    response = await gateway.admin("GET", path(gateway), token=gateway.api_key)
    assert response.status_code == 401


# --- reading --------------------------------------------------------------------------


async def test_an_unlimited_scope_reads_as_unlimited_rather_than_missing(
    gateway: GatewayHarness,
) -> None:
    """A 200 with nulls, not a 404.

    Deliberately unlike `/admin/budgets`, which 404s for an unbudgeted tenant, and the
    difference is not an inconsistency: a budget is a *record* that exists or does not,
    while a limit is a *property* of a tenant that is always defined — sometimes as
    "unlimited". An operator asking "what is this tenant's limit" deserves the answer
    rather than a not-found.
    """
    response = await gateway.admin("GET", path(gateway))

    assert response.status_code == 200
    body = response.json()
    assert body["requests_per_min"] is None
    assert body["tokens_per_min"] is None
    assert body["buckets"] == [], "an unlimited dimension has no bucket to report"
    assert body["scope"] == f"tenant#{gateway.tenant.id}"
    assert body["name"] == gateway.tenant.name


async def test_a_typod_id_is_a_404_and_an_unknown_scope_kind_is_too(
    gateway: GatewayHarness,
) -> None:
    """A limit must never be configured for something nobody can authenticate as."""
    missing = await gateway.admin("PUT", "/admin/limits/tenant/nope", json={"requests_per_min": 5})
    assert missing.status_code == 404
    assert missing.json()["error"]["type"] == "tenant_not_found"

    missing_key = await gateway.admin("GET", "/admin/limits/key/nope")
    assert missing_key.status_code == 404
    assert missing_key.json()["error"]["type"] == "key_not_found"

    nonsense = await gateway.admin("GET", f"/admin/limits/banana/{gateway.tenant.id}")
    assert nonsense.status_code == 404
    assert nonsense.json()["error"]["type"] == "unknown_scope"


async def test_the_view_reports_the_live_bucket_beside_the_configured_limit(
    gateway: GatewayHarness,
) -> None:
    """The join that makes this route worth having: config from Postgres, state from
    DynamoDB, in one answer to "why is this tenant getting 429s"."""
    gateway.book.set("ok", MockScript.anthropic_message("hello"))
    await put(gateway, {"requests_per_min": 10})

    for _ in range(3):
        assert (
            await gateway.post("/v1/messages", anthropic_request(), script="ok")
        ).status_code == 200

    body = (await gateway.admin("GET", path(gateway))).json()
    assert body["requests_per_min"] == 10
    (bucket,) = body["buckets"]
    assert bucket["dimension"] == DIM_REQUESTS
    assert bucket["limit_per_min"] == 10
    assert bucket["available"] == 7
    # Three units consumed at 6s each is 18 seconds of credit, and the prediction says so.
    assert bucket["reset_after_s"] == 18


async def test_both_dimensions_appear_when_both_are_configured(gateway: GatewayHarness) -> None:
    await put(gateway, {"requests_per_min": 10, "tokens_per_min": 5_000})

    body = (await gateway.admin("GET", path(gateway))).json()
    assert [bucket["dimension"] for bucket in body["buckets"]] == ["requests", "tokens"]
    assert [bucket["available"] for bucket in body["buckets"]] == [10, 5_000]


# --- writing --------------------------------------------------------------------------


async def test_put_replaces_rather_than_patches(gateway: GatewayHarness) -> None:
    """An absent dimension is *unlimited*, not *unchanged*.

    The alternative — patch semantics — would leave no way to remove a limit at all,
    which is a trap precisely when somebody is trying to undo one during an incident.
    """
    await put(gateway, {"requests_per_min": 10, "tokens_per_min": 5_000})

    replaced = await put(gateway, {"tokens_per_min": 5_000})

    assert replaced.status_code == 200
    assert replaced.json()["requests_per_min"] is None
    assert replaced.json()["tokens_per_min"] == 5_000
    assert [bucket["dimension"] for bucket in replaced.json()["buckets"]] == ["tokens"]


async def test_an_empty_body_is_how_a_scope_becomes_unlimited(gateway: GatewayHarness) -> None:
    await put(gateway, {"requests_per_min": 3})
    assert (await put(gateway, {})).json()["requests_per_min"] is None


@pytest.mark.parametrize("body", [{"requests_per_min": 0}, {"tokens_per_min": -1}])
async def test_a_limit_below_one_is_refused(gateway: GatewayHarness, body: dict[str, int]) -> None:
    """Zero would mean "admit nothing", which has two better spellings and no emission
    interval. Refused here, and by ``migrations/0004``'s CHECK against a hand-written
    UPDATE."""
    assert (await put(gateway, body)).status_code == 422


async def test_an_unknown_field_is_refused_by_name(gateway: GatewayHarness) -> None:
    """``extra="forbid"``, the same as every other admin model: a typo'd knob that is
    silently ignored is a limit an operator believes they set."""
    assert (await put(gateway, {"requests_per_minute": 5})).status_code == 422


async def test_the_key_scope_is_configured_the_same_way(gateway: GatewayHarness) -> None:
    """BUILD_PLAN's *"per key and per tenant"*, through the API rather than the store."""
    response = await put(gateway, {"requests_per_min": 2}, scope=SCOPE_KEY)

    assert response.status_code == 200
    body = response.json()
    assert body["scope"] == f"key#{gateway.key.id}"
    assert body["name"] == gateway.key.name
    assert (await gateway.store.get_key(gateway.key.id)).limits.requests_per_min == 2  # type: ignore[union-attr]


async def test_a_new_limit_applies_to_the_very_next_request(gateway: GatewayHarness) -> None:
    """The route invalidates the auth cache, because the limits ride the Principal.

    Without it a tightened limit would take up to ``AUTH_CACHE_TTL_S`` seconds to bite in
    the process that made the change — which is not what an operator reaching for this
    route during an incident means (H-037).
    """
    gateway.book.set("ok", MockScript.anthropic_message("hello"))
    assert (await gateway.post("/v1/messages", anthropic_request(), script="ok")).status_code == 200

    await put(gateway, {"requests_per_min": 1})

    assert (await gateway.post("/v1/messages", anthropic_request(), script="ok")).status_code == 200
    assert (await gateway.post("/v1/messages", anthropic_request(), script="ok")).status_code == 429


async def test_changing_a_limit_does_not_reset_the_bucket(gateway: GatewayHarness) -> None:
    """Raising a limit mid-minute must not hand back traffic already admitted under the
    old one — ``tat`` is an absolute time and means the same thing either way."""
    gateway.book.set("ok", MockScript.anthropic_message("hello"))
    await put(gateway, {"requests_per_min": 2})
    for _ in range(2):
        await gateway.post("/v1/messages", anthropic_request(), script="ok")

    body = (await put(gateway, {"requests_per_min": 20})).json()

    (bucket,) = body["buckets"]
    assert bucket["limit_per_min"] == 20
    # A full minute of credit was consumed at the old rate, and it is still consumed —
    # but at the new rate it is worth far less of the bucket.
    assert bucket["available"] < 20
    assert bucket["reset_after_s"] > 0


# --- turning it off -------------------------------------------------------------------


async def test_delete_clears_the_limit_and_empties_the_buckets(gateway: GatewayHarness) -> None:
    """The incident-response route, and it has to do both halves.

    Clearing the configuration alone would leave a bucket whose ``tat`` sits a minute in
    the future; emptying the bucket alone would leave the limit in force. "Turn this off"
    means both.
    """
    gateway.book.set("ok", MockScript.anthropic_message("hello"))
    await put(gateway, {"requests_per_min": 1})
    await gateway.post("/v1/messages", anthropic_request(), script="ok")
    assert (await gateway.post("/v1/messages", anthropic_request(), script="ok")).status_code == 429

    response = await gateway.admin("DELETE", path(gateway))

    assert response.status_code == 200
    assert response.json()["requests_per_min"] is None
    assert response.json()["buckets"] == []
    # And the very next request is served, rather than waiting out the old bucket.
    assert (await gateway.post("/v1/messages", anthropic_request(), script="ok")).status_code == 200
    assert await gateway.limits.store.clear(gateway.bucket_key(DIM_REQUESTS)) is False


# --- the listing ----------------------------------------------------------------------


async def test_the_listing_shows_only_scopes_that_have_a_limit(gateway: GatewayHarness) -> None:
    """An uncapped scope is not a row with nulls in it — it simply is not limited."""
    assert (await gateway.admin("GET", "/admin/limits")).json() == []

    await put(gateway, {"requests_per_min": 10})
    await put(gateway, {"tokens_per_min": 1_000}, scope=SCOPE_KEY)

    listing = (await gateway.admin("GET", "/admin/limits")).json()
    assert [
        (row["scope_kind"], row["requests_per_min"], row["tokens_per_min"]) for row in listing
    ] == [
        ("tenant", 10, None),
        ("key", None, 1_000),
    ]

    await gateway.admin("DELETE", path(gateway))
    assert [row["scope_kind"] for row in (await gateway.admin("GET", "/admin/limits")).json()] == [
        "key"
    ]
