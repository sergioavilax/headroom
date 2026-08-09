"""The cache gate: one lookup before the upstream, one store after it.

``headroom/db/cache.py`` makes an entry durable and ``eligibility.py`` decides what may
become one; this file is where a request meets them, and it holds the decisions about
*when*.

**Where it sits in the pipeline, and why.** H-039 left the question open in as many
words — *"a cache hit costs no provider call, so should it consume a bucket?"* The order
is now: authenticate → scope → route → **rate limit (429)** → **cache** → budget
reservation (402) → open the upstream.

* **After the rate limiter**, deliberately. A hit costs no provider work but it is not
  free: it costs a connection, a pgvector search, and — on the semantic path — a CPU
  embedding, which is the most expensive thing the gateway does to a request it never
  forwards. A tenant that could serve unlimited traffic as long as it repeated itself
  would have a trivially reachable denial of service. H-036's rule follows too: the units
  are not handed back when the lookup hits.
* **Before the budget gate**, equally deliberately, and this is the sharper decision. A
  hit spends nothing, so it takes no reservation and settles nothing — which keeps two
  DynamoDB round trips off the one path whose entire selling point is that the first
  token is already there. The consequence is stated rather than discovered: **a tenant
  over its cap still gets its cached answers.** That is the correct reading of what a
  budget bounds. It bounds *spend*, and a hit does not spend. Abuse is bounded by the
  rate limiter, which ran one step earlier (H-046).

**What a hit is not.** It is not an upstream call wearing a hat. The ledger row carries a
NULL ``upstream_status``, a NULL ``provider``, no token counts, and a NULL
``passthrough_overhead_ms`` — because there was no upstream byte for it to be measured
between. What it does carry is ``ttft_ms``, which is real, small, and the number the
demo is about.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from headroom.cache.eligibility import may_cache_request, may_store_response
from headroom.cache.embedding import CacheEmbedder
from headroom.cache.keys import context_hash, namespace_for, normalise_probe, request_hash
from headroom.core.cache import (
    DISPOSITION_BYPASS,
    DISPOSITION_DISABLED,
    DISPOSITION_HIT_EXACT,
    DISPOSITION_HIT_SEMANTIC,
    DISPOSITION_MISS,
    CacheEntry,
    CacheHit,
    CachePlan,
    CacheSettings,
    ResponseCacheStore,
)
from headroom.core.context import RequestContext
from headroom.dialects.base import Dialect
from headroom.metering.cost import COST_USAGE_UNKNOWN
from headroom.metering.usage import Usage

__all__ = ["ResponseCache"]


@dataclass(slots=True)
class ResponseCache:
    """Lookup and store, wired to one :class:`ResponseCacheStore` and one embedder.

    Held on the ``Gateway`` beside the budget gate and the rate limiter. Constructed
    always, used only by tenants that asked for it: :meth:`lookup` returns before
    touching either dependency when the tenant's mode is ``disabled``, which is the
    property ``tests/test_cache_gate.py`` measures off the embedder's and the store's own
    counters rather than taking on trust.
    """

    store: ResponseCacheStore
    embedder: CacheEmbedder

    async def lookup(
        self,
        ctx: RequestContext,
        dialect: Dialect,
        body: dict[str, object],
        *,
        settings: CacheSettings,
    ) -> CacheHit | None:
        """Try the cache, and record on the context what happened either way.

        Always stamps ``ctx.cache_disposition``: every proxied request that got this far
        has one of the five values, so "was the cache involved" is answerable for every
        row rather than only for the interesting ones.
        """
        if not settings.enabled:
            ctx.cache_disposition = DISPOSITION_DISABLED
            return None
        if ctx.tenant_id is None or ctx.model is None:  # pragma: no cover - proxy order
            ctx.cache_disposition = DISPOSITION_DISABLED
            return None

        eligibility = may_cache_request(dialect, body)
        if eligibility.probe is None:
            ctx.cache_disposition = DISPOSITION_BYPASS
            ctx.cache_reason = eligibility.reason
            return None

        namespace = namespace_for(
            tenant_id=ctx.tenant_id, dialect=dialect.name, model=ctx.model, stream=ctx.stream
        )
        exact_key = request_hash(namespace, body)
        entry = await self.store.get_exact(namespace, request_hash=exact_key, when=ctx.started_at)
        if entry is not None:
            return self._hit(ctx, CacheHit(entry=entry, disposition=DISPOSITION_HIT_EXACT))

        # Only now is anything embedded. An exact hit never pays for a vector, which
        # matters because the embedding is the single most expensive thing on this path.
        probe = normalise_probe(eligibility.probe.text)
        context_key = context_hash(namespace, eligibility.probe.redacted)
        embedding: tuple[float, ...] | None = None
        if settings.semantic:
            embedding = tuple(self.embedder.embed([probe])[0])
            matches = await self.store.search(
                namespace,
                embedding=embedding,
                context_hash=context_key,
                embedding_model=self.embedder.model_id,
                threshold=settings.threshold,
                limit=1,
                when=ctx.started_at,
            )
            if matches:
                return self._hit(
                    ctx,
                    CacheHit(
                        entry=matches[0].entry,
                        disposition=DISPOSITION_HIT_SEMANTIC,
                        similarity=matches[0].similarity,
                    ),
                )

        ctx.cache_disposition = DISPOSITION_MISS
        ctx.cache_plan = CachePlan(
            namespace=namespace,
            request_hash=exact_key,
            context_hash=context_key,
            probe=probe,
            ttl_s=settings.ttl,
            embedding=embedding,
            embedding_model=self.embedder.model_id if embedding is not None else None,
        )
        return None

    @staticmethod
    def _hit(ctx: RequestContext, hit: CacheHit) -> CacheHit:
        """Stamp a hit onto the context: disposition, provenance, and the avoided cost.

        ``provider`` is cleared. The route resolved to one and it was never called, and
        the ledger's ``provider`` column answers "which upstream served this" — a
        question whose honest answer here is *none*. Phase 6's per-provider health and
        failover accounting read that column, and a request no provider saw must not
        appear in them.
        """
        ctx.cache_disposition = hit.disposition
        ctx.cache_similarity = hit.similarity
        ctx.cache_source_request_id = hit.entry.source_request_id or None
        ctx.cache_avoided_usd = hit.entry.usd_cost
        ctx.provider = None
        return hit

    async def store_response(
        self,
        ctx: RequestContext,
        *,
        body: bytes,
        content_type: str,
        usage: Usage,
    ) -> None:
        """Write the response to the cache, if invariant 6 allows it.

        Called from the proxy's exits, after the response has been delivered in full —
        so a slow database cannot delay a byte, and so the decision is made with the
        finished request's outcome and stop reason in hand rather than guessed from the
        first frame.
        """
        plan = ctx.cache_plan
        if plan is None:
            return
        # Cleared first, so no second exit path can store the same request twice.
        ctx.cache_plan = None

        decision = may_store_response(
            outcome=ctx.outcome,
            upstream_status=ctx.upstream_status,
            stop_reason=usage.stop_reason,
            body=body,
            reasoning_tokens=usage.reasoning_tokens,
        )
        if decision.reason is not None:
            ctx.cache_reason = decision.reason
        if not decision.store:
            return

        embed = decision.embed and plan.embedding is not None
        await self.store.put(
            CacheEntry(
                tenant_id=plan.namespace.tenant_id,
                dialect=plan.namespace.dialect,
                model=plan.namespace.model,
                transport=plan.namespace.transport,
                request_hash=plan.request_hash,
                context_hash=plan.context_hash,
                body=body,
                content_type=content_type,
                stop_reason=usage.stop_reason,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                reasoning_tokens=usage.reasoning_tokens,
                # The cost this entry avoids from now on, copied in — H-024's rule
                # applied one table over: a figure from an invoice line that really
                # happened, not a re-pricing of a hypothetical. Read off the context
                # because ``Meter.measure`` has already stamped it there, and a second
                # opinion here would be a second source of truth for a number the tenant
                # was charged.
                usd_cost=ctx.usd_cost,
                cost_status=ctx.cost_status or COST_USAGE_UNKNOWN,
                source_request_id=ctx.request_id,
                embedding_model=plan.embedding_model if embed else None,
                embedding=plan.embedding if embed else None,
                probe=plan.probe if embed else None,
                expires_at=ctx.started_at + timedelta(seconds=plan.ttl_s),
            )
        )

    async def aclose(self) -> None:
        await self.store.aclose()
