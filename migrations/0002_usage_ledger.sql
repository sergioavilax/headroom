-- Phase 3 — metering: the cost ledger.
--
-- One row per completed request: who asked, what it was, what the provider reported,
-- what it cost, and how long each stage took. This table is the single source of
-- truth for the Phase 7 dashboard and for Phase 8's H2 overhead and error accounting,
-- which is why the failures are in it too.
--
-- Three properties are load-bearing and argued in docs/DECISIONS.md H-024 (this
-- schema), H-025 (which requests get a row and what a failure costs), and H-026
-- (prompt-cache tiers recorded but not priced):
--
--   1. A ROW CARRIES THE PRICE IT WAS BILLED AT. `price_effective_from`,
--      `usd_per_mtok_in`, and `usd_per_mtok_out` are copied in at write time, not
--      looked up later. Editing config/models.yaml, or a vendor publishing a new
--      rate, can never move a cost that already landed. That is Backline's D-017
--      scar turned into a column layout instead of a promise.
--   2. MONEY IS NUMERIC, NEVER FLOAT. Rates and costs are exact decimals from the
--      YAML file through Python's Decimal to this table and back out as strings.
--      DOUBLE PRECISION here would reintroduce, at the last step, the error the
--      whole pipeline is arranged to avoid.
--   3. NULL AND ZERO MEAN DIFFERENT THINGS. `usd_cost` is NULL when the cost is
--      unknown (no usage block, or a model with no price row for that date) and 0
--      when the request provably cost nothing (an upstream error, an unroutable
--      model). `cost_status` says which. A meter that writes 0 for "unknown" is
--      indistinguishable from one that works.

CREATE TABLE usage_ledger (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),

    -- The id on `x-headroom-request-id` and in the structured log line. UNIQUE so
    -- the writer is idempotent: a retried write after a crash cannot double-bill,
    -- and `ON CONFLICT DO NOTHING` needs this constraint to key on.
    request_id        TEXT        NOT NULL UNIQUE,

    -- RESTRICT, matching migration 0001's design (H-022). Tenants and keys are
    -- revoked and deactivated, never deleted, precisely so these references stay
    -- valid forever; a cascade here would delete history to tidy up a control-plane
    -- row, which is how a historical invoice becomes an orphan.
    tenant_id         UUID        NOT NULL REFERENCES tenants (id) ON DELETE RESTRICT,
    key_id            UUID        NOT NULL REFERENCES virtual_keys (id) ON DELETE RESTRICT,

    route             TEXT        NOT NULL,
    dialect           TEXT        NOT NULL,
    model             TEXT        NOT NULL,
    -- NULL when the request never resolved to one: an unroutable model, or a scope
    -- refusal that fired before routing (H-020 checks model scope before the route).
    provider          TEXT,
    streamed          BOOLEAN     NOT NULL DEFAULT FALSE,

    -- The RequestContext outcome and the H-009 error taxonomy, verbatim. These are
    -- stable identifiers from Phase 1 and 2 onward, so charting them is safe.
    outcome           TEXT        NOT NULL,
    status_code       INTEGER,
    upstream_status   INTEGER,
    error_source      TEXT,
    error_reason      TEXT,
    -- Anthropic `stop_reason` / OpenAI `finish_reason`. Phase 5 reads it before it is
    -- allowed to cache anything: a truncated answer is never cacheable (invariant 6).
    stop_reason       TEXT,

    -- What the provider's usage block said. NULL means the provider did not say —
    -- a materially different fact from 0, and one the meter refuses to paper over.
    -- `output_tokens` is REASONING-INCLUSIVE: the Phase 1 live smoke found a reply
    -- with 11 visible characters and 63 billed tokens, 57 of them chain of thought
    -- that never appeared in the content stream. The meter reads this block; it
    -- never counts the text.
    input_tokens        INTEGER,
    output_tokens       INTEGER,
    reasoning_tokens    INTEGER,
    -- Recorded, not priced (H-026). A row whose usage reports either of these is
    -- marked `partial`: the figure is an honest lower bound rather than a total.
    cache_read_tokens   INTEGER,
    cache_write_tokens  INTEGER,

    -- The price rows applied, copied in. See property 1 above.
    price_effective_from DATE,
    usd_per_mtok_in      NUMERIC(20, 10),
    usd_per_mtok_out     NUMERIC(20, 10),
    -- 12 decimal places: a millionth of a cent. A single output token at $1/Mtok is
    -- 0.000001 — six places — so the smallest realistic charge is stored exactly with
    -- six to spare, and the mock fixtures land on their expected values to the digit.
    usd_cost             NUMERIC(24, 12),
    -- priced | partial | unpriced_model | usage_unknown | not_billable
    cost_status          TEXT NOT NULL,

    -- Timings from the Phase 1 RequestContext, in milliseconds.
    -- `passthrough_overhead_ms` is the number Phase 8's H2 reports against its
    -- pre-registered p50 < 50 ms target, which is why it is a column and not a log line.
    upstream_latency_ms     DOUBLE PRECISION,
    ttft_ms                 DOUBLE PRECISION,
    passthrough_overhead_ms DOUBLE PRECISION,
    total_ms                DOUBLE PRECISION,

    -- Seams for the phases that will fill them (invariant 7: later phases extend).
    -- Present now so the shape of a ledger row stops changing after this migration.
    cache_disposition   TEXT,             -- Phase 5: exact | semantic | miss
    failover_hops       INTEGER NOT NULL DEFAULT 0,  -- Phase 6

    -- When the request was RECEIVED (wall clock, from RequestContext.started_at).
    -- This is the date the price is resolved against — never "now" — so a retried
    -- write, a replayed fixture, or a slow queue drain cannot move a cost across a
    -- price-schedule boundary.
    started_at        TIMESTAMPTZ NOT NULL,
    -- When the row was written. Differs from started_at by the queue's drain latency
    -- and is kept because the gap is the delivery guarantee, made visible.
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The dashboard's default view: one tenant's recent spend.
CREATE INDEX usage_ledger_tenant_started_idx ON usage_ledger (tenant_id, started_at DESC);
-- Spend by model over a window, across tenants.
CREATE INDEX usage_ledger_model_started_idx ON usage_ledger (model, started_at DESC);
-- The unfiltered explorer, and the nightly rollup Lambda in Phase 9.
CREATE INDEX usage_ledger_started_idx ON usage_ledger (started_at DESC);
-- Per-key attribution: a tenant with one runaway service and four well-behaved ones
-- is a bill nobody can explain from a tenant total alone.
CREATE INDEX usage_ledger_key_idx ON usage_ledger (key_id);
