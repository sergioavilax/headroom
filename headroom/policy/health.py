"""Provider health: a rolling window per provider, and the breaker that reads it.

BUILD_PLAN §P6 asks for two things in one sentence — *"provider health tracking
(rolling error/latency windows)"* and *"a circuit breaker trips a provider out of
rotation after a threshold and probes it back in"* — and they are one mechanism seen
twice: the window is the evidence, the breaker is the verdict.

Four decisions shape this file, and each is argued in docs/DECISIONS.md H-052.

**Health is in-process and is never shared.** It is not in Postgres and not in DynamoDB
— BUILD_PLAN L2 reserves DynamoDB for token buckets and budget reservations *only*, and
more importantly a breaker is not a fact about the world, it is a record of what **this
process** has been able to reach. A Fargate task whose NAT gateway is broken must trip
its own breaker without convincing the other three tasks that Anthropic is down. So each
process learns independently, which is correct, and the cost — a cold process pays a few
failures before it learns — is bounded by ``MIN_SAMPLES``.

**A provider is scored when its response finishes, not when it starts.** An upstream
that answers headers and then dies mid-answer is not a healthy upstream, and it is
exactly the failure a ``docker kill`` on a live vLLM produces. So the streaming path
reports its own outcome (``headroom/api/proxy.py``) while the failover executor reports
the attempts it made and the bodies it read — one observation per attempt, never two.

**Only provider failures count.** A 400 from a provider is a healthy provider correctly
rejecting a bad request, and counting it would let one client's malformed payloads trip
a breaker for every other tenant. Failures are transport faults, the statuses the phase
retries on (429 / 5xx), and a stream that did not finish.

**A recovered provider starts from a clean slate.** When a half-open probe succeeds the
window is cleared rather than merely appended to; otherwise the failures that tripped
the breaker are still in the window and the very next blip re-trips it, which is how a
breaker turns a ten-second outage into a ten-minute one.
"""

from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Final

__all__ = [
    "BREAKER_CLOSED",
    "BREAKER_HALF_OPEN",
    "BREAKER_OPEN",
    "HealthPolicy",
    "HealthSnapshot",
    "HealthTracker",
]

#: Serving normally. Every attempt is allowed.
BREAKER_CLOSED: Final = "closed"
#: Tripped. Attempts are skipped **while another candidate exists** — a breaker that
#: could refuse the last provider in a chain would convert an upstream's outage into
#: the gateway's own, which is strictly worse than trying and failing.
BREAKER_OPEN: Final = "open"
#: The cooldown has elapsed and exactly one probe is allowed through. Its result decides
#: whether the breaker closes or re-opens for another cooldown.
BREAKER_HALF_OPEN: Final = "half_open"


@dataclass(frozen=True, slots=True)
class HealthPolicy:
    """When a provider is considered unwell, and for how long.

    The numbers are published rather than tuned in place: ``tests/test_provider_health.py``
    asserts them, so changing one is a deliberate act with a diff attached.
    """

    #: How many recent observations the window holds. Twenty is enough for a ratio to
    #: mean something and short enough that a provider which recovered ten minutes ago
    #: is not still being judged on it.
    window: int = 20
    #: Never trip on fewer than this many observations. A single failure on a cold
    #: provider is a blip, not a diagnosis — and without this floor the first request
    #: of a process's life could trip a breaker at a failure ratio of 1.0.
    min_samples: int = 5
    #: Trip at or above this failure ratio. A half-failing provider is worse than a
    #: dead one (it burns a timeout per request), so the bar is deliberately not 1.0.
    failure_ratio: float = 0.5
    #: How long an open breaker stays open before it allows one probe. Long enough that
    #: a tripped provider is not hammered; short enough that recovery is visible in a
    #: demo and in a test that advances a clock by a known amount.
    cooldown_s: float = 10.0


@dataclass(frozen=True, slots=True)
class HealthSnapshot:
    """One provider's health as ``/admin/providers`` reports it.

    A value rather than a live object, so the admin surface cannot mutate what it reads
    and so a listing is a consistent picture rather than a sequence of racing ones.
    """

    provider: str
    kind: str
    state: str
    #: Observations currently in the window, and how many of them failed.
    samples: int
    failures: int
    failure_ratio: float
    #: Failures in a row, ignoring the window. The number an operator reads first.
    consecutive_failures: int
    #: Lifetime counters, so a tile can show "healthy now, 412 failures today".
    total_successes: int
    total_failures: int
    #: Latency of completed responses in the window, in milliseconds. ``None`` until
    #: something completes — a provider that has only ever failed has no latency, and
    #: reporting ``0`` for that would be the H-025 mistake in a different column.
    p50_latency_ms: float | None
    p95_latency_ms: float | None
    last_error: str | None
    #: Seconds until an open breaker will allow a probe. ``None`` unless open.
    reopen_in_s: float | None


@dataclass(slots=True)
class _Window:
    """One provider's rolling evidence and current verdict."""

    policy: HealthPolicy
    outcomes: deque[bool] = field(default_factory=deque)
    latencies: deque[float] = field(default_factory=deque)
    state: str = BREAKER_CLOSED
    opened_at: float = 0.0
    probe_in_flight: bool = False
    consecutive_failures: int = 0
    total_successes: int = 0
    total_failures: int = 0
    last_error: str | None = None

    def reset(self) -> None:
        """Forget everything but the lifetime counters.

        Called when a probe succeeds. Keeping the window would leave the failures that
        tripped the breaker sitting in it, so the next single failure would satisfy the
        ratio again and re-trip immediately — a recovered provider would never get out
        of the penalty box.
        """
        self.outcomes.clear()
        self.latencies.clear()
        self.state = BREAKER_CLOSED
        self.probe_in_flight = False
        self.consecutive_failures = 0

    def observe(self, ok: bool, latency_ms: float | None) -> None:
        self.outcomes.append(ok)
        while len(self.outcomes) > self.policy.window:
            self.outcomes.popleft()
        if ok and latency_ms is not None:
            self.latencies.append(latency_ms)
            while len(self.latencies) > self.policy.window:
                self.latencies.popleft()

    @property
    def failures(self) -> int:
        return sum(1 for ok in self.outcomes if not ok)

    @property
    def ratio(self) -> float:
        return 0.0 if not self.outcomes else self.failures / len(self.outcomes)

    def should_trip(self) -> bool:
        return len(self.outcomes) >= self.policy.min_samples and (
            self.ratio >= self.policy.failure_ratio
        )


class HealthTracker:
    """Every provider's rolling window, and the breaker built on top of them.

    One per gateway, held on :class:`~headroom.api.gateway.Gateway`. The clock is
    injected for the same reason the auth cache's is (H-018): a cooldown tested by
    sleeping through it is a test people delete when the suite gets slow.
    """

    __slots__ = ("_clock", "_kinds", "_windows", "policy")

    def __init__(
        self,
        policy: HealthPolicy | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.policy = policy if policy is not None else HealthPolicy()
        self._clock = clock
        self._windows: dict[str, _Window] = {}
        #: Provider name -> kind, learned from the registry at construction so the admin
        #: surface can report it without a second lookup. Optional: a provider nobody
        #: registered still gets a window, because the executor must never fail because
        #: health-keeping had a gap in its bookkeeping.
        self._kinds: dict[str, str] = {}

    def track(self, provider: str, kind: str) -> None:
        """Register a provider so it appears in a listing before its first request."""
        self._kinds[provider] = kind
        self._window(provider)

    # --- the breaker ---------------------------------------------------------------

    def admit(self, provider: str) -> bool:
        """May this provider be attempted right now?

        **This is a query with a side effect, deliberately** — the same trade H-032 took
        for the budget sweep on ``GET /admin/budgets``. An open breaker whose cooldown
        has elapsed transitions to half-open *here*, because the transition and the
        decision are the same event: something has to be the first request through, and
        making it a separate scheduled job would mean a breaker that only recovers if a
        background task happens to be running.

        Half-open admits exactly one probe. The flag is set and cleared without an
        ``await`` between them, so concurrent tasks on one event loop cannot both win it.
        """
        window = self._window(provider)
        if window.state == BREAKER_CLOSED:
            return True
        if window.state == BREAKER_OPEN:
            if self._clock() - window.opened_at < self.policy.cooldown_s:
                return False
            window.state = BREAKER_HALF_OPEN
            window.probe_in_flight = True
            return True
        # Half-open: one probe at a time, everyone else waits for its verdict.
        if window.probe_in_flight:
            return False
        window.probe_in_flight = True
        return True

    def record(
        self, provider: str, *, ok: bool, reason: str | None = None, latency_ms: float | None = None
    ) -> None:
        """One completed attempt against one provider. The only way evidence gets in."""
        window = self._window(provider)
        window.observe(ok, latency_ms)
        if ok:
            window.total_successes += 1
            window.consecutive_failures = 0
            if window.state == BREAKER_HALF_OPEN:
                # The probe came back clean. Clear the slate — see `_Window.reset`.
                window.reset()
            return

        window.total_failures += 1
        window.consecutive_failures += 1
        window.last_error = reason
        if window.state == BREAKER_HALF_OPEN:
            # The probe failed: straight back to open for another full cooldown, rather
            # than letting the next request probe again immediately.
            self._trip(window)
            return
        window.probe_in_flight = False
        if window.state == BREAKER_CLOSED and window.should_trip():
            self._trip(window)

    def _trip(self, window: _Window) -> None:
        window.state = BREAKER_OPEN
        window.opened_at = self._clock()
        window.probe_in_flight = False

    def state_of(self, provider: str) -> str:
        """The breaker's state, without moving it. For logs and for the admin surface."""
        return self._window(provider).state

    def clear(self, provider: str) -> None:
        """Forget a provider's history and close its breaker. The incident-response path.

        The analogue of ``DELETE /admin/limits`` (H-037): an operator who has just fixed
        a provider should not have to wait out a cooldown to prove it, and a breaker that
        can only be closed by the passage of time is a breaker nobody trusts in an
        incident. Lifetime counters survive, because they are the record.
        """
        self._window(provider).reset()

    # --- reporting -------------------------------------------------------------------

    def snapshot(self, provider: str) -> HealthSnapshot:
        window = self._window(provider)
        reopen: float | None = None
        if window.state == BREAKER_OPEN:
            reopen = max(0.0, self.policy.cooldown_s - (self._clock() - window.opened_at))
        return HealthSnapshot(
            provider=provider,
            kind=self._kinds.get(provider, "unknown"),
            state=window.state,
            samples=len(window.outcomes),
            failures=window.failures,
            failure_ratio=round(window.ratio, 4),
            consecutive_failures=window.consecutive_failures,
            total_successes=window.total_successes,
            total_failures=window.total_failures,
            p50_latency_ms=_percentile(window.latencies, 0.50),
            p95_latency_ms=_percentile(window.latencies, 0.95),
            last_error=window.last_error,
            reopen_in_s=None if reopen is None else round(reopen, 3),
        )

    def snapshots(self) -> list[HealthSnapshot]:
        """Every tracked provider, by name. What the Phase 7 health tiles read."""
        return [self.snapshot(name) for name in sorted(self._windows)]

    def known(self, provider: str) -> bool:
        return provider in self._windows

    def _window(self, provider: str) -> _Window:
        window = self._windows.get(provider)
        if window is None:
            window = _Window(policy=self.policy)
            self._windows[provider] = window
        return window


def _percentile(values: deque[float], fraction: float) -> float | None:
    """Nearest-rank percentile over a short deque. ``None`` when there is no data.

    Nearest-rank rather than interpolated: with a window of twenty samples the
    difference is noise, and an exact order statistic is a number an operator can find
    in the log line beside it.
    """
    if not values:
        return None
    ordered = sorted(values)
    rank = math.ceil(fraction * len(ordered))
    return round(ordered[min(len(ordered), max(1, rank)) - 1], 3)
