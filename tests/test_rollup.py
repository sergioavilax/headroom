"""The nightly rollup: which days, in what order, and what the Lambda says afterwards.

The *arithmetic* is not here — it is `LedgerStore.write_daily_rollup`, asserted against
both implementations by `tests/test_ledger_store.py`, because a second `GROUP BY` written
for a Lambda is a second set of decisions about NULL, `cost_status`, and what counts as a
hit (H-054's argument, one runtime over).

What is here is everything that is genuinely the Lambda's, and it is all keyless: the day
arithmetic around a midnight boundary, the three shapes of invocation event, where the
connection string comes from, and the handler's own contract with AWS — that it returns a
JSON-serialisable summary *and* leaves one line of it in the log.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import boto3
import pytest

from headroom.core.ledger import LedgerEntry, LedgerStore
from headroom.db.memory import InMemoryLedgerStore
from headroom.rollup import (
    DEFAULT_ROLLUP_DAYS,
    MAX_ROLLUP_WINDOW,
    resolve_days,
    run_rollup,
)
from headroom.rollup import handler as handler_module

TENANT = "aaaaaaaa-0000-4000-8000-000000000001"
KEY = "aaaaaaaa-0000-4000-8000-00000000000a"

#: Mid-afternoon, so "today" is unambiguous and a test that moves the clock has to say so.
NOW = datetime(2026, 8, 11, 15, 30, tzinfo=UTC)


def entry(request_id: str, *, at: datetime, usd: str | None = "0.0000115") -> LedgerEntry:
    return LedgerEntry(
        request_id=request_id,
        tenant_id=TENANT,
        key_id=KEY,
        route="/v1/messages",
        dialect="anthropic",
        model="mock-model-1",
        provider="mock",
        outcome="ok",
        status_code=200,
        upstream_status=200,
        input_tokens=11,
        output_tokens=7,
        usd_cost=None if usd is None else Decimal(usd),
        cost_status="usage_unknown" if usd is None else "priced",
        started_at=at,
    )


# --- which days ---------------------------------------------------------------------


def test_a_scheduled_run_covers_today_and_yesterday() -> None:
    """Not just yesterday, and the reason is the writer's drain queue.

    `LedgerWriter` is fire-and-forget (H-027), so a request that arrived at 23:59:59 can
    land its row after midnight. A rollup that only ever looked one day back would miss
    it for good — and rolling up the day in progress is also what makes the history view
    current rather than a day stale.
    """
    assert resolve_days({}, NOW) == [date(2026, 8, 10), date(2026, 8, 11)]
    assert resolve_days(None, NOW) == [date(2026, 8, 10), date(2026, 8, 11)]
    assert len(resolve_days({}, NOW)) == DEFAULT_ROLLUP_DAYS


def test_an_event_can_name_exactly_one_day() -> None:
    """The backfill shape, and the one the Phase 9 gate fires by hand."""
    assert resolve_days({"day": "2026-07-04"}, NOW) == [date(2026, 7, 4)]


def test_an_event_can_ask_for_a_window_oldest_first() -> None:
    days = resolve_days({"days": 4}, NOW)
    assert days == [date(2026, 8, 8), date(2026, 8, 9), date(2026, 8, 10), date(2026, 8, 11)]
    assert days == sorted(days), "oldest first, so a backfill reads forwards"


def test_a_mistyped_day_is_refused_rather_than_rounded() -> None:
    """A schedule that fired on the wrong day should fail, not summarise something near."""
    with pytest.raises(ValueError):
        resolve_days({"day": "2026-13-01"}, NOW)
    with pytest.raises(ValueError):
        resolve_days({"day": "yesterday"}, NOW)


def test_a_window_outside_the_bound_is_refused() -> None:
    with pytest.raises(ValueError, match="between 1 and"):
        resolve_days({"days": 0}, NOW)
    with pytest.raises(ValueError, match="between 1 and"):
        resolve_days({"days": MAX_ROLLUP_WINDOW + 1}, NOW)
    # The edges themselves are fine — a year plus a leap day is a supported backfill.
    assert len(resolve_days({"days": MAX_ROLLUP_WINDOW}, NOW)) == MAX_ROLLUP_WINDOW


def test_the_day_boundary_is_utc_and_nothing_else() -> None:
    """The schedule fires at 00:15 UTC; the days it picks must not depend on a zone.

    A `now` half an hour past midnight in Tokyo is still *yesterday* in UTC, and the
    ledger's `started_at` is resolved in UTC everywhere else in this project (H-023,
    H-033). A mismatch here would silently roll up the wrong 24 hours for a third of
    the world.
    """
    tokyo = datetime(2026, 8, 12, 0, 30, tzinfo=timezone(timedelta(hours=9)))
    assert resolve_days({}, tokyo) == [date(2026, 8, 10), date(2026, 8, 11)]

    just_after_midnight = datetime(2026, 8, 11, 0, 15, tzinfo=UTC)
    assert resolve_days({}, just_after_midnight) == [date(2026, 8, 10), date(2026, 8, 11)]


# --- running them --------------------------------------------------------------------


async def seeded() -> LedgerStore:
    store = InMemoryLedgerStore()
    await store.record(entry("hr_1", at=NOW))
    await store.record(entry("hr_2", at=NOW + timedelta(minutes=1)))
    await store.record(entry("hr_3", at=NOW - timedelta(days=1)))
    return store


async def test_a_run_reports_every_day_it_touched_in_order() -> None:
    store = await seeded()

    summary = await run_rollup(store, [date(2026, 8, 10), date(2026, 8, 11)])

    assert [day.day for day in summary.days] == [date(2026, 8, 10), date(2026, 8, 11)]
    assert [day.requests for day in summary.days] == [1, 2]
    assert summary.requests == 3
    assert summary.duration_ms >= 0


async def test_a_day_with_nothing_in_it_is_reported_as_zero_not_skipped() -> None:
    """The run's own summary is a receipt: a day it looked at and found empty says so."""
    summary = await run_rollup(InMemoryLedgerStore(), [date(2026, 8, 11)])

    assert len(summary.days) == 1
    assert summary.days[0].requests == 0
    assert summary.days[0].tenants == 0


async def test_the_summary_serialises_money_as_a_string() -> None:
    """The last mile of H-024. This dict becomes JSON, and JSON's only number is a double.

    Compared as a `Decimal` rather than as a literal, and that is not laziness. The two
    stores agree on the *value* and differ on the *scale of the string*: Postgres hands
    back `NUMERIC(24, 12)` as `"0.000023000000"`, while summing Python `Decimal`s keeps
    the operands' own scale and gives `"0.000023"`. Both are exact, both parse to the same
    picodollars (`ui/lib/format.ts` scales by position), and pinning one spelling here
    would make this test a statement about which store ran it.
    """
    store = await seeded()

    summary = await run_rollup(store, [date(2026, 8, 11)])
    rendered = json.loads(json.dumps(summary.as_dict()))

    rendered_cost = rendered["days"][0]["usd_cost"]
    assert isinstance(rendered_cost, str)
    assert Decimal(rendered_cost) == Decimal("0.000023")
    assert rendered["event"] == "daily_rollup"


async def test_a_run_leaves_the_rollups_it_reported() -> None:
    """The summary is not a claim about the store — it is read back out of it."""
    store = await seeded()

    await run_rollup(store, [date(2026, 8, 11)])

    [row] = await store.list_rollups()
    assert row.day == date(2026, 8, 11)
    assert row.requests == 2
    assert row.usd_cost == Decimal("0.000023")


# --- where the connection string comes from ---------------------------------------------


class _FakeSecrets:
    """Enough of a Secrets Manager client to prove which branch was taken."""

    def __init__(self, value: str) -> None:
        self.value = value
        self.requested: list[str] = []

    def get_secret_value(self, *, SecretId: str) -> dict[str, str]:
        self.requested.append(SecretId)
        return {"SecretString": self.value}


def test_the_environment_wins_so_a_local_run_never_touches_aws() -> None:
    env = {"DATABASE_URL": "postgresql://local/headroom", "DATABASE_URL_SECRET_ARN": "arn:x"}
    assert handler_module.database_url(env) == "postgresql://local/headroom"


def test_the_secret_is_read_at_invocation_when_only_an_arn_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The deployed branch. An ARN in the environment is not a secret; the value is."""
    fake = _FakeSecrets("postgresql://rds/headroom")
    # Patched on the module object the handler imported, which is this one — `boto3` is a
    # singleton and `handler_module.boto3 is boto3`. Reached through the import here
    # rather than through the handler's namespace because `--strict` refuses an implicit
    # re-export, and the alternative (adding `boto3` to the handler's `__all__`) would be
    # widening a module's public surface to make a test tidier.
    monkeypatch.setattr(boto3, "client", lambda _service: fake)

    resolved = handler_module.database_url({"DATABASE_URL_SECRET_ARN": "arn:aws:secretsmanager:x"})

    assert resolved == "postgresql://rds/headroom"
    assert fake.requested == ["arn:aws:secretsmanager:x"]


def test_with_neither_it_falls_back_to_the_same_default_every_other_entry_point_uses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """So `python -m headroom.rollup` works against `make up` with nothing exported.

    Unreachable in the deployed function, because Terraform always sets the ARN — which
    `tests/test_deploy_aws.py` asserts rather than assumes.
    """
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from headroom.db.migrate import DEFAULT_DATABASE_URL

    assert handler_module.database_url({}) == DEFAULT_DATABASE_URL


# --- the handler AWS actually calls ---------------------------------------------------------


def test_the_handler_returns_a_summary_and_logs_one_json_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Both, deliberately: the return value is what `aws lambda invoke` prints on the
    operator's terminal, and the log line is what survives in CloudWatch afterwards.

    A synchronous test on purpose — `handler` owns its own `asyncio.run`, which is the
    contract AWS's Python runtime expects, and calling it from inside a running loop would
    be testing something the Lambda never does.
    """
    store = InMemoryLedgerStore()
    asyncio.run(store.record(entry("hr_1", at=NOW)))
    monkeypatch.setattr(handler_module, "PostgresLedgerStore", lambda **_kwargs: store)
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused/headroom")

    result = handler_module.handler({"day": "2026-08-11"}, None)

    [day] = result["days"]
    assert (day["day"], day["tenants"], day["requests"]) == ("2026-08-11", 1, 1)
    assert Decimal(day["usd_cost"]) == Decimal("0.0000115")
    # Serialisable, because Lambda serialises it — a Decimal here would fail in
    # production and nowhere else.
    assert json.loads(json.dumps(result)) == result

    logged = json.loads(capsys.readouterr().out.strip())
    assert logged == result


def test_the_handler_closes_the_pool_even_when_the_rollup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Lambda that leaks a connection into the freeze holds it against `max_connections`
    until the container is reaped — and a nightly job gets a fresh container every time,
    so the leak is permanent rather than transient."""

    class Exploding(InMemoryLedgerStore):
        closed = False

        async def write_daily_rollup(self, day: date) -> list[Any]:
            raise RuntimeError("the ledger database is unreachable")

        async def aclose(self) -> None:
            type(self).closed = True

    store = Exploding()
    monkeypatch.setattr(handler_module, "PostgresLedgerStore", lambda **_kwargs: store)
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused/headroom")

    with pytest.raises(RuntimeError, match="unreachable"):
        handler_module.handler({}, None)

    assert Exploding.closed is True


def test_the_entry_the_rollup_reads_is_the_requests_own_arrival(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`started_at`, not `created_at` — the one field a late drain moves.

    A row created a minute after midnight for a request that arrived a minute before it
    belongs to the earlier day, and this is the assertion that says so at the boundary
    rather than in a comment.
    """
    before_midnight = datetime(2026, 8, 10, 23, 59, 30, tzinfo=UTC)
    after_midnight = datetime(2026, 8, 11, 0, 0, 30, tzinfo=UTC)
    store = InMemoryLedgerStore()
    asyncio.run(
        store.record(replace(entry("hr_late", at=before_midnight), created_at=after_midnight))
    )
    monkeypatch.setattr(handler_module, "PostgresLedgerStore", lambda **_kwargs: store)
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused/headroom")

    result = handler_module.handler({"days": 2}, None)

    by_day = {day["day"]: day["requests"] for day in result["days"]}
    assert by_day["2026-08-10"] == 1
    assert by_day["2026-08-11"] == 0
