"""The response cache's records and its storage contract.

``core/`` holds storage *interfaces* (BUILD_PLAN §0.5); ``headroom/db/cache.py`` and
``headroom/db/memory.py`` implement them, and ``tests/test_cache_store.py`` is one
contract suite over both — the H-021 shape, for the third time and for the same reason.

Two properties are load-bearing, and both are expressed as types here rather than as
rules somewhere else.

**Isolation is a value, not a habit.** :class:`CacheNamespace` is the *only* way to
address the cache: every read and every write takes one, and there is no method on
:class:`ResponseCacheStore` that can be called without naming a tenant. It is also what
salts the exact key (``headroom/cache/keys.py``), so the tenant participates in the
lookup twice — once in the predicate and once in the hash. That redundancy is deliberate
and it is what ``tests/test_cache_isolation.py`` sabotages: removing the scoping means
removing *this*, and the test proves that when you do, the leak is real and visible.

**An entry describes a response that actually happened.** It carries the bytes the
provider sent, the stop reason it sent them with, and the cost the request that fetched
them was billed — copied in, in H-024's spirit, because the "avoided cost" of a hit
should be a figure from a real invoice line rather than a re-pricing of a hypothetical.
What it does not carry is anything the gateway would have to invent: no synthesised
frames, no upstream headers describing a call that did not occur (H-043).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Final

__all__ = [
    "CACHE_DISABLED",
    "CACHE_EXACT",
    "CACHE_MODES",
    "CACHE_SEMANTIC",
    "DEFAULT_SIMILARITY_THRESHOLD",
    "DEFAULT_TTL_S",
    "DISPOSITIONS",
    "DISPOSITION_BYPASS",
    "DISPOSITION_DISABLED",
    "DISPOSITION_HIT_EXACT",
    "DISPOSITION_HIT_SEMANTIC",
    "DISPOSITION_MISS",
    "EMBEDDING_DIMENSIONS",
    "PROBE_SENTINEL",
    "TRANSPORTS",
    "TRANSPORT_BODY",
    "TRANSPORT_STREAM",
    "CacheEntry",
    "CacheHit",
    "CacheNamespace",
    "CachePlan",
    "CacheProbe",
    "CacheSettings",
    "CacheStats",
    "ResponseCacheStore",
    "SemanticMatch",
    "transport_for",
]

#: What the user's question is replaced by when the *rest* of a request is hashed. A NUL
#: on both sides because no JSON a client sends will contain one, so the redacted body
#: cannot accidentally equal a real one somebody wrote.
PROBE_SENTINEL: Final = "\x00headroom-probe\x00"

# --- what a tenant can switch on -------------------------------------------------------

#: Nothing is looked up, nothing is embedded, nothing is stored. The default, and a
#: first-class state rather than "semantic caching with the threshold turned up": a
#: disabled tenant does no cache work at all, which ``tests/test_cache_gate.py`` measures
#: off the embedder's and the store's own call counters.
CACHE_DISABLED: Final = "disabled"
#: Normalised-request hash only. Near-free, and it can only ever return the answer to the
#: byte-identical question that was asked before.
CACHE_EXACT: Final = "exact"
#: Exact first, then a pgvector cosine search over this tenant's namespace. Strictly a
#: superset: a semantic tenant still takes the exact hit when there is one, because it is
#: cheaper *and* safer than the search that would otherwise follow it.
CACHE_SEMANTIC: Final = "semantic"

CACHE_MODES: Final = (CACHE_DISABLED, CACHE_EXACT, CACHE_SEMANTIC)

#: ``vector(384)`` in migration 0005 — ``bge-small-en-v1.5``'s width (BUILD_PLAN L6).
#: A model of a different width is a migration, not a configuration change, which is why
#: this is a constant rather than something read off the embedder.
EMBEDDING_DIMENSIONS: Final = 384

#: The default a tenant gets when it expresses no preference. **Measured, not guessed.**
#:
#: On the committed corpus (``tests/fixtures/semantic_corpus.json``: 12 questions from 4
#: templates crossed with 3 artists, 24 paraphrases, embedded with ``bge-small-en-v1.5``):
#:
#: * a paraphrase against **its own** question scores 0.9237 at worst;
#: * a paraphrase against **any other** question scores 0.8511 at best.
#:
#: 0.90 sits in that gap, and sits there asymmetrically on purpose: 0.049 above the
#: highest wrong match and 0.024 below the lowest right one. The failure directions are
#: not symmetric — a false hit is a wrong answer served with confidence, a false miss is
#: an upstream call — so the larger margin belongs on the poison side.
#:
#: Stated with its limits, because that is the D-020 lesson: this is 12 questions and one
#: model. BUILD_PLAN §P8.H1 turns it into a curve over 133 questions and ~400 probes, and
#: this number is expected to move when that data exists. The config surface is per
#: tenant precisely so it can.
DEFAULT_SIMILARITY_THRESHOLD: Final = 0.90

#: 24 hours. Long enough that a question repeated across a working day hits; short enough
#: that a model swap, a prompt change, or a fact moving underneath is bounded by a day
#: rather than by whenever somebody remembers to purge. There is no "forever" — an entry
#: without an expiry is a wrong answer with a delay on it.
DEFAULT_TTL_S: Final = 86_400

# --- how a response was delivered ------------------------------------------------------

#: A complete JSON body. Serves non-streaming callers.
TRANSPORT_BODY: Final = "body"
#: The SSE byte stream, stored verbatim. Serves streaming callers, replayed as the exact
#: bytes the provider produced.
TRANSPORT_STREAM: Final = "stream"
TRANSPORTS: Final = (TRANSPORT_BODY, TRANSPORT_STREAM)


def transport_for(*, stream: bool) -> str:
    """Which transport a request asks for. Part of the key, never a rendering option."""
    return TRANSPORT_STREAM if stream else TRANSPORT_BODY


# --- what the ledger records -----------------------------------------------------------
#
# `cache_disposition` was pre-cut in migration 0002 and is filled from Phase 5 on. Five
# values rather than the three that comment anticipated, because an operator needs to
# tell "I turned it off" from "it is on and never applies to my traffic" — those have
# completely different fixes, and collapsing them would hide the more common one.

DISPOSITION_HIT_EXACT: Final = "cache_hit_exact"
DISPOSITION_HIT_SEMANTIC: Final = "cache_hit_semantic"
#: Eligible, looked up, nothing matched. The upstream ran.
DISPOSITION_MISS: Final = "cache_miss"
#: Caching is on for this tenant, and this request is not eligible — tools in the
#: conversation, a temperature above the bound, more than one turn. See
#: ``headroom/cache/eligibility.py``; the reason is on the log line.
DISPOSITION_BYPASS: Final = "cache_bypass"
#: The tenant has caching switched off. No lookup, no embedding, no work.
DISPOSITION_DISABLED: Final = "cache_disabled"

DISPOSITIONS: Final = (
    DISPOSITION_HIT_EXACT,
    DISPOSITION_HIT_SEMANTIC,
    DISPOSITION_MISS,
    DISPOSITION_BYPASS,
    DISPOSITION_DISABLED,
)


# --- records ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CacheSettings:
    """One tenant's cache policy, as it rides on the ``Principal``.

    Lives on the ``tenants`` row (migration 0005) for H-037's reason: the authenticator
    already reads that row on every request, so the policy costs no query, no second
    cache, and no new staleness bound — it inherits the auth cache's documented five
    seconds (H-018), which is the same sentence a scope or a limit change gets.
    """

    mode: str = CACHE_DISABLED
    #: ``None`` means the documented default, so changing :data:`DEFAULT_TTL_S` moves
    #: every tenant who never expressed a preference rather than only the new ones.
    ttl_s: int | None = None
    similarity_threshold: float | None = None

    @property
    def enabled(self) -> bool:
        return self.mode != CACHE_DISABLED

    @property
    def semantic(self) -> bool:
        return self.mode == CACHE_SEMANTIC

    @property
    def ttl(self) -> int:
        return DEFAULT_TTL_S if self.ttl_s is None else self.ttl_s

    @property
    def threshold(self) -> float:
        return (
            DEFAULT_SIMILARITY_THRESHOLD
            if self.similarity_threshold is None
            else self.similarity_threshold
        )


@dataclass(frozen=True, slots=True)
class CacheNamespace:
    """The isolation boundary, and the only address the cache has.

    Every store method takes one. There is deliberately no way to read or write an entry
    without naming a tenant, a dialect, a model, and a transport — the four facts that
    have to match before two requests can possibly want the same answer.

    :attr:`salt` is the other half of the story: the exact key is a hash *of this*
    together with the canonicalised request (``headroom/cache/keys.py``), so a tenant
    participates in a lookup twice — once in the SQL predicate, once inside the hash.
    Removing "the tenant scoping" therefore means removing this class's contribution, in
    both places at once, which is exactly what the isolation sabotage does.
    """

    tenant_id: str
    dialect: str
    model: str
    transport: str

    @property
    def salt(self) -> str:
        """What the exact key is salted with. Never sent anywhere; only hashed."""
        return f"{self.tenant_id}|{self.dialect}|{self.model}|{self.transport}"


@dataclass(frozen=True, slots=True)
class CacheProbe:
    """A single-turn request split into "the question" and "everything else".

    Produced by ``Dialect.cache_probe`` because where the user's turn lives is a dialect
    question — Anthropic keeps its system prompt in a top-level field, the OpenAI dialect
    keeps it in ``messages`` — and getting it wrong in either direction is a wrong answer
    rather than a missing feature.

    :attr:`redacted` is the whole request with the question replaced by
    :data:`PROBE_SENTINEL`. Hashing *that* is what makes a semantic hit require an
    identical system prompt, temperature, and ``max_tokens`` while allowing the question
    itself to merely be close.
    """

    text: str
    redacted: dict[str, Any]


@dataclass(frozen=True, slots=True)
class CacheEntry:
    """One stored response: the bytes, the provenance, and what it cost to obtain."""

    tenant_id: str
    dialect: str
    model: str
    transport: str
    request_hash: str
    #: Everything about the request *except* the user's question. A semantic match is
    #: allowed to move one field; this hash pins the rest.
    context_hash: str

    #: The upstream's bytes, verbatim. For ``stream`` transport this is the concatenated
    #: SSE byte stream exactly as it arrived.
    body: bytes
    content_type: str
    #: A *complete* stop reason, always: eligibility refuses to store anything else.
    stop_reason: str | None = None

    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    #: What the request that populated this entry was billed. The avoided cost of every
    #: hit against it — a fact rather than a re-priced hypothetical (H-045).
    usd_cost: Decimal | None = None
    cost_status: str = "usage_unknown"
    #: The request id that fetched this response. The audit trail from a hit back to the
    #: question it actually answers.
    source_request_id: str = ""

    #: Set together or not at all. ``None`` means the entry is exact-only.
    embedding_model: str | None = None
    embedding: tuple[float, ...] | None = None
    probe: str | None = None

    expires_at: datetime | None = None
    created_at: datetime | None = None
    id: str | None = None

    @property
    def embedded(self) -> bool:
        return self.embedding is not None


@dataclass(frozen=True, slots=True)
class CachePlan:
    """What a miss learned, kept so the store step does not have to learn it again.

    Held on the ``RequestContext`` between the lookup and the store, the way a budget
    reservation is (H-030's shape) — and like a reservation it is a *handle*, not a
    record: nothing here is logged or written, only used.

    Its presence is also the signal that makes the streaming path record bytes at all.
    No plan, no recording — which is what keeps a disabled tenant's requests free of any
    cache cost whatsoever rather than merely free of database round trips.
    """

    namespace: CacheNamespace
    request_hash: str
    context_hash: str
    probe: str
    ttl_s: int
    #: Computed during the semantic lookup and reused by the store, so a miss embeds its
    #: question exactly once. ``None`` in ``exact`` mode, where nothing is embedded.
    embedding: tuple[float, ...] | None = None
    embedding_model: str | None = None


@dataclass(frozen=True, slots=True)
class CacheHit:
    """A served entry, and how it was found."""

    entry: CacheEntry
    disposition: str
    #: Cosine similarity for a semantic hit; ``None`` for an exact one, where the
    #: question was not similar but identical.
    similarity: float | None = None


@dataclass(frozen=True, slots=True)
class SemanticMatch:
    """A neighbour and how close it was. Similarity, never distance — one direction only.

    Cosine similarity in ``[-1, 1]``, computed on unit-length vectors, so the number
    means the same thing in Postgres (``1 - (a <=> b)``), in the in-memory store (a dot
    product), and in the offline sweep §P8.H1 will run over the same rows.
    """

    entry: CacheEntry
    similarity: float


@dataclass(frozen=True, slots=True)
class CacheStats:
    """What ``/admin/cache`` reports about one tenant's entries.

    Note what is **not** here: hits and savings. Every hit writes a ledger row naming the
    entry's ``source_request_id``, so those come from ``usage_ledger`` — the table that is
    already the single source of truth for money — rather than from a second counter that
    could drift from it.
    """

    tenant_id: str
    entries: int = 0
    #: How many of them carry a vector. The rest are exact-only, either because the
    #: tenant is in ``exact`` mode or because the response was ineligible for semantic
    #: storage (H-044).
    semantic_entries: int = 0
    body_bytes: int = 0
    oldest: datetime | None = None
    newest: datetime | None = None


class ResponseCacheStore(ABC):
    """Where cached responses live. Four reads, two writes, and no way to skip a tenant.

    Two implementations and one contract suite over both (H-021). Unlike the budget and
    bucket stores, there is no concurrency claim to make here: a cache races benignly.
    Two requests that both miss both call an upstream and both store, and the loser of
    that race has cost one redundant call — which is a saving forgone, never a wrong
    answer. That is why :meth:`put` upserts rather than conditioning on anything.
    """

    @abstractmethod
    async def get_exact(
        self, namespace: CacheNamespace, *, request_hash: str, when: datetime
    ) -> CacheEntry | None:
        """The entry for a byte-identical request, if there is a live one.

        ``when`` is the request's own arrival time rather than a clock read inside the
        store — the same rule the price schedule (H-023) and the token buckets (H-035)
        follow, so an expiry cannot depend on how long a queue took.
        """

    @abstractmethod
    async def search(
        self,
        namespace: CacheNamespace,
        *,
        embedding: Sequence[float],
        context_hash: str,
        embedding_model: str,
        threshold: float,
        limit: int = 1,
        when: datetime,
    ) -> list[SemanticMatch]:
        """The nearest live entries above ``threshold``, closest first.

        Every argument narrows, and each one is a way a wrong answer could otherwise be
        served: ``namespace`` keeps tenants apart, ``context_hash`` keeps everything but
        the question identical, ``embedding_model`` keeps two vector spaces from being
        compared, ``threshold`` is the tenant's own bar, and ``when`` excludes what has
        expired.

        ``threshold=0.0`` with a larger ``limit`` is the offline-sweep primitive
        §P8.H1 needs: the full neighbour list with its similarities, so the admission
        decision can be replayed across the whole threshold range without re-embedding
        anything or calling a provider.
        """

    @abstractmethod
    async def put(self, entry: CacheEntry) -> CacheEntry:
        """Store an entry, replacing any it collides with on ``(tenant, request_hash)``.

        Replace rather than ignore: both rows are complete answers to a byte-identical
        request, and the newer one carries a fresher expiry. The loser of a concurrent
        miss has cost a redundant upstream call, which is the benign half of the race.
        """

    @abstractmethod
    async def purge_tenant(self, tenant_id: str) -> int:
        """Delete every entry a tenant owns. Returns how many. The operator's off switch."""

    @abstractmethod
    async def delete_expired(self, *, when: datetime) -> int:
        """Delete entries past their expiry. Returns how many.

        Housekeeping, not correctness: every read already excludes expired rows, so a
        sweep that never runs costs disk and never costs a wrong answer.
        """

    @abstractmethod
    async def stats(self, tenant_id: str) -> CacheStats:
        """What ``/admin/cache`` shows for one tenant."""

    async def aclose(self) -> None:
        """Release resources. A no-op for stores that hold none."""
        return None
