"""The part of the live smokes CI can run: their identity, without their spend.

**Why this file exists.** ``tests/test_live_smoke.py`` exercises the authentication
surface — it is a real HTTP request through the real gateway — and it is excluded from
every default collection, so no CI job has ever executed a line of it. Phase 2 made
``/v1/*`` require a virtual key and updated the keyless suite; the live smokes still
sent none, and stayed broken through Phase 3 until the operator ran them by hand and got
two 401 ``missing_api_key`` responses. A test nothing runs is a test that decays
silently, and the decay is invisible precisely because the marker that protects the
budget also hides the rot.

**What is and is not covered by collection.** Import-time bitrot is already caught: the
``-m "not live"`` filter deselects *after* collection, so every default run imports
``test_live_smoke.py`` and an ``ImportError`` there fails the suite. What collection
cannot see is behaviour — whether the request the smoke builds still authenticates. That
was the actual gap, so that is what this file closes: the smokes' own provisioning
helper is driven through the real :class:`~headroom.policy.auth.Authenticator`, against
the in-memory store, keylessly, on every CI run.

It cannot prove the live smokes work — only the operator's GPUs and Anthropic's servers
can do that. It proves the credential they present is one the gateway accepts, which is
the thing that broke.
"""

from __future__ import annotations

import importlib

import pytest

from headroom.core.context import RequestContext
from headroom.core.errors import MissingCredential
from headroom.db.memory import InMemoryTenantStore
from headroom.policy.auth import Authenticator
from headroom.policy.keys import hash_key

from .support.live import LIVE_TENANT_NAME, provision


async def authenticate(store: InMemoryTenantStore, headers: dict[str, str]) -> RequestContext:
    """Run the gateway's real authenticator over these headers; return the context."""
    ctx = RequestContext(route="/v1/messages", method="POST")
    await Authenticator(store).authenticate(headers, ctx)
    return ctx


def test_the_live_smokes_are_all_marked_live() -> None:
    """Importing the module is what a default collection already does; assert the marker.

    If this import ever fails, the whole suite fails with it — which is the point: the
    live module is not quarantined from CI, only its execution is.
    """
    module = importlib.import_module("tests.test_live_smoke")
    assert module.pytestmark.name == "live", (
        "tests/test_live_smoke.py must carry a module-level `live` marker; without it a "
        "default `make test` would spend money"
    )


async def test_a_provisioned_smoke_credential_authenticates() -> None:
    """The regression. A live smoke's headers must satisfy the Phase 2 auth surface."""
    store = InMemoryTenantStore()
    identity = await provision(store, key_name="wiring-test")

    ctx = await authenticate(store, identity.headers)

    assert ctx.tenant_id == identity.tenant.id
    assert ctx.key_id == identity.key.id


async def test_a_provisioned_smoke_identity_is_uncapped_and_unlimited() -> None:
    """Phase 4b's version of the same guard: the smokes must not gate themselves.

    A live smoke costs real money and is run by hand, so a 429 or a 402 from Headroom's
    own policy layer would burn an operator's time diagnosing a provider that was never
    called. Neither gate can fire here — a freshly provisioned tenant has no budget and
    no limits — and this asserts it keylessly rather than leaving it to be discovered on
    a run that costs a dollar.
    """
    store = InMemoryTenantStore()
    identity = await provision(store, key_name="wiring-test")

    record = await store.find_by_hash(hash_key(identity.api_key))

    assert record is not None
    assert record.tenant.limits.configured is False
    assert record.key.limits.configured is False


async def test_a_live_smoke_that_sent_no_key_would_be_refused() -> None:
    """The sabotage: the exact failure the operator hit, asserted keylessly.

    This is what ``tests/test_live_smoke.py`` did before 2026-08-09 — a bare
    ``content-type`` and nothing else — and it is a 401 whose reason is
    ``missing_api_key``, not a gateway or provider problem.
    """
    store = InMemoryTenantStore()
    await provision(store, key_name="wiring-test")

    with pytest.raises(MissingCredential):
        await authenticate(store, {"content-type": "application/json"})


async def test_provisioning_reuses_the_tenant_and_mints_a_fresh_key() -> None:
    """Run after run: one tenant to filter the ledger by, a new key every time.

    The key cannot be reused — its plaintext is never stored (H-017) — and the tenant
    must be, or ``/admin/usage?tenant_id=…`` would name a different tenant each run.
    """
    store = InMemoryTenantStore()

    first = await provision(store, key_name="run-1")
    second = await provision(store, key_name="run-2")

    assert second.tenant.id == first.tenant.id
    assert second.key.id != first.key.id
    assert second.api_key != first.api_key
    assert [tenant.name for tenant in await store.list_tenants()] == [LIVE_TENANT_NAME]


async def test_provisioning_reactivates_a_deactivated_smoke_tenant() -> None:
    """A tidied-up tenant must not turn the next live run into an unexplained 401."""
    store = InMemoryTenantStore()
    first = await provision(store, key_name="run-1")
    await store.update_tenant(first.tenant.id, active=False)

    second = await provision(store, key_name="run-2")

    assert second.tenant.id == first.tenant.id
    assert second.tenant.active
    ctx = await authenticate(store, second.headers)
    assert ctx.tenant_id == first.tenant.id
