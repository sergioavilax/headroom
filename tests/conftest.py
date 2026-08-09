"""The gateway fixture every proxy test runs against — keyless, mock-only, isolated.

BUILD_PLAN §0.2 invariant 4: every test runs on the MockProvider without a key. The
fixture below builds a complete gateway whose only provider is the mock, installs it on
the real application object, and drives it over ASGI — so tests exercise middleware,
routing, the proxy, and the dialects together rather than calling internals. A test
that passes here is a test about the thing the operator will curl.

Each test gets its own script book and provider, so a fault injected in one test cannot
leak into the next — the failure mode that makes fault-injection suites untrustworthy.

Since Phase 2 it also gets its own **control plane**: a fresh in-memory store holding
one tenant and one unrestricted virtual key, an authenticator with its own cache, and a
root admin token. Isolation matters as much here as it does for faults — a cached auth
decision or a revoked key leaking between tests would make the revocation-window
assertions meaningless.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from headroom.api.gateway import Gateway
from headroom.api.main import app as headroom_app
from headroom.db.memory import InMemoryTenantStore
from headroom.policy.auth import Authenticator
from headroom.policy.keys import display_prefix, hash_key, mint_key
from headroom.providers.mock import MockProvider, MockScriptBook
from headroom.providers.registry import ProviderRegistry

from .support.asgi import ContextRecorder
from .support.harness import ADMIN_TOKEN, GatewayHarness, mock_only_config


@pytest.fixture
async def gateway() -> AsyncIterator[GatewayHarness]:
    """A keyless gateway wired to a fresh MockProvider, served over ASGI."""
    book = MockScriptBook()
    provider = MockProvider("mock", book)
    registry = ProviderRegistry()
    registry.add(provider)
    config = mock_only_config()

    store = InMemoryTenantStore()
    tenant = await store.create_tenant("acme")
    plaintext = mint_key()
    key = await store.create_key(
        tenant_id=tenant.id,
        name="default",
        key_hash=hash_key(plaintext),
        key_prefix=display_prefix(plaintext),
    )
    assert key is not None  # the tenant was just created

    instance = Gateway(
        config=config,
        registry=registry,
        routing=config.routing_table(),
        store=store,
        authenticator=Authenticator(store),
        admin_token=ADMIN_TOKEN,
    )

    previous = getattr(headroom_app.state, "gateway", None)
    headroom_app.state.gateway = instance
    recorder = ContextRecorder(headroom_app)
    transport = httpx.ASGITransport(app=recorder)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://gateway") as client:
            yield GatewayHarness(
                app=headroom_app,
                book=book,
                provider=provider,
                client=client,
                recorder=recorder,
                store=store,
                authenticator=instance.authenticator,
                tenant=tenant,
                key=key,
                api_key=plaintext,
            )
    finally:
        await instance.aclose()
        headroom_app.state.gateway = previous
