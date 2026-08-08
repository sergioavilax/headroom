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

Empty by design at Phase 0. The first migration (tenants + virtual keys) lands in
Phase 2; the ledger in Phase 3; the pgvector semantic-cache tables in Phase 5.
