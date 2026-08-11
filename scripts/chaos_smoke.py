"""The chaos suite's keyless subset, driven over HTTP against a *running* gateway.

    python scripts/chaos_smoke.py --base-url http://localhost:8080 --key hk_...

BUILD_PLAN §P9's gate asks for "the chaos test's keyless subset against the deployed
stack". `tests/test_failover_chaos.py` cannot be that: it builds a gateway in-process and
drives it through an ASGI transport, so pointing it at a URL is not a flag, it is a
different program. This is that program — the same properties, asserted from the outside,
against whatever is on the other end of a hostname.

**It costs $0.00 and needs no key of the provider's.** Every fault is injected into the
MockProvider through the `x-headroom-mock-script` control header, which Phase 6 made
addressable from outside a test process for exactly this reason: `fault-529`,
`fault-timeout`, `fault-connect`, `fault-cut`, each aimable at one instance of a chain
with `@name`. The shipped `config/routing.yaml` chains `mock → mock_fallback` on both
dialects, so a deployed gateway has a chain to exercise with nothing configured.

**What it asserts**, and they are §P8.H3's three pre-registered clauses:

1. **Zero caller-visible 5xx for pre-first-token faults.** A 529, a timeout, and a
   connect failure on the primary each produce a 200 served by the fallback, with
   `x-headroom-failover-hops: 1` naming what was passed over.
2. **A mid-stream cut is a terminal error event, never a silent truncation** — and never
   a splice: the stream ends in an `event: error` carrying `upstream_stream_cut`, with no
   `message_stop`, and the fallback is not called.
3. **The gateway's own refusals never fail over.** Not asserted here — it needs a tenant
   at its cap or over its limit, which is `tests/test_failover_matrix.py`'s business
   against a gateway it controls. Stated so the omission is not mistaken for coverage.

Exit code 0 if every check passes, 1 otherwise, and every check prints its own line
either way — this is evidence for a phase log, so a failure has to be as legible as a
pass.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from typing import Any

import httpx

#: The model the shipped routing table sends to the mock chain, on both dialects.
MODEL = "mock-chaos-smoke"

#: The control header Phase 6 opened up. The proxy strips the `x-headroom-` prefix and
#: hands the rest to the provider as control input, never as part of the request body.
SCRIPT_HEADER = "x-headroom-mock-script"

#: Faults that happen *before* a byte reaches the caller, which is the entire class
#: failover is allowed to hide. Aimed at `mock` so `mock_fallback` answers normally.
PRE_TOKEN_FAULTS = ("fault-529@mock", "fault-timeout@mock", "fault-connect@mock")


@dataclass(slots=True)
class Report:
    """What happened, in the order it happened."""

    checks: list[tuple[bool, str]] = field(default_factory=list)

    def record(self, ok: bool, line: str) -> None:
        self.checks.append((ok, line))
        print(f"{'ok  ' if ok else 'FAIL'}  {line}")

    @property
    def failed(self) -> int:
        return sum(1 for ok, _ in self.checks if not ok)


def _body(prompt: str, *, stream: bool) -> dict[str, Any]:
    return {
        "model": MODEL,
        "max_tokens": 64,
        "stream": stream,
        "messages": [{"role": "user", "content": prompt}],
    }


def pre_token_faults(client: httpx.Client, report: Report) -> None:
    """Clause 1: a fault before the first byte is invisible to the caller."""
    for script in PRE_TOKEN_FAULTS:
        response = client.post(
            "/v1/messages",
            json=_body(f"chaos {script}", stream=False),
            headers={SCRIPT_HEADER: script},
        )
        hops = response.headers.get("x-headroom-failover-hops")
        passed_over = response.headers.get("x-headroom-failover-from")
        report.record(
            response.status_code == 200 and hops == "1" and passed_over == "mock",
            f"{script}: {response.status_code} hops={hops} from={passed_over} "
            f"(want 200 hops=1 from=mock)",
        )


def mid_stream_cut(client: httpx.Client, report: Report) -> None:
    """Clause 2: after the first byte the caller is told, and nothing is spliced."""
    frames: list[str] = []
    with client.stream(
        "POST",
        "/v1/messages",
        json=_body("chaos fault-cut", stream=True),
        headers={SCRIPT_HEADER: "fault-cut@mock"},
    ) as response:
        status = response.status_code
        for line in response.iter_lines():
            frames.append(line)
    body = "\n".join(frames)

    # The three halves of "honest", each checked separately so a failure says which one.
    report.record(
        "event: error" in body,
        "fault-cut: the stream ends in a terminal error event",
    )
    report.record(
        "upstream_stream_cut" in body,
        "fault-cut: the reason is upstream_stream_cut, not a generic api_error",
    )
    report.record(
        "message_stop" not in body,
        "fault-cut: no message_stop — a cut answer never claims to have finished",
    )
    # The splice test, from the outside: a second `message_start` would mean two models
    # wrote one answer (H-048's Frankenstein). One, and only one.
    report.record(
        body.count("event: message_start") == 1,
        f"fault-cut: exactly one message_start (saw {body.count('event: message_start')})",
    )
    report.record(
        status == 200, f"fault-cut: HTTP {status} — the status line was spent before the fault"
    )


def happy_path(client: httpx.Client, report: Report) -> None:
    """The control. Without it, "no 5xx" is satisfied by a gateway that answers nothing."""
    response = client.post("/v1/messages", json=_body("chaos control", stream=False))
    ok = response.status_code == 200 and "x-headroom-failover-hops" not in response.headers
    report.record(
        ok,
        f"no fault: {response.status_code}, and no failover headers at all "
        f"(a request the primary served has no story to tell)",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="chaos_smoke", description=__doc__)
    parser.add_argument(
        "--base-url", required=True, help="e.g. http://headroom-….elb.amazonaws.com:8080"
    )
    parser.add_argument("--key", required=True, help="a virtual key scoped to mock-* models")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args(argv)

    print(f"chaos smoke against {args.base_url}")
    report = Report()
    with httpx.Client(
        base_url=args.base_url.rstrip("/"),
        headers={"authorization": f"Bearer {args.key}", "content-type": "application/json"},
        timeout=args.timeout,
    ) as client:
        happy_path(client, report)
        pre_token_faults(client, report)
        mid_stream_cut(client, report)

    print(json.dumps({"checks": len(report.checks), "failed": report.failed}))
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
