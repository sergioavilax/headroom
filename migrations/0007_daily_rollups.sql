-- Phase 9 — the nightly cost rollup's destination.
--
-- One row per (UTC day, tenant): the day's ledger, aggregated once by a scheduled
-- Lambda so the dashboard's history view can ask "what has this tenant cost over the
-- last ninety days" without a ninety-day scan of `usage_ledger` on every poll.
--
-- Three things about this table are decisions rather than defaults, and all three are
-- inherited from the ledger it summarises (docs/DECISIONS.md H-073):
--
--   1. IT IS DERIVED, AND IT SAYS SO. Every column here is recomputable from
--      `usage_ledger` alone. The rollup is a cache of an aggregate, never a second
--      source of truth — which is why the writer replaces a day wholesale rather than
--      accumulating into it, and why nothing in the gateway's request path writes here.
--   2. MONEY IS NUMERIC, AT THE LEDGER'S OWN SCALE. NUMERIC(24, 12), the same
--      millionth-of-a-cent precision `usage_ledger.usd_cost` carries, so a rollup and a
--      re-run of the query it came from agree to the last digit rather than to a
--      rounding.
--   3. A SUM SAYS HOW MUCH OF THE PICTURE IT IS MISSING. `usd_cost` sums only the rows
--      that had a cost; `unpriced_requests` counts the ones it therefore skipped, and
--      `cache_avoided_unknown` does the same job for the savings column. That is
--      H-025's rule and `UsageTotals`' shape, carried forward — a rollup that folded
--      NULL into zero would be a confident understatement of a day nobody can re-check
--      once the window has passed.
--
-- The counters are NOT NULL because every one of them is a count or a sum-over-what-
-- was-known: "no requests" is genuinely zero, and a day with no traffic has no row at
-- all rather than a row of zeros (the `UsageBucket` rule — gap-filling belongs to
-- whoever knows the x-domain being drawn).

CREATE TABLE daily_rollups (
    -- The UTC day of `usage_ledger.started_at` — the request's own arrival time, which
    -- is what every other window in this project is resolved against (H-023, H-033).
    -- Never `created_at`: a row drained from the writer's queue after midnight belongs
    -- to the day the request happened, not the day the INSERT landed.
    day                   DATE        NOT NULL,

    -- RESTRICT, matching 0001 and 0002. A rollup attributes spend, so it points at a
    -- tenant that is deactivated rather than deleted (H-022).
    tenant_id             UUID        NOT NULL REFERENCES tenants (id) ON DELETE RESTRICT,

    requests              BIGINT      NOT NULL,
    input_tokens          BIGINT      NOT NULL,
    output_tokens         BIGINT      NOT NULL,
    reasoning_tokens      BIGINT      NOT NULL,

    usd_cost              NUMERIC(24, 12) NOT NULL,
    -- Rows the sum above could not include, because their cost was never known.
    unpriced_requests     BIGINT      NOT NULL,
    errored_requests      BIGINT      NOT NULL,

    -- Phase 5's five dispositions, counted. Five and not three: "I switched it off" and
    -- "it is on and never applies to my traffic" have different fixes.
    cache_hits_exact      BIGINT      NOT NULL,
    cache_hits_semantic   BIGINT      NOT NULL,
    cache_misses          BIGINT      NOT NULL,
    cache_bypasses        BIGINT      NOT NULL,
    cache_disabled        BIGINT      NOT NULL,
    cache_avoided_usd     NUMERIC(24, 12) NOT NULL,
    -- `unpriced_requests` for the savings column: skipping a NULL and adding it as zero
    -- give the identical sum, so only a count can say a saving was left out.
    cache_avoided_unknown BIGINT      NOT NULL,

    -- Phase 6. Requests whose primary provider did not serve them.
    failover_requests     BIGINT      NOT NULL,

    -- When this row was computed. The gap between `day` and this is how late the
    -- rollup ran, which is the one operational fact the row itself can carry — a
    -- schedule that silently stopped firing shows up here as a stale stamp.
    computed_at           TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (day, tenant_id)
);

-- The history view's query: one tenant, a window of days, oldest first. The primary key
-- already leads with `day`, which serves the cross-tenant form.
CREATE INDEX daily_rollups_tenant_day_idx ON daily_rollups (tenant_id, day DESC);
