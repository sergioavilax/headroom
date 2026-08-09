"""What a dialect has to know, and — just as importantly — what it must not do.

BUILD_PLAN L4 locks the shape: *passthrough per dialect*. Anthropic-dialect requests
go to Anthropic-dialect backends; OpenAI-dialect requests go to OpenAI-compatible
backends. **Cross-dialect translation is explicitly out of scope** — it is LiteLLM's
entire codebase and a swamp of edge cases, and the plan names the cut instead of
hiding it.

So a dialect here is not a translator. It answers five narrow questions the gateway
cannot avoid asking:

1. **Which model is this?** — routing needs it, and it is the only reason the request
   body is parsed at all.
2. **Did the caller ask for a stream?** — it decides which response path runs.
3. **Did this stream actually finish?** — the difference between an answer and a
   fragment (``tests/test_mid_stream_cut.py``).
4. **How do I say "it failed" in a way this dialect's SDK will understand?** — an
   error the client's SDK cannot parse is not much better than no error at all.
5. **What did the provider say this cost?** (Phase 3) — read out of the response's
   *usage block*, in this dialect's spelling of it, and never inferred from the text.
   The two shapes differ enough to be worth a method: Anthropic splits usage across
   ``message_start`` and ``message_delta``, while the OpenAI dialect appends a single
   usage-bearing chunk *after* the frame that ends the answer.
6. **Is this one plain question, and where is it?** (Phase 5) — the semantic cache
   embeds the user's turn and hashes everything else, so it has to know which part is
   which. Also a dialect question, and a consequential one: Anthropic's system prompt is
   a top-level field while the OpenAI dialect's is a message, so a single shared reading
   of ``messages`` would quietly drop one of them out of the key.

Note what is absent: nothing here rewrites, re-encodes, or normalizes a request or a
response. The body the client sent is the body the provider receives, byte for byte,
which is what makes assumption **A5** (tool blocks round-trip untouched) hold by
construction rather than by careful copying.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any

from headroom.core.cache import PROBE_SENTINEL, CacheProbe
from headroom.core.sse import SSEEvent
from headroom.metering.usage import Usage, UsageObserver

__all__ = [
    "Dialect",
    "dialect_for",
    "redact_message",
    "register_dialect",
    "text_of_content",
]


def text_of_content(content: Any) -> str | None:
    """A message's content as plain text, or ``None`` if it is not plain text.

    Both dialects accept either a bare string or a list of typed blocks, and both spell
    a text block ``{"type": "text", "text": …}``. Anything else in the list — an image,
    a document, a ``tool_result`` — returns ``None``, which makes the request ineligible.
    That is the intended behaviour and not a limitation to be lifted casually: an image
    is part of the question, and a cache that embedded only the words beside it would
    answer one picture's question with another picture's answer.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list) or not content:
        return None
    parts: list[str] = []
    for block in content:
        if not isinstance(block, Mapping) or block.get("type") != "text":
            return None
        text = block.get("text")
        if not isinstance(text, str):
            return None
        parts.append(text)
    return "\n".join(parts)


def redact_message(body: Mapping[str, Any], messages: list[Any], index: int) -> dict[str, Any]:
    """A shallow copy of ``body`` whose ``messages[index]`` content is the sentinel.

    Shallow everywhere except the one message being rewritten, because the result is
    canonicalised and hashed immediately and then discarded — it is never sent, never
    stored, and never handed back to a caller (``headroom/cache/keys.py``).
    """
    replaced = list(messages)
    replaced[index] = {**messages[index], "content": PROBE_SENTINEL}
    return {**body, "messages": replaced}


class Dialect(ABC):
    """One wire format, read-only. Instances are stateless singletons."""

    #: Short name used in config, the ledger, and log lines.
    name: str
    #: The path Headroom serves.
    route_path: str
    #: The path on the upstream provider. Same value today; a provider that mounts the
    #: API elsewhere would differ, and Phase 6 gets to keep that flexibility.
    upstream_path: str

    # --- reading the request ------------------------------------------------------

    @abstractmethod
    def model_of(self, body: Mapping[str, Any]) -> str | None:
        """The requested model, or None if the body does not name one."""

    @abstractmethod
    def wants_stream(self, body: Mapping[str, Any]) -> bool:
        """Whether the caller asked for a streamed response."""

    @abstractmethod
    def max_output_tokens(self, body: Mapping[str, Any]) -> int | None:
        """The caller's own ceiling on generated tokens, or ``None`` if they set none.

        Phase 4 reads this, and only this, to bound what a request can cost *before* it
        runs: it is the one number in the body that says how much the provider is
        permitted to generate. ``None`` is a real answer — the OpenAI dialect makes the
        field optional — and the budget gate substitutes a documented default rather
        than pretending the request is free (``headroom/policy/budgets.py``).

        Note what this still is not: validation. A nonsensical value is the provider's
        to reject, and anything not a positive integer reads here as "not stated".
        """

    # --- reading the response stream ----------------------------------------------

    @abstractmethod
    def is_terminal(self, event: SSEEvent) -> bool:
        """Whether this event means "the response is over".

        Seeing one is what licenses the gateway to end the stream silently. Never
        seeing one is a truncation, and the caller gets told.
        """

    # --- reading what it cost (Phase 3) -------------------------------------------

    @abstractmethod
    def usage_from_body(self, body: bytes) -> Usage:
        """The usage block of a complete, non-streamed response.

        Returns an empty :class:`~headroom.metering.usage.Usage` rather than raising
        when there is nothing to read — an upstream error body, a truncated payload, a
        provider that omits the block. "The provider did not say" is a state the ledger
        records; it is not an exception.
        """

    @abstractmethod
    def usage_observer(self) -> UsageObserver:
        """A fresh accumulator for one streamed response.

        Fed the same events the completion check sees, from the same tap (H-007), so
        metering a stream adds no second parse and cannot touch a forwarded byte.
        """

    # --- reading the question (Phase 5) ---------------------------------------------

    @abstractmethod
    def cache_probe(self, body: Mapping[str, Any]) -> CacheProbe | None:
        """The single user turn's text plus the request with that text redacted.

        ``None`` means *this request is not one plain question*, and every reason for it
        is a reason not to cache: more than one user turn (history the probe would not
        see), an assistant turn, non-text content, or a body whose ``messages`` are not
        the shape either API documents. The caller treats ``None`` as ineligible rather
        than as an error, because "not cacheable" is the overwhelmingly common answer.

        Conservative by construction: anything unrecognised returns ``None``. The cost of
        a false negative is a cache miss; the cost of a false positive is embedding one
        request and answering a different one with it.
        """

    # --- speaking failure ---------------------------------------------------------

    @abstractmethod
    def error_payload(
        self, *, status_code: int, reason: str, message: str, request_id: str
    ) -> dict[str, Any]:
        """The dialect's own error object, as a JSON-ready dict.

        Every payload carries a ``headroom`` block alongside the dialect's fields:
        the stable machine-readable ``reason`` and the request id, so an operator can
        go from a caller's screenshot to the exact ledger row. SDKs ignore unknown
        top-level keys, so this costs the caller nothing.
        """

    def error_body(self, *, status_code: int, reason: str, message: str, request_id: str) -> bytes:
        """``error_payload`` serialized for a non-streaming response."""
        payload = self.error_payload(
            status_code=status_code, reason=reason, message=message, request_id=request_id
        )
        return json.dumps(payload, separators=(",", ":")).encode("utf-8")

    @abstractmethod
    def terminal_error_event(self, *, reason: str, message: str, request_id: str) -> bytes:
        """The frame appended to a stream that failed after it began.

        This is the load-bearing method of the phase. It must produce something the
        dialect's own SDK raises on — not merely something a human could read in a
        transcript — because the failure it reports is invisible otherwise.
        """


_DIALECTS: dict[str, Dialect] = {}


def register_dialect(dialect: Dialect) -> Dialect:
    """Add a dialect to the lookup table used by config and routing."""
    _DIALECTS[dialect.name] = dialect
    return dialect


def dialect_for(name: str) -> Dialect:
    """Look a dialect up by name; raises ``KeyError`` for an unknown one."""
    return _DIALECTS[name]
