-- Phase 5 — the response cache: exact, semantic, and never poisoned.
--
-- Three things land here, and the order they are written in is the order they matter:
-- where a tenant's cache *policy* lives, where a cached *response* lives, and what a
-- ledger row says when one was served.
--
-- The property this whole file is arranged around is BUILD_PLAN §0.2 invariant 6 —
-- "truncated or partial upstream replies are never cached and never billed as complete"
-- — and its Phase 5 corollary, which the session brief states plainly: **no cache entry
-- ever serves across tenants.** Neither is enforced by a comment. Isolation is a NOT
-- NULL foreign key that every index leads with and every query filters on, and it is
-- *also* folded into `request_hash` (docs/DECISIONS.md H-042), so the two mechanisms
-- would both have to be removed for a leak to be possible — which is exactly what
-- `tests/test_cache_isolation.py` does deliberately, to prove the test can see it.

CREATE EXTENSION IF NOT EXISTS vector;

-- ---------------------------------------------------------------------------------
-- 1. Per-tenant cache policy — columns on `tenants`, for H-037's reason unchanged
-- ---------------------------------------------------------------------------------
--
-- BUILD_PLAN L2 puts *config* in Postgres, and `find_by_hash` already joins this row on
-- every authenticated request: the policy arrives on the Principal with no second query,
-- no second cache, and no second staleness bound to explain — it inherits the auth
-- cache's documented 5 seconds (H-018), exactly as the rate limits do.
--
-- `cache_mode` is NOT NULL DEFAULT 'disabled' and that default is the feature. Caching
-- is a first-class OFF state, per tenant: an existing tenant, and every tenant created
-- after this migration, caches nothing until somebody says otherwise. A cache that
-- switches itself on is a cache nobody consented to.

ALTER TABLE tenants
    ADD COLUMN cache_mode                 TEXT NOT NULL DEFAULT 'disabled',
    -- NULL means "use the documented default" for both of these. Nullable rather than
    -- defaulted in SQL so that changing the default in code changes it for every tenant
    -- who never expressed a preference, instead of only for tenants created afterwards.
    ADD COLUMN cache_ttl_s                INTEGER,
    -- The number BUILD_PLAN §P8.H1 sweeps. Per tenant because the plan says so — "the
    -- threshold is a first-class config precisely because P8.H1 is going to measure what
    -- it costs" — and NUMERIC rather than DOUBLE PRECISION because a threshold read back
    -- as 0.8999999999999999 would make an admin round-trip test a liar.
    ADD COLUMN cache_similarity_threshold NUMERIC(5, 4);

ALTER TABLE tenants
    ADD CONSTRAINT tenants_cache_mode_known
        CHECK (cache_mode IN ('disabled', 'exact', 'semantic')),
    ADD CONSTRAINT tenants_cache_ttl_positive
        CHECK (cache_ttl_s IS NULL OR cache_ttl_s > 0),
    -- A threshold of 0 would admit anything as "similar" and 1 would admit only an
    -- identical vector, which is the exact layer with extra steps. Both are refused by
    -- the database as well as by the API, so a hand-written UPDATE cannot install a
    -- cache that answers every question with the first answer it ever stored.
    ADD CONSTRAINT tenants_cache_threshold_ranged
        CHECK (cache_similarity_threshold IS NULL
               OR (cache_similarity_threshold > 0 AND cache_similarity_threshold < 1));

-- ---------------------------------------------------------------------------------
-- 2. The entries
-- ---------------------------------------------------------------------------------

CREATE TABLE response_cache (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),

    -- The isolation boundary. RESTRICT for H-022's reason: tenants are deactivated and
    -- never deleted, so this reference stays valid, and a cascade here would silently
    -- discard evidence of what a tenant was served.
    tenant_id     UUID        NOT NULL REFERENCES tenants (id) ON DELETE RESTRICT,

    -- The rest of the namespace. Kept as columns as well as being inside `request_hash`
    -- so the semantic query — which cannot use the hash, that being the whole point —
    -- has something to filter on.
    dialect       TEXT        NOT NULL,
    model         TEXT        NOT NULL,
    -- 'body' | 'stream'. Part of the key, not a rendering option: an entry is replayed
    -- as the **exact bytes the provider sent**, so a stream serves a streaming caller
    -- and a body serves a non-streaming one, and neither is ever converted into the
    -- other. Synthesising frames from a body is how a gateway ends up emitting an event
    -- sequence no provider ever produced (H-043).
    transport     TEXT        NOT NULL,

    -- The exact layer's key: SHA-256 over the canonicalised request *and* the namespace
    -- above. See headroom/cache/keys.py.
    request_hash  TEXT        NOT NULL,
    -- The semantic layer's guard rail: SHA-256 over everything in the request **except**
    -- the user's question. A semantic hit therefore requires an identical system prompt,
    -- an identical temperature, an identical max_tokens — everything — and merely a
    -- *similar* question. Similarity is allowed to move exactly one field.
    context_hash  TEXT        NOT NULL,

    -- --- what is replayed ---------------------------------------------------------
    -- The upstream's bytes, verbatim. Not a re-serialization: the same discipline the
    -- proxy holds to on the live path (H-007, H-016) applies to a stored copy, and it
    -- is what makes a replay byte-identical rather than merely equivalent.
    body          BYTEA       NOT NULL,
    content_type  TEXT        NOT NULL,
    -- Anthropic `stop_reason` / OpenAI `finish_reason`, as reported. Only a *complete*
    -- stop is ever stored (`end_turn`, `stop_sequence`, `stop`); `max_tokens`/`length`
    -- is a truncated answer and is invariant 6's headline case.
    stop_reason   TEXT,

    -- --- what it cost the request that populated it -------------------------------
    -- Copied in, in the H-024 spirit: the avoided cost of every future hit is a figure
    -- from an invoice line that really happened, not a re-pricing of a hypothetical.
    input_tokens     INTEGER,
    output_tokens    INTEGER,
    reasoning_tokens INTEGER,
    usd_cost         NUMERIC(24, 12),
    cost_status      TEXT NOT NULL,
    -- Provenance. The request that filled this entry, so a hit can be traced back to
    -- the question it actually answers — which is the answer key for cache correctness
    -- (§P8.H1) and the only way to audit a semantic hit after the fact.
    source_request_id TEXT NOT NULL,

    -- --- the semantic layer -------------------------------------------------------
    -- NULL together, always: a row is either embedded or exact-only. A response from a
    -- reasoning model is deliberately stored exact-only (H-044), so this is not a rare
    -- case to be surprised by later.
    embedding_model TEXT,
    embedding       vector(384),
    -- The text that was embedded, kept so an operator can see what a hit matched on and
    -- so the §P8.H1 sweep can recompute a similarity matrix from committed rows.
    probe           TEXT,

    -- --- housekeeping ---------------------------------------------------------------
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Every entry has one; there is no "forever". A stale answer is a wrong answer with
    -- a delay on it.
    expires_at   TIMESTAMPTZ NOT NULL,

    -- Note what is absent: a hit counter. Every hit already writes a ledger row naming
    -- this entry's `source_request_id`, so `SELECT count(*) FROM usage_ledger WHERE
    -- cache_source_request_id = ...` is the same number from the table that is already
    -- the single source of truth — and keeping it there costs no write on the read path
    -- and cannot drift from the ledger the way a second counter would.

    CONSTRAINT response_cache_transport_known CHECK (transport IN ('body', 'stream')),
    -- The embedding and the model that produced it stand or fall together. A vector
    -- whose space nobody recorded is a vector nothing may safely be compared against.
    CONSTRAINT response_cache_embedding_paired
        CHECK ((embedding IS NULL) = (embedding_model IS NULL))
);

-- The exact layer, and the uniqueness that makes a store idempotent. Leads with
-- `tenant_id`: every read of this table is a read *for one tenant*, and an index that
-- cannot be used without naming one is a structural reminder of that.
CREATE UNIQUE INDEX response_cache_exact_idx ON response_cache (tenant_id, request_hash);

-- The semantic layer's filter. `embedding_model` is in it because two models are two
-- vector spaces, and a cosine between them is a number with no meaning.
CREATE INDEX response_cache_semantic_idx
    ON response_cache (tenant_id, dialect, model, transport, context_hash, embedding_model)
    WHERE embedding IS NOT NULL;

-- Cosine ANN over the vectors. HNSW because pgvector has had it since 0.5 and it does
-- not need a training pass the way ivfflat does.
--
-- Worth stating plainly: this index is **approximate**, and combined with the
-- restrictive filter above it can return fewer neighbours than exist. The failure
-- direction is a cache *miss*, never a wrong hit — the threshold is applied in the SQL
-- itself, not by the index — so an approximate index can cost a saving and cannot cost
-- a correct answer. That asymmetry is why it is acceptable here at all.
CREATE INDEX response_cache_embedding_idx
    ON response_cache USING hnsw (embedding vector_cosine_ops)
    WHERE embedding IS NOT NULL;

-- The expiry sweep, and the admin purge.
CREATE INDEX response_cache_expiry_idx ON response_cache (expires_at);

-- ---------------------------------------------------------------------------------
-- 3. What the ledger says about a hit
-- ---------------------------------------------------------------------------------
--
-- `cache_disposition` was pre-cut in migration 0002 and is filled from here on. The
-- three columns below are what it needs beside it, and they are additive and nullable,
-- so every row already written reads exactly as it did (invariant 7).
--
-- What is deliberately NOT done: a hit does not borrow the ledger's `input_tokens` /
-- `output_tokens` / `usd_cost` columns to describe the response it replayed. Nothing was
-- generated and nothing was billed, so those stay NULL and 0 respectively, and every
-- existing `SUM(output_tokens)` in the dashboard and in the Phase 9 rollup keeps
-- counting tokens that were actually produced. The avoided figures live in their own
-- column precisely so a saving can never be mistaken for a spend.

ALTER TABLE usage_ledger
    -- What this hit would have cost, taken from the entry's own recorded cost — a fact
    -- about a request that happened, rather than a re-pricing of one that did not.
    ADD COLUMN cache_avoided_usd       NUMERIC(24, 12),
    -- Cosine similarity for a semantic hit; NULL for an exact one, where the question
    -- was not similar but identical. Five decimal places, which is finer than any
    -- threshold §P8.H1 will sweep at.
    ADD COLUMN cache_similarity        NUMERIC(6, 5),
    -- Which request populated the entry that served this one. Not a foreign key: the
    -- entry can expire or be purged, and the audit trail must outlive it.
    ADD COLUMN cache_source_request_id TEXT;

-- The dashboard's savings counter, and §P8.H1's row source: hits only, which is a small
-- minority of rows in any deployment where the cache is doing its job badly and a large
-- one where it is doing it well. Partial either way, because misses need no index.
CREATE INDEX usage_ledger_cache_hit_idx ON usage_ledger (tenant_id, started_at DESC)
    WHERE cache_disposition IN ('cache_hit_exact', 'cache_hit_semantic');
