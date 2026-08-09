"""What may be looked up, what may be stored, and what may be embedded.

This is the safety case. BUILD_PLAN §0.2 invariant 6 exists because of Backline's
**D-021**, where a cache served content that did not belong to the question asked, and
the Phase 5 form of that scar is worse than the original: *"a semantic cache that stores
an amputated answer poisons every future hit."* One bad write is served forever.

So the rules here are conservative in one direction on purpose. A false negative costs a
cache miss — an upstream call somebody was going to make anyway. A false positive costs a
wrong answer, delivered with confidence, repeatedly. Every rule below is written so that
anything unrecognised falls on the miss side, and ``tests/test_cache_poison.py`` attempts
to seed the cache through every one of them.

**Request side** (may we look this up, and may we store what comes back?)

* **One plain question.** BUILD_PLAN §P5 says *single-turn user content* and that is
  taken literally, for both layers rather than only the semantic one. Multi-turn exact
  caching would in fact be safe — the hash covers every byte — but the plan names the
  conservative rule, the value is low (verbatim repeats of a long conversation are rare),
  and widening the blast radius of a normalisation bug to whole conversations is not a
  trade this phase should make on its own authority (H-041).
* **No tools, anywhere, in any form.** Not merely "no ``tool_use`` block in the
  conversation": a request that *declares* ``tools`` is ineligible even when nothing has
  been called yet, because the same words with tools available may legitimately produce a
  tool call, and answering that with a cached paragraph is D-021 exactly. The scan is
  structural and deliberately over-broad — see :func:`declares_tools`.
* **Low temperature.** A caller asking for variety is asking for the opposite of a cache.
  The bound is a documented constant rather than a per-tenant knob: the plan makes the
  *similarity threshold* configurable and says nothing about this one, and one new dial
  per phase is enough.
* **One completion.** ``n > 1`` asks for several answers; a cache has one.

**Response side** (invariant 6, enforced)

Only a **complete** response is ever written: the request must have ended ``ok``, the
upstream must have answered < 400, the stream must have reached its terminal marker
(which is what an ``ok`` outcome means after Phase 1), and the stop reason must be one
that says *the model finished*. ``max_tokens`` and ``length`` are explicitly not that —
they are a complete *stream* of a truncated *answer*, which is the exact distinction
H-008 drew and the exact thing invariant 6 forbids storing.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from headroom.core.cache import CacheProbe
from headroom.dialects.base import Dialect

__all__ = [
    "COMPLETE_STOP_REASONS",
    "MAX_CACHEABLE_BODY_BYTES",
    "MAX_CACHEABLE_TEMPERATURE",
    "REASON_BODY_TOO_LARGE",
    "REASON_EMPTY_BODY",
    "REASON_INCOMPLETE",
    "REASON_MULTIPLE_COMPLETIONS",
    "REASON_NOT_SINGLE_TURN",
    "REASON_REASONING_RESPONSE",
    "REASON_TEMPERATURE",
    "REASON_TOOLS",
    "REASON_TOOL_OUTPUT",
    "REASON_UPSTREAM_ERROR",
    "Eligibility",
    "StoreDecision",
    "declares_tools",
    "may_cache_request",
    "may_store_response",
]

#: Above this, the caller is asking for variety and a cache is the wrong answer. 0.2
#: rather than 0.0 because a great deal of production traffic sets a small non-zero
#: temperature without wanting different answers to the same question, and refusing all
#: of it would make the feature useless on real workloads.
#:
#: Note the belt and braces: temperature is *also* inside the exact key and inside
#: ``context_hash``, so requests at two different temperatures never share an entry. This
#: bound is about whether caching is the right behaviour at all, not about correctness of
#: the match.
MAX_CACHEABLE_TEMPERATURE: Final = 0.2

#: 1 MiB. A bound on what one entry can cost the table, not a correctness rule — but an
#: unbounded one would let a single enormous response dominate a tenant's cache and the
#: backup that follows it.
MAX_CACHEABLE_BODY_BYTES: Final = 1024 * 1024

#: Stop reasons that mean *the model finished saying what it had to say*. Anthropic's
#: ``end_turn`` and ``stop_sequence``; the OpenAI dialect's ``stop``. Everything else is
#: excluded by omission, which is the right default for this list: a stop reason nobody
#: here has heard of is not evidence of completeness.
COMPLETE_STOP_REASONS: Final = frozenset({"end_turn", "stop_sequence", "stop"})

# Reasons, stable from here: they reach the log line and Phase 7 charts them.
REASON_NOT_SINGLE_TURN: Final = "not_single_turn"
REASON_TOOLS: Final = "tools_present"
REASON_TEMPERATURE: Final = "temperature_above_bound"
REASON_MULTIPLE_COMPLETIONS: Final = "multiple_completions"
REASON_UPSTREAM_ERROR: Final = "upstream_error"
REASON_INCOMPLETE: Final = "incomplete_response"
REASON_EMPTY_BODY: Final = "empty_body"
REASON_BODY_TOO_LARGE: Final = "body_too_large"
REASON_TOOL_OUTPUT: Final = "tool_output"
REASON_REASONING_RESPONSE: Final = "reasoning_response"

#: Object keys that mean tools are in play. ``functions``/``function_call`` are the
#: legacy spellings and are included because a deployment in front of an older client is
#: exactly where nobody would think to look.
_TOOL_KEYS: Final = frozenset({"tools", "tool_choice", "tool_calls", "functions", "function_call"})
#: Content-block ``type`` values that are a tool call or its result, across both dialects
#: and both vendors' extensions.
_TOOL_BLOCK_TYPES: Final = frozenset(
    {"tool_use", "tool_result", "server_tool_use", "web_search_tool_result", "tool_call"}
)

#: Byte markers scanned for in a *response*. Coarse on purpose: a streamed response is
#: SSE frames rather than a JSON document, so there is nothing to walk, and the cost of
#: refusing to cache an answer that merely discusses tool calls in prose is one cache
#: miss.
_TOOL_MARKERS: Final = (b'"tool_use"', b'"tool_calls"', b'"tool_result"', b'"function_call"')


def declares_tools(value: Any, *, depth: int = 0) -> bool:
    """Whether anything anywhere in a parsed request involves tools.

    A recursive structural scan rather than a check of the two or three places tools are
    *supposed* to appear. The reason is the failure mode: a shape this gateway has not
    anticipated — a new content-block type, a vendor extension, a nested message list —
    would slip past a targeted check silently and be discovered as a wrong answer. A scan
    that walks the whole body can only be wrong in the direction of refusing to cache
    something it could have.

    It inspects **keys and typed markers**, never free text, so a user asking a question
    *about* tool use is unaffected.
    """
    if depth > 32:  # pragma: no cover - JSON that deep is already pathological
        return True
    if isinstance(value, Mapping):
        if any(key in _TOOL_KEYS for key in value):
            return True
        if value.get("type") in _TOOL_BLOCK_TYPES or value.get("role") == "tool":
            return True
        return any(declares_tools(item, depth=depth + 1) for item in value.values())
    if isinstance(value, list):
        return any(declares_tools(item, depth=depth + 1) for item in value)
    return False


@dataclass(frozen=True, slots=True)
class Eligibility:
    """Whether this request may use the cache, and — if not — the reason for the log."""

    probe: CacheProbe | None = None
    reason: str | None = None

    @property
    def ok(self) -> bool:
        return self.probe is not None


def may_cache_request(dialect: Dialect, body: Mapping[str, Any]) -> Eligibility:
    """Decide once per request, before any lookup and before any embedding.

    The order of the checks is cheapest-first and is otherwise unimportant, because the
    result is a refusal either way — unlike the *gate* order in ``headroom/api/proxy.py``,
    where which refusal a caller sees is a decision in itself (H-039).
    """
    if declares_tools(body):
        return Eligibility(reason=REASON_TOOLS)
    temperature = body.get("temperature")
    if (
        isinstance(temperature, int | float)
        and not isinstance(temperature, bool)
        and float(temperature) > MAX_CACHEABLE_TEMPERATURE
    ):
        return Eligibility(reason=REASON_TEMPERATURE)
    completions = body.get("n")
    if isinstance(completions, int) and not isinstance(completions, bool) and completions > 1:
        return Eligibility(reason=REASON_MULTIPLE_COMPLETIONS)
    probe = dialect.cache_probe(body)
    if probe is None:
        return Eligibility(reason=REASON_NOT_SINGLE_TURN)
    return Eligibility(probe=probe)


@dataclass(frozen=True, slots=True)
class StoreDecision:
    """Whether a finished response may be stored, and whether it may be embedded.

    Two booleans rather than one, because they are genuinely different questions and the
    reasoning-model case is the reason (H-044): such a response is perfectly safe to
    replay for the **identical** question and is refused an embedding, so it can be hit
    exactly and can never be hit by similarity.
    """

    store: bool = False
    embed: bool = False
    reason: str | None = None


def may_store_response(
    *,
    outcome: str,
    upstream_status: int | None,
    stop_reason: str | None,
    body: bytes,
    reasoning_tokens: int | None,
) -> StoreDecision:
    """Invariant 6, enforced. Everything here is a reason *not* to write.

    ``outcome`` is the ``RequestContext``'s, so a mid-stream cut, an incomplete stream,
    and a client disconnect are all already excluded by the time this is asked — Phase 1
    made "the stream reached its terminal marker" the meaning of ``ok`` (H-008), and this
    function inherits that rather than re-deriving it from bytes.

    **The checks are ordered most-specific-first**, because the reason reaches the log
    line and an operator acts on it. An upstream 429 fails the outcome check *and* the
    status check; reporting it as ``incomplete_response`` would be true and useless,
    while ``upstream_error`` says which half of the system to go and look at. (Found by
    ``tests/test_cache_poison.py``, which asserted the precise reason and got the vague
    one.)
    """
    if upstream_status is not None and upstream_status >= 400:
        return StoreDecision(reason=REASON_UPSTREAM_ERROR)
    if outcome != "ok":
        return StoreDecision(reason=REASON_INCOMPLETE)
    if upstream_status is None:
        # Ended ``ok`` with no upstream status at all: nothing was fetched, so there is
        # nothing to store. Unreachable from the proxy today — a hit has no plan — and
        # left explicit rather than relying on that staying true.
        return StoreDecision(reason=REASON_UPSTREAM_ERROR)
    if stop_reason not in COMPLETE_STOP_REASONS:
        # The headline case: `max_tokens` / `length` is a complete stream of a truncated
        # answer, and storing it is precisely what D-021 did one layer up.
        return StoreDecision(reason=REASON_INCOMPLETE)
    if not body:
        return StoreDecision(reason=REASON_EMPTY_BODY)
    if len(body) > MAX_CACHEABLE_BODY_BYTES:
        return StoreDecision(reason=REASON_BODY_TOO_LARGE)
    if any(marker in body for marker in _TOOL_MARKERS):
        return StoreDecision(reason=REASON_TOOL_OUTPUT)
    if reasoning_tokens:
        # Storable, not embeddable. An exact hit replays this chain of thought for the
        # question it was actually produced for; a *semantic* hit would replay reasoning
        # about someone else's question, visibly naming the wrong subject — a failure
        # mode beyond "the answer is wrong", and one §P8.H1's metric would not even see.
        return StoreDecision(store=True, embed=False, reason=REASON_REASONING_RESPONSE)
    return StoreDecision(store=True, embed=True)
