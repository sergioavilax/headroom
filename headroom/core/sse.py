"""Server-Sent Events: an incremental parser used as an *observer*, plus a writer.

The gateway forwards upstream stream bytes to the client **unchanged** — it never
re-frames them (docs/DECISIONS.md H-007). But it still has to *understand* the stream:
Phase 1 needs to know whether the upstream reached its dialect's terminal marker (else
the caller gets a silent truncation), and Phase 3 will need the usage payload.

So the parser here is a passive tap. The proxy feeds it a copy of every chunk while
forwarding the original, which buys full knowledge of the event sequence at zero cost
to fidelity: nothing the parser does can alter a byte the client receives.

Parsing follows the WHATWG event-stream rules closely enough for that job: CR, LF, and
CRLF all terminate a line; a leading colon marks a comment; one optional space after
the field colon is stripped; ``data`` fields accumulate newline-separated; and a blank
line dispatches the event (an event with no data lines is not dispatched).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

__all__ = ["SSEEvent", "SSEObserver", "format_sse", "iter_sse_events"]


@dataclass(frozen=True, slots=True)
class SSEEvent:
    """One dispatched event: the fields a consumer can act on."""

    event: str | None
    data: str
    id: str | None = None
    retry: int | None = None

    def json(self) -> Any:
        """The data payload parsed as JSON, or ``None`` if it is not JSON.

        Deliberately forgiving: ``data: [DONE]`` is a valid frame in the OpenAI
        dialect and must not raise here.
        """
        try:
            return json.loads(self.data)
        except ValueError:
            return None


class SSEObserver:
    """Feed it chunks in arrival order; it yields the events they complete.

    Stateful across calls, because a chunk boundary can fall anywhere — mid-line,
    mid-UTF-8-sequence, between the ``\\r`` and the ``\\n`` of a CRLF. Bytes that do
    not yet complete a line are held until the next chunk.
    """

    __slots__ = ("_buffer", "_data", "_event", "_id", "_retry")

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._data: list[str] = []
        self._event: str | None = None
        self._id: str | None = None
        self._retry: int | None = None

    def feed(self, chunk: bytes) -> list[SSEEvent]:
        """Absorb a chunk; return every event it completed (possibly none)."""
        self._buffer.extend(chunk)
        events: list[SSEEvent] = []
        for line in self._take_complete_lines():
            event = self._consume_line(line)
            if event is not None:
                events.append(event)
        return events

    def close(self) -> list[SSEEvent]:
        """Flush at end-of-stream.

        A well-formed stream ends with a blank line, so this is usually empty. It is
        not empty when the upstream was cut mid-frame — and the gateway would rather
        see a partially-buffered event than silently forget it.
        """
        events: list[SSEEvent] = []
        if self._buffer:
            trailing = bytes(self._buffer)
            self._buffer.clear()
            event = self._consume_line(trailing)
            if event is not None:
                events.append(event)
        final = self._dispatch()
        if final is not None:
            events.append(final)
        return events

    # -- internals ---------------------------------------------------------------

    def _take_complete_lines(self) -> list[bytes]:
        """Split off every line the buffer definitely contains.

        A trailing lone ``\\r`` is held back: the next chunk may begin with ``\\n``,
        and treating the pair as two line breaks would dispatch a phantom event.
        """
        lines: list[bytes] = []
        start = 0
        index = 0
        limit = len(self._buffer)
        while index < limit:
            byte = self._buffer[index]
            if byte == 0x0A:  # \n
                lines.append(bytes(self._buffer[start:index]))
                index += 1
                start = index
            elif byte == 0x0D:  # \r
                if index + 1 == limit:
                    break  # might be the first half of a CRLF — wait for more
                lines.append(bytes(self._buffer[start:index]))
                index += 2 if self._buffer[index + 1] == 0x0A else 1
                start = index
            else:
                index += 1
        del self._buffer[:start]
        return lines

    def _consume_line(self, line: bytes) -> SSEEvent | None:
        if not line:
            return self._dispatch()
        if line.startswith(b":"):
            return None  # a comment (":keep-alive") — not an event
        field, _, value = line.partition(b":")
        text = value[1:] if value.startswith(b" ") else value
        decoded = text.decode("utf-8", errors="replace")
        match field:
            case b"data":
                self._data.append(decoded)
            case b"event":
                self._event = decoded
            case b"id":
                self._id = decoded
            case b"retry":
                self._retry = int(decoded) if decoded.isdigit() else self._retry
        return None

    def _dispatch(self) -> SSEEvent | None:
        if not self._data:
            self._event = None  # a dataless block resets the type, per the spec
            return None
        event = SSEEvent(
            event=self._event, data="\n".join(self._data), id=self._id, retry=self._retry
        )
        self._data = []
        self._event = None
        return event


def iter_sse_events(raw: bytes) -> Iterator[SSEEvent]:
    """Parse a complete stream body. For tests and offline analysis, not the hot path."""
    observer = SSEObserver()
    yield from observer.feed(raw)
    yield from observer.close()


def format_sse(data: str, event: str | None = None) -> bytes:
    """Serialize one event. Used only for events the gateway itself originates."""
    lines = [] if event is None else [f"event: {event}"]
    lines.extend(f"data: {line}" for line in data.split("\n"))
    return ("\n".join(lines) + "\n\n").encode("utf-8")
