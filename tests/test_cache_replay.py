"""Replay fidelity: a hit must be indistinguishable from the response it replays.

The live path's bar is assumption A4 — *content and event equality, never identical
chunking*. A replay clears it by a wider margin than the live path can, because the entry
holds the upstream's bytes and yields exactly those: **byte-identical**, which is a strict
superset of content equality.

That is not an accident of implementation, it is the reason transport is part of the key
(H-043). The alternative design — one canonical entry rendered into whichever transport
the caller asked for — needs an assembler that rebuilds a message from frames and a
synthesiser that emits frames no provider sent. H-016 proved by sabotage that a single
re-encode is invisible to every test that does not compare bytes; a cache is a worse place
to start rebuilding payloads than the proxy, because its output is served forever.

So the tests here are the A4 suite pointed at a cached answer, plus the H-016 fixtures —
the ones carrying literal 2-, 3-, and 4-byte UTF-8 sequences — because those are the only
fixtures in the repo that can *see* a re-encode.
"""

from __future__ import annotations

import json

import pytest

from headroom.cache.replay import (
    CACHE_AGE_HEADER,
    CACHE_SOURCE_HEADER,
    CACHE_STATUS_HEADER,
)
from headroom.core.cache import CACHE_EXACT, DISPOSITION_HIT_EXACT
from headroom.core.sse import iter_sse_events
from headroom.providers import mock_scripts
from headroom.providers.mock import MockScript
from tests.support.fixtures import anthropic_request, openai_request
from tests.support.harness import GatewayHarness

TEXT = "Björk's 2024-Q3 rate — 18.5% — held steady 𝄞"


def events(raw: bytes) -> list[tuple[str | None, str]]:
    return [(event.event, event.data) for event in iter_sse_events(raw)]


# --- non-streamed ----------------------------------------------------------------------


async def test_a_replayed_body_is_byte_identical(gateway: GatewayHarness) -> None:
    await gateway.set_cache(CACHE_EXACT)
    gateway.book.set("ok", MockScript.anthropic_message(TEXT))

    live = await gateway.post("/v1/messages", anthropic_request(), script="ok")
    replayed = await gateway.post("/v1/messages", anthropic_request(), script="ok")

    assert replayed.headers[CACHE_STATUS_HEADER] == DISPOSITION_HIT_EXACT
    assert replayed.content == live.content
    assert replayed.status_code == live.status_code == 200
    assert replayed.headers["content-type"].startswith("application/json")


async def test_a_replayed_body_survives_a_reasoning_payload_byte_for_byte(
    gateway: GatewayHarness,
) -> None:
    """The H-016 fixture: the *same* character in both literal and escaped form, in one
    string, so a normalisation is caught whichever direction it runs."""
    await gateway.set_cache(CACHE_EXACT)
    gateway.book.set("reason", MockScript.openai_reasoning_completion())

    live = await gateway.post("/v1/chat/completions", openai_request(), script="reason")
    replayed = await gateway.post("/v1/chat/completions", openai_request(), script="reason")

    assert replayed.content == live.content == mock_scripts.openai_reasoning_body()


# --- streamed ---------------------------------------------------------------------------


async def test_a_replayed_stream_is_byte_identical(gateway: GatewayHarness) -> None:
    await gateway.set_cache(CACHE_EXACT)
    gateway.book.set("ok", MockScript.anthropic_stream(TEXT))

    live = await gateway.post("/v1/messages", anthropic_request(stream=True), script="ok")
    replayed = await gateway.post("/v1/messages", anthropic_request(stream=True), script="ok")

    assert replayed.headers[CACHE_STATUS_HEADER] == DISPOSITION_HIT_EXACT
    assert replayed.content == live.content


async def test_a_replayed_stream_has_the_same_event_sequence(
    gateway: GatewayHarness,
) -> None:
    """The A4 assertion itself: event names and data, in order, exactly as the client's
    decoder sees them."""
    await gateway.set_cache(CACHE_EXACT)
    gateway.book.set("ok", MockScript.anthropic_stream(TEXT))

    live = await gateway.post("/v1/messages", anthropic_request(stream=True), script="ok")
    replayed = await gateway.post("/v1/messages", anthropic_request(stream=True), script="ok")

    assert events(replayed.content) == events(live.content)
    assert events(replayed.content)[-1][0] == "message_stop"


async def test_a_replayed_openai_stream_still_ends_in_done(
    gateway: GatewayHarness,
) -> None:
    """``[DONE]`` is the claim that makes a fragment look finished, so a replay that lost
    it would turn every cached answer into a truncation — and one that gained it where
    there was none would be worse."""
    await gateway.set_cache(CACHE_EXACT)
    gateway.book.set("ok", MockScript.openai_stream(TEXT))

    live = await gateway.post("/v1/chat/completions", openai_request(stream=True), script="ok")
    replayed = await gateway.post("/v1/chat/completions", openai_request(stream=True), script="ok")

    assert replayed.content == live.content
    assert events(replayed.content)[-1][1].strip() == "[DONE]"


@pytest.mark.parametrize("chunk_size", [1, 3, 13, 512])
async def test_a_stream_recorded_from_awful_chunking_replays_intact(
    gateway: GatewayHarness, chunk_size: int
) -> None:
    """The stored copy is assembled from whatever boundaries the network produced —
    including ones that split a UTF-8 character down the middle — and the replay is still
    byte-identical. Nothing in the recording path decodes anything, which is why."""
    await gateway.set_cache(CACHE_EXACT)
    gateway.book.set("ok", MockScript.anthropic_stream(TEXT, chunk_size=chunk_size))

    live = await gateway.post("/v1/messages", anthropic_request(stream=True), script="ok")
    replayed = await gateway.post("/v1/messages", anthropic_request(stream=True), script="ok")

    assert replayed.content == live.content
    assert events(replayed.content) == events(live.content)


async def test_a_replayed_reasoning_stream_keeps_its_chain_of_thought(
    gateway: GatewayHarness,
) -> None:
    """What a cached replay of a reasoning model *means* (H-044), demonstrated.

    The caller receives the **original** chain of thought, not a fresh one — the deltas
    reach them byte for byte, because a replay cannot re-reason. For an exact hit that is
    exactly right: it is the same question. It is also why such a response is stored
    exact-only and never embedded, so no *paraphrase* can ever be answered with reasoning
    performed on a different question's text.
    """
    await gateway.set_cache(CACHE_EXACT)
    gateway.book.set("reason", MockScript.openai_reasoning_stream())

    live = await gateway.post("/v1/chat/completions", openai_request(stream=True), script="reason")
    replayed = await gateway.post(
        "/v1/chat/completions", openai_request(stream=True), script="reason"
    )

    assert replayed.content == live.content
    assert b"reasoning_content" in replayed.content
    # Stored, but stored exact-only.
    stats = await gateway.cache.store.stats(gateway.tenant.id)
    assert stats.entries == 1
    assert stats.semantic_entries == 0


# --- what a replay does and does not carry -------------------------------------------------


async def test_a_replay_does_not_forward_the_original_upstream_headers(
    gateway: GatewayHarness,
) -> None:
    """A cached ``retry-after`` describes a call that did not happen (H-043).

    The upstream's own rate-limit and request-id headers cross untouched on the *live*
    path, because they are the signal a caller needs to behave well. Replaying them would
    be reporting somebody else's rate limit as this request's.
    """
    await gateway.set_cache(CACHE_EXACT)
    gateway.book.set(
        "ok",
        MockScript(
            body=mock_scripts.anthropic_message_body("hello"),
            headers={"retry-after": "42", "anthropic-ratelimit-requests-remaining": "7"},
        ),
    )

    live = await gateway.post("/v1/messages", anthropic_request(), script="ok")
    replayed = await gateway.post("/v1/messages", anthropic_request(), script="ok")

    assert live.headers["retry-after"] == "42"
    assert "retry-after" not in replayed.headers
    assert "anthropic-ratelimit-requests-remaining" not in replayed.headers


async def test_a_replay_carries_its_provenance_and_its_age(
    gateway: GatewayHarness,
) -> None:
    await gateway.set_cache(CACHE_EXACT)
    gateway.book.set("ok", MockScript.anthropic_message("hello"))

    await gateway.post("/v1/messages", anthropic_request(), script="ok")
    source_id = gateway.last_context().request_id
    replayed = await gateway.post("/v1/messages", anthropic_request(), script="ok")

    assert replayed.headers[CACHE_SOURCE_HEADER] == source_id
    assert int(replayed.headers[CACHE_AGE_HEADER]) >= 0


async def test_a_replayed_stream_still_gets_its_own_request_id(
    gateway: GatewayHarness,
) -> None:
    """Two requests, two ids: the replay is a new request that happens to reuse an old
    answer, and the ledger has to be able to tell them apart."""
    await gateway.set_cache(CACHE_EXACT)
    gateway.book.set("ok", MockScript.anthropic_stream("hello"))

    first = await gateway.post("/v1/messages", anthropic_request(stream=True), script="ok")
    second = await gateway.post("/v1/messages", anthropic_request(stream=True), script="ok")

    assert first.headers["x-headroom-request-id"] != second.headers["x-headroom-request-id"]
    assert second.headers[CACHE_SOURCE_HEADER] == first.headers["x-headroom-request-id"]


async def test_a_replayed_stream_records_an_ordered_context(
    gateway: GatewayHarness,
) -> None:
    """A request's end must never precede its first output byte — the ordering bug Phase 1
    found in its own error path, checked again on the one new exit this phase adds."""
    await gateway.set_cache(CACHE_EXACT)
    gateway.book.set("ok", MockScript.anthropic_stream("hello"))

    await gateway.post("/v1/messages", anthropic_request(stream=True), script="ok")
    await gateway.post("/v1/messages", anthropic_request(stream=True), script="ok")

    ctx = gateway.last_context()
    assert ctx.first_token_out_at is not None
    assert ctx.completed_at is not None
    assert ctx.received_at <= ctx.first_token_out_at <= ctx.completed_at
    # No upstream byte ever arrived, so the mark that measures the gap does not exist.
    assert ctx.first_upstream_byte_at is None
    assert ctx.passthrough_overhead_ms is None


async def test_a_replayed_stream_writes_its_ledger_row(gateway: GatewayHarness) -> None:
    """The metering runs *after* the bytes on the streaming path, so this is the assertion
    that the callback really fires rather than being dropped with the generator."""
    await gateway.set_cache(CACHE_EXACT)
    gateway.book.set("ok", MockScript.anthropic_stream("hello"))

    await gateway.post("/v1/messages", anthropic_request(stream=True), script="ok")
    await gateway.post("/v1/messages", anthropic_request(stream=True), script="ok")

    row = await gateway.ledger_row()
    assert row.cache_disposition == DISPOSITION_HIT_EXACT
    assert row.streamed is True
    assert row.stop_reason == "end_turn"


async def test_the_replayed_body_is_still_valid_json_the_sdk_would_accept(
    gateway: GatewayHarness,
) -> None:
    await gateway.set_cache(CACHE_EXACT)
    gateway.book.set("ok", MockScript.anthropic_message(TEXT))

    await gateway.post("/v1/messages", anthropic_request(), script="ok")
    replayed = await gateway.post("/v1/messages", anthropic_request(), script="ok")

    payload = json.loads(replayed.content)
    assert payload["type"] == "message"
    assert payload["content"][0]["text"] == TEXT
    assert payload["stop_reason"] == "end_turn"
