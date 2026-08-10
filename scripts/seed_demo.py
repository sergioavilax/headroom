#!/usr/bin/env python
"""Fill a local stack with traffic worth looking at, through the public API only.

``make seed``. Phase 7's gate says the dashboard has to render *a seeded compose
environment truthfully*, and a reviewer cannot check "truthfully" against an empty
database. This script is the seed: four tenants with genuinely different profiles, and
enough traffic through them that every view has something real to show — spend, budgets
near the line, buckets being emptied, cache hits with their savings, a failover, and a
handful of honest failures.

**It talks to the same two APIs the dashboard does and nothing else.** ``/admin/*`` with
the root token to configure, ``/v1/*`` with a minted virtual key to generate traffic. No
SQL, no direct store access, no back door — so every number it produces is a number the
gateway really computed, and a bug in the metering path shows up here rather than being
papered over by a fixture. That is the same rule the console itself holds to (H-054).

**It spends nothing.** Every request goes to ``mock-model-1``, which
``config/routing.yaml`` routes to the MockProvider: no key, no network, no GPU. The
failovers are injected with ``x-headroom-mock-script: fault-529@mock``, which breaks the
chain's primary and leaves ``mock_fallback`` answering — the keyless rehearsal of the
two-GPU demo.

Re-running is safe. Tenants are reused by name; the seed's own key is revoked and
re-minted each time, because a key's plaintext exists exactly once (H-017) and this
script has to hold one. The revoked keys pile up in the Tenants & Keys view on purpose:
that is what a control plane looks like after a few weeks.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import httpx

#: The compose gateway's host port (H-006 moved it off 8000 so Backline can coexist).
DEFAULT_GATEWAY = "http://localhost:8080"

#: Everything here runs on the MockProvider, which `config/routing.yaml` reaches with
#: this prefix in both dialects — and chains to `mock_fallback`, which is what makes the
#: keyless failover rehearsal possible.
MODEL = "mock-model-1"

#: The canonical mock reply prices to exactly $0.0000115 (11 in, 7 out at the flat mock
#: rates, H-023). Budgets below are stated as multiples of it so "spent 82% of the cap"
#: is arithmetic a reader can check rather than a number that came from nowhere.
UNIT_COST = Decimal("0.0000115")


def usd(units: int) -> str:
    """A budget expressed in whole mock requests, as the quoted string the API wants."""
    return format(UNIT_COST * units, "f")


@dataclass(slots=True)
class Profile:
    """One tenant, and what makes it interesting to look at."""

    name: str
    blurb: str
    #: ``disabled`` | ``exact``. Never ``semantic`` — the compose image ships without
    #: the ``embed`` extra by design (Phase 5), so asking for it here would 503 on a
    #: fresh clone. `HEADROOM_EMBEDDER=hashing` is the local route to a semantic demo.
    cache_mode: str = "disabled"
    #: Whole mock requests' worth of monthly budget. ``None`` leaves the tenant uncapped.
    budget_units: int | None = None
    requests_per_min: int | None = None
    tokens_per_min: int | None = None
    #: Models this tenant's key may reach. Empty is unrestricted.
    allowed_models: list[str] = field(default_factory=list)

    # --- traffic ------------------------------------------------------------------
    plain: int = 0
    streamed: int = 0
    #: Distinct questions asked twice each — a miss then a hit, so a cache-enabled
    #: tenant shows a real hit rate and a real saving rather than a seeded number.
    repeats: int = 0
    #: Requests that declare tools, which are ineligible for the cache in any mode
    #: (H-041). The reason Backline's own traffic caches nothing, made visible.
    tool_calls: int = 0
    #: Requests whose primary 529s, so the fallback serves and the row carries a hop.
    failovers: int = 0
    #: Upstream 400s, mid-stream cuts, and timeouts — the rows an operator goes looking
    #: for, and the ones that make "errored_requests" a number rather than a column.
    upstream_errors: int = 0
    cuts: int = 0
    timeouts: int = 0
    #: Requests fired past a rate limit or a budget, to leave real 429s and 402s behind.
    over_limit: int = 0
    over_budget: int = 0


PROFILES = [
    # Budgets are sized against the traffic below them, so the picture is deliberate
    # rather than emergent: `backline` lands comfortably inside its cap, `atlas-research`
    # lands just under and then gets refused. One upstream call costs 1 unit; one
    # *reservation* costs about 8, because the estimate bounds the worst case before the
    # answer exists (H-034) — which is why a tenant with 3 units left is refused a
    # request that would have cost 1. That is the gate doing its job, and it is worth
    # having on screen.
    Profile(
        name="backline",
        blurb="the sibling project's agents — tool-heavy, so nothing caches",
        cache_mode="exact",
        budget_units=40,  # ~24 spent: a healthy channel strip with real headroom
        requests_per_min=120,
        plain=9,
        streamed=6,
        tool_calls=7,
        failovers=2,
        upstream_errors=1,
    ),
    Profile(
        name="atlas-research",
        blurb="a read-heavy analytics workload — repeats itself, so the cache pays",
        cache_mode="exact",
        # 15 upstream calls at 1 unit each, and one *reservation* is about 8 — so 23 is
        # the cap that admits all of them and refuses the next. At 18 the gate starts
        # refusing on the tenth, which is correct behaviour and cuts the cache demo in
        # half; the arithmetic is worth doing once here rather than being surprised by.
        budget_units=23,
        allowed_models=["mock-*"],
        plain=4,
        repeats=9,
        streamed=2,
        over_budget=3,
    ),
    Profile(
        name="nightshift",
        blurb="a batch job that does not know when to stop — rate limited",
        requests_per_min=3,
        plain=3,
        over_limit=6,
    ),
    Profile(
        name="probe",
        blurb="synthetic monitoring — small, uncapped, and unlucky",
        plain=4,
        streamed=1,
        failovers=3,
        cuts=2,
        timeouts=1,
        upstream_errors=2,
    ),
]

#: Distinct questions for the repeat traffic, so a cache-enabled tenant's hit rate is
#: produced by asking the same thing twice rather than declared.
QUESTIONS = [
    "What was Radiohead's total streaming revenue in 2019?",
    "How many monthly listeners did Coldplay have in 2021?",
    "Which Björk album sold best in Japan?",
    "What share of Arcade Fire's 2020 revenue came from vinyl?",
    "How did The National's catalogue perform in 2024-Q3?",
    "What was Sufjan Stevens' average streaming rate in 2018?",
    "Which Grimes single charted highest in Germany?",
    "How much did Bon Iver earn from sync licensing in 2022?",
    "What was Aphex Twin's back-catalogue growth in 2023?",
]


class Seeder:
    """The admin and proxy calls, in the order a demo wants them."""

    def __init__(self, client: httpx.AsyncClient, token: str, *, spread_s: float) -> None:
        self._client = client
        self._admin = {"authorization": f"Bearer {token}"}
        self._spread_s = spread_s
        self._pace = 0.0
        self.requests = 0

    # --- admin ---------------------------------------------------------------------

    async def admin(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = await self._client.request(method, path, headers=self._admin, **kwargs)
        if response.status_code >= 400:
            raise SystemExit(
                f"{method} {path} -> {response.status_code} {response.text.strip()}\n"
                "The admin API refused. Is HEADROOM_ADMIN_TOKEN the one the gateway was "
                "started with? (An unset token on the gateway answers 503 — H-019.)"
            )
        return response

    async def tenant(self, name: str) -> str:
        """The tenant's id, creating it only if this is a fresh database."""
        existing = (await self.admin("GET", "/admin/tenants")).json()
        for found in existing:
            if found["name"] == name:
                if not found["active"]:
                    await self.admin(
                        "PATCH", f"/admin/tenants/{found['id']}", json={"active": True}
                    )
                return str(found["id"])
        return str((await self.admin("POST", "/admin/tenants", json={"name": name})).json()["id"])

    async def key(self, tenant_id: str, *, allowed_models: list[str]) -> str:
        """A fresh key, after revoking whatever the last run left behind.

        A key's plaintext exists in exactly one response and is never recoverable
        (H-017), so a script that needs to *use* a key has to mint one. Revoking the
        previous ``seed-demo`` key first keeps the list readable and leaves a real
        revoked row in the Tenants & Keys view, which is what one looks like.
        """
        for key in (await self.admin("GET", "/admin/keys", params={"tenant_id": tenant_id})).json():
            if key["name"] == "seed-demo" and key["status"] == "active":
                await self.admin("DELETE", f"/admin/keys/{key['id']}")
        created = await self.admin(
            "POST",
            "/admin/keys",
            json={"tenant_id": tenant_id, "name": "seed-demo", "allowed_models": allowed_models},
        )
        return str(created.json()["key"])

    async def configure(self, profile: Profile, tenant_id: str) -> None:
        """Reset, then set — so the second run of this script looks like the first.

        Every one of these three ``DELETE``s is the incident-response route the admin API
        already ships, doing exactly what it says: purge the cache, empty the buckets,
        drop the counters. Without them a re-run would start with last run's spend
        against the same cap and refuse everything, and the tenant that exists to show a
        cache hit rate would show a hit rate of 100% because its entries were still
        there. The demo's *shape* is the thing being seeded, and a shape that depends on
        how many times somebody has run the seeder is not one.
        """
        await self.admin("DELETE", f"/admin/cache/{tenant_id}")  # disables *and* purges
        await self.admin("PUT", f"/admin/cache/{tenant_id}", json={"mode": profile.cache_mode})

        await self.admin("DELETE", f"/admin/limits/tenant/{tenant_id}")  # clears + empties
        # PUT replaces, so an unlimited dimension is expressed by omitting it (H-037).
        await self.admin(
            "PUT",
            f"/admin/limits/tenant/{tenant_id}",
            json={
                "requests_per_min": profile.requests_per_min,
                "tokens_per_min": profile.tokens_per_min,
            },
        )

        # 404 when there is no cap, which is the ordinary case on a fresh database.
        await self._client.delete(f"/admin/budgets/{tenant_id}", headers=self._admin)
        if profile.budget_units is not None:
            await self.admin(
                "PUT",
                f"/admin/budgets/{tenant_id}",
                json={"usd": usd(profile.budget_units), "window": "monthly"},
            )

    # --- traffic --------------------------------------------------------------------

    async def proxy(
        self,
        key: str,
        *,
        text: str = "How did the catalogue perform last quarter?",
        stream: bool = False,
        script: str | None = None,
        tools: bool = False,
        dialect: str = "anthropic",
    ) -> int:
        """One request through ``/v1/*``, exactly as a tenant's own client would send it."""
        headers = {"authorization": f"Bearer {key}", "content-type": "application/json"}
        if script is not None:
            headers["x-headroom-mock-script"] = script
        if dialect == "anthropic":
            path = "/v1/messages"
            body: dict[str, Any] = {
                "model": MODEL,
                "max_tokens": 64,
                "messages": [{"role": "user", "content": text}],
            }
        else:
            path = "/v1/chat/completions"
            body = {
                "model": MODEL,
                "max_tokens": 64,
                "messages": [{"role": "user", "content": text}],
                "stream_options": {"include_usage": True},
            }
        if stream:
            body["stream"] = True
        if tools:
            body["tools"] = [
                {
                    "name": "get_track_metrics",
                    "description": "Streaming metrics for an artist.",
                    "input_schema": {
                        "type": "object",
                        "properties": {"artist": {"type": "string"}},
                        "required": ["artist"],
                    },
                }
            ]
        try:
            response = await self._client.post(path, json=body, headers=headers, timeout=30.0)
            status = response.status_code
        except httpx.ReadTimeout:
            # `fault-timeout` makes the *upstream* hang; the gateway answers 504 well
            # inside the client timeout above, so this is belt and braces.
            status = 504
        self.requests += 1
        await self._breathe()
        return status

    async def _breathe(self) -> None:
        """Pace the run across ``--spread-s`` so the charts have more than one bucket.

        Zero by default: a seed that takes three minutes is a seed nobody waits for. The
        rows land at the moment they are made either way — nothing here back-dates a
        ledger row, because nothing *can*, and a chart drawn from invented timestamps
        would be the one thing this script exists not to produce.
        """
        if self._pace:
            await asyncio.sleep(self._pace)

    def pace_for(self, total: int) -> None:
        self._pace = (self._spread_s / total) if self._spread_s and total else 0.0

    async def run_profile(self, profile: Profile, key: str) -> None:
        for index in range(profile.plain):
            await self.proxy(key, text=f"Quarterly rollup, batch {index}")
        for index in range(profile.streamed):
            await self.proxy(
                key,
                text=f"Stream the rollup, batch {index}",
                stream=True,
                dialect="openai" if index % 2 else "anthropic",
            )
        for question in QUESTIONS[: profile.repeats]:
            await self.proxy(key, text=question)  # miss
            await self.proxy(key, text=question)  # hit, if this tenant caches
        for index in range(profile.tool_calls):
            await self.proxy(key, text=f"Look up artist {index}", tools=True)
        # Every fault probe asks a distinct question, for the reason spelled out at the
        # over-budget loop below: on a caching tenant a repeat is a *hit*, which never
        # reaches a provider — so the second injected fault would quietly not happen.
        for index in range(profile.failovers):
            # The primary 529s and the chain's second member answers: a 200 for the
            # caller, `failover_hops: 1` on the row. The kill demo, keylessly.
            await self.proxy(key, text=f"Failover probe {index}", script="fault-529@mock")
        for index in range(profile.upstream_errors):
            await self.proxy(key, text=f"Refused-upstream probe {index}", script="fault-400")
        for index in range(profile.cuts):
            await self.proxy(key, text=f"Cut probe {index}", stream=True, script="fault-cut@mock")
        for index in range(profile.timeouts):
            await self.proxy(key, text=f"Timeout probe {index}", script="fault-timeout")
        for index in range(profile.over_limit + profile.over_budget):
            # A distinct question each time, deliberately. Repeating one would make the
            # second and third *cache hits* on a caching tenant — and a hit takes no
            # budget reservation at all (H-046), so the requests meant to demonstrate a
            # 402 would sail through instead. Correct behaviour, wrong demo.
            await self.proxy(key, text=f"Hammering past the limit, attempt {index}")


async def wait_for_gateway(client: httpx.AsyncClient, *, attempts: int = 30) -> None:
    for _ in range(attempts):
        try:
            if (await client.get("/healthz", timeout=2.0)).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        await asyncio.sleep(1.0)
    raise SystemExit(
        f"the gateway at {client.base_url} never answered /healthz — is `make up` done?"
    )


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--gateway", default=os.environ.get("HEADROOM_GATEWAY_URL", DEFAULT_GATEWAY)
    )
    parser.add_argument(
        "--spread-s",
        type=float,
        default=0.0,
        help="pace the traffic across this many seconds so the charts have several "
        "buckets (default 0: as fast as the gateway will take it)",
    )
    args = parser.parse_args()

    token = os.environ.get("HEADROOM_ADMIN_TOKEN", "").strip()
    if not token:
        print(
            "HEADROOM_ADMIN_TOKEN is not set.\n"
            "It is the same token the gateway was started with; export it with a leading "
            "space so it stays out of your shell history (BUILD_PLAN §0.2 invariant 3).",
            file=sys.stderr,
        )
        return 2

    started = time.monotonic()
    async with httpx.AsyncClient(base_url=args.gateway, timeout=30.0) as client:
        await wait_for_gateway(client)
        seeder = Seeder(client, token, spread_s=args.spread_s)
        planned = sum(
            profile.plain
            + profile.streamed
            + profile.repeats * 2
            + profile.tool_calls
            + profile.failovers
            + profile.upstream_errors
            + profile.cuts
            + profile.timeouts
            + profile.over_limit
            + profile.over_budget
            for profile in PROFILES
        )
        seeder.pace_for(planned)

        print(f"seeding {args.gateway} — {len(PROFILES)} tenants, ~{planned} requests\n")
        for profile in PROFILES:
            tenant_id = await seeder.tenant(profile.name)
            key = await seeder.key(tenant_id, allowed_models=profile.allowed_models)
            await seeder.configure(profile, tenant_id)
            print(f"  {profile.name:<16} {profile.blurb}")
            await seeder.run_profile(profile, key)

        totals = (await seeder.admin("GET", "/admin/usage/totals")).json()

    print(f"\n{seeder.requests} requests in {time.monotonic() - started:.1f}s\n")
    print(f"  {'tenant':<38} {'reqs':>5} {'spend':>16} {'hits':>5} {'saved':>16} {'errs':>5}")
    for total in sorted(totals, key=lambda row: -Decimal(row["usd_cost"])):
        hits = total["cache_hits_exact"] + total["cache_hits_semantic"]
        print(
            f"  {total['tenant_id']:<38} {total['requests']:>5} "
            f"{total['usd_cost']:>16} {hits:>5} {total['cache_avoided_usd']:>16} "
            f"{total['errored_requests']:>5}"
        )
    print("\nThe dashboard reads exactly these numbers: http://localhost:3001")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
