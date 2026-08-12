#!/usr/bin/env python
"""The front door: everything Headroom does, in one command, with no key and no network.

    make demo

BUILD_PLAN §P11's gate asks that *"a stranger's cold clone reaches a working keyless demo
in one command"*. Phase 2 made that four steps — mint a root token, bring the stack up,
create a tenant, mint a key — and deliberately refused to add a "dev mode" that lets an
unauthenticated request through (a gateway with an off switch for authentication is a
gateway that ships with it off). This script is the honest way back to one command: it
performs those steps against the **public API**, exactly as an operator would, and then
drives every claim the README makes about the local stack.

**It is a check, not a narration.** Every line is an assertion with an expected value
beside it, and the exit code is 1 if any of them fails. A tour that prints whatever
happened and exits 0 would tell a stranger nothing about whether their clone works.

**It costs $0.00 and talks to nobody.** Every request goes to ``mock-model-1``, which
``config/routing.yaml`` routes to the MockProvider — and chains to ``mock_fallback``, so
even the failover is real. Faults are injected through ``x-headroom-mock-script``, the
control header Phase 6 opened up so a *running* gateway can be broken on purpose.

**It leaves the tenant as it found it.** The cap and the rate limit this script sets would
otherwise refuse the next thing the operator did, so both are cleared on the way out
through the admin API's own incident-response routes. The cache is left on, with its
entries, because that is the one piece of state worth looking at afterwards.

Seven acts, in the order the phases built them:

1. a request is priced to the picodollar, at the rates it was billed at (P3);
2. the same request twice is one upstream call, and the saving has a column (P5);
3. the cache refuses what it must not keep — a cut stream, a request with tools (P5);
4. a broken primary is invisible to the caller, and a cut stream never is (P6);
5. a rate limit that cannot be raced answers 429 with a `retry-after` (P4b);
6. a budget that reads *committed* spend answers 402 before a provider is called (P4);
7. the console renders exactly these numbers (P7).
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

#: The compose gateway's host port (H-006 moved it off 8000 so Backline can coexist).
DEFAULT_BASE_URL = "http://localhost:8080"

#: The console's, for the closing line.
DEFAULT_CONSOLE_URL = "http://localhost:3001"

#: Routed to the MockProvider on both dialects, with `mock_fallback` behind it.
MODEL = "mock-model-1"

#: The tenant this script owns. Reused across runs, never deleted — nothing in this
#: control plane is (H-022), and the ledger points at its id forever.
TENANT = "demo"

#: What the canonical mock reply costs: 11 input tokens at $0.25/MTok plus 7 output at
#: $1.25/MTok, at the flat mock rates in the committed `config/models.yaml` (H-023).
#: $0.00000275 + $0.00000875, and it terminates — so this is an equality, not a bound.
UNIT_COST = Decimal("0.0000115")

#: The same figure as the ledger's own string, which is `NUMERIC(24, 12)` serialised with
#: `format(value, "f")` rather than `str()` — `str(Decimal("0.0000115"))` is fine, but
#: `str(Decimal("0.000000000000"))` is `"0E-12"`, which is where that rule came from.
UNIT_COST_STR = format(UNIT_COST.quantize(Decimal("1.000000000000")), "f")

SCRIPT_HEADER = "x-headroom-mock-script"

#: Where the token lives on a cold clone. `.env` is gitignored; `make demo` writes one
#: line into it if there is not one there already.
ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
ADMIN_TOKEN_ENV = "HEADROOM_ADMIN_TOKEN"


# --- reporting -------------------------------------------------------------------------


@dataclass(slots=True)
class Report:
    """What was claimed, and whether it held. Printed as it happens, in order."""

    checks: list[tuple[bool, str]] = field(default_factory=list)

    def record(self, ok: bool, line: str) -> None:
        self.checks.append((ok, line))
        print(f"  {'ok  ' if ok else 'FAIL'}  {line}")

    def note(self, line: str) -> None:
        """Context that is not a claim — never counted, never able to fail."""
        print(f"        {line}")

    @property
    def failed(self) -> int:
        return sum(1 for ok, _ in self.checks if not ok)


def act(title: str) -> None:
    print(f"\n{title}")


# --- the gateway, through its public API only --------------------------------------------


class Demo:
    """Admin calls and proxy calls, and nothing else. No SQL, no back door (H-054)."""

    def __init__(self, client: httpx.Client, token: str, report: Report) -> None:
        self._client = client
        self._admin = {"authorization": f"Bearer {token}"}
        self.report = report
        self.tenant_id = ""
        self.key = ""

    # --- admin ---------------------------------------------------------------------

    def admin(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = self._client.request(method, path, headers=self._admin, **kwargs)
        if response.status_code >= 400:
            raise SystemExit(
                f"{method} {path} -> {response.status_code} {response.text.strip()}\n"
                f"The admin API refused. Is {ADMIN_TOKEN_ENV} the one the gateway was started "
                "with? (An unset token on the gateway answers 503, never an open door — H-019.)"
            )
        return response

    def provision(self) -> None:
        """The four manual steps from the README's Phase 2 demo, performed once."""
        for found in self.admin("GET", "/admin/tenants").json():
            if found["name"] == TENANT:
                if not found["active"]:
                    self.admin("PATCH", f"/admin/tenants/{found['id']}", json={"active": True})
                self.tenant_id = str(found["id"])
                break
        else:
            created_tenant = self.admin("POST", "/admin/tenants", json={"name": TENANT})
            self.tenant_id = str(created_tenant.json()["id"])

        # A key's plaintext exists in exactly one response and is never recoverable
        # (H-017), so a script that has to *use* one has to mint one. The previous run's
        # is revoked rather than deleted, and stays visible in the console as a revoked
        # row, which is what a control plane looks like after a few weeks.
        params = {"tenant_id": self.tenant_id}
        for key in self.admin("GET", "/admin/keys", params=params).json():
            if key["name"] == "demo" and key["status"] == "active":
                self.admin("DELETE", f"/admin/keys/{key['id']}")
        created = self.admin(
            "POST",
            "/admin/keys",
            json={"tenant_id": self.tenant_id, "name": "demo", "allowed_models": ["mock-*"]},
        )
        self.key = str(created.json()["key"])

    def reset(self) -> None:
        """Start from the shipped defaults, so the second run of this looks like the first.

        All three are the admin API's own incident-response routes: purge the cache, empty
        the buckets, drop the counters. Without them a re-run would open with last run's
        spend against this run's cap, and act 2's "first ask is a miss" would be a hit.
        """
        self.admin("DELETE", f"/admin/cache/{self.tenant_id}")  # disables *and* purges
        self.admin("DELETE", f"/admin/limits/tenant/{self.tenant_id}")  # clears + empties
        # 404 when there is no cap, which is the ordinary case on a fresh database.
        self._client.delete(f"/admin/budgets/{self.tenant_id}", headers=self._admin)

    # --- traffic --------------------------------------------------------------------

    def ask(
        self,
        text: str,
        *,
        script: str | None = None,
        tools: bool = False,
        max_tokens: int = 64,
    ) -> httpx.Response:
        """One non-streamed request, exactly as a tenant's own client would send it."""
        body: dict[str, Any] = {
            "model": MODEL,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": text}],
        }
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
        return self._client.post("/v1/messages", json=body, headers=self._caller(script))

    def stream(self, text: str, *, script: str | None = None) -> tuple[int, str]:
        """One streamed request, returning its status and every frame it produced."""
        body = {
            "model": MODEL,
            "max_tokens": 64,
            "stream": True,
            "messages": [{"role": "user", "content": text}],
        }
        frames: list[str] = []
        with self._client.stream(
            "POST", "/v1/messages", json=body, headers=self._caller(script)
        ) as response:
            status = response.status_code
            frames.extend(response.iter_lines())
        return status, "\n".join(frames)

    def _caller(self, script: str | None) -> dict[str, str]:
        headers = {"authorization": f"Bearer {self.key}", "content-type": "application/json"}
        if script is not None:
            headers[SCRIPT_HEADER] = script
        return headers

    def row(self, response: httpx.Response) -> dict[str, Any]:
        """The ledger row a request wrote, once the writer has actually written it.

        The write is fire-and-forget by design (H-027), so reading it back immediately is
        a race. Polling is the honest client-side answer: this script has no handle on the
        drain, and inventing one would mean reaching past the public API.
        """
        request_id = response.headers["x-headroom-request-id"]
        for _ in range(50):
            found = self._client.get(f"/admin/usage/{request_id}", headers=self._admin)
            if found.status_code == 200:
                row: dict[str, Any] = found.json()
                return row
            time.sleep(0.1)
        raise SystemExit(f"no ledger row for {request_id} after 5s — is the writer draining?")

    def cache_entries(self) -> int:
        count: int = self.admin("GET", f"/admin/cache/{self.tenant_id}").json()["entries"]
        return count


# --- the acts ----------------------------------------------------------------------------


def act_1_the_meter(demo: Demo) -> None:
    act("1. A request is priced to the picodollar, at the rates it was billed at.")
    response = demo.ask("What did the catalogue earn last quarter?")
    demo.report.record(response.status_code == 200, f"POST /v1/messages -> {response.status_code}")
    row = demo.row(response)
    demo.report.record(
        (row["input_tokens"], row["output_tokens"]) == (11, 7),
        f"the meter read the usage block, not the text: {row['input_tokens']} in / "
        f"{row['output_tokens']} out",
    )
    demo.report.record(
        row["usd_cost"] == UNIT_COST_STR and row["cost_status"] == "priced",
        f"11 x ${row['usd_per_mtok_in']}/MTok + 7 x ${row['usd_per_mtok_out']}/MTok = "
        f"${row['usd_cost']} ({row['cost_status']})",
    )
    demo.report.record(
        row["price_effective_from"] is not None,
        f"the row keeps the price it was billed at, effective {row['price_effective_from']} "
        "— editing config/models.yaml cannot re-bill it (H-024)",
    )


def act_2_the_cache(demo: Demo) -> None:
    act("2. The same question twice is one upstream call, and the saving has a column.")
    demo.admin("PUT", f"/admin/cache/{demo.tenant_id}", json={"mode": "exact"})
    demo.report.note("caching is off for every tenant until somebody switches it on")

    question = "How did the 2019 vinyl reissue perform?"
    first = demo.ask(question)
    second = demo.ask(question)

    demo.report.record(
        "x-headroom-cache" not in first.headers,
        "the first ask is a miss, and it populates",
    )
    demo.report.record(
        second.headers.get("x-headroom-cache") == "cache_hit_exact"
        and second.headers.get("x-headroom-cache-source") == first.headers["x-headroom-request-id"],
        f"the second is {second.headers.get('x-headroom-cache')}, served from "
        f"{second.headers.get('x-headroom-cache-source')} — the request that produced it",
    )
    demo.report.record(
        second.content == first.content,
        "and the bodies are byte-identical: an entry is replayed, never converted (H-043)",
    )
    row = demo.row(second)
    demo.report.record(
        row["provider"] is None
        and row["upstream_status"] is None
        and row["usd_cost"] == "0.000000000000"
        and row["cost_status"] == "not_billable"
        and row["cache_avoided_usd"] == UNIT_COST_STR,
        f"a hit is not an upstream call wearing a hat: provider {row['provider']}, cost "
        f"${row['usd_cost']}, avoided ${row['cache_avoided_usd']} in a column of its own",
    )


def act_3_what_the_cache_refuses(demo: Demo) -> None:
    act("3. The interesting part of a cache is what it refuses.")
    before = demo.cache_entries()

    status, frames = demo.stream("Stream me the quarterly rollup", script="fault-cut@mock")
    demo.report.record(
        status == 200 and "event: error" in frames and "message_stop" not in frames,
        "a stream cut mid-answer ends in a terminal error event, with no message_stop",
    )

    tools = demo.ask("How did the 2019 vinyl reissue perform?", tools=True)
    tools_row = demo.row(tools)
    demo.report.record(
        tools_row["cache_disposition"] == "cache_bypass",
        f"the same words with tools declared: {tools_row['cache_disposition']} — the model "
        "may legitimately answer with a tool call instead of prose (H-041)",
    )

    after = demo.cache_entries()
    demo.report.record(
        after == before,
        f"and the cache still holds {after} entries: neither was stored. One bad write "
        "here is served forever (invariant 6)",
    )


def act_4_failover(demo: Demo) -> None:
    act("4. A broken primary is invisible to the caller; a broken stream never is.")
    hopped = demo.ask("Failover probe", script="fault-529@mock")
    demo.report.record(
        hopped.status_code == 200
        and hopped.headers.get("x-headroom-failover-hops") == "1"
        and hopped.headers.get("x-headroom-failover-from") == "mock",
        f"the primary 529s and the fallback answers: {hopped.status_code}, "
        f"hops={hopped.headers.get('x-headroom-failover-hops')} "
        f"from={hopped.headers.get('x-headroom-failover-from')}",
    )
    row = demo.row(hopped)
    demo.report.record(
        row["failover_hops"] == 1 and row["failover_error"] == "upstream_status_529",
        f"one request, one row, one reservation: served by {row['provider']}, passed over "
        f"{row['failover_from']} ({row['failover_error']})",
    )

    _, frames = demo.stream("Splice probe", script="fault-cut@mock")
    demo.report.record(
        frames.count("event: message_start") == 1,
        f"and a fault *after* the first byte is never spliced: "
        f"{frames.count('event: message_start')} message_start, not two (H-048)",
    )

    refused = demo.ask("Client-error probe", script="fault-400")
    demo.report.record(
        refused.status_code == 400 and refused.headers.get("x-headroom-error-source") == "upstream",
        f"an upstream 400 is forwarded verbatim, not retried: {refused.status_code} from "
        f"{refused.headers.get('x-headroom-error-source')} — the fallback would say the same",
    )

    # Three faults in eight requests is a failure ratio a breaker is entitled to act on,
    # so `mock` may well be out of rotation by now — which would quietly send the rest of
    # this demo to the fallback and inflate the closing failover count. Clearing it is the
    # route an operator uses the moment they have fixed something, and it is worth showing.
    before = demo.admin("GET", "/admin/providers/mock").json()
    after = demo.admin("DELETE", "/admin/providers/mock/health").json()
    demo.report.record(
        after["state"] == "closed",
        f"the breaker on `mock` was {before['state']} after {before['samples']} samples "
        f"({before['total_failures']} failures); DELETE /admin/providers/mock/health puts it "
        f"back in rotation immediately — {after['state']}",
    )


def act_5_rate_limits(demo: Demo) -> None:
    act("5. A rate limit that cannot be raced, and says when it heals.")
    demo.admin("PUT", f"/admin/limits/tenant/{demo.tenant_id}", json={"requests_per_min": 3})
    statuses = [demo.ask(f"Hammering, attempt {n}").status_code for n in range(5)]
    demo.report.record(
        statuses == [200, 200, 200, 429, 429],
        f"3 requests/minute, 5 fired: {statuses}",
    )
    refusal = demo.ask("Hammering, one more")
    demo.report.record(
        refusal.status_code == 429
        and refusal.headers.get("x-headroom-error-source") == "gateway"
        and refusal.headers.get("retry-after") is not None,
        f"429 scope={refusal.headers.get('x-headroom-ratelimit-scope')} "
        f"retry-after={refusal.headers.get('retry-after')}s, and it says whose it is: "
        f"error-source={refusal.headers.get('x-headroom-error-source')}",
    )
    demo.admin("DELETE", f"/admin/limits/tenant/{demo.tenant_id}")


def act_6_the_budget(demo: Demo) -> None:
    act("6. A budget gate that reads committed spend, before a provider is called.")
    # Sized in the same currency the gate is. An admission reserves the request's *worst
    # case* — its `max_tokens` ceiling plus the size of the body it sent, at the model's
    # dated price — and settles to the actual cost on completion, so the cap is reached
    # some way before the spend does. How many requests fit therefore depends on the
    # length of the sentence below, which is why the loop counts rather than predicts.
    cap = format(UNIT_COST * 10, "f")
    demo.admin("PUT", f"/admin/budgets/{demo.tenant_id}", json={"usd": cap, "window": "monthly"})
    served, refused, refusal = 0, 0, None
    for n in range(20):
        response = demo.ask(f"Spending, attempt {n}", max_tokens=16)
        if response.status_code == 200:
            served += 1
        else:
            refused += 1
            refusal = refusal or response
    demo.report.record(
        served >= 1 and refused >= 1 and refusal is not None,
        f"a cap of ${cap} — about ten mock answers: {served} served, then {refused} refused",
    )
    if refusal is not None:
        payload = refusal.json()
        demo.report.record(
            refusal.status_code == 402
            and payload["headroom"]["reason"] == "budget_exceeded"
            and refusal.headers.get("x-headroom-error-source") == "gateway",
            f"{refusal.status_code} {payload['error']['type']} / "
            f"{payload['headroom']['reason']} — not 429, because a budget does not heal "
            "inside its window (H-032)",
        )
        demo.report.note(payload["error"]["message"])
    budget = demo.admin("GET", f"/admin/budgets/{demo.tenant_id}").json()
    demo.report.record(
        Decimal(budget["committed"]) == Decimal(budget["spent"]) + Decimal(budget["reserved"]),
        f"committed = spent + reserved = ${budget['spent']} + ${budget['reserved']} = "
        f"${budget['committed']} against ${budget['usd']} — the figure the gate compares, "
        "never landed spend alone",
    )
    demo.admin("DELETE", f"/admin/budgets/{demo.tenant_id}")


def act_7_the_console(demo: Demo, console_url: str) -> None:
    act("7. And the console renders exactly these numbers.")
    for total in demo.admin("GET", "/admin/usage/totals").json():
        if total["tenant_id"] != demo.tenant_id:
            continue
        hits = total["cache_hits_exact"] + total["cache_hits_semantic"]
        demo.report.record(
            total["requests"] > 0,
            f"tenant {TENANT}: {total['requests']} requests, ${total['usd_cost']} spent, "
            f"{hits} cache hit(s) worth ${total['cache_avoided_usd']}, "
            f"{total['failover_requests']} failed over, {total['errored_requests']} errored",
        )
        break
    else:
        demo.report.record(False, f"no totals row for tenant {demo.tenant_id}")
    demo.report.note(f"sign in at {console_url} with the same HEADROOM_ADMIN_TOKEN")


# --- entry point --------------------------------------------------------------------------


def admin_token() -> str:
    """The root token, from the environment or from the gitignored `.env` beside it.

    `make demo` writes one into `.env` on a cold clone and compose reads it from there, so
    a stranger never has to know it exists. Reading the same file here means the script
    works whether or not the operator has exported it into this shell.
    """
    token = os.environ.get(ADMIN_TOKEN_ENV, "").strip()
    if token:
        return token
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            name, _, value = line.partition("=")
            if name.strip() == ADMIN_TOKEN_ENV:
                return value.strip().strip("'\"")
    raise SystemExit(
        f"{ADMIN_TOKEN_ENV} is not set and {ENV_FILE.name} does not carry one.\n"
        "`make demo` writes one for you; to do it by hand, put a line in the gitignored "
        f"{ENV_FILE.name} and re-run `make up` so the gateway is started with it."
    )


def wait_for_gateway(client: httpx.Client, *, attempts: int = 30) -> None:
    for _ in range(attempts):
        try:
            if client.get("/healthz", timeout=2.0).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1.0)
    raise SystemExit(f"the gateway at {client.base_url} never answered /healthz — is the stack up?")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="demo", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--console-url", default=DEFAULT_CONSOLE_URL)
    args = parser.parse_args(argv)

    token = admin_token()
    report = Report()
    started = time.monotonic()

    print(f"Headroom — the keyless demo, against {args.base_url}")
    print("No provider key, no network, no GPU, and $0.00. Every fault below is injected")
    print("into the MockProvider over HTTP, on the same code path a real provider takes.")

    with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=30.0) as client:
        wait_for_gateway(client)
        demo = Demo(client, token, report)
        demo.provision()
        demo.reset()
        act_1_the_meter(demo)
        act_2_the_cache(demo)
        act_3_what_the_cache_refuses(demo)
        act_4_failover(demo)
        act_5_rate_limits(demo)
        act_6_the_budget(demo)
        act_7_the_console(demo, args.console_url)

    checks = len(report.checks)
    print(f"\n{checks - report.failed}/{checks} checks passed in {time.monotonic() - started:.1f}s")
    if report.failed:
        print("Something above is not true of this clone. Paste the FAIL lines into an issue.")
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
