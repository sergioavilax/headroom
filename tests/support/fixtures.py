"""Request bodies for the two dialects.

Small on purpose: these are the *caller's* side of the wire, and every test that
asserts fidelity compares against the exact bytes built here. The tool-block fixtures
carry deliberately awkward JSON — non-alphabetical keys, nested objects, a non-ASCII
character, an escaped quote — because a proxy that quietly re-serializes a body is
invisible against tidy input and obvious against this.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "ANTHROPIC_TOOLS",
    "DEFAULT_MODEL",
    "anthropic_request",
    "anthropic_tool_result_request",
    "openai_request",
]

#: Matches the ``mock-`` route prefix the test gateway is configured with, so an
#: unrouted model is a deliberate choice rather than an accident of naming.
DEFAULT_MODEL = "mock-model-1"

ANTHROPIC_TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_track_metrics",
        "description": 'Streaming metrics for an artist. Say "hi" politely.',
        "input_schema": {
            "type": "object",
            "properties": {
                "period": {"type": "string"},
                "artist": {"type": "string"},
                "filters": {
                    "type": "object",
                    "properties": {
                        "include_features": {"type": "boolean"},
                        "regions": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
            "required": ["period", "artist"],
        },
    }
]


def anthropic_request(
    *,
    model: str = DEFAULT_MODEL,
    stream: bool = False,
    text: str = "How did Björk's catalogue perform in 2024-Q3?",
    **extra: Any,
) -> dict[str, Any]:
    """A minimal Messages request."""
    body: dict[str, Any] = {
        "model": model,
        "max_tokens": 64,
        "messages": [{"role": "user", "content": text}],
    }
    if stream:
        body["stream"] = True
    body.update(extra)
    return body


def anthropic_tool_result_request(
    *, model: str = DEFAULT_MODEL, stream: bool = False
) -> dict[str, Any]:
    """The A5 follow-up turn: assistant ``tool_use`` echoed back with a ``tool_result``.

    This is the conversation shape Backline's agents actually send, and the one that
    dies first if a gateway normalizes bodies — the assistant turn replays the tool_use
    block verbatim, so any re-encoding shows up as a mismatched ``tool_use_id`` or a
    mangled ``input`` at the provider.
    """
    body: dict[str, Any] = {
        "model": model,
        "max_tokens": 64,
        "tools": ANTHROPIC_TOOLS,
        "messages": [
            {"role": "user", "content": "How did Björk's catalogue perform in 2024-Q3?"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Let me look that up."},
                    {
                        "type": "tool_use",
                        "id": "toolu_mock_0001",
                        "name": "get_track_metrics",
                        "input": {
                            "period": "2024-Q3",
                            "artist": "Björk",
                            "filters": {
                                "include_features": True,
                                "regions": ["NA", "EU"],
                                "note": 'say "hi"',
                            },
                        },
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_mock_0001",
                        "content": '{"streams": 1240933, "note": "say \\"hi\\""}',
                    }
                ],
            },
        ],
    }
    if stream:
        body["stream"] = True
    return body


def openai_request(
    *,
    model: str = DEFAULT_MODEL,
    stream: bool = False,
    text: str = "How did Björk's catalogue perform in 2024-Q3?",
    **extra: Any,
) -> dict[str, Any]:
    """A minimal chat-completions request."""
    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": text}],
    }
    if stream:
        body["stream"] = True
        body["stream_options"] = {"include_usage": True}
    body.update(extra)
    return body
