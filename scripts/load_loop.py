"""A background load loop, and a definition of "dropped" you can argue with.

    python scripts/load_loop.py --base-url http://…:8080 --key hk_… --duration-s 600

BUILD_PLAN §P10 asks for *"a rolling `helm upgrade` with zero dropped requests from a
background load loop"*. "Zero dropped requests" is a claim, and a claim needs an
instrument that could have said otherwise — so this script's real content is not the loop,
it is `classify()`: three outcomes, drawn where the gateway's own vocabulary draws them.

**ok**       — a 2xx. Under `--stream`, a 2xx whose stream reached its terminal marker.
**shed**     — a 402 or a 429 carrying `x-headroom-error-source: gateway`. The gateway
               refusing on purpose, because a tenant is over its cap (H-032) or over its
               limit (H-038). That is the product working; counting it as a drop would
               make a budget gate look like an outage.
**dropped**  — everything else. A transport failure, a 5xx, a stream that ended without
               its terminal marker, and — deliberately — *any* refusal that does not carry
               the gateway's own marker. H-038 built those three markers so that "whose
               failure is this" is answerable from the outside; this is the first thing
               outside the test suite to read them.

The asymmetry is the point: `shed` requires positive evidence that the gateway meant it,
and everything else falls to `dropped`. A classifier whose unknown case was "probably
fine" could not report a zero it had earned.

**Cost: $0.00.** By default every request goes to a `mock-` model, which the shipped
`config/routing.yaml` sends to the MockProvider chain. That is the correct instrument as
well as the free one: what a rolling upgrade can break is the *gateway's* availability,
and putting a provider's latency and error rate in the middle of the measurement would
only add noise to somebody else's number. `--model` and `--dialect` point the same three
outcomes at the operator's own vLLM chain for the two-GPU kill demo, which is also free.

**The metric that catches what a rate does not.** A rollout that dropped nothing but was
unreachable for nine seconds has an error count of zero and is still an outage.
`max_gap_ms` is the longest stretch in the run with no successful response — measured from
the start of the window to the first success and from the last success to the end, as well
as between successes — so a silent stall is visible as a number rather than as an absence.

Exit code 0 if `dropped` is zero, 1 otherwise. Ctrl-C prints the summary rather than a
traceback: this runs for the length of a `helm upgrade` and its output is evidence.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import itertools
import json
import signal
import sys
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

#: The shipped routing table sends this prefix to `mock` with `mock_fallback` behind it,
#: on both dialects. No key of a provider's, no network beyond the gateway, no spend.
MODEL = "mock-load-loop"

#: The two dialects, as a path and a terminal SSE marker. Both are needed because this
#: script has two jobs: the rolling-upgrade measurement runs on the Anthropic-dialect mock
#: chain, and the two-vLLM kill demo runs OpenAI-dialect against the operator's own GPUs —
#: same instrument, same three outcomes, so "zero dropped" means the same thing in both
#: captures rather than meaning "nobody was counting" in one of them.
DIALECTS = {
    "anthropic": ("/v1/messages", "event: message_stop"),
    # H-008: `[DONE]` is convention rather than protocol on this dialect, and a chunk
    # carrying a non-null `finish_reason` is equally a completed stream. Either counts.
    "openai": ("/v1/chat/completions", "data: [DONE]"),
}

#: H-009: every error response the gateway composes carries this, and an upstream's own
#: error carries `upstream` instead. H-038 made it trustworthy by stripping the whole
#: `x-headroom-*` namespace from every upstream response, so a provider cannot forge it.
ERROR_SOURCE_HEADER = "x-headroom-error-source"
SOURCE_GATEWAY = "gateway"

#: Statuses the gateway returns when it is deliberately refusing traffic: 402 for a budget
#: (H-032) and 429 for a rate limit (H-038). Both are only `shed` with the header above.
DELIBERATE_REFUSALS = frozenset({402, 429})

OK = "ok"
SHED = "shed"
DROPPED = "dropped"

#: Anthropic's terminal SSE marker. A streamed response that never reaches it was cut,
#: which H-008 makes the gateway say out loud — and which a caller counting only status
#: codes would score as a success.
TERMINAL_MARKER = "event: message_stop"


def classify(
    *,
    status: int | None,
    error_source: str | None,
    transport_error: str | None = None,
    stream_complete: bool | None = None,
) -> str:
    """One request's outcome. The whole falsifiability of "zero dropped" lives here.

    `status is None` means the request never got an answer at all — a connection reset, a
    read timeout, a DNS failure while a load balancer re-resolved. That is the shape a
    dropped request actually has across an NLB, and it is the case a classifier written
    around status codes forgets.

    `stream_complete` is `None` for a non-streamed request and a bool under `--stream`.
    """
    if transport_error is not None or status is None:
        return DROPPED
    if status in DELIBERATE_REFUSALS and error_source == SOURCE_GATEWAY:
        return SHED
    if 200 <= status < 300:
        # A 200 whose stream stopped early is not a served request. The status line was
        # spent before the fault, which is exactly why H-008 appends a terminal error
        # event — and why this reads the body rather than the status.
        return OK if stream_complete is not False else DROPPED
    return DROPPED


@dataclass(slots=True)
class Incident:
    """A single non-ok request, with enough to find it in the gateway's own log."""

    at: float
    kind: str
    status: int | None
    detail: str

    def as_dict(self, origin: float) -> dict[str, Any]:
        return {
            "t_s": round(self.at - origin, 3),
            "kind": self.kind,
            "status": self.status,
            "detail": self.detail,
        }


@dataclass(slots=True)
class Tally:
    """Counts, latencies, and the times at which requests succeeded."""

    started_at: float = field(default_factory=time.monotonic)
    finished_at: float = 0.0
    counts: dict[str, int] = field(default_factory=lambda: {OK: 0, SHED: 0, DROPPED: 0})
    by_status: dict[str, int] = field(default_factory=dict)
    latencies_ms: list[float] = field(default_factory=list)
    ok_times: list[float] = field(default_factory=list)
    incidents: list[Incident] = field(default_factory=list)

    def record(
        self, kind: str, status: int | None, latency_ms: float, at: float, detail: str
    ) -> None:
        self.counts[kind] += 1
        key = str(status) if status is not None else "transport"
        self.by_status[key] = self.by_status.get(key, 0) + 1
        self.latencies_ms.append(latency_ms)
        if kind == OK:
            self.ok_times.append(at)
        else:
            # Bounded: a run that is failing continuously should print a summary, not a
            # transcript. The counts stay exact either way.
            if len(self.incidents) < 200:
                self.incidents.append(Incident(at=at, kind=kind, status=status, detail=detail))

    def max_gap_ms(self) -> float:
        """The longest stretch with no successful response, in milliseconds.

        Includes the head and the tail of the window: a loop whose last success was eight
        seconds before the run ended did not recover, and a summary that only looked
        between successes would not say so.
        """
        end = self.finished_at or time.monotonic()
        marks = [self.started_at, *self.ok_times, end]
        return max(later - earlier for earlier, later in itertools.pairwise(marks)) * 1000

    def percentile(self, share: float) -> float:
        if not self.latencies_ms:
            return 0.0
        ordered = sorted(self.latencies_ms)
        index = min(len(ordered) - 1, int(share * len(ordered)))
        return round(ordered[index], 2)

    def summary(self, label: str, model: str = MODEL, dialect: str = "anthropic") -> dict[str, Any]:
        duration = (self.finished_at or time.monotonic()) - self.started_at
        return {
            "label": label,
            # In the summary because the summary is the evidence: a `dropped: 0` against a
            # model nobody can identify is a number without a subject.
            "model": model,
            "dialect": dialect,
            "duration_s": round(duration, 2),
            "requests": sum(self.counts.values()),
            "ok": self.counts[OK],
            "shed": self.counts[SHED],
            "dropped": self.counts[DROPPED],
            "by_status": dict(sorted(self.by_status.items())),
            "latency_ms": {
                "p50": self.percentile(0.50),
                "p95": self.percentile(0.95),
                "p99": self.percentile(0.99),
            },
            "max_gap_ms": round(self.max_gap_ms(), 1),
            "incidents": [incident.as_dict(self.started_at) for incident in self.incidents],
        }


def _body(prompt: str, model: str, *, stream: bool) -> dict[str, Any]:
    return {
        "model": model,
        "max_tokens": 64,
        "stream": stream,
        "messages": [{"role": "user", "content": prompt}],
    }


async def one_request(
    client: httpx.AsyncClient, tally: Tally, index: int, *, stream: bool, model: str, dialect: str
) -> None:
    path, terminal = DIALECTS[dialect]
    began = time.monotonic()
    try:
        if stream:
            frames: list[str] = []
            async with client.stream(
                "POST", path, json=_body(f"load {index}", model, stream=True)
            ) as response:
                async for line in response.aiter_lines():
                    frames.append(line)
                status: int | None = response.status_code
                source = response.headers.get(ERROR_SOURCE_HEADER)
            body = "\n".join(frames)
            complete: bool | None = terminal in body or '"finish_reason":"stop"' in body
            detail = "" if complete else body[-200:]
        else:
            response = await client.post(path, json=_body(f"load {index}", model, stream=False))
            status, source, complete = (
                response.status_code,
                response.headers.get(ERROR_SOURCE_HEADER),
                None,
            )
            detail = "" if response.status_code < 400 else response.text[:200]
        kind = classify(status=status, error_source=source, stream_complete=complete)
    except (httpx.HTTPError, OSError) as exc:
        # The shape a dropped request really has across a load balancer: no status line at
        # all. `type(exc).__name__` is kept because "ConnectError" and "ReadTimeout" fail
        # for different reasons and an evidence file should say which.
        kind, status, detail = DROPPED, None, f"{type(exc).__name__}: {exc}"

    tally.record(kind, status, (time.monotonic() - began) * 1000, time.monotonic(), detail)


async def worker(
    client: httpx.AsyncClient,
    tally: Tally,
    stop: asyncio.Event,
    *,
    interval_s: float,
    stream: bool,
    model: str,
    dialect: str,
    offset: int,
) -> None:
    index = offset
    while not stop.is_set():
        await one_request(client, tally, index, stream=stream, model=model, dialect=dialect)
        index += 1
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval_s)


async def run(args: argparse.Namespace) -> dict[str, Any]:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    tally = Tally()
    limits = httpx.Limits(max_connections=args.concurrency * 2)
    async with httpx.AsyncClient(
        base_url=args.base_url.rstrip("/"),
        headers={"authorization": f"Bearer {args.key}", "content-type": "application/json"},
        timeout=args.timeout,
        limits=limits,
    ) as client:
        workers = [
            asyncio.create_task(
                worker(
                    client,
                    tally,
                    stop,
                    interval_s=args.interval_ms / 1000,
                    stream=args.stream,
                    model=args.model,
                    dialect=args.dialect,
                    offset=n * 1_000_000,
                )
            )
            for n in range(args.concurrency)
        ]
        if args.duration_s:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=args.duration_s)
            stop.set()
        else:
            await stop.wait()
        await asyncio.gather(*workers)

    tally.finished_at = time.monotonic()
    return tally.summary(args.label, args.model, args.dialect)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="load_loop", description=__doc__)
    parser.add_argument("--base-url", required=True, help="e.g. http://…elb.amazonaws.com:8080")
    parser.add_argument("--key", required=True, help="a virtual key scoped to mock-* models")
    parser.add_argument(
        "--duration-s", type=float, default=0.0, help="0 runs until Ctrl-C (the default)"
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=4,
        help="requests in flight. More than one, so a rollout is overlapped rather than "
        "sampled: a serial loop can pass through a whole pod replacement between requests.",
    )
    parser.add_argument("--interval-ms", type=float, default=200.0, help="pause per worker")
    parser.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="per-request deadline, seconds. The default is tuned to the mock provider "
        "(p99 under 100 ms) and is WRONG for a real model: Phase 10 §11's first run "
        "scored fourteen legitimate 27B completions as `dropped` because non-streamed "
        "inference on the operator's GPUs takes 12-16 s to first token. Set it above the "
        "slowest response the chain under test can legitimately produce, or the "
        "instrument reports the instrument.",
    )
    parser.add_argument(
        "--model",
        default=MODEL,
        help="anything the routing table resolves. The default is a mock- model, which "
        "costs nothing; the two-vLLM kill demo points this at the operator's own GPUs, "
        "which also cost nothing.",
    )
    parser.add_argument("--dialect", choices=sorted(DIALECTS), default="anthropic")
    parser.add_argument(
        "--stream",
        action="store_true",
        help="stream every request and require the terminal marker. The harder test: an "
        "in-flight stream is what a terminating pod is most able to break.",
    )
    parser.add_argument("--label", default="load-loop", help="goes in the summary, for evidence")
    parser.add_argument("--out", help="write the summary JSON here as well as to stdout")
    args = parser.parse_args(argv)

    print(
        f"load loop against {args.base_url} — {args.model} on the {args.dialect} dialect, "
        f"{args.concurrency} in flight, {args.interval_ms:.0f} ms apart"
        + (f", for {args.duration_s:.0f}s" if args.duration_s else ", until Ctrl-C"),
        file=sys.stderr,
        flush=True,
    )
    summary = asyncio.run(run(args))
    rendered = json.dumps(summary, indent=2)
    print(rendered)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(rendered + "\n")
    return 1 if summary["dropped"] else 0


if __name__ == "__main__":
    sys.exit(main())
