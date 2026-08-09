"""The auth matrix: every way a request can fail to be somebody, with exact statuses.

BUILD_PLAN Phase 2 says "401/403 semantics exact", and exact is the operative word —
the two statuses answer different questions and a gateway that blurs them is one that
either leaks its tenant list to strangers (403 for an unknown key tells an attacker the
key format is right) or breaks every SDK's retry logic (401 for a scope problem tells a
correctly-configured client to go find a better credential, which it cannot).

So the rule, and every row below checks one cell of it:

* **401 — we do not know who you are.** Nothing presented, something unusable, a key
  that was never issued, a key that was revoked, a key whose tenant is switched off.
  All five carry distinct ``headroom.reason`` values and the same status, because the
  ``reason`` is for the operator reading a log and the status is for the stranger.
* **403 — we know exactly who you are, and no.** A live key reaching past its scope.

Two orderings are asserted here as well as the statuses, because they are where the
information leaks would be (docs/DECISIONS.md H-020): authentication happens **before**
the body is parsed, and the model scope is checked **before** routing.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from headroom.core.storage import TenantStore
from headroom.policy.keys import display_prefix, hash_key, mint_key
from headroom.providers.mock import MockScript

from .support.fixtures import anthropic_request, openai_request
from .support.harness import GatewayHarness

ANTHROPIC = ("/v1/messages", anthropic_request)
OPENAI = ("/v1/chat/completions", openai_request)
BOTH_DIALECTS = pytest.mark.parametrize(
    ("path", "make_body"), [ANTHROPIC, OPENAI], ids=["anthropic", "openai"]
)


def body_of(response: Any) -> dict[str, Any]:
    parsed: dict[str, Any] = json.loads(response.content)
    return parsed


def reason_of(response: Any) -> str:
    reason: str = body_of(response)["headroom"]["reason"]
    return reason


async def issue_key(
    store: TenantStore,
    tenant_id: str,
    *,
    name: str = "scoped",
    allowed_models: tuple[str, ...] = (),
    allowed_providers: tuple[str, ...] = (),
) -> tuple[str, str]:
    """Mint a key into ``store`` and return ``(plaintext, key_id)``."""
    plaintext = mint_key()
    key = await store.create_key(
        tenant_id=tenant_id,
        name=name,
        key_hash=hash_key(plaintext),
        key_prefix=display_prefix(plaintext),
        allowed_models=allowed_models,
        allowed_providers=allowed_providers,
    )
    assert key is not None
    return plaintext, key.id


# --- 401: we do not know who you are ------------------------------------------------


@BOTH_DIALECTS
async def test_a_request_with_no_key_is_401(
    gateway: GatewayHarness, path: str, make_body: Any
) -> None:
    response = await gateway.post(path, make_body(), authenticate=False)

    assert response.status_code == 401
    assert reason_of(response) == "missing_api_key"
    assert response.headers["x-headroom-error-source"] == "gateway"
    assert gateway.last_context().outcome == "missing_api_key"
    assert gateway.last_context().tenant_id is None


@BOTH_DIALECTS
async def test_a_malformed_key_is_401_and_says_so(
    gateway: GatewayHarness, path: str, make_body: Any
) -> None:
    """A provider key pasted by mistake never reaches the database."""
    response = await gateway.post(path, make_body(), api_key="sk-ant-not-a-headroom-key")

    assert response.status_code == 401
    assert reason_of(response) == "malformed_api_key"


@BOTH_DIALECTS
async def test_a_well_formed_but_unknown_key_is_401(
    gateway: GatewayHarness, path: str, make_body: Any
) -> None:
    response = await gateway.post(path, make_body(), api_key=mint_key())

    assert response.status_code == 401
    assert reason_of(response) == "unknown_api_key"


@BOTH_DIALECTS
async def test_a_revoked_key_is_401(gateway: GatewayHarness, path: str, make_body: Any) -> None:
    plaintext, key_id = await issue_key(gateway.store, gateway.tenant.id)
    await gateway.store.revoke_key(key_id)

    response = await gateway.post(path, make_body(), api_key=plaintext)

    assert response.status_code == 401
    assert reason_of(response) == "revoked_api_key"


@BOTH_DIALECTS
async def test_a_live_key_on_an_inactive_tenant_is_401(
    gateway: GatewayHarness, path: str, make_body: Any
) -> None:
    """Deactivating a tenant is as final as revoking every one of its keys."""
    plaintext, _ = await issue_key(gateway.store, gateway.tenant.id)
    await gateway.store.update_tenant(gateway.tenant.id, active=False)

    response = await gateway.post(path, make_body(), api_key=plaintext)

    assert response.status_code == 401
    assert reason_of(response) == "inactive_tenant"


async def test_an_empty_bearer_header_counts_as_missing(gateway: GatewayHarness) -> None:
    response = await gateway.post(
        "/v1/messages",
        anthropic_request(),
        authenticate=False,
        headers={"authorization": "Bearer "},
    )

    assert response.status_code == 401
    assert reason_of(response) == "missing_api_key"


# --- the credential's spelling ------------------------------------------------------


@pytest.mark.parametrize(
    ("header", "template"),
    [
        ("authorization", "Bearer {key}"),
        ("authorization", "{key}"),
        ("x-api-key", "{key}"),
        ("api-key", "{key}"),
    ],
    ids=["bearer", "bare-authorization", "x-api-key", "api-key"],
)
async def test_a_key_is_accepted_in_every_spelling_clients_use(
    gateway: GatewayHarness, header: str, template: str
) -> None:
    """The Anthropic SDK sends ``x-api-key``; the OpenAI SDK sends a bearer token.

    Assumption A2 — Backline points its Anthropic provider at Headroom with nothing
    but a ``base_url`` override — dies if the gateway is fussy about which one arrives
    on which route.
    """
    gateway.book.set("ok", MockScript.anthropic_message("hi"))

    response = await gateway.post(
        "/v1/messages",
        anthropic_request(),
        script="ok",
        authenticate=False,
        headers={header: template.format(key=gateway.api_key)},
    )

    assert response.status_code == 200
    assert gateway.last_context().tenant_id == gateway.tenant.id


async def test_the_virtual_key_is_never_forwarded_upstream(gateway: GatewayHarness) -> None:
    """H-010's rule, now that the credential is worth stealing.

    A gateway that forwarded the caller's ``authorization`` would hand its own tenants'
    virtual keys to Anthropic. The provider's credential is added by the provider.
    """
    gateway.book.set("ok", MockScript.anthropic_message("hi"))

    await gateway.post("/v1/messages", anthropic_request(), script="ok")

    sent = gateway.last_upstream_request().headers
    assert "authorization" not in sent
    assert "x-api-key" not in sent
    assert gateway.api_key not in json.dumps(dict(sent))


# --- 403: we know who you are, and no -----------------------------------------------


async def test_a_model_outside_the_key_scope_is_403(gateway: GatewayHarness) -> None:
    plaintext, _ = await issue_key(
        gateway.store, gateway.tenant.id, allowed_models=("mock-model-2",)
    )

    response = await gateway.post("/v1/messages", anthropic_request(), api_key=plaintext)

    assert response.status_code == 403
    assert reason_of(response) == "model_out_of_scope"
    ctx = gateway.last_context()
    assert ctx.outcome == "model_out_of_scope"
    # The tenant is known — that is exactly what makes this a 403 and not a 401 — and
    # the request is attributed, so Phase 3 can meter a denial to whoever caused it.
    assert ctx.tenant_id == gateway.tenant.id
    assert ctx.model == "mock-model-1"
    assert ctx.provider is None, "scope is checked before the route is resolved"


async def test_a_model_inside_the_key_scope_is_allowed(gateway: GatewayHarness) -> None:
    gateway.book.set("ok", MockScript.anthropic_message("hi"))
    plaintext, _ = await issue_key(
        gateway.store, gateway.tenant.id, allowed_models=("mock-model-1",)
    )

    response = await gateway.post(
        "/v1/messages", anthropic_request(), script="ok", api_key=plaintext
    )

    assert response.status_code == 200


async def test_scope_entries_match_exactly_unless_they_end_in_a_star(
    gateway: GatewayHarness,
) -> None:
    """``mock-model-1`` must not quietly admit ``mock-model-10``.

    Prefix-by-default is the rule that would, and it is the rule a routing table uses
    two modules away — so the difference is asserted rather than assumed.
    """
    gateway.book.set("ok", MockScript.anthropic_message("hi"))
    exact, _ = await issue_key(
        gateway.store, gateway.tenant.id, name="exact", allowed_models=("mock-model-1",)
    )
    starred, _ = await issue_key(
        gateway.store, gateway.tenant.id, name="starred", allowed_models=("mock-model-*",)
    )

    denied = await gateway.post(
        "/v1/messages", anthropic_request(model="mock-model-10"), script="ok", api_key=exact
    )
    allowed = await gateway.post(
        "/v1/messages", anthropic_request(model="mock-model-10"), script="ok", api_key=starred
    )

    assert denied.status_code == 403
    assert allowed.status_code == 200


async def test_a_provider_outside_the_key_scope_is_403(gateway: GatewayHarness) -> None:
    plaintext, _ = await issue_key(gateway.store, gateway.tenant.id, allowed_providers=("vllm_a",))

    response = await gateway.post("/v1/messages", anthropic_request(), api_key=plaintext)

    assert response.status_code == 403
    assert reason_of(response) == "provider_out_of_scope"
    ctx = gateway.last_context()
    assert ctx.provider == "mock", "the route was resolved; the key may not reach it"
    assert ctx.outcome == "provider_out_of_scope"


async def test_an_empty_scope_means_unrestricted(gateway: GatewayHarness) -> None:
    """Empty is "no restriction", not "restricted to nothing"."""
    gateway.book.set("ok", MockScript.anthropic_message("hi"))

    response = await gateway.post("/v1/messages", anthropic_request(), script="ok")

    assert response.status_code == 200
    assert gateway.key.allowed_models == ()
    assert gateway.key.allowed_providers == ()


# --- ordering: what the caller is told first ---------------------------------------


async def test_an_anonymous_request_with_a_broken_body_is_401_not_400(
    gateway: GatewayHarness,
) -> None:
    """Authentication runs before the body is parsed.

    A gateway that told a stranger their JSON was malformed would be debugging requests
    for people it has not identified — and would leak that the endpoint exists and what
    it expects.
    """
    response = await gateway.post("/v1/messages", b"{oh no", authenticate=False)

    assert response.status_code == 401
    assert reason_of(response) == "missing_api_key"
    assert gateway.last_context().model is None


async def test_an_out_of_scope_model_is_403_even_when_it_is_also_unroutable(
    gateway: GatewayHarness,
) -> None:
    """403 beats 404, so a key cannot enumerate the routing table.

    ``gpt-4o`` is not routed by the test gateway. A key scoped away from it must not be
    able to tell that apart from a model that *is* routed but off-limits.
    """
    plaintext, _ = await issue_key(
        gateway.store, gateway.tenant.id, allowed_models=("mock-model-1",)
    )

    unroutable = await gateway.post(
        "/v1/messages", anthropic_request(model="gpt-4o"), api_key=plaintext
    )
    routable = await gateway.post(
        "/v1/messages", anthropic_request(model="mock-model-2"), api_key=plaintext
    )

    assert unroutable.status_code == routable.status_code == 403
    assert reason_of(unroutable) == reason_of(routable) == "model_out_of_scope"


async def test_an_authorised_key_still_gets_404_for_an_unrouted_model(
    gateway: GatewayHarness,
) -> None:
    """The 404 survives for a key that is allowed to ask — Phase 1's behaviour intact."""
    response = await gateway.post("/v1/messages", anthropic_request(model="gpt-4o"))

    assert response.status_code == 404
    assert reason_of(response) == "model_not_routed"


# --- the happy path -----------------------------------------------------------------


@BOTH_DIALECTS
async def test_a_valid_key_reaches_the_provider_and_stamps_the_tenant(
    gateway: GatewayHarness, path: str, make_body: Any
) -> None:
    script = (
        MockScript.anthropic_message("hi")
        if path == "/v1/messages"
        else MockScript.openai_completion("hi")
    )
    gateway.book.set("ok", script)

    response = await gateway.post(path, make_body(), script="ok")

    assert response.status_code == 200
    ctx = gateway.last_context()
    assert ctx.tenant_id == gateway.tenant.id
    assert ctx.key_id == gateway.key.id
    assert ctx.outcome == "ok"


async def test_a_streamed_request_authenticates_the_same_way(gateway: GatewayHarness) -> None:
    gateway.book.set("ok", MockScript.anthropic_stream("hello"))

    response = await gateway.post("/v1/messages", anthropic_request(stream=True), script="ok")

    assert response.status_code == 200
    assert b"message_stop" in response.content
    assert gateway.last_context().tenant_id == gateway.tenant.id


async def test_the_error_body_is_spoken_in_the_callers_dialect(gateway: GatewayHarness) -> None:
    """A 401 an SDK cannot parse is barely better than a hang-up (H-009's rule)."""
    anthropic = await gateway.post("/v1/messages", anthropic_request(), authenticate=False)
    openai = await gateway.post("/v1/chat/completions", openai_request(), authenticate=False)

    assert body_of(anthropic)["type"] == "error"
    assert body_of(anthropic)["error"]["type"] == "authentication_error"
    assert body_of(openai)["error"]["type"] == "authentication_error"
    # And both carry the request id, so a caller's screenshot leads to a log line.
    assert body_of(anthropic)["headroom"]["request_id"].startswith("hr_")


async def test_healthz_needs_no_key(gateway: GatewayHarness) -> None:
    """Liveness is not a tenant's business; a probe has no virtual key to present."""
    response = await gateway.client.get("/healthz")

    assert response.status_code == 200
