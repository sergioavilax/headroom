"""The load loop's definition of "dropped", tested — because the claim rests on it.

BUILD_PLAN §P10 asks for *"a rolling `helm upgrade` with zero dropped requests"*. A zero is
only worth reading if the instrument could have produced something else, and the whole of
that instrument is `classify()`: a 402 the budget gate meant is not a dropped request, and
a connection reset with no status line at all is.

So the classifier is exercised across every outcome the gateway can actually produce,
including the two that are easy to get wrong in opposite directions — a deliberate refusal
scored as an outage, and a truncated stream scored as a success because the status line was
200 before the fault (H-008's whole subject).

`scripts/` is not a package, so the module is loaded by path rather than imported. That is
deliberate: adding an `__init__.py` to make one test tidier would change the shape of a
directory three other things already live in.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path) -> ModuleType:
    """Import a module by path, registering it before it runs.

    The registration is not tidiness: `@dataclass` resolves `KW_ONLY` by looking the
    defining module up in `sys.modules`, so a module executed without being registered
    raises `AttributeError: 'NoneType' object has no attribute '__dict__'` from inside
    `dataclasses` — a traceback that says nothing at all about the import that caused it.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


load_loop = _load("load_loop", REPO / "scripts" / "load_loop.py")


# --- the three outcomes ---------------------------------------------------------------------


@pytest.mark.parametrize("status", [200, 201, 299])
def test_a_two_hundred_is_a_served_request(status: int) -> None:
    assert load_loop.classify(status=status, error_source=None) == load_loop.OK


@pytest.mark.parametrize("status", [402, 429])
def test_the_gateways_own_refusal_is_shed_and_not_a_drop(status: int) -> None:
    """A 402 means a tenant is over its cap (H-032) and a 429 means over its limit
    (H-038). Both are the product doing its job under load, which is the *opposite* of an
    availability failure — and a rolling-upgrade report that counted them would say a
    budget gate was an outage."""
    assert load_loop.classify(status=status, error_source="gateway") == load_loop.SHED


@pytest.mark.parametrize("status", [402, 429])
def test_the_same_status_without_the_gateways_marker_is_a_drop(status: int) -> None:
    """The asymmetry that makes a zero mean something: `shed` needs positive evidence that
    the gateway meant it. H-038 built three independent markers for exactly this question
    and made them trustworthy by stripping the whole `x-headroom-*` namespace from every
    upstream response — so an absent marker really does mean "not ours"."""
    assert load_loop.classify(status=status, error_source=None) == load_loop.DROPPED
    assert load_loop.classify(status=status, error_source="upstream") == load_loop.DROPPED


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_a_five_hundred_is_always_a_drop(status: int) -> None:
    """§P8.H3 publishes "zero caller-visible 5xx at every intensity". A 5xx during a
    rolling upgrade is the thing this loop exists to catch, and it is never excused."""
    assert load_loop.classify(status=status, error_source="gateway") == load_loop.DROPPED


def test_no_answer_at_all_is_a_drop() -> None:
    """The shape a dropped request really has across a load balancer: not a status code,
    but a connection that went nowhere. A classifier written around statuses forgets this
    case, and it is the majority of what a bad rollout produces."""
    assert load_loop.classify(status=None, error_source=None) == load_loop.DROPPED
    assert (
        load_loop.classify(status=None, error_source=None, transport_error="ConnectError")
        == load_loop.DROPPED
    )


def test_a_two_hundred_whose_stream_was_cut_is_a_drop() -> None:
    """The status line is spent before a mid-stream fault, so HTTP says 200 and the caller
    got half an answer. H-008 makes the gateway say so with a terminal error event; this is
    the loop reading it. A run that scored these as successes would report zero drops
    through exactly the failure a rolling upgrade of a streaming proxy causes."""
    assert load_loop.classify(status=200, error_source=None, stream_complete=False) == (
        load_loop.DROPPED
    )
    assert load_loop.classify(status=200, error_source=None, stream_complete=True) == load_loop.OK


def test_the_non_streamed_case_is_not_confused_with_an_incomplete_stream() -> None:
    """`None` means "not a streamed request", and must never be read as `False`."""
    assert load_loop.classify(status=200, error_source=None, stream_complete=None) == load_loop.OK


# --- the gap metric ---------------------------------------------------------------------------


def test_the_gap_metric_counts_the_head_and_the_tail_of_the_window() -> None:
    """A rollout that dropped nothing but was unreachable for nine seconds has an error
    count of zero and is still an outage. The gap has to include the stretch before the
    first success and after the last one, or a run that never recovered reports a small
    number."""
    tally = load_loop.Tally()
    tally.started_at = 100.0
    tally.ok_times = [101.0, 102.0, 110.0, 111.0]
    tally.finished_at = 112.0
    assert tally.max_gap_ms() == pytest.approx(8000.0)

    # Nothing ever succeeded: the gap is the whole window, not zero.
    empty = load_loop.Tally()
    empty.started_at = 100.0
    empty.finished_at = 105.0
    assert empty.max_gap_ms() == pytest.approx(5000.0)

    # The tail counts: a loop whose last success was eight seconds before the end did not
    # recover, and a summary that only looked between successes would not say so.
    trailing = load_loop.Tally()
    trailing.started_at = 100.0
    trailing.ok_times = [100.5, 101.0]
    trailing.finished_at = 109.0
    assert trailing.max_gap_ms() == pytest.approx(8000.0)


def test_the_summary_reports_every_outcome_and_bounds_the_incident_list() -> None:
    """The counts stay exact however badly a run goes; only the transcript is bounded. A
    loop failing continuously should print a summary, not a novel."""
    tally = load_loop.Tally()
    for index in range(500):
        tally.record(load_loop.DROPPED, 503, 5.0, 100.0 + index, "no healthy upstream")
    tally.record(load_loop.OK, 200, 4.0, 700.0, "")
    tally.record(load_loop.SHED, 402, 3.0, 701.0, "budget_exceeded")
    tally.finished_at = 702.0

    summary = tally.summary("upgrade")
    assert summary["requests"] == 502
    assert summary["dropped"] == 500
    assert summary["ok"] == 1
    assert summary["shed"] == 1
    assert summary["by_status"] == {"200": 1, "402": 1, "503": 500}
    assert len(summary["incidents"]) == 200, "the transcript is bounded and the counts are not"
    assert summary["label"] == "upgrade"


def test_a_transport_failure_is_counted_under_a_name_a_reader_recognises() -> None:
    """`by_status` has no status to key a connection reset under, and `null` in an evidence
    file is a question rather than an answer."""
    tally = load_loop.Tally()
    tally.record(load_loop.DROPPED, None, 15000.0, 100.0, "ReadTimeout")
    assert tally.summary("x")["by_status"] == {"transport": 1}


# --- the script's own promises ------------------------------------------------------------------


def test_the_loop_spends_nothing() -> None:
    """Every request goes to a `mock-` model, which the shipped routing table sends to the
    MockProvider chain. That is the correct instrument as well as the free one: what a
    rolling upgrade can break is the gateway's availability, and a provider's own latency
    in the middle of the measurement is somebody else's number."""
    assert load_loop.MODEL.startswith("mock-")
    routing = (REPO / "config" / "routing.yaml").read_text(encoding="utf-8")
    assert 'prefix: "mock-"' in routing


# --- the key, before the first request --------------------------------------------------------


@pytest.mark.parametrize("key", ["", "   ", "\t\n"])
def test_a_key_that_is_not_a_key_is_refused_before_anything_is_measured(key: str) -> None:
    """H-095, and the run that paid for it: 8122 requests, 8122 dropped, every one of them
    `Illegal header value b'Bearer '` — a fresh terminal in which `$MOCK_KEY` had never
    been exported. Client-side, nothing reached the cluster, nothing was harmed. What it
    produced was ten minutes and a summary that read exactly like a total outage.

    `parser.error` exits 2, which is neither the 0 of a clean run nor the 1 of a real
    drop: a script that shells this out can tell "I asked wrong" from "it broke".

    `--duration-s` is bounded only so that *removing the guard* makes this test fail
    rather than hang: the default is 0, which means "until Ctrl-C", and that is exactly
    what an unguarded empty key gets you — found by sabotaging this guard and watching
    the run never come back.
    """
    with pytest.raises(SystemExit) as raised:
        load_loop.main(
            ["--base-url", "http://gateway.invalid", "--key", key, "--duration-s", "0.1"]
        )

    assert raised.value.code == 2


def test_a_real_key_survives_the_check_with_its_whitespace_trimmed() -> None:
    """A key pasted with a trailing newline is a key, and must not become a refusal — the
    guard is against nothing at all, not against clumsy copying."""
    assert load_loop.checked_key("  hk_abc123\n") == "hk_abc123"


def test_the_refusal_says_which_variable_and_where_to_get_it() -> None:
    """An error that only says "empty" sends an operator to the flag rather than to the
    export they skipped, at the one moment they are already under time pressure."""
    with pytest.raises(ValueError) as raised:
        load_loop.checked_key("")

    message = str(raised.value)
    assert "Illegal header value" in message, "the symptom they will have already seen"
    assert "deploy/k8s/README.md" in message, "and where the key is minted"


def test_the_marker_the_loop_reads_is_the_one_the_gateway_writes() -> None:
    """Rename the header in `headroom/api/proxy.py` and this loop silently reclassifies
    every deliberate refusal as a dropped request — turning a clean rollout into a failed
    one, with nothing red anywhere. The same silent-coupling failure the Phase 9 alarms
    have, and the same fix."""
    proxy = (REPO / "headroom" / "api" / "proxy.py").read_text(encoding="utf-8")
    assert f'ERROR_SOURCE_HEADER = "{load_loop.ERROR_SOURCE_HEADER}"' in proxy
    errors = (REPO / "headroom" / "core" / "errors.py").read_text(encoding="utf-8")
    assert f'SOURCE_GATEWAY: Final = "{load_loop.SOURCE_GATEWAY}"' in errors


def test_the_terminal_marker_is_the_one_the_anthropic_dialect_really_sends() -> None:
    """`message_stop` is what H-008 defines completion as on this dialect, and what
    `scripts/chaos_smoke.py` already counts on. A loop looking for a marker nobody emits
    would score every streamed request as a drop."""
    chaos = (REPO / "scripts" / "chaos_smoke.py").read_text(encoding="utf-8")
    assert "message_stop" in chaos
    assert load_loop.TERMINAL_MARKER == "event: message_stop"
