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

Still to come: the pgvector semantic-cache tables in Phase 5.

The shape of `0001` — UUID keys, `revoked_at` as the state rather than a boolean,
`ON DELETE RESTRICT`, `TEXT[]` scopes where empty means unrestricted — is argued in
[docs/DECISIONS.md](../docs/DECISIONS.md) H-022. `0002` copies the rates it billed at
into every row so a price change can never re-bill history, stores money as `NUMERIC`
rather than `DOUBLE PRECISION`, and keeps NULL (unknown) distinct from 0 (provably
free) — H-024 and H-025. Both are applied, so both are immutable: a change is
`0003_*.sql`.

`make up` applies migrations inside the gateway container once the stack is healthy;
`make migrate` applies them from the host against `DATABASE_URL`.
