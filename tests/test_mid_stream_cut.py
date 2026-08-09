"""The mid-stream cut: a stream that dies after N chunks must never look complete.

**This file was written before the happy path** — BUILD_PLAN's risk register item 1
makes it the shape the passthrough design has to satisfy, not a case bolted on after
the fact. If a gateway forwards three chunks of an answer and the upstream then dies,
the caller has two possible experiences:

1. The stream just *stops*. The client's SSE decoder sees a clean EOF, the SDK
   returns whatever accumulated, and a truncated answer is indistinguishable from a
   complete one. It gets cached (P5), billed (P3), and acted on. This is the failure
   mode the whole phase exists to prevent.
2. The stream ends with a **terminal error event** in the caller's own dialect. The
   SDK raises. Nothing downstream mistakes the fragment for an answer.

Only (2) is acceptable, and it has to hold for *both* ways a stream can end early:
an exception mid-iteration (the connection dropped) and a clean end that never sent
the dialect's terminal marker (the upstream returned EOF mid-answer). Both are
covered below, for both dialects.
"""

import json

from headroom.core.sse import iter_sse_events
from headroom.providers.mock import MockScript

from .support.fixtures import anthropic_request, openai_request
from .support.harness import GatewayHarness


def _sse_events(raw: bytes) -> list[tuple[str | None, str]]:
    """(event-name, data) pairs, in order, as the client's decoder would see them."""
    return [(event.event, event.data) for event in iter_sse_events(raw)]


# --------------------------------------------------------------------------------
# Anthropic dialect
# --------------------------------------------------------------------------------


async def test_anthropic_cut_mid_stream_ends_in_a_terminal_error_event(
    gateway: GatewayHarness,
) -> None:
    """Three chunks, then the upstream connection dies."""
    gateway.book.set(
        "cut",
        MockScript.anthropic_stream("The quick brown fox jumps", cut_after_chunks=3),
    )

    response = await gateway.post("/v1/messages", anthropic_request(stream=True), script="cut")

    assert response.status_code == 200
    events = _sse_events(response.content)

    # The caller is told, in the dialect's own terms.
    assert events[-1][0] == "error", f"stream did not end in an error event: {events}"
    payload = json.loads(events[-1][1])
    assert payload["type"] == "error"
    assert payload["error"]["type"] == "api_error"
    assert payload["headroom"]["reason"] == "upstream_stream_cut"

    # And is never told the message finished.
    assert "message_stop" not in [name for name, _ in events]


async def test_anthropic_stream_ending_without_message_stop_is_also_an_error(
    gateway: GatewayHarness,
) -> None:
    """The quieter failure: upstream returns a clean EOF mid-answer.

    No exception is raised anywhere — the byte stream simply ends. A passthrough that
    only guards against *exceptions* forwards this as a complete response.
    """
    chunks = MockScript.anthropic_stream("Half an answer").chunks
    truncated = [c for c in chunks if b"message_stop" not in c]
    gateway.book.set("truncated", MockScript(chunks=truncated))

    response = await gateway.post(
        "/v1/messages", anthropic_request(stream=True), script="truncated"
    )

    events = _sse_events(response.content)
    assert events[-1][0] == "error"
    assert json.loads(events[-1][1])["headroom"]["reason"] == "upstream_stream_incomplete"


# --------------------------------------------------------------------------------
# OpenAI dialect
# --------------------------------------------------------------------------------


async def test_openai_cut_mid_stream_ends_in_a_terminal_error_frame(
    gateway: GatewayHarness,
) -> None:
    """OpenAI-dialect clients learn of failure from a `data:` frame carrying `error`.

    `openai-python` raises `APIError` on a data frame with an `error` key, so this is
    the wire shape that actually makes an SDK raise rather than return a fragment.
    """
    gateway.book.set(
        "cut", MockScript.openai_stream("The quick brown fox jumps", cut_after_chunks=3)
    )

    response = await gateway.post("/v1/chat/completions", openai_request(stream=True), script="cut")

    assert response.status_code == 200
    events = _sse_events(response.content)

    payload = json.loads(events[-1][1])
    assert payload["error"]["type"] == "api_error"
    assert payload["headroom"]["reason"] == "upstream_stream_cut"

    # `[DONE]` is the OpenAI dialect's "the answer is complete" marker. Forwarding one
    # the upstream never sent is exactly the lie this test exists to prevent.
    assert "[DONE]" not in [data for _, data in events]


async def test_openai_stream_ending_without_done_is_also_an_error(gateway: GatewayHarness) -> None:
    chunks = MockScript.openai_stream("Half an answer").chunks
    truncated = [c for c in chunks if b"[DONE]" not in c and b"finish_reason" not in c]
    gateway.book.set("truncated", MockScript(chunks=truncated))

    response = await gateway.post(
        "/v1/chat/completions", openai_request(stream=True), script="truncated"
    )

    events = _sse_events(response.content)
    assert json.loads(events[-1][1])["headroom"]["reason"] == "upstream_stream_incomplete"


# --------------------------------------------------------------------------------
# What the caller keeps
# --------------------------------------------------------------------------------


async def test_the_partial_content_before_the_cut_is_still_delivered_verbatim(
    gateway: GatewayHarness,
) -> None:
    """A cut is not a reason to discard what already arrived.

    The bytes the upstream did send reach the client unchanged; only the terminal
    error event is appended. P5 will refuse to *cache* this response — that is a
    separate rule (invariant 6) enforced in a separate phase — but the caller who is
    watching tokens appear still gets the tokens that existed.
    """
    script = MockScript.anthropic_stream("The quick brown fox jumps", cut_after_chunks=3)
    gateway.book.set("cut", script)

    response = await gateway.post("/v1/messages", anthropic_request(stream=True), script="cut")

    emitted = b"".join(script.chunks[:3])
    assert response.content.startswith(emitted)


async def test_the_request_context_records_the_cut(gateway: GatewayHarness) -> None:
    """P3 meters and P6 counts failover hops off this; both need the truth here."""
    gateway.book.set("cut", MockScript.anthropic_stream("Fox", cut_after_chunks=2))

    await gateway.post("/v1/messages", anthropic_request(stream=True), script="cut")

    ctx = gateway.last_context()
    assert ctx.outcome == "upstream_stream_cut"
    assert ctx.stream is True
    assert ctx.first_token_out_at is not None, "bytes reached the client before the cut"
    assert ctx.completed_at is not None, "a cut still completes the request"
