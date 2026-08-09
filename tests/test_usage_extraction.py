"""Reading the usage block — the one place token counts are allowed to come from.

The rule this file defends is stated in ``headroom/metering/usage.py`` and was found
the hard way on real hardware: **the meter reads the usage block, never the text.** A
reasoning model's answer can be eleven characters long and cost sixty-three tokens, and
no amount of counting what the client received recovers the difference.

The dialects report usage in shapes that are different enough to be worth testing
separately — Anthropic splits it across two events several frames apart, and the OpenAI
dialect appends it in a chunk that arrives *after* the frame ending the answer.
"""

from __future__ import annotations

from headroom.core.sse import iter_sse_events
from headroom.dialects.anthropic import ANTHROPIC
from headroom.dialects.base import Dialect
from headroom.dialects.openai import OPENAI
from headroom.metering.usage import Usage
from headroom.providers import mock_scripts


def observe(dialect: Dialect, chunks: list[bytes]) -> Usage:
    """Feed a whole stream through the dialect's observer, as the proxy would."""
    observer = dialect.usage_observer()
    for event in iter_sse_events(b"".join(chunks)):
        observer.feed(event)
    return observer.usage


# --- Anthropic ----------------------------------------------------------------------


def test_anthropic_non_streamed_usage() -> None:
    usage = ANTHROPIC.usage_from_body(mock_scripts.anthropic_message_body("hi"))

    assert usage.input_tokens == 11
    assert usage.output_tokens == 7
    assert usage.stop_reason == "end_turn"


def test_anthropic_streamed_usage_is_assembled_from_two_events() -> None:
    """``message_start`` carries the prompt count, ``message_delta`` the generated one."""
    usage = observe(ANTHROPIC, mock_scripts.anthropic_stream_chunks("hello there"))

    assert usage.input_tokens == 11
    assert usage.output_tokens == 7
    assert usage.stop_reason == "end_turn"


def test_anthropic_streamed_and_non_streamed_agree() -> None:
    """Same request, two transports, one bill. Anything else is a routing-dependent price."""
    streamed = observe(ANTHROPIC, mock_scripts.anthropic_stream_chunks("hi"))
    buffered = ANTHROPIC.usage_from_body(mock_scripts.anthropic_message_body("hi"))

    assert (streamed.input_tokens, streamed.output_tokens) == (
        buffered.input_tokens,
        buffered.output_tokens,
    )


def test_a_stream_cut_before_message_delta_leaves_the_output_count_unknown() -> None:
    """**The placeholder trap.**

    ``message_start`` reports ``output_tokens: 0`` before a single token exists. Taking
    that at face value would bill a severed answer as a complete, free one — so the
    observer ignores it, and a cut stream honestly reports "we do not know".
    """
    chunks = mock_scripts.anthropic_stream_chunks("hello there")
    # Everything up to and including content deltas, but not the closing message_delta.
    usage = observe(ANTHROPIC, chunks[:-2])

    assert usage.input_tokens == 11
    assert usage.output_tokens is None
    assert not usage.is_complete


def test_anthropic_scripted_token_counts_pass_through_verbatim() -> None:
    """The mock can script any counts; the meter reports exactly what it was told."""
    usage = observe(
        ANTHROPIC,
        mock_scripts.anthropic_stream_chunks("hi", input_tokens=4_242, output_tokens=99),
    )

    assert (usage.input_tokens, usage.output_tokens) == (4_242, 99)


def test_anthropic_tool_use_usage_is_read_the_same_way() -> None:
    usage = observe(ANTHROPIC, mock_scripts.anthropic_tool_use_stream_chunks())

    assert (usage.input_tokens, usage.output_tokens) == (214, 63)
    assert usage.stop_reason == "tool_use"


def test_anthropic_prompt_cache_tiers_are_recorded() -> None:
    """Recorded so the row can be marked ``partial``; see ``tests/test_cost.py``."""
    body = (
        b'{"type":"message","stop_reason":"end_turn","usage":{"input_tokens":11,'
        b'"output_tokens":7,"cache_read_input_tokens":4000,'
        b'"cache_creation_input_tokens":250}}'
    )

    usage = ANTHROPIC.usage_from_body(body)

    assert usage.cache_read_tokens == 4_000
    assert usage.cache_write_tokens == 250
    assert usage.reports_cache_activity


def test_an_anthropic_error_body_reports_nothing_rather_than_raising() -> None:
    body = ANTHROPIC.error_body(
        status_code=429, reason="rate_limit", message="slow down", request_id="req_1"
    )

    assert ANTHROPIC.usage_from_body(body).is_empty


# --- OpenAI -------------------------------------------------------------------------


def test_openai_non_streamed_usage() -> None:
    usage = OPENAI.usage_from_body(mock_scripts.openai_completion_body("hi"))

    assert usage.input_tokens == 11
    assert usage.output_tokens == 7
    assert usage.stop_reason == "stop"


def test_openai_streamed_usage_arrives_after_the_finish_frame() -> None:
    """**The ordering that decided the shape of the passthrough loop.**

    With ``include_usage``, the chunk carrying ``finish_reason`` comes first, then the
    usage-only chunk, then ``[DONE]``. A meter that stopped reading at the dialect's
    terminal marker would see the finish reason and none of the counts.
    """
    usage = observe(OPENAI, mock_scripts.openai_stream_chunks("hello there"))

    assert usage.input_tokens == 11
    assert usage.output_tokens == 7
    assert usage.stop_reason == "stop"


def test_openai_streamed_and_non_streamed_agree() -> None:
    streamed = observe(OPENAI, mock_scripts.openai_stream_chunks("hi"))
    buffered = OPENAI.usage_from_body(mock_scripts.openai_completion_body("hi"))

    assert (streamed.input_tokens, streamed.output_tokens) == (
        buffered.input_tokens,
        buffered.output_tokens,
    )


def test_a_stream_without_include_usage_reports_nothing() -> None:
    """The gap H-028 chose to leave open, asserted rather than assumed.

    Headroom does not inject ``stream_options`` on the caller's behalf, so a client
    that did not ask for usage produces an unmeterable stream. The finish reason still
    arrives; the counts do not, and the ledger records that honestly.
    """
    usage = observe(OPENAI, mock_scripts.openai_stream_chunks("hi", include_usage=False))

    assert usage.input_tokens is None
    assert usage.output_tokens is None
    assert usage.stop_reason == "stop"
    assert not usage.is_complete


def test_reasoning_tokens_are_inside_the_output_count_not_beside_it() -> None:
    """63 completion tokens, 57 of them reasoning — and 11 visible characters.

    ``reasoning_tokens`` is a breakdown, not an addend. Adding it to the total would
    double-charge for the part nobody can see.
    """
    usage = observe(OPENAI, mock_scripts.openai_reasoning_stream_chunks())

    assert usage.output_tokens == 63
    assert usage.reasoning_tokens == 57
    assert usage.input_tokens == 21


def test_reasoning_usage_is_identical_in_the_non_streamed_form() -> None:
    usage = OPENAI.usage_from_body(mock_scripts.openai_reasoning_body())

    assert (usage.input_tokens, usage.output_tokens, usage.reasoning_tokens) == (21, 63, 57)


def test_an_exhausted_budget_still_reports_its_usage() -> None:
    """A model that spent everything on reasoning ended correctly and is billed for it."""
    usage = observe(
        OPENAI,
        mock_scripts.openai_reasoning_stream_chunks(text="", finish_reason="length"),
    )

    assert usage.output_tokens == 63
    assert usage.stop_reason == "length"
    assert usage.is_complete


def test_openai_cached_prompt_tokens_are_recorded() -> None:
    body = (
        b'{"choices":[{"finish_reason":"stop"}],"usage":{"prompt_tokens":11,'
        b'"completion_tokens":7,"prompt_tokens_details":{"cached_tokens":8}}}'
    )

    usage = OPENAI.usage_from_body(body)

    assert usage.cache_read_tokens == 8
    assert usage.input_tokens == 11, "prompt_tokens is inclusive of cached tokens in this dialect"


def test_an_openai_error_body_reports_nothing_rather_than_raising() -> None:
    body = OPENAI.error_body(
        status_code=500, reason="api_error", message="boom", request_id="req_1"
    )

    assert OPENAI.usage_from_body(body).is_empty


# --- shared properties ---------------------------------------------------------------


def test_a_stream_chopped_one_byte_at_a_time_still_meters() -> None:
    """A4's treatment applied to metering: frame boundaries are not usage boundaries.

    The observer reads *dispatched events*, which the SSE parser reassembles from
    whatever the network produced — so a usage block split across a hundred chunks is
    the same usage block.
    """
    whole = b"".join(mock_scripts.openai_stream_chunks("hello there"))
    observer = OPENAI.usage_observer()
    from headroom.core.sse import SSEObserver

    sse = SSEObserver()
    for piece in mock_scripts.split_every(whole, 1):
        for event in sse.feed(piece):
            observer.feed(event)
    for event in sse.close():
        observer.feed(event)

    assert (observer.usage.input_tokens, observer.usage.output_tokens) == (11, 7)


def test_a_body_that_is_not_json_reports_nothing() -> None:
    for dialect in (ANTHROPIC, OPENAI):
        assert dialect.usage_from_body(b"<html>502 Bad Gateway</html>").is_empty


def test_a_body_with_no_usage_object_reports_nothing() -> None:
    for dialect in (ANTHROPIC, OPENAI):
        assert dialect.usage_from_body(b'{"id":"x"}').is_empty


def test_merging_never_lets_silence_erase_a_stated_count() -> None:
    """Streams state usage in pieces; a later frame that says nothing must not unsay."""
    merged = Usage(input_tokens=11).merge(Usage(output_tokens=7))

    assert (merged.input_tokens, merged.output_tokens) == (11, 7)
    assert merged.merge(Usage()).input_tokens == 11


def test_a_boolean_is_not_a_token_count() -> None:
    """``True`` is an ``int`` in Python, and would otherwise meter as one token."""
    body = b'{"usage":{"input_tokens":true,"output_tokens":7}}'

    assert ANTHROPIC.usage_from_body(body).input_tokens is None
