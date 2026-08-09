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

import time
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx

from headroom.api.gateway import Gateway
from headroom.api.main import app as headroom_app
from headroom.cache.gate import ResponseCache
from headroom.core.budgets import Budget, BudgetScope, BudgetStore
from headroom.core.cache import CacheSettings, ResponseCacheStore
from headroom.core.config import GatewayConfig, ProviderSpec, RouteSpec
from headroom.core.context import RequestContext
from headroom.core.ledger import LedgerEntry, LedgerStore
from headroom.core.limits import SCOPE_TENANT, BucketKey, BucketState, RateLimit, RateLimitStore
from headroom.core.storage import Tenant, TenantStore, VirtualKey
from headroom.db.memory import (
    InMemoryBudgetStore,
    InMemoryLedgerStore,
    InMemoryRateLimitStore,
    InMemoryResponseCacheStore,
    InMemoryTenantStore,
)
from headroom.metering.meter import Meter
from headroom.metering.prices import load_price_book
from headroom.metering.writer import LedgerWriter
from headroom.policy.auth import Authenticator
from headroom.policy.budgets import BudgetGate
from headroom.policy.failover import BackoffPolicy, Failover
from headroom.policy.health import HealthPolicy, HealthTracker
from headroom.policy.keys import display_prefix, hash_key, mint_key
from headroom.policy.limits import RateLimiter
from headroom.providers.base import UpstreamRequest
from headroom.providers.mock import MockProvider, MockScriptBook
from headroom.providers.registry import ProviderRegistry

from .asgi import ASGIRun, ContextRecorder, start_request
from .corpus import CorpusEmbedder

__all__ = [
    "ADMIN_TOKEN",
    "FakeClock",
    "FakeSleeper",
    "GatewayHarness",
    "gateway_harness",
    "mock_only_config",
]


@dataclass(slots=True)
class FakeSleeper:
    """An ``asyncio.sleep`` that records instead of waiting.

    The failover executor's backoff is the one part of Phase 6 that is *about* the
    passage of time, and a test that verified it by passing time would be slow, flaky,
    and the first thing deleted when somebody optimises the suite. So the executor takes
    its ``sleep`` as a parameter and CI gets this: every requested delay in order,
    asserted exactly, in microseconds of wall clock.
    """

    delays: list[float] = field(default_factory=list)

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)

    @property
    def total_s(self) -> float:
        return sum(self.delays)


@dataclass(slots=True)
class FakeClock:
    """A monotonic clock a test advances by hand — the breaker's cooldown, controlled."""

    now: float = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


#: The root admin token the test gateway is built with. A literal in a test file, not
#: a secret: nothing it guards outlives the process (BUILD_PLAN §0.2 invariant 3 is
#: about real credentials in the repo, and this guards an in-memory store).
ADMIN_TOKEN = "test-root-admin-token"


def mock_only_config(
    chain: Sequence[str] = ("mock",), *, max_attempts: int | None = None
) -> GatewayConfig:
    """A gateway configuration with mock providers and nothing that can spend money.

    Routes are ``mock-`` prefixed rather than catch-all, so "this model is not routed"
    stays a case a test can deliberately ask for.

    ``chain`` is Phase 6's addition and its default is the whole point: one provider, no
    fallbacks, no ``max_attempts`` — which is one attempt, no backoff, and no breaker on
    the path, i.e. exactly what every test written before this phase was running against.
    A failover test asks for ``("mock_a", "mock_b")`` and gets a two-link chain in both
    dialects.
    """
    primary, *fallbacks = chain

    def rule() -> RouteSpec:
        return RouteSpec(
            prefix="mock-",
            provider=primary,
            fallbacks=list(fallbacks),
            max_attempts=max_attempts,
        )

    return GatewayConfig(
        providers={name: ProviderSpec(kind="mock") for name in chain},
        routes={"anthropic": [rule()], "openai": [rule()]},
    )


@asynccontextmanager
async def gateway_harness(
    *,
    budgets: BudgetStore | None = None,
    limits: RateLimitStore | None = None,
    cache: ResponseCacheStore | None = None,
    tenant: str = "acme",
    chain: Sequence[str] = ("mock",),
    max_attempts: int | None = None,
    backoff: BackoffPolicy | None = None,
    health: HealthPolicy | None = None,
    clock: Callable[[], float] | None = None,
) -> AsyncIterator[GatewayHarness]:
    """Build a complete keyless gateway and serve it over ASGI.

    The body of ``tests/conftest.py``'s ``gateway`` fixture, lifted here so a test that
    needs a *different* backing store can build its own — which
    ``tests/test_budget_stampede.py`` does, because the headline test has to run against
    DynamoDB Local rather than against a dict that cannot interleave.

    Everything else is exactly what every other test gets: one MockProvider, one tenant,
    one unrestricted key, the committed price book, and its own control plane.

    Phase 6 adds four keywords, all defaulted so that nothing changes for anyone who does
    not pass them. ``chain`` builds a multi-provider failover route (each member is its
    own ``MockProvider`` sharing one script book — a script registered as
    ``"name@mock_b"`` binds to that instance alone). ``backoff``/``health``/``clock``
    replace the executor's timing so a chaos test measures sleeps instead of taking them:
    :class:`FakeSleeper` records what was requested and returns immediately, and the
    breaker's cooldown is advanced by hand rather than waited out.
    """
    book = MockScriptBook()
    providers = {name: MockProvider(name, book) for name in chain}
    registry = ProviderRegistry()
    for provider in providers.values():
        registry.add(provider)
    config = mock_only_config(chain, max_attempts=max_attempts)
    sleeper = FakeSleeper()
    tracker = HealthTracker(health, clock=clock if clock is not None else time.monotonic)
    for configured in registry:
        tracker.track(configured.name, configured.kind)
    failover = Failover(
        registry=registry,
        health=tracker,
        backoff=backoff if backoff is not None else BackoffPolicy(),
        sleep=sleeper,
        # A fixed full-jitter draw: every recorded delay is the ceiling, so a test can
        # assert an exact number and still be asserting about the real formula. The
        # randomness itself is tested where it belongs, against `BackoffPolicy`.
        jitter=lambda: 1.0,
    )

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
    limiter = RateLimiter(store=limits if limits is not None else InMemoryRateLimitStore())
    # The corpus embedder, not the real one: real ``bge-small-en-v1.5`` vectors for the
    # committed fixture and deterministic hashed ones for everything a test invents, with
    # no torch anywhere (``tests/support/corpus.py``). Injected rather than resolved from
    # the environment, so no test can be made to depend on which extras are installed.
    responses = ResponseCache(
        store=cache if cache is not None else InMemoryResponseCacheStore(),
        embedder=CorpusEmbedder(),
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
        limits=limiter,
        cache=responses,
        health=tracker,
        failover=failover,
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
                provider=providers[chain[0]],
                providers=providers,
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
                limits=limiter,
                cache=responses,
                health=tracker,
                failover=failover,
                sleeper=sleeper,
            )
    finally:
        await instance.aclose()
        headroom_app.state.gateway = previous


@dataclass
class GatewayHarness:
    """A running gateway plus the handles a test needs to interrogate it."""

    app: Any
    book: MockScriptBook
    #: The chain's primary. Named ``provider`` since Phase 1 and still the one a
    #: single-provider test means when it says "the provider".
    provider: MockProvider
    #: Every provider in the chain, by name. One entry unless a test asked for more.
    providers: dict[str, MockProvider]
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
    #: Phase 4b. The rate limiter and its bucket store, so a test can set a limit and
    #: read a bucket back. Note what is *absent*: any way to drain it. A bucket
    #: consumption is synchronous and never settles, so there is nothing in flight to
    #: wait for — the asymmetry with the budget gate above is the design, not an omission.
    limits: RateLimiter
    #: Phase 5. The response cache and the store behind it, so a test can switch caching
    #: on for the harness tenant, read entries back, and — via the embedder's own call
    #: counter — assert that a disabled tenant embedded nothing at all.
    cache: ResponseCache
    #: Phase 6. Provider health and the executor that reads it, so a test can trip a
    #: breaker deliberately and assert what the chain did about it.
    health: HealthTracker
    failover: Failover
    #: Every backoff the executor *asked* for, in order. The whole point of injecting it:
    #: a jittered exponential backoff verified by actually sleeping is a test somebody
    #: deletes the week the suite gets slow.
    sleeper: FakeSleeper
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

    # --- rate limits --------------------------------------------------------------

    async def set_limits(
        self,
        *,
        requests_per_min: int | None = None,
        tokens_per_min: int | None = None,
        scope: str = SCOPE_TENANT,
    ) -> None:
        """Give the harness's tenant (or its key) a limit, effective immediately.

        The cache invalidation is not decoration: the limits ride the ``Principal``
        (H-037), so a test that set a limit without dropping the cached principal would
        be asserting against the previous request's configuration.
        """
        limits = RateLimit(requests_per_min=requests_per_min, tokens_per_min=tokens_per_min)
        if scope == SCOPE_TENANT:
            await self.store.set_tenant_limits(self.tenant.id, limits)
            self.authenticator.cache.invalidate_tenant(self.tenant.id)
            return
        await self.store.set_key_limits(self.key.id, limits)
        self.authenticator.cache.invalidate_key(self.key.id)

    def bucket_key(self, dimension: str, *, scope: str = SCOPE_TENANT) -> BucketKey:
        """The bucket one dimension of one scope consumes from."""
        scope_id = self.tenant.id if scope == SCOPE_TENANT else self.key.id
        return BucketKey(scope_kind=scope, scope_id=scope_id, dimension=dimension)

    async def bucket(
        self,
        dimension: str,
        *,
        limit_per_min: int,
        scope: str = SCOPE_TENANT,
        when: datetime | None = None,
    ) -> BucketState:
        """A bucket as it stands, for asserting on what a burst left behind."""
        return await self.limits.store.state(
            self.bucket_key(dimension, scope=scope),
            limit_per_min=limit_per_min,
            when=when if when is not None else datetime.now(UTC),
        )

    # --- the response cache -----------------------------------------------------------

    async def set_cache(
        self,
        mode: str,
        *,
        ttl_s: int | None = None,
        similarity_threshold: float | None = None,
        tenant_id: str | None = None,
    ) -> None:
        """Switch caching on (or off) for a tenant, effective immediately.

        The invalidation is not decoration: the policy rides the ``Principal`` (H-037's
        placement), so a test that set a mode without dropping the cached principal would
        be asserting against the previous request's configuration — the same trap
        :meth:`set_limits` documents.
        """
        target = tenant_id if tenant_id is not None else self.tenant.id
        await self.store.set_cache_settings(
            target,
            CacheSettings(mode=mode, ttl_s=ttl_s, similarity_threshold=similarity_threshold),
        )
        self.authenticator.cache.invalidate_tenant(target)

    async def cache_entries(self, tenant_id: str | None = None) -> int:
        """How many entries a tenant owns. The poison attempts' assertion, in one call."""
        stats = await self.cache.store.stats(tenant_id if tenant_id is not None else self.tenant.id)
        return stats.entries
