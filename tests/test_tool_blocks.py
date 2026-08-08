"""Assumption **A5**: tool blocks round-trip through the Anthropic dialect untouched.

From BUILD_PLAN's assumed-facts register: *"Tool-use blocks round-trip through
Anthropic-dialect passthrough untouched (Backline's agents are tool-heavy; H2 dies
without this)"* — verified at the Phase 1 gate, which is this file, and again at the
H2 pre-flight.

The stakes are specific. Phase 8's H2 points Backline's 133-question suite at Headroom
by overriding one base URL and changing nothing else. Backline's agents are tool-heavy,
and a tool round trip fails *silently* when it fails: a re-encoded ``tool_use.input``
still parses, still looks plausible in a transcript, and produces a subtly wrong answer
that the answer key scores as a miss. The experiment would report a gateway-overhead
regression that is really a serialization bug.

So fidelity is asserted at the **byte** level, in both directions, on payloads built to
break anything that re-serializes:

* keys in non-alphabetical order, so a dict round trip reorders them;
* a non-ASCII character as an escape (``\\u00f6``), so a re-encode with
  ``ensure_ascii=False`` emits it literally instead;
* an escaped quote inside a string, so a re-escape doubles the backslash;
* nested objects and arrays, so a "helpful" normalization has somewhere to be helpful.

Every one of those survives here for a structural reason, not a careful one: the proxy
reads the body as bytes and sends those same bytes. There is no code that could
re-encode them.
"""

from __future__ import annotations

import json

from headroom.providers.mock import MockScript
from headroom.providers.mock_scripts import (
    anthropic_tool_use_body,
    anthropic_tool_use_stream_chunks,
)

from .support.fixtures import (
    ANTHROPIC_TOOLS,
    anthropic_request,
    anthropic_tool_result_request,
)
from .support.harness import GatewayHarness
from .support.streams import event_pairs


async def test_a_tool_use_reply_reaches_the_client_byte_for_byte(gateway: GatewayHarness) -> None:
    """Turn one: the model asks for a tool. Every byte of the block survives."""
    expected = anthropic_tool_use_body()
    gateway.book.set("tool_use", MockScript(body=expected))

    response = await gateway.post(
        "/v1/messages",
        anthropic_request(tools=ANTHROPIC_TOOLS),
        script="tool_use",
    )

    assert response.status_code == 200
    assert response.content == expected, "the tool_use reply was altered in transit"

    # And it is still semantically what the SDK will parse, not merely equal bytes.
    block = json.loads(response.content)["content"][1]
    assert block["type"] == "tool_use"
    assert block["id"] == "toolu_mock_0001"
    assert block["input"]["artist"] == "Björk"
    assert block["input"]["filters"]["note"] == 'say "hi"'
    assert block["input"]["filters"]["regions"] == ["NA", "EU"]


async def test_a_tool_result_follow_up_reaches_the_provider_byte_for_byte(
    gateway: GatewayHarness,
) -> None:
    """Turn two: the caller sends the result back. The upstream gets exactly that.

    This is the direction that actually breaks agents. The follow-up replays the
    assistant's ``tool_use`` block verbatim alongside the ``tool_result``, and Anthropic
    matches them by ``tool_use_id``; a body the gateway re-encoded arrives with the
    right shape and the wrong bytes, and the failure surfaces several turns later as a
    confused model rather than as an error.
    """
    raw = json.dumps(anthropic_tool_result_request(), ensure_ascii=False).encode("utf-8")
    gateway.book.set("after_tool", MockScript.anthropic_message("Streams were 1,240,933."))

    response = await gateway.post("/v1/messages", raw, script="after_tool")

    assert response.status_code == 200
    forwarded = gateway.last_upstream_request().body
    assert forwarded == raw, "the tool_result turn was altered before reaching upstream"

    # Spot-check the parts a re-encode would quietly damage.
    sent = json.loads(forwarded)
    tool_use = sent["messages"][1]["content"][1]
    tool_result = sent["messages"][2]["content"][0]
    assert tool_use["id"] == tool_result["tool_use_id"] == "toolu_mock_0001"
    assert tool_use["input"]["filters"]["note"] == 'say "hi"'
    assert tool_result["content"] == '{"streams": 1240933, "note": "say \\"hi\\""}'
    assert sent["tools"] == ANTHROPIC_TOOLS


async def test_escaped_unicode_survives_exactly_as_written(gateway: GatewayHarness) -> None:
    """``\\u00f6`` stays ``\\u00f6``; a literal ``ö`` stays a literal ``ö``.

    Both encode the same character and neither is wrong — but they are different bytes,
    and a gateway that swaps one for the other has re-serialized the payload. That is
    the tell this test exists to catch, because it is the cheapest thing to get wrong
    and the hardest to notice.
    """
    head = b'{"model":"mock-model-1","max_tokens":8,"messages":[{"role":"user","content":'
    escaped = head + b'"Bj\\u00f6rk"}]}'
    literal = head + '"Björk"}]}'.encode()
    gateway.book.set("ok", MockScript.anthropic_message("ok"))

    await gateway.post("/v1/messages", escaped, script="ok")
    assert gateway.last_upstream_request().body == escaped

    await gateway.post("/v1/messages", literal, script="ok")
    assert gateway.last_upstream_request().body == literal


async def test_a_streamed_tool_use_reassembles_into_valid_input_json(
    gateway: GatewayHarness,
) -> None:
    """Streamed tool calls arrive as partial JSON *fragments*, split mid-token.

    ``input_json_delta`` events carry pieces of a JSON string, not JSON documents. A
    proxy that tried to parse each delta would fail on every one of them; a proxy that
    forwards bytes lets the client's accumulator do its job.
    """
    chunks = anthropic_tool_use_stream_chunks()
    gateway.book.set("tool_stream", MockScript(chunks=chunks))

    response = await gateway.post(
        "/v1/messages",
        anthropic_request(stream=True, tools=ANTHROPIC_TOOLS),
        script="tool_stream",
    )

    assert response.status_code == 200
    assert event_pairs(response.content) == event_pairs(b"".join(chunks))

    partials = [
        json.loads(data)["delta"]["partial_json"]
        for name, data in event_pairs(response.content)
        if name == "content_block_delta"
    ]
    assert json.loads("".join(partials)) == {"period": "2024-Q3", "artist": "Björk"}
    assert gateway.last_context().outcome == "ok"


async def test_a_cut_during_a_tool_call_is_still_an_error_not_a_half_tool(
    gateway: GatewayHarness,
) -> None:
    """The worst possible truncation: half a tool call.

    A tool stream cut mid-``input_json_delta`` leaves the client holding unparseable
    partial JSON. Without a terminal error event, an agent framework would either
    crash on the parse or — much worse — call the tool with whatever it managed to
    reconstruct. This is the concrete shape of the risk in register item 2.
    """
    chunks = anthropic_tool_use_stream_chunks()
    gateway.book.set("cut_tool", MockScript(chunks=chunks, cut_after_chunks=4))

    response = await gateway.post(
        "/v1/messages",
        anthropic_request(stream=True, tools=ANTHROPIC_TOOLS),
        script="cut_tool",
    )

    events = event_pairs(response.content)
    assert events[-1][0] == "error"
    assert json.loads(events[-1][1])["headroom"]["reason"] == "upstream_stream_cut"
    assert "message_stop" not in [name for name, _ in events]
