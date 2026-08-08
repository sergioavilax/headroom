"""The SSE observer, exercised at the boundaries the network actually produces.

The proxy forwards bytes untouched and only *watches* them go by, so this parser never
affects what a client receives — but it decides whether a stream counted as complete,
and that decision is the difference between an answer and a fragment. A parser that
loses an event because a chunk boundary fell in the wrong place would make the gateway
append spurious errors to perfectly good responses; one that hallucinates an event
would let a truncation through. Both failure modes live at the boundaries below.
"""

from __future__ import annotations

from headroom.core.sse import SSEObserver, format_sse, iter_sse_events


def test_a_simple_event_parses() -> None:
    events = list(iter_sse_events(b"event: ping\ndata: {}\n\n"))

    assert len(events) == 1
    assert events[0].event == "ping"
    assert events[0].data == "{}"


def test_a_frame_split_across_chunks_still_arrives_once() -> None:
    observer = SSEObserver()
    payload = b"event: message_stop\ndata: {}\n\n"

    collected = [event for byte in payload for event in observer.feed(bytes([byte]))]

    assert len(collected) == 1
    assert collected[0].event == "message_stop"


def test_a_crlf_split_across_chunks_is_not_two_line_breaks() -> None:
    """The subtle one: ``\\r`` at the end of a chunk, ``\\n`` at the start of the next.

    Treating the pair as two terminators dispatches a phantom empty event and ends the
    frame early — so the parser holds a trailing lone ``\\r`` back until it can see
    what follows.
    """
    observer = SSEObserver()

    first = observer.feed(b"event: message_stop\r\ndata: {}\r")
    second = observer.feed(b"\n\r\n")

    assert first == []
    assert len(second) == 1
    assert second[0].event == "message_stop"
    assert second[0].data == "{}"


def test_all_three_line_terminators_work() -> None:
    for terminator in (b"\n", b"\r\n", b"\r"):
        raw = terminator.join([b"event: ping", b"data: 1", b"", b""])
        events = list(iter_sse_events(raw))
        assert [(e.event, e.data) for e in events] == [("ping", "1")], terminator


def test_comments_are_ignored() -> None:
    """Keep-alive comments are common and must not look like events."""
    events = list(iter_sse_events(b": keep-alive\n\n: another\ndata: real\n\n"))

    assert [e.data for e in events] == ["real"]


def test_multi_line_data_is_joined_with_newlines() -> None:
    events = list(iter_sse_events(b"data: line one\ndata: line two\n\n"))

    assert events[0].data == "line one\nline two"


def test_a_block_with_no_data_dispatches_nothing() -> None:
    """Per the spec — and it matters here: a dataless block must not be mistaken for a
    terminal event just because it carried an ``event:`` line."""
    events = list(iter_sse_events(b"event: ping\n\ndata: real\n\n"))

    assert [(e.event, e.data) for e in events] == [(None, "real")]


def test_the_event_name_does_not_leak_into_the_next_event() -> None:
    events = list(iter_sse_events(b"event: first\ndata: 1\n\ndata: 2\n\n"))

    assert [(e.event, e.data) for e in events] == [("first", "1"), (None, "2")]


def test_id_and_retry_fields_are_captured() -> None:
    events = list(iter_sse_events(b"id: 42\nretry: 3000\ndata: x\n\n"))

    assert events[0].id == "42"
    assert events[0].retry == 3000


def test_only_one_leading_space_is_stripped_after_the_colon() -> None:
    events = list(iter_sse_events(b"data:  two spaces\n\n"))

    assert events[0].data == " two spaces"


def test_a_field_with_no_colon_is_a_field_with_an_empty_value() -> None:
    events = list(iter_sse_events(b"data\ndata: x\n\n"))

    assert events[0].data == "\nx"


def test_close_flushes_a_frame_that_never_got_its_blank_line() -> None:
    """Exactly what a mid-stream cut leaves behind.

    The gateway would rather see a partially-buffered event than silently forget it —
    forgetting is how a truncated stream ends up looking merely quiet.
    """
    observer = SSEObserver()

    assert observer.feed(b"event: message_stop\ndata: {}\n") == []
    flushed = observer.close()

    assert len(flushed) == 1
    assert flushed[0].event == "message_stop"


def test_close_on_a_clean_stream_flushes_nothing() -> None:
    observer = SSEObserver()
    observer.feed(b"data: x\n\n")

    assert observer.close() == []


def test_a_multi_byte_character_split_down_the_middle_survives() -> None:
    """A byte-at-a-time stream tears UTF-8 sequences apart; the reassembly must not."""
    observer = SSEObserver()
    payload = "data: Björk 🎧\n\n".encode()

    events = [event for byte in payload for event in observer.feed(bytes([byte]))]

    assert [e.data for e in events] == ["Björk 🎧"]


def test_json_helper_tolerates_a_non_json_payload() -> None:
    """``data: [DONE]`` is a valid frame in the OpenAI dialect and must not raise."""
    events = list(iter_sse_events(b"data: [DONE]\n\n"))

    assert events[0].json() is None


def test_format_sse_round_trips() -> None:
    raw = format_sse('{"type":"error"}', event="error")

    assert raw == b'event: error\ndata: {"type":"error"}\n\n'
    parsed = next(iter(iter_sse_events(raw)))
    assert (parsed.event, parsed.data) == ("error", '{"type":"error"}')


def test_format_sse_splits_multi_line_data_into_multiple_fields() -> None:
    raw = format_sse("one\ntwo")

    assert raw == b"data: one\ndata: two\n\n"
    assert next(iter(iter_sse_events(raw))).data == "one\ntwo"
