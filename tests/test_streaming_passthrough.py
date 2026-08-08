"""The streaming gate: what the mock emitted is what the client receives.

Assumption **A4**, with its nuance intact: *SSE passthrough preserves event order and
content; chunk boundaries are **not** guaranteed identical, and the gate asserts
content/event equality, never byte-identical chunking.* So every test here compares one
of two things — the event sequence, or the reassembled content — and the pathological
cases exist to prove the claim survives boundaries no reasonable network would produce.

The nastiest case is ``chunk_size=1``: the whole stream re-chopped into single bytes,
which splits SSE frames mid-field, JSON mid-token, and multi-byte UTF-8 mid-character.
A proxy that decodes, re-frames, or normalizes per chunk cannot survive it; one that
forwards bytes and only *observes* them does so without trying.
"""

from __future__ import annotations

import pytest

from headroom.providers.mock import MockScript

from .support.fixtures import anthropic_request, openai_request
from .support.harness import GatewayHarness
from .support.streams import anthropic_text, event_pairs, openai_text

REPLY = "The quick brown fox jumps over the lazy dog"

#: Multi-byte characters, chosen so a byte-level split lands inside a code point.
#: "Björk 🎧" is 4 bytes of UTF-8 in two separate characters — an emoji split down the
#: middle is the exact failure a per-chunk `.decode()` produces, and it is silent.
UNICODE_REPLY = "Björk's 🎧 catalogue — naïve, résumé, 日本語, and 𝄞 too"


async def test_anthropic_stream_arrives_intact(gateway: GatewayHarness) -> None:
    script = gateway.book.set("ok", MockScript.anthropic_stream(REPLY))

    response = await gateway.post("/v1/messages", anthropic_request(stream=True), script="ok")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert event_pairs(response.content) == event_pairs(b"".join(script.chunks))
    assert anthropic_text(response.content) == REPLY


async def test_openai_stream_arrives_intact(gateway: GatewayHarness) -> None:
    script = gateway.book.set("ok", MockScript.openai_stream(REPLY))

    response = await gateway.post("/v1/chat/completions", openai_request(stream=True), script="ok")

    assert response.status_code == 200
    assert event_pairs(response.content) == event_pairs(b"".join(script.chunks))
    assert openai_text(response.content) == REPLY
    assert response.content.rstrip().endswith(b"data: [DONE]")


@pytest.mark.parametrize("chunk_size", [1, 2, 3, 7, 13, 512])
async def test_anthropic_survives_pathological_chunk_boundaries(
    gateway: GatewayHarness, chunk_size: int
) -> None:
    """The same stream, re-chopped every ``chunk_size`` bytes, must reassemble exactly.

    ``chunk_size=1`` is the mean one. At that size every SSE frame is split across
    dozens of chunks, every JSON token is fragmented, and every multi-byte character is
    torn apart. The client-side reassembly still has to be byte-identical.
    """
    whole = b"".join(MockScript.anthropic_stream(UNICODE_REPLY).chunks)
    gateway.book.set("chopped", MockScript.anthropic_stream(UNICODE_REPLY, chunk_size=chunk_size))

    response = await gateway.post("/v1/messages", anthropic_request(stream=True), script="chopped")

    assert response.content == whole, f"stream corrupted at chunk_size={chunk_size}"
    assert anthropic_text(response.content) == UNICODE_REPLY


@pytest.mark.parametrize("chunk_size", [1, 5, 64])
async def test_openai_survives_pathological_chunk_boundaries(
    gateway: GatewayHarness, chunk_size: int
) -> None:
    whole = b"".join(MockScript.openai_stream(UNICODE_REPLY).chunks)
    gateway.book.set("chopped", MockScript.openai_stream(UNICODE_REPLY, chunk_size=chunk_size))

    response = await gateway.post(
        "/v1/chat/completions", openai_request(stream=True), script="chopped"
    )

    assert response.content == whole, f"stream corrupted at chunk_size={chunk_size}"
    assert openai_text(response.content) == UNICODE_REPLY


async def test_terminal_marker_split_across_chunks_still_counts_as_complete(
    gateway: GatewayHarness,
) -> None:
    """Completion detection must survive the boundary too.

    A one-byte-at-a-time stream delivers ``message_stop`` across dozens of chunks. If
    the observer only looked at whole chunks it would never see the terminal event, and
    the gateway would append a spurious error to a perfectly good response — turning
    the truncation guard into a source of false alarms.
    """
    gateway.book.set("chopped", MockScript.anthropic_stream(REPLY, chunk_size=1))

    response = await gateway.post("/v1/messages", anthropic_request(stream=True), script="chopped")

    names = [name for name, _ in event_pairs(response.content)]
    assert names[-1] == "message_stop"
    assert "error" not in names
    assert gateway.last_context().outcome == "ok"


async def test_keepalive_comments_and_pings_do_not_confuse_completion(
    gateway: GatewayHarness,
) -> None:
    """A stream padded with SSE comments still ends exactly once, in the right place."""
    chunks = list(MockScript.anthropic_stream(REPLY).chunks)
    padded: list[bytes] = []
    for chunk in chunks:
        padded.extend((b": keep-alive\n\n", chunk))
    gateway.book.set("padded", MockScript(chunks=padded))

    response = await gateway.post("/v1/messages", anthropic_request(stream=True), script="padded")

    names = [name for name, _ in event_pairs(response.content)]
    assert "error" not in names
    assert names[-1] == "message_stop"
    assert anthropic_text(response.content) == REPLY


async def test_an_empty_chunk_is_not_mistaken_for_end_of_stream(gateway: GatewayHarness) -> None:
    """Zero-length reads happen; they mean nothing and must not truncate anything."""
    chunks = list(MockScript.anthropic_stream(REPLY).chunks)
    with_blanks = [b"", *chunks[:2], b"", *chunks[2:], b""]
    gateway.book.set("blanks", MockScript(chunks=with_blanks))

    response = await gateway.post("/v1/messages", anthropic_request(stream=True), script="blanks")

    assert anthropic_text(response.content) == REPLY
    assert gateway.last_context().outcome == "ok"
