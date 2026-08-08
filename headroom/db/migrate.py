"""Raw-SQL migration runner.

Applies ``migrations/*.sql`` in lexicographic filename order, recording applied
versions in a ``schema_migrations`` table; each file runs inside a single
transaction. Chosen over Alembic in docs/DECISIONS.md H-003.

Phase 0 ships zero migrations — the first one lands in Phase 2 with tenants and
virtual keys — so this runner is a deliberate no-op today: with no files on disk it
never opens a connection, which keeps ``make migrate`` honest on a fresh clone that
has not booted the stack yet.

Usage: ``python -m headroom.db.migrate`` (or ``make migrate``); the database URL
comes from ``DATABASE_URL``, defaulting to the compose stack's Postgres.
"""

import asyncio
import os
import sys
from pathlib import Path

import asyncpg

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"

# The compose stack's Postgres as seen from the host (H-006: host port 5433).
DEFAULT_DATABASE_URL = "postgresql://headroom:headroom@localhost:5433/headroom"

_ENSURE_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


def database_url() -> str:
    """The configured Postgres URL (env first, compose default second)."""
    return os.environ.get("DATABASE_URL") or DEFAULT_DATABASE_URL


def discover_migrations(migrations_dir: Path = MIGRATIONS_DIR) -> list[Path]:
    """All migration files, sorted by filename — the filename *is* the order."""
    return sorted(migrations_dir.glob("*.sql"))


async def connect_with_retry(
    url: str, attempts: int = 10, delay_s: float = 1.0
) -> asyncpg.Connection:
    """Connect to Postgres, retrying briefly — callers can race the db's first boot."""
    last_exc: Exception | None = None
    for _ in range(attempts):
        try:
            return await asyncpg.connect(url, timeout=5)
        except (OSError, asyncpg.PostgresError, TimeoutError) as exc:
            last_exc = exc
            await asyncio.sleep(delay_s)
    raise RuntimeError(f"could not connect to {url!r} after {attempts} attempts") from last_exc


async def run_migrations(
    url: str | None = None, migrations_dir: Path = MIGRATIONS_DIR
) -> list[str]:
    """Apply pending migrations; return the versions applied by this run."""
    pending = discover_migrations(migrations_dir)
    if not pending:
        return []
    conn = await connect_with_retry(url or database_url())
    applied_now: list[str] = []
    try:
        await conn.execute(_ENSURE_TABLE)
        rows = await conn.fetch("SELECT version FROM schema_migrations")
        already_applied = {row["version"] for row in rows}
        for path in pending:
            version = path.stem
            if version in already_applied:
                continue
            sql = path.read_text(encoding="utf-8")
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute("INSERT INTO schema_migrations (version) VALUES ($1)", version)
            applied_now.append(version)
    finally:
        await conn.close()
    return applied_now


def main() -> int:
    if not discover_migrations():
        print("migrations: none on disk yet (the first one lands in Phase 2) — nothing to do")
        return 0
    applied = asyncio.run(run_migrations())
    if applied:
        print(f"applied {len(applied)} migration(s): {', '.join(applied)}")
    else:
        print("migrations: up to date, nothing to apply")
    return 0


if __name__ == "__main__":
    sys.exit(main())
