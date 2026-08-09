"""What the two live smokes need that the mock harness cannot give them: an identity.

Phase 2 made every ``/v1/*`` request require a virtual key. The keyless suite was moved
onto the authenticated path in that PR (``GatewayHarness.post`` presents one by
default); these two tests were not, and nobody found out for a phase and a half because
they are excluded from every CI run. This module is the fix, and it is deliberately more
than a header: a live smoke now **provisions its own tenant and key in the real control
plane**, so the operator's only setup is still ``make up`` plus the provider env var the
test already required.

Three properties are load-bearing.

**The tenant is reused; the key never is.** ``live-smoke`` is created once and found
again on every later run — so ``/admin/usage?tenant_id=…`` is a stable place to look —
while the key is minted fresh each time, because a plaintext key exists exactly once, in
the response that created it (H-017), and this store never held it. The key is revoked
on the way out: it has served its one request, and a credential nobody can produce is
tidier dead than alive.

**The stores are the real ones.** ``build_gateway`` is given Postgres, not the in-memory
pair the keyless fixture uses, because the point of a live smoke's ledger row is that it
is still there when the operator curls ``/admin/usage`` afterwards. That makes the
compose database a prerequisite, handled the way H-012 handles every other one: skip
when the endpoint was merely inferred and nothing is listening, fail when someone stated
it was there.

**The ids reach the operator's terminal.** ``announce`` writes through pytest's capture,
because a passing test's stdout is swallowed without ``-s`` and the request id is the
whole reason the run was worth doing.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
import pytest

from headroom.api.gateway import Gateway, build_gateway
from headroom.api.main import app as headroom_app
from headroom.api.middleware import REQUEST_ID_HEADER
from headroom.core.ledger import LedgerEntry, format_usd
from headroom.core.storage import Tenant, TenantNameConflict, TenantStore, VirtualKey
from headroom.db.ledger import PostgresLedgerStore
from headroom.db.migrate import run_migrations
from headroom.db.tenants import PostgresTenantStore
from headroom.policy.keys import display_prefix, hash_key, mint_key

from .services import COMPOSE_DATABASE_URL, resolve_endpoint

__all__ = [
    "LIVE_TENANT_NAME",
    "LiveGateway",
    "LiveIdentity",
    "announce",
    "live_gateway",
    "provision",
    "report",
]

#: The tenant every live smoke bills to. One name, forever, so the operator can filter
#: the ledger by it without first hunting for an id a test invented.
LIVE_TENANT_NAME = "live-smoke"

#: Where the ledger row has to land for the operator to read it back afterwards.
DATABASE = resolve_endpoint("DATABASE_URL", COMPOSE_DATABASE_URL)


# --- identity ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LiveIdentity:
    """A tenant, a key, and the one copy of that key's plaintext there will ever be."""

    tenant: Tenant
    key: VirtualKey
    api_key: str

    @property
    def headers(self) -> dict[str, str]:
        """What a request has to carry since Phase 2. This is the bug this file fixes."""
        return {
            "content-type": "application/json",
            "authorization": f"Bearer {self.api_key}",
        }


async def ensure_tenant(store: TenantStore, name: str = LIVE_TENANT_NAME) -> Tenant:
    """The smoke tenant: created on the first run, found — and reactivated — after.

    Reactivation is not defensive padding. Tenants are deactivated rather than deleted
    (H-022), so an operator who deactivated this one while tidying up would otherwise
    get a 401 whose message is about a key, on a run that provisioned that key seconds
    earlier.
    """
    with contextlib.suppress(TenantNameConflict):
        return await store.create_tenant(name)
    for tenant in await store.list_tenants():
        if tenant.name != name:
            continue
        if tenant.active:
            return tenant
        reactivated = await store.update_tenant(tenant.id, active=True)
        assert reactivated is not None  # it was listed one statement ago
        return reactivated
    raise AssertionError(f"tenant {name!r} conflicted on create and then could not be found")


async def provision(
    store: TenantStore, *, key_name: str, tenant_name: str = LIVE_TENANT_NAME
) -> LiveIdentity:
    """Create (or reuse) the smoke tenant and mint it a fresh unrestricted key."""
    tenant = await ensure_tenant(store, tenant_name)
    plaintext = mint_key()
    key = await store.create_key(
        tenant_id=tenant.id,
        name=key_name,
        key_hash=hash_key(plaintext),
        key_prefix=display_prefix(plaintext),
    )
    assert key is not None, f"the tenant {tenant.id} was resolved one statement ago"
    return LiveIdentity(tenant=tenant, key=key, api_key=plaintext)


# --- the gateway under a live smoke ----------------------------------------------------


@dataclass(slots=True)
class LiveGateway:
    """The real gateway, an authenticated client for it, and the identity it bills."""

    client: httpx.AsyncClient
    gateway: Gateway
    identity: LiveIdentity

    async def post(self, path: str, body: object) -> httpx.Response:
        """POST to the gateway carrying this run's virtual key."""
        return await self.client.post(path, json=body, headers=self.identity.headers)

    def request_id(self, response: httpx.Response) -> str:
        """The id the middleware stamped — the operator's handle on this request."""
        request_id: str | None = response.headers.get(REQUEST_ID_HEADER.decode("ascii"))
        assert request_id, "no x-headroom-request-id on the response; is the middleware installed?"
        return request_id

    async def ledger_row(self, response: httpx.Response) -> LedgerEntry | None:
        """The row this request wrote, once the writer has actually written it.

        Drains first: the write is fire-and-forget by design (H-027), so reading the
        store without waiting would be a race — and here the loser is a live run the
        operator paid for.
        """
        writer = self.gateway.meter.writer
        if writer is not None:
            await writer.drain()
        return await self.gateway.ledger.get(self.request_id(response))


def require_database() -> str:
    """The control plane's URL, or the H-012 verdict on why this smoke cannot run."""
    if DATABASE.skip_reason is not None:
        pytest.skip(f"{DATABASE.skip_reason} — a live smoke provisions a tenant and writes a row")
    if not DATABASE.reachable:
        pytest.fail(f"DATABASE_URL was set to {DATABASE.url} and nothing is listening there")
    return DATABASE.url


@contextlib.asynccontextmanager
async def live_gateway(key_name: str) -> AsyncIterator[LiveGateway]:
    """A real gateway on the real control plane, with a freshly provisioned key.

    Real providers, the committed routing table, the committed price book — the only
    thing invented here is the identity, and it is invented through the same
    ``TenantStore`` the admin API writes through.
    """
    url = require_database()
    # Idempotent, and it removes the one setup step the operator would otherwise have
    # to remember on a fresh volume: a live smoke that failed on a missing table would
    # look like a gateway bug, having already spent the money.
    await run_migrations(url)

    store = PostgresTenantStore(url=url)
    ledger = PostgresLedgerStore(url=url)
    instance = build_gateway(store=store, ledger=ledger)
    identity = await provision(store, key_name=key_name)

    previous = getattr(headroom_app.state, "gateway", None)
    headroom_app.state.gateway = instance
    transport = httpx.ASGITransport(app=headroom_app)
    try:
        async with httpx.AsyncClient(
            transport=transport, base_url="http://gateway", timeout=60.0
        ) as client:
            yield LiveGateway(client=client, gateway=instance, identity=identity)
    finally:
        # Before `aclose`, which closes the pool this needs. Suppressed because a
        # failed cleanup must not replace the assertion that actually failed.
        with contextlib.suppress(Exception):
            await store.revoke_key(identity.key.id)
        await instance.aclose()
        headroom_app.state.gateway = previous


# --- telling the operator what just happened -------------------------------------------


def announce(capture: pytest.CaptureFixture[str], **fields: object) -> None:
    """Put one line in front of the operator, whatever pytest is capturing.

    A plain ``print`` would be swallowed: stdout from a *passing* test is never shown
    without ``-s``, and the ids below are the entire deliverable of a live run.
    """
    rendered = "  ".join(f"{name}={value}" for name, value in fields.items())
    with capture.disabled():
        print(f"\n[live] {rendered}")


def report(
    capture: pytest.CaptureFixture[str],
    *,
    live: LiveGateway,
    request_id: str,
    row: LedgerEntry | None,
) -> None:
    """Everything the operator needs to find this request in ``/admin/usage``."""
    announce(
        capture,
        request_id=request_id,
        tenant=live.identity.tenant.name,
        tenant_id=live.identity.tenant.id,
        key=f"{live.identity.key.key_prefix}…",
    )
    if row is None:
        announce(capture, ledger="NO ROW — the writer did not land one for this request")
        return
    announce(
        capture,
        model=row.model,
        provider=row.provider,
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        reasoning_tokens=row.reasoning_tokens,
        usd_cost=format_usd(row.usd_cost),
        cost_status=row.cost_status,
    )
    announce(
        capture,
        verify=(
            'curl -sS -H "Authorization: Bearer $HEADROOM_ADMIN_TOKEN" '
            f"localhost:8080/admin/usage/{request_id}"
        ),
    )
    # Time-sensitive, so it goes on the terminal rather than only into a doc: the
    # Postgres half of the tenant-store contract suite truncates the control plane, and
    # `usage_ledger` references it, so a `make test` between the smoke and the curl
    # takes this row with it.
    announce(capture, note="read it back before the next `make test` — that truncates the ledger")
