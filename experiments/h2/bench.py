"""What a gateway in Python actually costs, measured where the provider costs nothing.

H-065's secondary A, and the honest answer to §P8.H2's real question. The ledger cannot
separate the gateway's admission work from the provider's own time — `upstream_latency_ms`
is *request received → first upstream byte* and contains both, with no mark between them.
So the measurement is taken against the **MockProvider**, which answers in microseconds:
what remains in `upstream_latency_ms` is authentication, routing, the rate limiter, the
cache lookup and the budget reservation.

    uv run python -m experiments.h2.bench                 # ~2,000 requests, $0.00

**Two configurations, because the difference is the interesting number.** The first runs
the whole pipeline on in-memory stores; the second puts **DynamoDB Local** behind the rate
limiter and the budget gate, which is the shipped admission path. The delta between them is
what the two conditional writes cost per request — a figure this repo has argued about for
two phases (H-030, H-035, H-039) and never measured.

**Caveat, recorded with the number rather than under it:** this excludes TLS and DNS setup
to a real upstream. httpx amortises those over a keep-alive pool, and a caller talking
directly to the provider pays them too, so they are not gateway overhead — but they are not
zero either, and this measurement cannot see them.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Any, Final

from experiments.h2.analyze import percentile
from experiments.provenance import RESULTS_DIR, provenance, write_json
from headroom.providers.mock import MockScript

__all__ = ["DEFAULT_REQUESTS", "RESULT_PATH", "main", "run"]

#: PRE_REGISTRATION §H2.3 says "≥ 2,000". Enough that a p99 is a hundred samples rather
#: than one, and small enough to run in under a minute on a laptop.
DEFAULT_REQUESTS: Final = 2_000
WARMUP: Final = 50

RESULT_PATH: Final = RESULTS_DIR / "h2_bench.json"


def dynamodb_endpoint() -> str | None:
    """The compose emulator, or ``None`` when nothing is listening.

    Deliberately not `tests.support.budgets.require_dynamodb`, which calls `pytest.skip` —
    correct inside a test and a raised `BaseException` in a script. The reachability rule is
    H-012's either way: an inferred endpoint that is down is a missing configuration, not a
    failure.
    """
    from tests.support.services import resolve_endpoint

    endpoint = resolve_endpoint("DYNAMODB_ENDPOINT_URL", "http://localhost:8001")
    return endpoint.url if endpoint.reachable else None


async def _drive(*, requests: int, endpoint: str | None) -> dict[str, Any]:
    from contextlib import AsyncExitStack

    from tests.support.fixtures import anthropic_request
    from tests.support.harness import gateway_harness

    stack = AsyncExitStack()
    budgets = limits = None
    if endpoint is not None:
        from uuid import uuid4

        from headroom.db.buckets import DynamoRateLimitStore
        from headroom.db.budgets import DynamoBudgetStore
        from headroom.db.dynamo import DynamoClient

        client = DynamoClient(endpoint_url=endpoint)
        stack.push_async_callback(client.aclose)
        budgets = DynamoBudgetStore(client, table=f"headroom_bench_budgets_{uuid4().hex[:8]}")
        limits = DynamoRateLimitStore(client, table=f"headroom_bench_buckets_{uuid4().hex[:8]}")

    async with stack, gateway_harness(budgets=budgets, limits=limits) as harness:
        harness.book.set("bench", MockScript.anthropic_message("bench"))
        latencies: list[float] = []
        totals: list[float] = []
        for index in range(requests + WARMUP):
            response = await harness.post(
                "/v1/messages", anthropic_request(text=f"bench {index}"), script="bench"
            )
            if response.status_code != 200:
                raise SystemExit(f"bench request {index} returned {response.status_code}")
            if index < WARMUP:
                continue  # first-call imports, lazy pools, and one auth-cache miss
            context = harness.last_context()
            if context.upstream_latency_ms is not None:
                latencies.append(context.upstream_latency_ms)
            if context.total_ms is not None:
                totals.append(context.total_ms)

    return {
        "stores": "dynamodb-local" if endpoint else "in-memory",
        "requests": len(latencies),
        "admission_ms": {
            "metric": "upstream_latency_ms against the MockProvider",
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
            "p99": percentile(latencies, 0.99),
            "max": round(max(latencies), 4) if latencies else None,
        },
        "total_ms": {
            "p50": percentile(totals, 0.50),
            "p95": percentile(totals, 0.95),
            "p99": percentile(totals, 0.99),
        },
    }


async def run(*, requests: int = DEFAULT_REQUESTS, real_stores: bool = True) -> dict[str, Any]:
    configurations: list[dict[str, Any]] = [await _drive(requests=requests, endpoint=None)]
    if real_stores:
        endpoint = dynamodb_endpoint()
        if endpoint is None:
            configurations.append(
                {"stores": "dynamodb-local", "unavailable": "nothing listening — `make up` first"}
            )
        else:
            configurations.append(await _drive(requests=requests, endpoint=endpoint))

    result: dict[str, Any] = {
        "schema": "h2_bench/1",
        "provenance": provenance(
            produced_by="experiments/h2/bench.py",
            notes="Keyless, $0.00. The MockProvider answers in microseconds, so what is "
            "measured is the gateway's own admission path.",
        ),
        "caveat": (
            "excludes TLS and DNS setup to a real upstream, which httpx amortises over a "
            "keep-alive pool and which a direct caller pays too (H-065)"
        ),
        "configurations": configurations,
    }
    memory = configurations[0]["admission_ms"]
    dynamo = next(
        (row["admission_ms"] for row in configurations[1:] if "admission_ms" in row), None
    )
    if dynamo and memory["p50"] is not None and dynamo["p50"] is not None:
        result["conditional_write_cost_ms"] = {
            "statement": "what the rate limiter's and budget gate's DynamoDB writes add",
            "p50": round(dynamo["p50"] - memory["p50"], 4),
            "p95": round(float(dynamo["p95"] or 0) - float(memory["p95"] or 0), 4),
        }
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m experiments.h2.bench",
        description="Measure the gateway's admission cost on the MockProvider. Keyless, free.",
    )
    parser.add_argument("--requests", type=int, default=DEFAULT_REQUESTS)
    parser.add_argument(
        "--no-dynamo", action="store_true", help="skip the DynamoDB-Local configuration"
    )
    parser.add_argument("--out", default=str(RESULT_PATH))
    args = parser.parse_args(argv)

    result = asyncio.run(run(requests=args.requests, real_stores=not args.no_dynamo))
    write_json(Path(args.out), result)
    for row in result["configurations"]:
        if "admission_ms" not in row:
            print(f"  {row['stores']:<16} unavailable: {row['unavailable'][:80]}")
            continue
        spread = row["admission_ms"]
        print(
            f"  {row['stores']:<16} n={row['requests']:<6} "
            f"p50 {spread['p50']} ms · p95 {spread['p95']} ms · p99 {spread['p99']} ms"
        )
    if "conditional_write_cost_ms" in result:
        cost = result["conditional_write_cost_ms"]
        print(f"  two DynamoDB conditional writes cost p50 {cost['p50']} ms, p95 {cost['p95']} ms")
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover - operator entry point
    raise SystemExit(main())
