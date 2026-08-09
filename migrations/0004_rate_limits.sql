-- Phase 4b — token-bucket rate limits: where the *configuration* lives.
--
-- BUILD_PLAN L2 splits the datastores by role and this migration is that split taken
-- literally: "Postgres 16 + pgvector for config, virtual keys, the cost ledger, request
-- log, and the semantic cache. DynamoDB (conditional writes) for token buckets and
-- budget reservations ONLY." So the *limits* are columns here and the *buckets* are
-- items in DynamoDB. Nothing about a bucket's state is in this database.
--
-- Two consequences worth stating, both argued in docs/DECISIONS.md H-037:
--
--   1. The limits are free to read on the request path. `find_by_hash` already joins
--      these two rows on every authentication, so the numbers arrive on the Principal
--      with no second query and no second cache — and they inherit the auth cache's
--      documented 5-second staleness bound (H-018) rather than inventing another one.
--   2. A limit change therefore takes effect within 5 seconds across processes, and on
--      the very next request in the process that made it (the admin route invalidates
--      its own cache entry, exactly as a scope change does).
--
-- NULL means unlimited, in both columns of both tables, which is why they are nullable
-- rather than defaulted to some large number: "no limit" and "a very high limit" are
-- different facts, and only one of them can be reported honestly by an admin API.
--
-- Additive and nullable: every existing row reads as unlimited, which is exactly the
-- behaviour before this migration (BUILD_PLAN §0.2 invariant 7).

ALTER TABLE tenants
    ADD COLUMN requests_per_min INTEGER,
    ADD COLUMN tokens_per_min   INTEGER;

ALTER TABLE virtual_keys
    ADD COLUMN requests_per_min INTEGER,
    ADD COLUMN tokens_per_min   INTEGER;

-- A limit of zero would mean "admit nothing", which is a way to disable a tenant that
-- already has two better spellings (`tenants.active = false`, and revoking its keys) and
-- one bad property: the token bucket's emission interval is 60s/limit, and a zero limit
-- has no emission interval at all. The constraint keeps that case out of the database
-- rather than out of one code path.
ALTER TABLE tenants
    ADD CONSTRAINT tenants_requests_per_min_positive CHECK (requests_per_min IS NULL OR requests_per_min > 0),
    ADD CONSTRAINT tenants_tokens_per_min_positive   CHECK (tokens_per_min   IS NULL OR tokens_per_min   > 0);

ALTER TABLE virtual_keys
    ADD CONSTRAINT virtual_keys_requests_per_min_positive CHECK (requests_per_min IS NULL OR requests_per_min > 0),
    ADD CONSTRAINT virtual_keys_tokens_per_min_positive   CHECK (tokens_per_min   IS NULL OR tokens_per_min   > 0);

-- `/admin/limits` lists every scope that has one. A partial index because the answer is
-- expected to be a small minority of rows forever: most tenants are uncapped, and a
-- sequential scan of the tenant table to build an admin listing is fine right up until
-- it is not.
CREATE INDEX tenants_rate_limited_idx ON tenants (id)
    WHERE requests_per_min IS NOT NULL OR tokens_per_min IS NOT NULL;

CREATE INDEX virtual_keys_rate_limited_idx ON virtual_keys (id)
    WHERE requests_per_min IS NOT NULL OR tokens_per_min IS NOT NULL;
