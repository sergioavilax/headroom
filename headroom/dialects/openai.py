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
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Final

from headroom.core.sse import SSEEvent, format_sse
from headroom.dialects.base import Dialect, register_dialect

__all__ = ["DONE_SENTINEL", "OPENAI", "OpenAIDialect"]

DONE_SENTINEL: Final = "[DONE]"

# OpenAI's error `type` values, by status.
_ERROR_TYPES: Final[Mapping[int, str]] = {
    400: "invalid_request_error",
    401: "authentication_error",
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
