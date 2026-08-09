"""The Anthropic dialect: ``POST /v1/messages``.

Stream shape (from the Messages API streaming reference): ``message_start``,
``content_block_start``, a run of ``content_block_delta``, ``content_block_stop``,
``message_delta`` — which carries ``stop_reason`` and the output-token count Phase 3
will meter — and finally ``message_stop``. ``ping`` events may appear anywhere.

**``message_stop`` is the only "we finished" signal**, and Anthropic always sends it,
so the completeness test here is strict: a stream that ends without ``message_stop``
(or without an upstream ``error`` event, which is upstream telling the truth itself)
is a truncation, and the caller is told. The OpenAI dialect has to be more lenient;
see the note there.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Final

from headroom.core.sse import SSEEvent, format_sse
from headroom.dialects.base import Dialect, register_dialect

__all__ = ["ANTHROPIC", "AnthropicDialect"]

#: Sent upstream when the caller did not send one of their own. The Messages API
#: requires the header; a caller using raw curl usually forgets it.
DEFAULT_API_VERSION: Final = "2023-06-01"

# Anthropic's documented error types, by status. Only documented values appear here:
# a gateway is a bad place to invent vocabulary, and the exact cause travels in
# `headroom.reason` where no SDK can be surprised by it (docs/DECISIONS.md H-009).
_ERROR_TYPES: Final[Mapping[int, str]] = {
    400: "invalid_request_error",
    401: "authentication_error",
    403: "permission_error",
    404: "not_found_error",
    413: "request_too_large",
    429: "rate_limit_error",
    500: "api_error",
    529: "overloaded_error",
}

_TERMINAL_EVENTS: Final = frozenset({"message_stop", "error"})


class AnthropicDialect(Dialect):
    name = "anthropic"
    route_path = "/v1/messages"
    upstream_path = "/v1/messages"

    def model_of(self, body: Mapping[str, Any]) -> str | None:
        model = body.get("model")
        return model if isinstance(model, str) else None

    def wants_stream(self, body: Mapping[str, Any]) -> bool:
        return body.get("stream") is True

    def is_terminal(self, event: SSEEvent) -> bool:
        # The event name is authoritative and free to read. Falling back to the data
        # payload's `type` covers a server that omits the `event:` line — legal SSE,
        # and the JSON parse only runs in that uncommon case rather than per chunk.
        if event.event is not None:
            return event.event in _TERMINAL_EVENTS
        payload = event.json()
        return isinstance(payload, dict) and payload.get("type") in _TERMINAL_EVENTS

    def error_payload(
        self, *, status_code: int, reason: str, message: str, request_id: str
    ) -> dict[str, Any]:
        return {
            "type": "error",
            "error": {
                "type": _ERROR_TYPES.get(status_code, "api_error"),
                "message": message,
            },
            "headroom": {"reason": reason, "request_id": request_id},
        }

    def terminal_error_event(self, *, reason: str, message: str, request_id: str) -> bytes:
        """``event: error`` — part of the Messages API streaming spec.

        ``anthropic-python`` raises on this event rather than returning the partial
        message, which is the behaviour that makes a truncation impossible to mistake
        for an answer. The status is 500 → ``api_error``: the failure happened after
        the 200 status line was sent, so the HTTP status can no longer carry it.
        """
        payload = self.error_payload(
            status_code=500, reason=reason, message=message, request_id=request_id
        )
        return format_sse(json.dumps(payload, separators=(",", ":")), event="error")


ANTHROPIC: Final = register_dialect(AnthropicDialect())
