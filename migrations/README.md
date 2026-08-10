# migrations

Raw SQL, applied in **filename order** by `headroom/db/migrate.py`
(`make migrate` / `python -m headroom.db.migrate`). No ORM, no Alembic — the
rationale and its costs are in [docs/DECISIONS.md](../docs/DECISIONS.md) H-003.

## Convention

- **Filename**: `NNNN_snake_case_summary.sql` — a zero-padded 4-digit ordinal and a
  short description, e.g. `0001_tenants_and_keys.sql`. The ordinal is the ordering
  key; lexicographic sort must equal intended order, which is why it is padded.
- **One concern per file.** A phase may add several.
- **Applied files are immutable.** Never edit a migration that has run anywhere —
  add a new one. The runner records `version` (the filename stem) in
  `schema_migrations` and skips what it has already applied, so an edited file
  silently never re-runs.
- **Each file runs in one transaction.** Statements that cannot run inside a
  transaction block (`CREATE INDEX CONCURRENTLY`, some `ALTER TYPE`) need their own
  file and a note in the PR; the runner does not special-case them today.
- **Extensions are declared where they are used**: the semantic cache's migration
  opens with `CREATE EXTENSION IF NOT EXISTS vector;` (the compose image is
  `pgvector/pgvector:pg16`, so the extension is available but not pre-created).
- **Idempotence is not assumed.** The `schema_migrations` ledger provides
  exactly-once application; `IF NOT EXISTS` guards are welcome but not a substitute.
- **No down-migrations.** The plan's schema is append-only and the local reset path
  is `docker compose down -v`.

## Status

| File | Phase | What it adds |
|---|---|---|
| `0001_tenants_and_virtual_keys.sql` | 2 | `tenants`, `virtual_keys` — the control plane |
| `0002_usage_ledger.sql` | 3 | `usage_ledger` — one priced, attributed row per request |
| `0003_ledger_budget_columns.sql` | 4 | `budget_status`, `budget_reserved_usd`, `budget_settled_usd` on `usage_ledger` |
| `0004_rate_limits.sql` | 4b | `requests_per_min`, `tokens_per_min` on `tenants` and `virtual_keys` |
| `0005_response_cache.sql` | 5 | `CREATE EXTENSION vector`; `response_cache`; cache policy on `tenants`; the avoided-cost columns on `usage_ledger` |
| `0006_ledger_failover.sql` | 6 | `failover_from`, `failover_error` on `usage_ledger`, plus a partial index on the rows that hopped |

All five are applied, so all five are immutable: a change is `0006_*.sql`.

**Budgets themselves are not here, and neither are token buckets.** Per-tenant caps,
their counters, their live reservations, and the buckets' `tat` values live on
**DynamoDB**, not in Postgres (BUILD_PLAN L2) — because the whole point is an atomic
conditional write, and that is DynamoDB's primitive. `0003` only adds what the *ledger*
has to say about a request the budget gate saw. See
[headroom/db/budgets.py](../headroom/db/budgets.py) and
[headroom/db/buckets.py](../headroom/db/buckets.py) for the two item shapes.

`0004` is the other side of that split: the rate limits are *configuration*, which
BUILD_PLAN L2 puts in Postgres, and putting them on these two rows means the statement
that already authenticates every request returns them too — so the limiter reads no
configuration of its own on the hot path (H-037). NULL means unlimited in all four
columns, deliberately nullable rather than defaulted to a large number: "no limit" and
"a very high limit" are different facts and the admin API has to report which.

The shape of `0001` — UUID keys, `revoked_at` as the state rather than a boolean,
`ON DELETE RESTRICT`, `TEXT[]` scopes where empty means unrestricted — is argued in
[docs/DECISIONS.md](../docs/DECISIONS.md) H-022. `0002` copies the rates it billed at
into every row so a price change can never re-bill history, stores money as `NUMERIC`
rather than `DOUBLE PRECISION`, and keeps NULL (unknown) distinct from 0 (provably
free) — H-024 and H-025. `0003` keeps both of those rules for the two amounts it adds,
and is additive-only: existing rows genuinely had no budget outcome, so NULL is the
truthful value rather than a gap to backfill. `0004` is additive and nullable for the
same reason — every existing row reads as unlimited, which is exactly the behaviour
before it ran.

`0005` is where the `vector` extension finally gets created — the image has offered it
since Phase 0 (H-001) and nothing needed it until now. Two of its choices are worth
knowing before reading the file. `cache_mode` on `tenants` is `NOT NULL DEFAULT
'disabled'`, so every existing tenant and every future one caches nothing until somebody
says otherwise; a cache that switches itself on is a cache nobody consented to. And the
`usage_ledger` columns it adds are *avoided* cost and provenance, deliberately **not** the
existing token and cost columns: a cache hit generates nothing and is billed nothing, so
every `SUM(output_tokens)` written before this migration keeps meaning what it meant.

`0006` finishes a column `0002` opened. `failover_hops` has been on `usage_ledger` since
that migration, reserved for Phase 6 and defaulting to zero, so every existing row already
means "the primary served it". The two columns beside it record the **first** candidate a
request passed over and why — because `provider`, `upstream_status`, and `error_reason`
between them describe only who served and what happened *last*, and the operational
question is why the request left where it was routed (H-051). Additive, nullable, and the
index is partial: the overwhelming majority of rows have nothing to say here.

`make up` applies migrations inside the gateway container once the stack is healthy;
`make migrate` applies them from the host against `DATABASE_URL`.
