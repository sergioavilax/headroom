"""The migration runner's contracts that hold with no database in sight."""

from pathlib import Path

from headroom.db.migrate import MIGRATIONS_DIR, discover_migrations, run_migrations


def test_repo_migrations_dir_exists_and_documents_itself() -> None:
    assert MIGRATIONS_DIR.is_dir()
    assert (MIGRATIONS_DIR / "README.md").is_file()


def test_discovery_is_filename_ordered_and_sql_only(tmp_path: Path) -> None:
    for name in ("0010_later.sql", "0002_second.sql", "0001_first.sql", "README.md"):
        (tmp_path / name).write_text("-- noop\n", encoding="utf-8")

    assert [path.name for path in discover_migrations(tmp_path)] == [
        "0001_first.sql",
        "0002_second.sql",
        "0010_later.sql",
    ]


async def test_run_migrations_never_connects_when_there_is_nothing_to_apply(
    tmp_path: Path,
) -> None:
    """A deliberately unreachable URL: reaching the connect step would raise."""
    assert await run_migrations("postgresql://nobody@127.0.0.1:1/nowhere", tmp_path) == []
