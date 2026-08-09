-- Phase 4 — the budget gate's account of a request, on the request's own ledger row.
--
-- The phase brief requires it: "The request log line and ledger row record the budget
-- outcome (reserved amount, settled amount, or refusal)." Three nullable columns,
-- added rather than backfilled — migration 0002 is applied and therefore immutable
-- (H-003), and every row written before this one genuinely had no budget outcome, so
-- NULL is the truthful value rather than a gap.
--
-- This is a documented deviation from H-024's closing remark that "the shape of a
-- ledger row stops changing after this migration". That sentence was about the Phase 5
-- and Phase 6 seams, which really are already present as columns; it did not
-- anticipate a fourth phase needing a fact of its own on the row, and the alternative
-- — a second table joined on request_id — would put the answer to "why was this
-- refused" one join away from the row that refused it. Recorded in docs/PHASE_LOG.md.
--
-- Two things the money columns keep from H-024, because they are the same money:
--   * NUMERIC(24, 12), never DOUBLE PRECISION. The reserved amount and the settled
--     amount are Decimals from the same arithmetic that produces usd_cost, and they
--     are compared against it in tests to the last digit.
--   * NULL and 0 are different facts. budget_settled_usd is 0 when the request was
--     released because nothing was billable, and NULL when no budget applied at all.

ALTER TABLE usage_ledger
    -- no_budget | reserved | exceeded — headroom/core/budgets.py's ADMIT_* values.
    -- NULL means the request never reached the gate (it named no model, or failed
    -- authentication), which under H-025 is also a request that gets no row at all;
    -- the column is nullable for the rows that predate this migration.
    ADD COLUMN budget_status        TEXT,

    -- What the gate held before the provider was called: the conservative estimate
    -- from headroom/policy/budgets.py — max_tokens (or a documented default) plus the
    -- body's own size, at the dated price. Present on a grant and on a refusal, so a
    -- 402 row says exactly how much the request wanted.
    ADD COLUMN budget_reserved_usd  NUMERIC(24, 12),

    -- What the hold became when the request finished. Equal to usd_cost on a priced
    -- request, 0 when nothing was billable, and — the interesting case — equal to the
    -- reservation when the cost is unknown, because a model ran and neither releasing
    -- to zero nor inventing a figure would be honest (docs/DECISIONS.md H-031).
    ADD COLUMN budget_settled_usd   NUMERIC(24, 12);

-- The dashboard's "who is hitting their cap" question, and Phase 9's alarm on
-- budget-gate refusals. Partial, because refusals are a tiny fraction of rows and an
-- index over all of them would be mostly NULLs.
CREATE INDEX usage_ledger_budget_status_idx
    ON usage_ledger (budget_status, started_at DESC)
    WHERE budget_status IS NOT NULL;
