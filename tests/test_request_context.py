"""The request context exists, is populated, and is ordered — on *every* path.

BUILD_PLAN makes this a Phase 1 deliverable so that Phase 3's metering and Phase 7's
tracing never have to retrofit it. Retrofitted instrumentation is always partial in the
same way: the happy path gets covered, and the paths nobody pictured — the timeout, the
mid-stream cut, the caller who hung up — stay dark. Those are precisely the paths
Headroom's experiments are about, so this file walks all of them and checks the same
invariant each time.

The invariant is ordering: ``received ≤ first upstream byte ≤ first token out ≤
completed``, for whichever marks a given path reaches. It holds by construction because
every mark comes from ``time.perf_counter()`` — a monotonic clock that cannot step
backwards under NTP and turn a latency reading negative — but "by construction" is a
claim, and this is the file that checks it.
"""

from __future__ import annotations

from itertools import pairwise

from headroom.core.context import RequestContext
from headroom.providers.mock import MockScript

from .support.fixtures import anthropic_request, openai_request
from .support.harness import GatewayHarness


def assert_ordered(ctx: RequestContext) -> None:
    """Every timing that exists is in order, and the request has finished."""
    assert ctx.completed_at is not None, "the context was never closed"
    marks = [
        ("received", ctx.received_at),
        ("first_upstream_byte", ctx.first_upstream_byte_at),
        ("first_token_out", ctx.first_token_out_at),
        ("completed", ctx.completed_at),
    ]
    present = [(name, value) for name, value in marks if value is not None]
    for (earlier_name, earlier), (later_name, later) in pairwise(present):
        assert earlier <= later, f"{earlier_name} came after {later_name}"


async def test_a_streamed_request_records_every_stage(gateway: GatewayHarness) -> None:
    gateway.book.set("ok", MockScript.anthropic_stream("The quick brown fox"))

    await gateway.post("/v1/messages", anthropic_request(stream=True), script="ok")

    ctx = gateway.last_context()
    assert_ordered(ctx)
    assert ctx.first_upstream_byte_at is not None
    assert ctx.first_token_out_at is not None
    assert ctx.outcome == "ok"
    assert ctx.status_code == 200
    assert ctx.upstream_status == 200

    # The fields Phase 3's ledger row is made of.
    assert ctx.request_id.startswith("hr_")
    assert ctx.dialect == "anthropic"
    assert ctx.route == "/v1/messages"
    assert ctx.model == "mock-model-1"
    assert ctx.provider == "mock"
    assert ctx.stream is True
    # Tenancy is a Phase 2 concern; the field exists now so Phase 2 only has to fill it.
    assert ctx.tenant_id is None


async def test_a_non_streamed_request_records_every_stage(gateway: GatewayHarness) -> None:
    gateway.book.set("ok", MockScript.openai_completion("done"))

    await gateway.post("/v1/chat/completions", openai_request(), script="ok")

    ctx = gateway.last_context()
    assert_ordered(ctx)
    assert ctx.dialect == "openai"
    assert ctx.stream is False
    assert ctx.outcome == "ok"


async def test_derived_durations_are_non_negative_and_consistent(gateway: GatewayHarness) -> None:
    """Phase 8's H2 reports a per-request overhead number computed from these."""
    gateway.book.set("ok", MockScript.anthropic_stream("The quick brown fox"))

    await gateway.post("/v1/messages", anthropic_request(stream=True), script="ok")

    ctx = gateway.last_context()
    assert ctx.upstream_latency_ms is not None and ctx.upstream_latency_ms >= 0
    assert ctx.time_to_first_token_ms is not None and ctx.time_to_first_token_ms >= 0
    assert ctx.passthrough_overhead_ms is not None and ctx.passthrough_overhead_ms >= 0
    assert ctx.total_ms is not None and ctx.total_ms >= ctx.time_to_first_token_ms


async def test_an_upstream_error_is_recorded_with_its_status(gateway: GatewayHarness) -> None:
    gateway.book.set("throttled", MockScript.error(429, retry_after="5"))

    await gateway.post("/v1/messages", anthropic_request(), script="throttled")

    ctx = gateway.last_context()
    assert_ordered(ctx)
    assert ctx.outcome == "upstream_error"
    assert ctx.status_code == 429
    assert ctx.upstream_status == 429
    assert ctx.error_source == "upstream"
    assert ctx.error_reason == "upstream_status_429"


async def test_a_timeout_is_recorded_without_an_upstream_byte(gateway: GatewayHarness) -> None:
    """No upstream byte ever arrived, so that mark stays ``None`` — and must.

    A mark that gets filled in with a plausible value on a path where the event never
    happened is worse than a missing one: it produces a latency figure for a response
    that does not exist, and Phase 8 would publish it.
    """
    gateway.book.set("slow", MockScript.timeout())

    await gateway.post("/v1/messages", anthropic_request(), script="slow")

    ctx = gateway.last_context()
    assert_ordered(ctx)
    assert ctx.first_upstream_byte_at is None
    assert ctx.upstream_latency_ms is None
    assert ctx.passthrough_overhead_ms is None
    assert ctx.first_token_out_at is not None, "the error response was still sent"
    assert ctx.outcome == "upstream_timeout"
    assert ctx.status_code == 504


async def test_a_mid_stream_cut_is_recorded_with_both_byte_marks(gateway: GatewayHarness) -> None:
    """Bytes flowed before the cut, so both marks exist — and the outcome is honest."""
    gateway.book.set("cut", MockScript.anthropic_stream("The quick brown fox", cut_after_chunks=3))

    await gateway.post("/v1/messages", anthropic_request(stream=True), script="cut")

    ctx = gateway.last_context()
    assert_ordered(ctx)
    assert ctx.first_upstream_byte_at is not None
    assert ctx.first_token_out_at is not None
    assert ctx.outcome == "upstream_stream_cut"
    assert ctx.error_source == "upstream"
    # The HTTP status was 200 and stays 200: it was sent before anything went wrong.
    # That is exactly why the failure has to be reported inside the stream.
    assert ctx.status_code == 200


async def test_a_request_that_never_reaches_a_provider_is_still_recorded(
    gateway: GatewayHarness,
) -> None:
    await gateway.post("/v1/messages", anthropic_request(model="gpt-4o"))

    ctx = gateway.last_context()
    assert_ordered(ctx)
    assert ctx.provider is None
    assert ctx.upstream_status is None
    assert ctx.model == "gpt-4o"
    assert ctx.outcome == "model_not_routed"
    assert ctx.error_source == "gateway"


async def test_a_malformed_body_is_still_recorded(gateway: GatewayHarness) -> None:
    """The earliest possible failure still produces a complete, ordered context."""
    await gateway.post("/v1/messages", b"{oh no")

    ctx = gateway.last_context()
    assert_ordered(ctx)
    assert ctx.model is None
    assert ctx.dialect == "anthropic"
    assert ctx.outcome == "invalid_request_body"


async def test_every_request_gets_its_own_context(gateway: GatewayHarness) -> None:
    gateway.book.set("ok", MockScript.anthropic_message("hi"))

    await gateway.post("/v1/messages", anthropic_request(), script="ok")
    await gateway.post("/v1/messages", anthropic_request(), script="ok")

    ids = [ctx.request_id for ctx in gateway.recorder.contexts]
    assert len(ids) == len(set(ids)) == 2


async def test_the_log_shape_is_complete(gateway: GatewayHarness) -> None:
    """What one request writes to the log — and, from Phase 3, to the ledger."""
    gateway.book.set("ok", MockScript.anthropic_stream("hello"))

    await gateway.post("/v1/messages", anthropic_request(stream=True), script="ok")

    fields = gateway.last_context().as_log_fields()
    assert set(fields) == {
        "request_id",
        "route",
        "dialect",
        "tenant_id",
        "model",
        "provider",
        "stream",
        "outcome",
        "status",
        "upstream_status",
        "error_source",
        "error_reason",
        "upstream_latency_ms",
        "ttft_ms",
        "passthrough_overhead_ms",
        "total_ms",
    }
    assert fields["outcome"] == "ok"
    assert fields["ttft_ms"] is not None


async def test_a_recorded_outcome_is_never_overwritten_by_the_backstop(
    gateway: GatewayHarness,
) -> None:
    """The middleware closes any context the proxy left open — without editing history.

    Without first-call-wins, the backstop would relabel a diagnosed
    ``upstream_stream_cut`` as a generic outcome, and the ledger would lose the one
    fact worth knowing about that request.
    """
    gateway.book.set("cut", MockScript.anthropic_stream("fox", cut_after_chunks=2))

    await gateway.post("/v1/messages", anthropic_request(stream=True), script="cut")

    assert gateway.last_context().outcome == "upstream_stream_cut"
