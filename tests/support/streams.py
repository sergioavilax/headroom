"""Reading a streamed response the way a client's SDK would.

Assumption A4 says chunk boundaries are **not** preserved through the proxy, so a test
that compares chunk lists is testing the wrong thing and will fail for the right
reasons at the worst moment. What must be equal is what a client can actually observe:
the sequence of events, and the content they reassemble to. These helpers produce
exactly those two views, so the gate asserts them and nothing else.
"""

from __future__ import annotations

import json
from typing import Any

from headroom.core.sse import iter_sse_events

__all__ = ["anthropic_text", "event_pairs", "openai_text"]


def event_pairs(raw: bytes) -> list[tuple[str | None, str]]:
    """(event name, data) for every event in a stream body, in order."""
    return [(event.event, event.data) for event in iter_sse_events(raw)]


def anthropic_text(raw: bytes) -> str:
    """The assistant text a Messages-API client would accumulate."""
    parts: list[str] = []
    for event in iter_sse_events(raw):
        payload = event.json()
        if not isinstance(payload, dict) or payload.get("type") != "content_block_delta":
            continue
        delta = payload.get("delta")
        if isinstance(delta, dict) and delta.get("type") == "text_delta":
            parts.append(str(delta.get("text", "")))
    return "".join(parts)


def openai_text(raw: bytes) -> str:
    """The assistant text a chat-completions client would accumulate."""
    parts: list[str] = []
    for event in iter_sse_events(raw):
        payload: Any = event.json()
        if not isinstance(payload, dict):
            continue
        for choice in payload.get("choices") or []:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta")
            if isinstance(delta, dict) and isinstance(delta.get("content"), str):
                parts.append(delta["content"])
    return "".join(parts)


def json_events(raw: bytes) -> list[Any]:
    """Every event's payload, parsed. ``data: [DONE]`` comes through as ``None``."""
    return [
        json.loads(event.data) if event.data.strip() != "[DONE]" else None
        for event in iter_sse_events(raw)
    ]
