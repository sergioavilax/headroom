"""What the meter reads: the provider's own usage block, and nothing else.

**The rule this module exists to enforce:** *the meter reads the usage block; it
never infers from the text.* That is not a stylistic preference — Phase 1's live
vLLM smoke found the counterexample on real hardware and
``tests/test_reasoning_passthrough.py`` pins it keylessly: a reasoning model emitted
**eleven visible characters** and billed **sixty-three completion tokens**, fifty-seven
of them chain-of-thought that never appeared in ``delta.content`` at all. A meter that
counts what it can see undercharges by 90% on that request and by an unknowable amount
on the next one. Reasoning tokens are output tokens, they are inside the counts the
provider reports, and the only place they exist is the usage block.

So :class:`Usage` is deliberately a *record of what the provider said*, with every
field nullable. ``None`` means "the provider did not tell us", which is a different and
much more important fact than ``0``. The ledger keeps that distinction all the way to
the column, and the cost layer refuses to price a request whose counts are unknown
rather than guessing a zero.

**Cache tiers are recorded, not priced.** Anthropic reports cache reads and cache
writes as separate token classes billed at rates ``config/models.yaml`` has no field
for. They are captured here so the ledger can say what happened, and a request that
reports them is marked ``partial`` downstream — a bound, honestly labelled, instead of
a total that looks complete (docs/DECISIONS.md H-026).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, replace

from headroom.core.sse import SSEEvent

__all__ = ["Usage", "UsageObserver"]


@dataclass(frozen=True, slots=True)
class Usage:
    """Token counts exactly as one provider reported them.

    Every field is ``None`` until the provider states it. Nothing here is derived,
    summed, or inferred from response content.
    """

    #: Prompt tokens the meter will price at the input rate. Per dialect this is
    #: Anthropic's ``usage.input_tokens`` (which excludes cached prompt tokens) and
    #: OpenAI's ``usage.prompt_tokens`` (which includes them). The difference is why
    #: :attr:`cache_read_tokens` is recorded beside it and why a request that reports
    #: cache activity is never billed as complete.
    input_tokens: int | None = None
    #: Generated tokens, **reasoning-inclusive** — the provider's own total, not the
    #: visible answer's length.
    output_tokens: int | None = None
    #: The share of :attr:`output_tokens` spent on chain of thought, when the provider
    #: breaks it out. Informational: it is already inside ``output_tokens`` and must
    #: never be added to it.
    reasoning_tokens: int | None = None
    #: Prompt tokens served from the provider's cache (Anthropic
    #: ``cache_read_input_tokens``; OpenAI ``prompt_tokens_details.cached_tokens``).
    cache_read_tokens: int | None = None
    #: Prompt tokens written to the provider's cache (Anthropic
    #: ``cache_creation_input_tokens``). No OpenAI-dialect equivalent is reported.
    cache_write_tokens: int | None = None
    #: Why generation ended — Anthropic's ``stop_reason``, OpenAI's ``finish_reason``.
    #: Phase 5 reads it before it is allowed to cache anything (invariant 6).
    stop_reason: str | None = None

    @property
    def is_empty(self) -> bool:
        """Nothing was reported at all — no usage block reached the gateway."""
        return self.input_tokens is None and self.output_tokens is None

    @property
    def is_complete(self) -> bool:
        """Both billable counts are known, so a cost can be computed exactly."""
        return self.input_tokens is not None and self.output_tokens is not None

    @property
    def reports_cache_activity(self) -> bool:
        """The provider billed some prompt tokens at a cache tier this file cannot price."""
        return bool(self.cache_read_tokens) or bool(self.cache_write_tokens)

    def merge(self, other: Usage) -> Usage:
        """Overlay ``other``'s stated fields onto this one; ``None`` never overwrites.

        Streams report usage in pieces — Anthropic puts ``input_tokens`` in
        ``message_start`` and ``output_tokens`` in ``message_delta``, several frames
        apart — so accumulating means merging partial records, and a later frame that
        is silent about a field must not erase what an earlier one said.
        """
        return Usage(
            input_tokens=_prefer(other.input_tokens, self.input_tokens),
            output_tokens=_prefer(other.output_tokens, self.output_tokens),
            reasoning_tokens=_prefer(other.reasoning_tokens, self.reasoning_tokens),
            cache_read_tokens=_prefer(other.cache_read_tokens, self.cache_read_tokens),
            cache_write_tokens=_prefer(other.cache_write_tokens, self.cache_write_tokens),
            stop_reason=_prefer(other.stop_reason, self.stop_reason),
        )

    def with_stop_reason(self, stop_reason: str | None) -> Usage:
        """A copy carrying ``stop_reason``, leaving the counts alone."""
        if stop_reason is None:
            return self
        return replace(self, stop_reason=stop_reason)


def _prefer[T](new: T | None, old: T | None) -> T | None:
    return old if new is None else new


class UsageObserver(ABC):
    """Accumulates usage across a streamed response, one SSE event at a time.

    A dialect returns one of these from ``Dialect.usage_observer()``. It is fed the
    *same* events the completion detector sees — the copy H-007's tap already
    produces — so metering a stream costs no extra parsing pass and, critically,
    cannot alter a byte the client receives.

    Implementations must be cheap on the hot path: the overwhelming majority of
    events in a long response are content deltas that carry no usage, and
    first-token latency is the product.
    """

    __slots__ = ()

    @abstractmethod
    def feed(self, event: SSEEvent) -> None:
        """Absorb one dispatched event. Called for every event, in order."""

    @property
    @abstractmethod
    def usage(self) -> Usage:
        """Everything reported so far. Safe to read at any point, including mid-cut."""
