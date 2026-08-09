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

**Usage arrives in two halves, and only one of them is trustworthy early.**
``message_start`` carries the prompt counts — and an ``output_tokens`` of ``0``, which
is a placeholder rather than a measurement. The real generated count lands in
``message_delta`` beside the ``stop_reason``, at the very end. The observer below
therefore *ignores* ``output_tokens`` on ``message_start`` entirely. That is what makes
a cut stream honest: a connection that dies mid-answer leaves the output count
unknown, and unknown is what the ledger records — where taking the placeholder at face
value would have written a confident, free, and completely wrong row.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Final

from headroom.core.sse import SSEEvent, format_sse
from headroom.dialects.base import Dialect, register_dialect
from headroom.metering.usage import Usage, UsageObserver

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

    def usage_from_body(self, body: bytes) -> Usage:
        payload = _load(body)
        if payload is None:
            return Usage()
        stop_reason = payload.get("stop_reason")
        return _usage_of(payload.get("usage")).with_stop_reason(
            stop_reason if isinstance(stop_reason, str) else None
        )

    def usage_observer(self) -> UsageObserver:
        return _AnthropicUsageObserver()

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


# --------------------------------------------------------------------------------
# Usage extraction
# --------------------------------------------------------------------------------
#
# Everything below reads the provider's own `usage` object and nothing else. There is
# deliberately no code path that looks at content, counts characters, or estimates:
# the Phase 1 live smoke found a reply whose visible text was 11 characters and whose
# billed output was 63 tokens, and a meter that reads text is wrong by that margin.

#: Events that can carry usage. Used as a cheap gate before any JSON parse — on a long
#: response the overwhelming majority of frames are `content_block_delta`, which never
#: carries a count, and first-token latency is the product.
_USAGE_EVENTS: Final = frozenset({"message_start", "message_delta"})


def _load(body: bytes) -> Mapping[str, Any] | None:
    """Parse a response body, forgivingly: an error page is not an exception here."""
    try:
        payload = json.loads(body)
    except ValueError:
        return None
    return payload if isinstance(payload, Mapping) else None


def _int(value: Any) -> int | None:
    """A reported count, or ``None``. ``bool`` is excluded because it is an ``int``."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _usage_of(block: Any, *, include_output: bool = True) -> Usage:
    """Map one Anthropic ``usage`` object onto :class:`Usage`.

    ``include_output=False`` is the ``message_start`` case: its ``output_tokens`` is a
    zero placeholder emitted before a single token exists, and treating it as a
    measurement is how a cut stream would get billed as a complete, free answer.
    """
    if not isinstance(block, Mapping):
        return Usage()
    return Usage(
        input_tokens=_int(block.get("input_tokens")),
        output_tokens=_int(block.get("output_tokens")) if include_output else None,
        cache_read_tokens=_int(block.get("cache_read_input_tokens")),
        cache_write_tokens=_int(block.get("cache_creation_input_tokens")),
    )


class _AnthropicUsageObserver(UsageObserver):
    """Accumulates ``message_start`` + ``message_delta`` into one usage record."""

    __slots__ = ("_usage",)

    def __init__(self) -> None:
        self._usage = Usage()

    @property
    def usage(self) -> Usage:
        return self._usage

    def feed(self, event: SSEEvent) -> None:
        # The event name is authoritative and free to read. When a server omits the
        # `event:` line — legal SSE — fall back to the payload's own `type`, which
        # costs a parse only on frames that mention usage at all.
        name = event.event
        if name is None:
            if '"usage"' not in event.data:
                return
        elif name not in _USAGE_EVENTS:
            return

        payload = event.json()
        if not isinstance(payload, Mapping):
            return
        kind = name or payload.get("type")

        if kind == "message_start":
            message = payload.get("message")
            block = message.get("usage") if isinstance(message, Mapping) else None
            self._usage = self._usage.merge(_usage_of(block, include_output=False))
            return

        if kind == "message_delta":
            self._usage = self._usage.merge(_usage_of(payload.get("usage")))
            delta = payload.get("delta")
            stop_reason = delta.get("stop_reason") if isinstance(delta, Mapping) else None
            if isinstance(stop_reason, str):
                self._usage = self._usage.with_stop_reason(stop_reason)
