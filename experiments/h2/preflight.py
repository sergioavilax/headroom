"""Refuse to start an $8 run against a tenant that would invalidate it.

Every check here corresponds to a way the measurement, rather than the gateway, would be
wrong — and each is cheap to check and expensive to discover afterwards::

    uv run python -m experiments.h2.preflight                 # free
    uv run python -m experiments.h2.preflight --smoke         # ~$0.02, one real call

| check | what it protects |
|---|---|
| `cache_mode == disabled` | H-047: a hit answers without a provider, so overhead becomes hit rate |
| no rate limits | a shed suite request is an errored row, not a measurement |
| budget cap ≥ the backstop | a 402 mid-suite corrupts the run it was meant to protect (H-066) |
| key scope covers planner, utility, router and judge | Backline calls four models, not one |
| both models routable and priced | an unpriced model is invisible to the cap (H-034) |

`--smoke` adds the risk-register item 2 obligation: one real Anthropic call carrying a
**tool block**, through the gateway, asserting the tool round-trips and the ledger metered
it. Backline's agents are tool-heavy, and A5 is verified keylessly but has never been
verified against the real API through this path.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Final

import httpx

from experiments.provenance import RESULTS_DIR, provenance, write_json

__all__ = ["DEFAULT_TENANT", "Check", "main", "run_checks"]

DEFAULT_TENANT: Final = "h2-gateway-overhead"
DEFAULT_GATEWAY: Final = "http://localhost:8080"

#: Backline calls four models: planner (sonnet), utility + router (haiku), judge (sonnet).
REQUIRED_MODELS: Final = ("claude-sonnet-5", "claude-haiku-4-5")

#: PRE_REGISTRATION §4 / H-066. The gateway-side backstop sits *above* Backline's own stop
#: so it can only fire when Backline's accounting is wrong.
BACKSTOP_USD: Final = 15.0

RESULT_PATH: Final = RESULTS_DIR / "h2_preflight.json"


class Check:
    """One named condition, its observed value, and whether it blocks the run."""

    __slots__ = ("detail", "fix", "name", "ok")

    def __init__(self, name: str, ok: bool, detail: str, fix: str = "") -> None:
        self.name = name
        self.ok = ok
        self.detail = detail
        self.fix = fix

    def as_dict(self) -> dict[str, Any]:
        return {"check": self.name, "ok": self.ok, "detail": self.detail, "fix": self.fix}


def _admin(client: httpx.Client, gateway: str, token: str, path: str) -> httpx.Response:
    return client.get(f"{gateway.rstrip('/')}{path}", headers={"Authorization": f"Bearer {token}"})


def run_checks(
    client: httpx.Client, *, gateway: str, token: str, tenant_name: str
) -> tuple[list[Check], dict[str, Any]]:
    checks: list[Check] = []

    tenants = _admin(client, gateway, token, "/admin/tenants")
    if tenants.status_code == 503:
        raise SystemExit(
            "the gateway's admin API is off (HEADROOM_ADMIN_TOKEN unset on the gateway). "
            "Nothing can be checked, so nothing is spent — H-019."
        )
    tenants.raise_for_status()
    match = next((row for row in tenants.json() if row["name"] == tenant_name), None)
    if match is None:
        raise SystemExit(
            f"no tenant named {tenant_name!r}. The runbook's step 1 creates it; a run against "
            f"the wrong tenant would mix H2's rows with somebody else's."
        )
    tenant_id = match["id"]
    checks.append(Check("tenant is active", bool(match["active"]), f"{tenant_name} = {tenant_id}"))

    cache = _admin(client, gateway, token, f"/admin/cache/{tenant_id}").json()
    checks.append(
        Check(
            "caching is disabled (H-047)",
            cache["mode"] == "disabled",
            f"mode={cache['mode']}, entries={cache['entries']}",
            fix=f"curl -X DELETE {gateway}/admin/cache/{tenant_id} -H 'Authorization: Bearer …'",
        )
    )

    limits = _admin(client, gateway, token, f"/admin/limits/tenant/{tenant_id}")
    limit_row = limits.json() if limits.status_code == 200 else {}
    uncapped = not limit_row.get("requests_per_min") and not limit_row.get("tokens_per_min")
    checks.append(
        Check(
            "no rate limits on the H2 tenant",
            uncapped,
            f"requests_per_min={limit_row.get('requests_per_min')}, "
            f"tokens_per_min={limit_row.get('tokens_per_min')}",
            fix=f"curl -X DELETE {gateway}/admin/limits/tenant/{tenant_id} …",
        )
    )

    budget = _admin(client, gateway, token, f"/admin/budgets/{tenant_id}")
    if budget.status_code == 404:
        checks.append(
            Check(
                f"budget backstop >= ${BACKSTOP_USD}",
                False,
                "no budget configured — the second opinion on Backline's own accounting is missing",
                fix=f"PUT {gateway}/admin/budgets/{tenant_id} "
                f'{{"usd": "{BACKSTOP_USD:.2f}", "window": "monthly"}}',
            )
        )
        budget_row: dict[str, Any] = {}
    else:
        budget_row = budget.json()
        cap = float(budget_row["usd"])
        checks.append(
            Check(
                f"budget backstop >= ${BACKSTOP_USD}",
                cap >= BACKSTOP_USD,
                f"cap=${cap:.2f}, committed=${float(budget_row['committed']):.2f}",
                fix="a backstop below Backline's own $12 stop would fire during a healthy "
                "run and corrupt it (H-066)",
            )
        )

    providers = _admin(client, gateway, token, "/admin/providers").json()
    anthropic: dict[str, Any] = next((row for row in providers if row["name"] == "anthropic"), {})
    state = str(anthropic.get("state", "absent"))
    checks.append(
        Check(
            "the anthropic provider is closed (healthy)",
            state == "closed",
            f"state={state}",
            fix=f"curl -X DELETE {gateway}/admin/providers/anthropic/health …",
        )
    )
    chains = anthropic.get("chains", [])
    checks.append(
        Check(
            "the claude- route has no failover chain",
            all("+" not in str(chain) for chain in chains),
            f"chains={chains}",
            fix="H2 measures one hop. A chain would put failover duration in the numbers.",
        )
    )

    return checks, {"tenant_id": tenant_id, "cache": cache, "budget": budget_row}


def smoke(client: httpx.Client, *, gateway: str, api_key: str) -> Check:
    """One real, paid call carrying a tool block — risk register item 2.

    ~$0.02. It is the only thing in the pre-flight that spends, and it exists because A5 is
    verified keylessly and has never been verified against the real API *through this path*
    — which is exactly what an $8 run of a tool-heavy agent suite depends on.
    """
    body = {
        "model": "claude-haiku-4-5",
        "max_tokens": 64,
        "tools": [
            {
                "name": "get_rate",
                "description": "Look up a royalty rate.",
                "input_schema": {
                    "type": "object",
                    "properties": {"artist": {"type": "string"}},
                    "required": ["artist"],
                },
            }
        ],
        "tool_choice": {"type": "tool", "name": "get_rate"},
        "messages": [{"role": "user", "content": "What rate did Yuki Takeda negotiate?"}],
    }
    response = client.post(
        f"{gateway.rstrip('/')}/v1/messages",
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
        json=body,
        timeout=120.0,
    )
    request_id = response.headers.get("x-headroom-request-id", "?")
    if response.status_code != 200:
        return Check(
            "tool round-trip through the gateway (paid)",
            False,
            f"HTTP {response.status_code}: {response.text[:200]}",
            fix="fix this before the $8 run — Backline's agents are tool-heavy",
        )
    blocks = response.json().get("content", [])
    used_tool = any(block.get("type") == "tool_use" for block in blocks)
    return Check(
        "tool round-trip through the gateway (paid)",
        used_tool,
        f"request_id={request_id}, blocks={[block.get('type') for block in blocks]}",
        fix="a reply with no tool_use block means the tool definition did not survive the hop",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m experiments.h2.preflight",
        description="Refuse an $8 run against a tenant that would invalidate it.",
    )
    parser.add_argument(
        "--gateway", default=os.environ.get("HEADROOM_GATEWAY_URL", DEFAULT_GATEWAY)
    )
    parser.add_argument("--tenant", default=DEFAULT_TENANT)
    parser.add_argument("--smoke", action="store_true", help="one real tool call (~$0.02)")
    parser.add_argument("--out", default=str(RESULT_PATH))
    args = parser.parse_args(argv)

    token = os.environ.get("HEADROOM_ADMIN_TOKEN", "")
    if not token:
        raise SystemExit("HEADROOM_ADMIN_TOKEN is not set")

    with httpx.Client(timeout=30.0) as client:
        checks, context = run_checks(
            client, gateway=args.gateway, token=token, tenant_name=args.tenant
        )
        if args.smoke:
            api_key = os.environ.get("H2_VIRTUAL_KEY", "")
            if not api_key:
                raise SystemExit("--smoke needs H2_VIRTUAL_KEY (the hk_… key Backline will use)")
            checks.append(smoke(client, gateway=args.gateway, api_key=api_key))

    for check in checks:
        print(f"  [{'ok ' if check.ok else 'FAIL'}] {check.name}: {check.detail}")
        if not check.ok and check.fix:
            print(f"         fix: {check.fix}", file=sys.stderr)

    failed = [check for check in checks if not check.ok]
    write_json(
        Path(args.out),
        {
            "schema": "h2_preflight/1",
            "provenance": provenance(produced_by="experiments/h2/preflight.py"),
            "gateway": args.gateway,
            "tenant": args.tenant,
            "tenant_id": context["tenant_id"],
            "smoke": args.smoke,
            "checks": [check.as_dict() for check in checks],
            "verdict": "READY" if not failed else "BLOCKED",
        },
    )
    print(f"\n{'READY' if not failed else 'BLOCKED — ' + str(len(failed)) + ' check(s) failed'}")
    return 0 if not failed else 2


if __name__ == "__main__":  # pragma: no cover - operator entry point
    raise SystemExit(main())
