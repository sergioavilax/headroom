"""The eligibility rules, unit by unit. The end-to-end proof is ``test_cache_poison.py``.

These are the predicates invariant 6 is made of. Tested here directly as well as through
the gateway, because a rule that is only exercised end to end is a rule whose *reason*
nobody can see: the request-side table below is a list of the shapes a cache must refuse,
and it should be readable as one.
"""

from __future__ import annotations

from typing import Any

import pytest

from headroom.cache.eligibility import (
    COMPLETE_STOP_REASONS,
    MAX_CACHEABLE_BODY_BYTES,
    MAX_CACHEABLE_TEMPERATURE,
    REASON_BODY_TOO_LARGE,
    REASON_EMPTY_BODY,
    REASON_INCOMPLETE,
    REASON_MULTIPLE_COMPLETIONS,
    REASON_NOT_SINGLE_TURN,
    REASON_REASONING_RESPONSE,
    REASON_TEMPERATURE,
    REASON_TOOL_OUTPUT,
    REASON_TOOLS,
    REASON_UPSTREAM_ERROR,
    declares_tools,
    may_cache_request,
    may_store_response,
)
from headroom.dialects.anthropic import ANTHROPIC
from headroom.dialects.openai import OPENAI
from tests.support.fixtures import (
    ANTHROPIC_TOOLS,
    anthropic_request,
    anthropic_tool_result_request,
    openai_request,
)

BODY = b'{"content":[{"type":"text","text":"hi"}]}'


# --- the tool scan -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"tools": []}, id="an_empty_tools_array"),
        pytest.param({"tools": ANTHROPIC_TOOLS}, id="tools_declared"),
        pytest.param({"tool_choice": {"type": "auto"}}, id="tool_choice"),
        pytest.param({"functions": [{"name": "f"}]}, id="legacy_functions"),
        pytest.param({"function_call": "auto"}, id="legacy_function_call"),
        pytest.param(
            {"messages": [{"role": "user", "content": [{"type": "tool_result", "x": 1}]}]},
            id="a_tool_result_block",
        ),
        pytest.param(
            {"messages": [{"role": "assistant", "content": [{"type": "tool_use", "x": 1}]}]},
            id="a_tool_use_block",
        ),
        pytest.param({"messages": [{"role": "tool", "content": "{}"}]}, id="an_openai_tool_turn"),
        pytest.param(
            {"messages": [{"role": "assistant", "tool_calls": [{"id": "c"}]}]}, id="tool_calls"
        ),
        pytest.param(
            {"a": {"b": {"c": [{"d": {"type": "server_tool_use"}}]}}}, id="buried_five_deep"
        ),
    ],
)
def test_the_tool_scan_finds_tools_wherever_they_are(body: dict[str, Any]) -> None:
    assert declares_tools(body) is True


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(anthropic_request(), id="a_plain_request"),
        # The scan reads keys and typed markers, never free text — so a user asking a
        # question *about* tool use is unaffected.
        pytest.param(
            anthropic_request(text="explain what a tool_use block is in the Messages API"),
            id="a_question_about_tool_use",
        ),
        pytest.param({"messages": [{"role": "user", "content": "tool_calls"}]}, id="the_word"),
    ],
)
def test_the_tool_scan_does_not_read_prose(body: dict[str, Any]) -> None:
    assert declares_tools(body) is False


# --- request side ------------------------------------------------------------------------


def test_a_plain_single_turn_request_is_eligible() -> None:
    decision = may_cache_request(ANTHROPIC, anthropic_request())
    assert decision.ok
    assert decision.probe is not None


def test_tools_present_but_unused_are_still_ineligible() -> None:
    """The adjacent case, decided explicitly (H-041).

    Nothing has been *called* here — there is one user turn and a ``tools`` array — and it
    is refused anyway. The same words with tools available may legitimately produce a tool
    call, so answering with a cached paragraph is the D-021 shape exactly; and the tools
    array is part of the prompt, which a probe embedding only the question would ignore.
    """
    body = anthropic_request()
    body["tools"] = ANTHROPIC_TOOLS
    decision = may_cache_request(ANTHROPIC, body)
    assert not decision.ok
    assert decision.reason == REASON_TOOLS


def test_a_conversation_carrying_tool_blocks_is_ineligible() -> None:
    decision = may_cache_request(ANTHROPIC, anthropic_tool_result_request())
    assert not decision.ok
    assert decision.reason == REASON_TOOLS


@pytest.mark.parametrize("temperature", [0.21, 0.7, 1.0, 2])
def test_a_high_temperature_is_ineligible(temperature: float) -> None:
    decision = may_cache_request(ANTHROPIC, anthropic_request(temperature=temperature))
    assert not decision.ok
    assert decision.reason == REASON_TEMPERATURE


@pytest.mark.parametrize("temperature", [0, 0.0, 0.1, MAX_CACHEABLE_TEMPERATURE])
def test_a_low_temperature_is_eligible(temperature: float) -> None:
    assert may_cache_request(ANTHROPIC, anthropic_request(temperature=temperature)).ok


def test_asking_for_several_completions_is_ineligible() -> None:
    decision = may_cache_request(OPENAI, openai_request(n=2))
    assert not decision.ok
    assert decision.reason == REASON_MULTIPLE_COMPLETIONS


def test_a_multi_turn_conversation_is_ineligible() -> None:
    body = anthropic_request()
    body["messages"] = [
        {"role": "user", "content": "one"},
        {"role": "assistant", "content": "two"},
        {"role": "user", "content": "three"},
    ]
    decision = may_cache_request(ANTHROPIC, body)
    assert not decision.ok
    assert decision.reason == REASON_NOT_SINGLE_TURN


# --- response side: invariant 6 ------------------------------------------------------------


def ok_store(**overrides: Any) -> Any:
    defaults: dict[str, Any] = {
        "outcome": "ok",
        "upstream_status": 200,
        "stop_reason": "end_turn",
        "body": BODY,
        "reasoning_tokens": None,
    }
    return may_store_response(**{**defaults, **overrides})


def test_a_complete_response_may_be_stored_and_embedded() -> None:
    decision = ok_store()
    assert decision.store and decision.embed


@pytest.mark.parametrize("stop_reason", sorted(COMPLETE_STOP_REASONS))
def test_every_complete_stop_reason_is_storable(stop_reason: str) -> None:
    assert ok_store(stop_reason=stop_reason).store


@pytest.mark.parametrize(
    "stop_reason",
    [
        # THE headline case. A complete *stream* of a truncated *answer* — exactly the
        # distinction H-008 drew, and exactly what D-021 stored one layer up.
        pytest.param("max_tokens", id="anthropic_truncation"),
        pytest.param("length", id="openai_truncation"),
        pytest.param("tool_use", id="a_tool_call"),
        pytest.param("content_filter", id="filtered"),
        pytest.param("refusal", id="refused"),
        pytest.param(None, id="never_reported"),
    ],
)
def test_a_response_that_did_not_finish_is_never_stored(stop_reason: str | None) -> None:
    decision = ok_store(stop_reason=stop_reason)
    assert not decision.store
    assert decision.reason == REASON_INCOMPLETE


@pytest.mark.parametrize(
    "outcome",
    ["upstream_stream_cut", "upstream_stream_incomplete", "client_disconnect", "upstream_error"],
)
def test_a_request_that_did_not_end_ok_is_never_stored(outcome: str) -> None:
    decision = ok_store(outcome=outcome)
    assert not decision.store
    assert decision.reason == REASON_INCOMPLETE


@pytest.mark.parametrize("status", [400, 401, 429, 500, 529, None])
def test_an_upstream_error_is_never_stored(status: int | None) -> None:
    decision = ok_store(upstream_status=status)
    assert not decision.store
    assert decision.reason == REASON_UPSTREAM_ERROR


def test_an_empty_body_is_never_stored() -> None:
    assert ok_store(body=b"").reason == REASON_EMPTY_BODY


def test_a_body_over_the_bound_is_never_stored() -> None:
    decision = ok_store(body=b"x" * (MAX_CACHEABLE_BODY_BYTES + 1))
    assert not decision.store
    assert decision.reason == REASON_BODY_TOO_LARGE


@pytest.mark.parametrize("marker", [b'"tool_use"', b'"tool_calls"', b'"tool_result"'])
def test_a_response_carrying_a_tool_call_is_never_stored(marker: bytes) -> None:
    decision = ok_store(body=b"{" + marker + b":1}")
    assert not decision.store
    assert decision.reason == REASON_TOOL_OUTPUT


def test_a_reasoning_response_is_storable_but_not_embeddable() -> None:
    """H-044, and the reason it is two booleans rather than one.

    An exact hit replays this chain of thought for the question it was actually produced
    for, which is right. A *semantic* hit would replay reasoning that visibly names a
    different subject — a failure mode beyond "the answer is wrong", and one §P8.H1's
    silent-wrong-answer metric would not even capture.
    """
    decision = ok_store(reasoning_tokens=57)
    assert decision.store
    assert not decision.embed
    assert decision.reason == REASON_REASONING_RESPONSE


def test_zero_reasoning_tokens_is_not_a_reasoning_response() -> None:
    """A provider that reports the field as 0 has told us there was none."""
    assert ok_store(reasoning_tokens=0).embed
