"""The OpenAI dialect: ``POST /v1/chat/completions`` — the vLLM path (BUILD_PLAN L5).

Two places where this dialect is genuinely less well-specified than Anthropic's, and
both matter to the mid-stream-cut guarantee:

**Completion is a convention, not a spec.** OpenAI ends a stream with a literal
``data: [DONE]`` frame, and vLLM follows suit, but it is convention rather than
protocol — and a chunk carrying a non-null ``finish_reason`` is an equally honest
statement that the model stopped. Treating *either* as terminal keeps the gateway from
crying truncation at a compliant-but-terse backend, while still catching a stream that
stopped mid-sentence with neither signal. (A ``finish_reason`` of ``length`` means a
*complete stream* of a *truncated answer* — a real distinction, and Phase 5's business:
invariant 6 will refuse to cache it. Here the only question is whether the stream ended
or was cut.)

**There is no error event type.** The de-facto mechanism is a data frame whose JSON
carries an ``error`` key; ``openai-python`` raises ``APIError`` on exactly that shape.
So that is what a cut produces here — and, critically, no ``[DONE]`` after it, because
``[DONE]`` is precisely the claim that would make a fragment look finished.

**Usage is opt-in, and the gateway does not opt in on the caller's behalf.** A streamed
chat completion carries token counts only when the request set
``stream_options: {"include_usage": true}``; without it the trailing usage chunk simply
never arrives and the response is unmeterable. Headroom could inject the option upstream
and strip the extra chunk on the way back — and deliberately does not, because both
halves of that trick require rewriting bytes the whole proxy is built never to touch
(docs/DECISIONS.md H-028). Such a request is recorded with ``usage_unknown`` and a NULL
cost instead: an honest gap the dashboard can show and a caller can close by asking for
usage, rather than an estimate nobody can audit.

One consequence worth stating: ``prompt_tokens`` here is *inclusive* of any cached
prompt tokens, where Anthropic's ``input_tokens`` excludes them. Both are recorded as
"the tokens priced at the input rate" for their dialect, with the cache count kept
beside them — see ``headroom/metering/usage.py``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Final

from headroom.core.sse import SSEEvent, format_sse
from headroom.dialects.base import Dialect, register_dialect
from headroom.metering.usage import Usage, UsageObserver

__all__ = ["DONE_SENTINEL", "OPENAI", "OpenAIDialect"]

DONE_SENTINEL: Final = "[DONE]"

# OpenAI's error `type` values, by status.
_ERROR_TYPES: Final[Mapping[int, str]] = {
    400: "invalid_request_error",
    401: "authentication_error",
    # OpenAI's own type *and* code for "you exceeded your current quota", which is what
    # a budget refusal is from the caller's side (Phase 4).
    402: "insufficient_quota",
    403: "permission_error",
    404: "not_found_error",
    413: "invalid_request_error",
    429: "rate_limit_error",
    500: "api_error",
    529: "api_error",
}


class OpenAIDialect(Dialect):
    name = "openai"
    route_path = "/v1/chat/completions"
    upstream_path = "/v1/chat/completions"

    def model_of(self, body: Mapping[str, Any]) -> str | None:
        model = body.get("model")
        return model if isinstance(model, str) else None

    def wants_stream(self, body: Mapping[str, Any]) -> bool:
        return body.get("stream") is True

    def max_output_tokens(self, body: Mapping[str, Any]) -> int | None:
        """``max_completion_tokens``, else the legacy ``max_tokens``, else ``None``.

        Both spellings are in the wild — OpenAI deprecated ``max_tokens`` in favour of
        ``max_completion_tokens``, vLLM accepts either — and unlike the Anthropic
        dialect neither is required. A request that states no ceiling is the case the
        budget gate's documented default exists for.
        """
        stated = _positive_int(body.get("max_completion_tokens"))
        return stated if stated is not None else _positive_int(body.get("max_tokens"))

    def is_terminal(self, event: SSEEvent) -> bool:
        data = event.data
        if data.strip() == DONE_SENTINEL:
            return True
        # Cheap substring gate before the JSON parse: on a long response the vast
        # majority of chunks are content deltas that can match neither branch, and
        # first-token latency is the product.
        if '"finish_reason"' not in data and '"error"' not in data:
            return False
        payload = event.json()
        if not isinstance(payload, dict):
            return False
        if payload.get("error") is not None:
            return True
        choices = payload.get("choices")
        if not isinstance(choices, list):
            return False
        return any(
            isinstance(choice, dict) and choice.get("finish_reason") is not None
            for choice in choices
        )

    def usage_from_body(self, body: bytes) -> Usage:
        try:
            payload = json.loads(body)
        except ValueError:
            return Usage()
        if not isinstance(payload, Mapping):
            return Usage()
        return _usage_of(payload).with_stop_reason(_finish_reason(payload))

    def usage_observer(self) -> UsageObserver:
        return _OpenAIUsageObserver()

    def error_payload(
        self, *, status_code: int, reason: str, message: str, request_id: str
    ) -> dict[str, Any]:
        return {
            "error": {
                "message": message,
                "type": _ERROR_TYPES.get(status_code, "api_error"),
                "param": None,
                "code": reason,
            },
            "headroom": {"reason": reason, "request_id": request_id},
        }

    def terminal_error_event(self, *, reason: str, message: str, request_id: str) -> bytes:
        """A bare ``data:`` frame carrying ``error`` — no event name, and no ``[DONE]``."""
        payload = self.error_payload(
            status_code=500, reason=reason, message=message, request_id=request_id
        )
        return format_sse(json.dumps(payload, separators=(",", ":")))


OPENAI: Final = register_dialect(OpenAIDialect())


# --------------------------------------------------------------------------------
# Usage extraction
# --------------------------------------------------------------------------------
#
# The usage object, and only the usage object. `completion_tokens` is the number the
# provider bills; `completion_tokens_details.reasoning_tokens` is the share of it spent
# on chain of thought, which is already inside the total and must never be added to it.
# Phase 1's reasoning fixture is the proof that the two cannot be recovered from text:
# 11 visible characters, 63 completion tokens, 57 of them reasoning.


def _int(value: Any) -> int | None:
    """A reported count, or ``None``. ``bool`` is excluded because it is an ``int``."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _positive_int(value: Any) -> int | None:
    """A stated ceiling, or ``None`` for anything that is not one."""
    parsed = _int(value)
    return parsed if parsed is not None and parsed > 0 else None


def _nested_int(payload: Mapping[str, Any], outer: str, inner: str) -> int | None:
    block = payload.get(outer)
    return _int(block.get(inner)) if isinstance(block, Mapping) else None


def _usage_of(payload: Mapping[str, Any]) -> Usage:
    """Map a chunk's or a body's ``usage`` object onto :class:`Usage`."""
    block = payload.get("usage")
    if not isinstance(block, Mapping):
        return Usage()
    return Usage(
        input_tokens=_int(block.get("prompt_tokens")),
        output_tokens=_int(block.get("completion_tokens")),
        reasoning_tokens=_nested_int(block, "completion_tokens_details", "reasoning_tokens"),
        cache_read_tokens=_nested_int(block, "prompt_tokens_details", "cached_tokens"),
    )


def _finish_reason(payload: Mapping[str, Any]) -> str | None:
    """The first non-null ``finish_reason`` among the choices, if any."""
    choices = payload.get("choices")
    if not isinstance(choices, list):
        return None
    for choice in choices:
        if not isinstance(choice, Mapping):
            continue
        reason = choice.get("finish_reason")
        if isinstance(reason, str):
            return reason
    return None


class _OpenAIUsageObserver(UsageObserver):
    """Watches for the trailing usage chunk and the frame that carries the finish reason.

    Ordering matters and is the reason this is an accumulator rather than a check on
    the last frame: with ``include_usage``, the chunk bearing ``finish_reason`` comes
    *before* the usage-only chunk, which comes before ``[DONE]``. A meter that stopped
    reading at the terminal marker would miss the counts entirely.
    """

    __slots__ = ("_usage",)

    def __init__(self) -> None:
        self._usage = Usage()

    @property
    def usage(self) -> Usage:
        return self._usage

    def feed(self, event: SSEEvent) -> None:
        # Cheap substring gates before any JSON parse. Every content chunk carries
        # `"finish_reason": null`, so its mere presence is not a signal — its
        # *null-ness* is. Skipping the compact spelling covers the frames that
        # dominate a long response without ever being wrong: a differently-spaced
        # server just falls through to the parse below and costs one extra `loads`.
        data = event.data
        if '"usage"' not in data and (
            '"finish_reason"' not in data or '"finish_reason":null' in data
        ):
            return
        payload = event.json()
        if not isinstance(payload, Mapping):
            return  # `[DONE]`, or a keep-alive comment that reached here somehow
        self._usage = self._usage.merge(_usage_of(payload))
        self._usage = self._usage.with_stop_reason(_finish_reason(payload))
