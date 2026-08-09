"""The gateway harness: a real app, a mock-only gateway, and handles to interrogate it.

Lives in ``support`` rather than ``conftest`` so tests can import the type and annotate
their fixture parameter — ``mypy --strict`` covers the test suite too, and a test file
full of untyped ``gateway`` parameters is a test file mypy cannot check.

Since Phase 2 the harness also carries an identity: a seeded tenant, an unrestricted
key, and the plaintext for it. :meth:`GatewayHarness.post` presents that key by
default, so every test written before tenancy existed now exercises the *authenticated*
path unchanged — and a test that wants to be anonymous, or to present something wrong,
says so explicitly with ``authenticate=False`` or ``api_key=…``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx

from headroom.api.gateway import Gateway
from headroom.api.main import app as headroom_app
from headroom.core.budgets import Budget, BudgetScope, BudgetStore
from headroom.core.config import GatewayConfig, ProviderSpec, RouteSpec
from headroom.core.context import RequestContext
from headroom.core.ledger import LedgerEntry, LedgerStore
from headroom.core.storage import Tenant, TenantStore, VirtualKey
from headroom.db.memory import InMemoryBudgetStore, InMemoryLedgerStore, InMemoryTenantStore
from headroom.metering.meter import Meter
from headroom.metering.prices import load_price_book
from headroom.metering.writer import LedgerWriter
from headroom.policy.auth import Authenticator
from headroom.policy.budgets import BudgetGate
from headroom.policy.keys import display_prefix, hash_key, mint_key
from headroom.providers.base import UpstreamRequest
from headroom.providers.mock import MockProvider, MockScriptBook
from headroom.providers.registry import ProviderRegistry

from .asgi import ASGIRun, ContextRecorder, start_request

__all__ = ["ADMIN_TOKEN", "GatewayHarness", "gateway_harness", "mock_only_config"]

#: The root admin token the test gateway is built with. A literal in a test file, not
#: a secret: nothing it guards outlives the process (BUILD_PLAN §0.2 invariant 3 is
#: about real credentials in the repo, and this guards an in-memory store).
ADMIN_TOKEN = "test-root-admin-token"


def mock_only_config() -> GatewayConfig:
    """A gateway configuration with one provider and nothing that can spend money.

    Routes are ``mock-`` prefixed rather than catch-all, so "this model is not routed"
    stays a case a test can deliberately ask for.
    """
    return GatewayConfig(
        providers={"mock": ProviderSpec(kind="mock")},
        routes={
            "anthropic": [RouteSpec(prefix="mock-", provider="mock")],
            "openai": [RouteSpec(prefix="mock-", provider="mock")],
        },
    )


@asynccontextmanager
async def gateway_harness(
    *, budgets: BudgetStore | None = None, tenant: str = "acme"
) -> AsyncIterator[GatewayHarness]:
    """Build a complete keyless gateway and serve it over ASGI.

    The body of ``tests/conftest.py``'s ``gateway`` fixture, lifted here so a test that
    needs a *different* backing store can build its own — which
    ``tests/test_budget_stampede.py`` does, because the headline test has to run against
    DynamoDB Local rather than against a dict that cannot interleave.

    Everything else is exactly what every other test gets: one MockProvider, one tenant,
    one unrestricted key, the committed price book, and its own control plane.
    """
    book = MockScriptBook()
    provider = MockProvider("mock", book)
    registry = ProviderRegistry()
    registry.add(provider)
    config = mock_only_config()

    store = InMemoryTenantStore()
    created = await store.create_tenant(tenant)
    plaintext = mint_key()
    key = await store.create_key(
        tenant_id=created.id,
        name="default",
        key_hash=hash_key(plaintext),
        key_prefix=display_prefix(plaintext),
    )
    assert key is not None  # the tenant was just created

    ledger = InMemoryLedgerStore()
    writer = LedgerWriter(ledger)
    prices = load_price_book()
    gate = BudgetGate(
        store=budgets if budgets is not None else InMemoryBudgetStore(), prices=prices
    )
    instance = Gateway(
        config=config,
        registry=registry,
        routing=config.routing_table(),
        store=store,
        authenticator=Authenticator(store),
        ledger=ledger,
        meter=Meter(prices=prices, writer=writer),
        budgets=gate,
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
                tenant=created,
                key=key,
                api_key=plaintext,
                ledger=ledger,
                meter=instance.meter,
                writer=writer,
                budgets=gate,
            )
    finally:
        await instance.aclose()
        headroom_app.state.gateway = previous


@dataclass
class GatewayHarness:
    """A running gateway plus the handles a test needs to interrogate it."""

    app: Any
    book: MockScriptBook
    provider: MockProvider
    client: httpx.AsyncClient
    recorder: ContextRecorder
    store: TenantStore
    authenticator: Authenticator
    tenant: Tenant
    key: VirtualKey
    #: The plaintext of :attr:`key`. Held here because the store never will.
    api_key: str
    ledger: LedgerStore
    meter: Meter
    #: Exposed so a test can drain the fire-and-forget queue before asserting. The
    #: writer is asynchronous *on purpose* (a slow database must never delay a stream),
    #: which means a test that asserted immediately after the response would be racing
    #: it — :meth:`ledger_row` waits properly instead of sleeping and hoping.
    writer: LedgerWriter
    #: Phase 4. The budget gate and the store behind it, so a test can set a cap, read
    #: the counters back, and drain the settlements a disconnect left detached.
    budgets: BudgetGate
    admin_token: str = ADMIN_TOKEN

    # --- proxy requests -----------------------------------------------------------

    def request_headers(
        self,
        *,
        script: str | None = None,
        headers: Mapping[str, str] | None = None,
        api_key: str | None = None,
        authenticate: bool = True,
    ) -> dict[str, str]:
        """The headers a proxy request goes out with, credential included."""
        built = {"content-type": "application/json"}
        if authenticate:
            built["authorization"] = f"Bearer {api_key if api_key is not None else self.api_key}"
        if script is not None:
            built["x-headroom-mock-script"] = script
        built.update(headers or {})
        return built

    async def post(
        self,
        path: str,
        body: Any,
        *,
        script: str | None = None,
        headers: Mapping[str, str] | None = None,
        api_key: str | None = None,
        authenticate: bool = True,
    ) -> httpx.Response:
        """POST to the gateway, optionally naming a mock script.

        ``body`` may be a dict (encoded as JSON) or raw bytes. Tests that assert
        byte-level fidelity pass bytes, so the exact payload is the test's to control
        rather than the HTTP client's.

        The harness's own virtual key is presented unless ``authenticate=False``;
        ``api_key`` swaps in a different one, which is how the auth matrix presents a
        revoked, unknown, or malformed credential.
        """
        request_headers = self.request_headers(
            script=script, headers=headers, api_key=api_key, authenticate=authenticate
        )
        if isinstance(body, bytes):
            return await self.client.post(path, content=body, headers=request_headers)
        return await self.client.post(path, json=body, headers=request_headers)

    def start(
        self,
        path: str,
        body: Any,
        *,
        script: str | None = None,
        headers: Mapping[str, str] | None = None,
        api_key: str | None = None,
        authenticate: bool = True,
    ) -> ASGIRun:
        """Drive a request over raw ASGI — the non-buffering proof's entry point."""
        return start_request(
            self.app,
            path=path,
            body=body,
            headers=self.request_headers(
                script=script, headers=headers, api_key=api_key, authenticate=authenticate
            ),
        )

    # --- admin requests -----------------------------------------------------------

    async def admin(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        token: str | None = None,
        authenticate: bool = True,
        params: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        """Call ``/admin/*`` with the root token (or deliberately without it)."""
        headers: dict[str, str] = {}
        if authenticate:
            headers["authorization"] = f"Bearer {token if token is not None else self.admin_token}"
        return await self.client.request(
            method, path, json=json, headers=headers, params=dict(params or {})
        )

    def last_context(self) -> RequestContext:
        """The ``RequestContext`` of the most recent request."""
        assert self.recorder.contexts, "no request has been made yet"
        ctx: RequestContext = self.recorder.contexts[-1]
        return ctx

    def last_upstream_request(self) -> UpstreamRequest:
        """What the provider was actually handed — the request half of fidelity."""
        assert self.provider.received, "the provider was never called"
        return self.provider.received[-1]

    # --- the ledger ---------------------------------------------------------------

    async def ledger_row(self, request_id: str | None = None) -> LedgerEntry:
        """The ledger row for a request, once the writer has actually written it.

        Drains the queue first — the write is fire-and-forget by design, so reading
        the store without waiting would be a race that passes on a fast machine and
        fails in CI. Asserts the row exists, so a test that expects one and gets none
        fails on the missing row rather than on an ``AttributeError`` three lines later.
        """
        await self.writer.drain()
        resolved = request_id if request_id is not None else self.last_context().request_id
        row = await self.ledger.get(resolved)
        assert row is not None, f"no ledger row was written for request {resolved}"
        return row

    async def ledger_row_or_none(self, request_id: str | None = None) -> LedgerEntry | None:
        """The same, for tests asserting that a request is deliberately *not* metered."""
        await self.writer.drain()
        resolved = request_id if request_id is not None else self.last_context().request_id
        return await self.ledger.get(resolved)

    # --- the budget ---------------------------------------------------------------

    async def set_budget(
        self, usd: str | Decimal, *, window: str = "monthly", when: datetime | None = None
    ) -> Budget:
        """Give the harness's tenant a cap. Returns it, so a test can assert on it."""
        return await self.budgets.store.set_budget(
            self.scope,
            usd=Decimal(usd) if isinstance(usd, str) else usd,
            window=window,
            when=when if when is not None else datetime.now(UTC),
        )

    async def budget(self, *, when: datetime | None = None) -> Budget:
        """The tenant's budget as it stands, with settlements already drained.

        Draining first for the same reason :meth:`ledger_row` does: a disconnect leaves
        its settlement running as a detached task, and a test that read the counters
        without waiting would be racing it.
        """
        await self.budgets.drain()
        found = await self.budgets.store.get(
            self.scope, when=when if when is not None else datetime.now(UTC)
        )
        assert found is not None, "no budget is configured for the harness tenant"
        return found

    @property
    def scope(self) -> BudgetScope:
        """The budget scope of the harness's tenant."""
        return BudgetScope.tenant(self.tenant.id)
