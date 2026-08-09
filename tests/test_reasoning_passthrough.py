"""Reasoning deltas: a field the gateway has never heard of, carried intact.

The live vLLM smoke found this on real hardware (docs/PHASE_LOG.md, Phase 1, *Live
smoke — first run (vLLM)*). The operator's instance serves Qwen3.6 behind
``--reasoning-parser qwen3``, which splits the model's chain of thought out of the
answer and into a delta field of its own — ``reasoning_content`` in vLLM's
OpenAI-compat schema, ``reasoning`` in other builds. No module under ``headroom/``
mentions either name; no fixture produced one before this file; the deltas reached the
client untouched anyway.

That is assumption **A5's** property observed somewhere A5 does not reach. A5 says
Anthropic ``tool_use`` blocks round-trip byte-for-byte and ``tests/test_tool_blocks.py``
proves it; this file asserts the more general claim underneath both — under H-007 the
proxy forwards body bytes it never re-serializes, so a field invented *after* the
passthrough was written survives for exactly the same structural reason a known one
does. Written keyless, so it holds in CI, where there is no GPU and no reasoning model.

Two consequences beyond fidelity, and they are the reason this file is worth its weight:

* **An exhausted token budget is a complete stream, not a truncation.** A reasoning
  model that spends ``max_tokens`` before its first content delta ends *correctly* —
  ``finish_reason: "length"``, ``[DONE]`` present, no error — and hands back nothing.
  The gateway is right to call that ``ok``; only the caller can judge it useless. That
  is what the live smoke hit, and why that test now asserts on ``finish_reason``.
* **A cut during reasoning must still be an error.** It produces the same empty answer
  as the case above and must never be confused with it — invariant 6, one layer up: a
  partial reply is not a complete one, however complete it looks.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from headroom.providers.mock import MockScript
from headroom.providers.mock_scripts import (
    REASONING_MODEL,
    REASONING_TRACE,
    openai_reasoning_body,
    openai_reasoning_stream_chunks,
)

from .support.fixtures import openai_request
from .support.harness import GatewayHarness
from .support.streams import (
    event_pairs,
    json_events,
    openai_finish_reasons,
    openai_reasoning_text,
    openai_text,
)

#: The visible answer every fixture below ends with, when it gets that far.
ANSWER = "headroom ok"


def _payloads(raw: bytes) -> list[dict[str, Any]]:
    """Every parsed frame except ``[DONE]``, which carries no JSON."""
    return [payload for payload in json_events(raw) if isinstance(payload, dict)]


def _delta_order(raw: bytes) -> list[str]:
    """``"reasoning"`` / ``"content"`` for each delta that carried text, in order."""
    order: list[str] = []
    for payload in _payloads(raw):
        for choice in payload.get("choices") or []:
            delta = choice.get("delta") or {}
            if delta.get("reasoning_content"):
                order.append("reasoning")
            elif delta.get("content"):
                order.append("content")
    return order


@pytest.mark.parametrize("field", ["reasoning_content", "reasoning"])
async def test_reasoning_deltas_reach_the_client_byte_for_byte(
    gateway: GatewayHarness, field: str
) -> None:
    """Both spellings in the wild, neither known here, both intact.

    Parametrized rather than pinned because the point is *not* that Headroom supports
    vLLM's field name. It is that the field name cannot matter: there is no branch
    anywhere in the proxy that could treat one differently from the other.
    """
    chunks = openai_reasoning_stream_chunks(reasoning_field=field)
    gateway.book.set("reasoning", MockScript(chunks=chunks))

    response = await gateway.post(
        "/v1/chat/completions",
        openai_request(model=REASONING_MODEL, stream=True),
        script="reasoning",
    )

    assert response.status_code == 200
    assert response.content == b"".join(chunks), "the reasoning stream was altered in transit"
    assert openai_reasoning_text(response.content, field) == REASONING_TRACE
    assert openai_text(response.content) == ANSWER
    assert openai_finish_reasons(response.content) == ["stop"]
    assert gateway.last_context().outcome == "ok"


async def test_the_chain_of_thought_never_leaks_into_the_answer(
    gateway: GatewayHarness,
) -> None:
    """Reasoning arrives before the answer and stays out of it.

    A client accumulating ``delta.content`` — which is every OpenAI SDK — must end
    holding the answer alone, with the chain of thought reachable only by a caller that
    asks for it by name.
    """
    gateway.book.set("reasoning", MockScript(chunks=openai_reasoning_stream_chunks()))

    response = await gateway.post(
        "/v1/chat/completions",
        openai_request(model=REASONING_MODEL, stream=True),
        script="reasoning",
    )

    visible = openai_text(response.content)
    assert visible == ANSWER
    assert "Björk" not in visible, "the chain of thought leaked into the answer"

    order = _delta_order(response.content)
    first_answer = order.index("content")
    assert order[:first_answer] == ["reasoning"] * first_answer
    assert "reasoning" not in order[first_answer:], "a thought arrived after the answer began"


async def test_the_token_count_is_only_knowable_from_the_usage_block(
    gateway: GatewayHarness,
) -> None:
    """Eleven visible characters, sixty-three billed tokens, fifty-seven of them unseen.

    Phase 3 meters streamed usage (assumption A3), and this is the case that decides
    *where* it reads from. Reasoning tokens are inside ``completion_tokens`` and are not
    recoverable from anything the client can see, so a meter that counts the text it
    forwarded undercounts by 57 on this one request — and by whatever the model felt
    like thinking on the next.
    """
    gateway.book.set("reasoning", MockScript(chunks=openai_reasoning_stream_chunks()))

    response = await gateway.post(
        "/v1/chat/completions",
        openai_request(model=REASONING_MODEL, stream=True),
        script="reasoning",
    )

    usage = next(payload["usage"] for payload in _payloads(response.content) if "usage" in payload)
    assert usage == {
        "prompt_tokens": 21,
        "completion_tokens": 63,
        "total_tokens": 84,
        "completion_tokens_details": {"reasoning_tokens": 57},
    }
    assert len(openai_text(response.content)) == 11, "the visible answer is eleven characters"


@pytest.mark.parametrize("chunk_size", [1, 3, 64])
async def test_a_reasoning_stream_survives_being_chopped_one_byte_at_a_time(
    gateway: GatewayHarness, chunk_size: int
) -> None:
    """A4's treatment, applied to a field nothing in this repo understands.

    The trace carries a literal ``ö`` and a literal ``𝄞`` because that is what vLLM
    actually sends — real multi-byte UTF-8 on the wire, not ASCII escapes — so
    ``chunk_size=1`` splits characters down the middle as well as frames and JSON
    tokens. Reassembly still has to be byte-identical.
    """
    whole = b"".join(openai_reasoning_stream_chunks())
    gateway.book.set("chopped", MockScript.openai_reasoning_stream(chunk_size=chunk_size))

    response = await gateway.post(
        "/v1/chat/completions",
        openai_request(model=REASONING_MODEL, stream=True),
        script="chopped",
    )

    assert response.content == whole, f"stream corrupted at chunk_size={chunk_size}"
    assert openai_reasoning_text(response.content) == REASONING_TRACE
    assert openai_text(response.content) == ANSWER


async def test_an_exhausted_budget_is_a_complete_stream_not_a_truncation(
    gateway: GatewayHarness,
) -> None:
    """The live-smoke failure, reproduced without a GPU.

    ``max_tokens=16`` against a reasoning model: the whole budget goes to the chain of
    thought, the thought stops mid-sentence, and the stream ends properly with
    ``finish_reason: "length"`` and ``[DONE]``. Nothing is wrong with the gateway — the
    request outcome is ``ok`` and it should be — but a client that judges success by
    "did any text arrive" reads a well-formed answer-less reply as a transport failure.
    That confusion is what sent a session chasing a gateway bug, and this test pins the
    distinction so the next one does not.
    """
    stopped_mid_thought = "They want the literal string"
    gateway.book.set(
        "budget",
        MockScript.openai_reasoning_stream(
            reasoning=stopped_mid_thought,
            text="",
            finish_reason="length",
            reasoning_tokens=16,
            completion_tokens=16,
        ),
    )

    response = await gateway.post(
        "/v1/chat/completions",
        openai_request(model=REASONING_MODEL, stream=True),
        script="budget",
    )

    assert response.status_code == 200
    events = event_pairs(response.content)
    assert "[DONE]" in [data for _, data in events], "a length stop is still a finished stream"
    assert not any('"error"' in data for _, data in events), response.text

    assert openai_finish_reasons(response.content) == ["length"]
    assert openai_text(response.content) == "", "the budget went to reasoning; no answer exists"
    assert openai_reasoning_text(response.content) == stopped_mid_thought
    assert gateway.last_context().outcome == "ok", "a complete stream is a successful request"


async def test_a_cut_during_reasoning_is_an_error_not_an_empty_answer(
    gateway: GatewayHarness,
) -> None:
    """The case that looks identical to the one above and must not be confused with it.

    Same empty answer, same absent content deltas — and a completely different meaning.
    One ran out of budget and finished; this one died. The client is told so in its own
    dialect, ``[DONE]`` never appears, and no ``finish_reason`` was ever sent, so
    nothing downstream can mistake the fragment for a short answer.
    """
    gateway.book.set("cut", MockScript.openai_reasoning_stream(cut_after_chunks=3))

    response = await gateway.post(
        "/v1/chat/completions",
        openai_request(model=REASONING_MODEL, stream=True),
        script="cut",
    )

    events = event_pairs(response.content)
    assert "[DONE]" not in [data for _, data in events]
    assert json.loads(events[-1][1])["headroom"]["reason"] == "upstream_stream_cut"

    assert openai_finish_reasons(response.content) == [], "a cut stream never finished"
    assert openai_text(response.content) == ""
    assert gateway.last_context().outcome == "upstream_stream_cut"

    # The thinking that did arrive still reaches the client — a cut is not a reason to
    # discard what already landed (see test_mid_stream_cut.py for the same rule on
    # content). It is only a reason to refuse to call it complete.
    assert openai_reasoning_text(response.content) == "They want "


async def test_a_non_streamed_reasoning_reply_reaches_the_client_byte_for_byte(
    gateway: GatewayHarness,
) -> None:
    """A literal ``ö`` and an escaped ``\\u00f6`` in one payload; neither may become
    the other.

    Both encode the same character and both are valid JSON, which is what makes the
    swap invisible to every check except this one. ``tests/test_tool_blocks.py`` asserts
    this on the request side; the reply side is where a reasoning model's output lives.
    """
    expected = openai_reasoning_body()
    gateway.book.set("reasoning", MockScript(body=expected))

    response = await gateway.post(
        "/v1/chat/completions",
        openai_request(model=REASONING_MODEL),
        script="reasoning",
    )

    assert response.status_code == 200
    assert response.content == expected, "the reasoning reply was altered in transit"
    literal_o = b"Bj\xc3\xb6rk"  # ö as UTF-8, the way vLLM streams it
    escaped_o = b"Bj\\u00f6rk"  # ö as a JSON escape: six plain ASCII characters
    assert response.content.count(literal_o) == 1, "the literal ö was escaped in transit"
    assert response.content.count(escaped_o) == 1, "the escaped ö was made literal in transit"

    # And it is still what an SDK will parse: two spellings, one character, twice.
    message = json.loads(response.content)["choices"][0]["message"]
    assert message["content"] == ANSWER
    assert message["reasoning_content"].count("Björk") == 2
    assert 'string "headroom ok"' in message["reasoning_content"]
