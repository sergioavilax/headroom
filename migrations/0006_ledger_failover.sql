-- 0006 — where a request went when it could not go where it was routed (Phase 6).
--
-- `failover_hops` has been on this table since 0002, reserved for exactly this phase
-- and shipping as a NOT NULL DEFAULT 0 so every existing row already means "the primary
-- served it". Two columns join it, and both answer a question the row could not.
--
-- Why two columns and not one, and why they describe the FIRST failure:
--
--   `provider` says who ultimately served the request — it moves with the executor, so
--   after a hop it names the fallback. `upstream_status` and `error_reason` describe the
--   LAST thing that happened, which on an exhausted chain is the failure the caller was
--   handed. Neither of those can say why the request left where it was routed, and that
--   is the operational question: "we are serving from vllm_b — what happened to vllm_a?"
--   So `failover_from` and `failover_error` record the first candidate passed over and
--   why. First and last together describe a two-hop chain completely; a longer one is
--   summarised here and written out in full on the structured log line, which carries
--   the whole trail (docs/DECISIONS.md H-051).
--
-- Additive and nullable, and `0002` is not edited (H-003). NULL means "no hop happened",
-- which is the same fact `failover_hops = 0` carries — kept consistent by the writer
-- rather than by a CHECK, because a constraint spanning three columns would fail a row
-- for a bookkeeping slip and lose the invoice line with it.

ALTER TABLE usage_ledger
    ADD COLUMN failover_from  TEXT,
    ADD COLUMN failover_error TEXT;

-- The dashboard's "show me the requests that failed over" and the P8.H3 report's own
-- query. Partial, because the overwhelming majority of rows have nothing to say here
-- and an index over all of them would be mostly zeroes.
CREATE INDEX usage_ledger_failover_idx
    ON usage_ledger (started_at DESC)
    WHERE failover_hops > 0;
