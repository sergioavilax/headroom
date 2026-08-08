"""Canonical wire payloads for the MockProvider — both dialects, byte-exact.

These builders are the shape of every keyless test in the project, so two properties
matter more than convenience:

**Deterministic.** No clock, no randomness, no dict iteration order left to chance.
Ids are derived from the content by hash, ``created`` is pinned to 0. The same call
produces the same bytes on every machine and every run, which is what lets a test
assert equality against them rather than around them.

**Faithful.** The event sequences match what the real APIs send, including the ones a
naive mock omits — ``ping`` events in the Anthropic stream, the trailing usage-only
chunk in the OpenAI one. A test double that is easier to satisfy than production is a
test double that certifies nothing.

Usage blocks are arguments rather than constants: Phase 3 meters streamed usage
(assumption A3) and needs to script exact token counts, including the values that make
a hand-computed cost check land on a known figure.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Final

from headroom.core.sse import format_sse

__all__ = [
    "anthropic_message_body",
    "anthropic_stream_chunks",
    "anthropic_tool_use_body",
    "anthropic_tool_use_stream_chunks",
    "openai_completion_body",
    "openai_stream_chunks",
    "split_every",
    "text_deltas",
]

DEFAULT_MODEL: Final = "mock-model-1"

#: Words with their trailing whitespace, so concatenating the deltas rebuilds the text
#: exactly — the property the streamed content-equality tests rest on.
_WORD = re.compile(r"\S+\s*")


def text_deltas(text: str) -> list[str]:
    """Split text into streaming deltas that reassemble to exactly ``text``."""
    return _WORD.findall(text) or ([text] if text else [])


def _stable_id(prefix: str, text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}{digest}"


def _dumps(payload: Any) -> str:
    return json.dumps(payload, separators=(",", ":"))


def split_every(payload: bytes, size: int) -> list[bytes]:
    """Chop bytes into fixed-size pieces, ignoring every frame and character boundary.

    The pathological chunker. Real networks split wherever they like — mid-SSE-frame,
    mid-JSON-token, mid-UTF-8-sequence — and a proxy that decodes or re-frames per
    chunk corrupts exactly those cases. ``split_every(payload, 1)`` is the meanest
    version available and still has to round-trip byte-for-byte (assumption A4).
    """
    if size < 1:
        raise ValueError("chunk size must be at least 1")
    return [payload[index : index + size] for index in range(0, len(payload), size)]


# --------------------------------------------------------------------------------
# Anthropic dialect
# --------------------------------------------------------------------------------


def anthropic_message_body(
    text: str,
    *,
    model: str = DEFAULT_MODEL,
    input_tokens: int = 11,
    output_tokens: int = 7,
    stop_reason: str = "end_turn",
) -> bytes:
    """A complete non-streamed ``Message``."""
    payload = {
        "id": _stable_id("msg_mock_", text),
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }
    return _dumps(payload).encode("utf-8")


def anthropic_stream_chunks(
    text: str,
    *,
    model: str = DEFAULT_MODEL,
    input_tokens: int = 11,
    output_tokens: int = 7,
    stop_reason: str = "end_turn",
    include_ping: bool = True,
) -> list[bytes]:
    """The full ``message_start`` … ``message_stop`` sequence, one chunk per event."""
    message_id = _stable_id("msg_mock_", text)
    chunks = [
        format_sse(
            _dumps(
                {
                    "type": "message_start",
                    "message": {
                        "id": message_id,
                        "type": "message",
                        "role": "assistant",
                        "model": model,
                        "content": [],
                        "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {"input_tokens": input_tokens, "output_tokens": 0},
                    },
                }
            ),
            event="message_start",
        ),
        format_sse(
            _dumps(
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                }
            ),
            event="content_block_start",
        ),
    ]
    if include_ping:
        chunks.append(format_sse(_dumps({"type": "ping"}), event="ping"))
    chunks.extend(
        format_sse(
            _dumps(
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": delta},
                }
            ),
            event="content_block_delta",
        )
        for delta in text_deltas(text)
    )
    chunks.append(
        format_sse(_dumps({"type": "content_block_stop", "index": 0}), event="content_block_stop")
    )
    chunks.append(
        format_sse(
            _dumps(
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                    "usage": {"output_tokens": output_tokens},
                }
            ),
            event="message_delta",
        )
    )
    chunks.append(format_sse(_dumps({"type": "message_stop"}), event="message_stop"))
    return chunks


#: A non-streamed reply containing a ``tool_use`` block — the A5 fixture.
#:
#: Written as a byte literal rather than built from a dict on purpose. Its key order is
#: not alphabetical, the input object is nested two deep, and it carries a non-ASCII
#: character and an embedded quote. Any accidental re-serialization anywhere in the
#: proxy — a ``json.loads``/``json.dumps`` round trip, a "helpful" normalization —
#: reorders keys or re-escapes that character, and the byte-equality assertion in
#: ``tests/test_tool_blocks.py`` fails. Verifying A5 means verifying *these* bytes.
ANTHROPIC_TOOL_USE_BODY: Final = (
    b'{"id":"msg_mock_tooluse_0001","type":"message","role":"assistant",'
    b'"model":"mock-model-1","content":['
    b'{"type":"text","text":"Let me look that up."},'
    b'{"type":"tool_use","id":"toolu_mock_0001","name":"get_track_metrics",'
    b'"input":{"period":"2024-Q3","artist":"Bj\\u00f6rk","filters":'
    b'{"include_features":true,"regions":["NA","EU"],"note":"say \\"hi\\""}}}'
    b'],"stop_reason":"tool_use","stop_sequence":null,'
    b'"usage":{"input_tokens":214,"output_tokens":63}}'
)


def anthropic_tool_use_body() -> bytes:
    """The A5 non-streaming fixture: a reply whose ``tool_use`` block must survive."""
    return ANTHROPIC_TOOL_USE_BODY


def anthropic_tool_use_stream_chunks(*, model: str = DEFAULT_MODEL) -> list[bytes]:
    """The streamed form: ``input_json_delta`` fragments that rebuild the tool input.

    Tool input arrives as partial JSON *strings* split across events. A proxy that
    treats each delta as a document rather than a fragment breaks here and nowhere
    else, which is why the splits below land mid-token.
    """
    partials = ['{"period":', '"2024-Q3","arti', 'st":"Bj\\u00f6rk"', "}"]
    chunks = [
        format_sse(
            _dumps(
                {
                    "type": "message_start",
                    "message": {
                        "id": "msg_mock_tooluse_stream",
                        "type": "message",
                        "role": "assistant",
                        "model": model,
                        "content": [],
                        "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {"input_tokens": 214, "output_tokens": 0},
                    },
                }
            ),
            event="message_start",
        ),
        format_sse(
            _dumps(
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {
                        "type": "tool_use",
                        "id": "toolu_mock_0001",
                        "name": "get_track_metrics",
                        "input": {},
                    },
                }
            ),
            event="content_block_start",
        ),
    ]
    chunks.extend(
        format_sse(
            _dumps(
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "input_json_delta", "partial_json": partial},
                }
            ),
            event="content_block_delta",
        )
        for partial in partials
    )
    chunks.append(
        format_sse(_dumps({"type": "content_block_stop", "index": 0}), event="content_block_stop")
    )
    chunks.append(
        format_sse(
            _dumps(
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "tool_use", "stop_sequence": None},
                    "usage": {"output_tokens": 63},
                }
            ),
            event="message_delta",
        )
    )
    chunks.append(format_sse(_dumps({"type": "message_stop"}), event="message_stop"))
    return chunks


# --------------------------------------------------------------------------------
# OpenAI dialect
# --------------------------------------------------------------------------------


def openai_completion_body(
    text: str,
    *,
    model: str = DEFAULT_MODEL,
    prompt_tokens: int = 11,
    completion_tokens: int = 7,
    finish_reason: str = "stop",
) -> bytes:
    """A complete non-streamed ``chat.completion``."""
    payload = {
        "id": _stable_id("chatcmpl-mock-", text),
        "object": "chat.completion",
        "created": 0,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }
    return _dumps(payload).encode("utf-8")


def openai_stream_chunks(
    text: str,
    *,
    model: str = DEFAULT_MODEL,
    prompt_tokens: int = 11,
    completion_tokens: int = 7,
    finish_reason: str = "stop",
    include_usage: bool = True,
) -> list[bytes]:
    """Role chunk, content deltas, a finish chunk, the usage chunk, then ``[DONE]``.

    The usage-only trailing chunk is what ``stream_options: {"include_usage": true}``
    produces and what vLLM supports — assumption A3, which Phase 3 converts to
    verified. It ships in the default fixture so P3 inherits a mock that already
    behaves like the thing it will meter.
    """
    completion_id = _stable_id("chatcmpl-mock-", text)

    def chunk(choices: list[dict[str, Any]], **extra: Any) -> bytes:
        payload: dict[str, Any] = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": 0,
            "model": model,
            "choices": choices,
        }
        payload.update(extra)
        return format_sse(_dumps(payload))

    chunks = [
        chunk([{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}])
    ]
    chunks.extend(
        chunk([{"index": 0, "delta": {"content": delta}, "finish_reason": None}])
        for delta in text_deltas(text)
    )
    chunks.append(chunk([{"index": 0, "delta": {}, "finish_reason": finish_reason}]))
    if include_usage:
        chunks.append(
            chunk(
                [],
                usage={
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                },
            )
        )
    chunks.append(format_sse("[DONE]"))
    return chunks
