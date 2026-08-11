"""``/admin/usage``: the ledger as the Phase 7 dashboard will read it.

Three things are worth asserting about a reporting surface, and they are all about
what it *refuses* to do: it will not let a caller in without the root token, it will
not write, and it will not turn exact money into a JSON float on the way out.
"""

from __future__ import annotations

import json
from decimal import Decimal

from headroom.api.usage import MAX_BUCKETS
from headroom.core.ledger import MAX_ROLLUP_DAYS, LedgerQuery
from headroom.providers.mock import MockScript
from headroom.providers.mock_scripts import REASONING_MODEL, openai_reasoning_stream_chunks

from .support.corpus import load_corpus
from .support.fixtures import ANTHROPIC_TOOLS, anthropic_request, openai_request
from .support.harness import GatewayHarness


async def seed(gateway: GatewayHarness, *, count: int = 1) -> None:
    """Drive real requests through the gateway, then let the writer catch up."""
    gateway.book.set("ok", MockScript.anthropic_message("hi"))
    for _ in range(count):
        await gateway.post("/v1/messages", anthropic_request(), script="ok")
    await gateway.writer.drain()


# --- the credential ---------------------------------------------------------------------


async def test_the_usage_api_needs_the_root_admin_token(gateway: GatewayHarness) -> None:
    for path in ("/admin/usage", "/admin/usage/totals"):
        response = await gateway.admin("GET", path, authenticate=False)
        assert response.status_code == 401, path


async def test_a_virtual_key_is_not_an_admin_token_here_either(
    gateway: GatewayHarness,
) -> None:
    """The Phase 2 rule, extended to the surface that shows everyone's spend."""
    response = await gateway.admin("GET", "/admin/usage", token=gateway.api_key)

    assert response.status_code == 401


async def test_the_ledger_is_read_only(gateway: GatewayHarness) -> None:
    """No verb writes. A cost ledger with a write endpoint eventually gets written to."""
    for method in ("POST", "PATCH", "DELETE", "PUT"):
        response = await gateway.admin(method, "/admin/usage")
        assert response.status_code == 405, method


# --- listing -----------------------------------------------------------------------------


async def test_a_metered_request_appears_in_the_list(gateway: GatewayHarness) -> None:
    await seed(gateway)

    response = await gateway.admin("GET", "/admin/usage")

    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["model"] == "mock-model-1"
    assert rows[0]["tenant_id"] == gateway.tenant.id
    assert rows[0]["cost_status"] == "priced"


async def test_money_leaves_the_api_as_a_string(gateway: GatewayHarness) -> None:
    """**JSON has one number type and it is a double.**

    Serializing a NUMERIC as a JSON number would undo, at the last step, the exactness
    the price file, the Decimal arithmetic, and the column type all exist to preserve.
    Asserted against the raw bytes, because ``response.json()`` would hide a float that
    happened to round-trip today.
    """
    await seed(gateway)

    response = await gateway.admin("GET", "/admin/usage")

    assert b'"usd_cost":"0.000011500000"' in response.content.replace(b", ", b",")
    row = response.json()[0]
    assert isinstance(row["usd_cost"], str)
    assert Decimal(row["usd_cost"]) == Decimal("0.0000115")


async def test_a_row_reports_the_rates_it_was_billed_at(gateway: GatewayHarness) -> None:
    """Not today's rates. "Why did this cost that" is the explorer's whole question."""
    await seed(gateway)

    row = (await gateway.admin("GET", "/admin/usage")).json()[0]

    assert row["usd_per_mtok_in"] == "0.25"
    assert row["usd_per_mtok_out"] == "1.25"
    assert row["price_effective_from"] == "1970-01-01"


async def test_rows_filter_by_tenant_and_model(gateway: GatewayHarness) -> None:
    await seed(gateway)

    matching = await gateway.admin("GET", "/admin/usage", params={"tenant_id": gateway.tenant.id})
    other = await gateway.admin(
        "GET", "/admin/usage", params={"tenant_id": "00000000-0000-4000-8000-000000000000"}
    )
    wrong_model = await gateway.admin("GET", "/admin/usage", params={"model": "gpt-5"})

    assert len(matching.json()) == 1
    assert other.json() == []
    assert wrong_model.json() == []


async def test_the_list_pages(gateway: GatewayHarness) -> None:
    await seed(gateway, count=3)

    page = await gateway.admin("GET", "/admin/usage", params={"limit": "2"})
    rest = await gateway.admin("GET", "/admin/usage", params={"limit": "2", "offset": "2"})

    assert len(page.json()) == 2
    assert len(rest.json()) == 1


async def test_one_row_by_request_id(gateway: GatewayHarness) -> None:
    """The path from a caller's ``x-headroom-request-id`` to what they were charged."""
    gateway.book.set("ok", MockScript.anthropic_message("hi"))
    proxied = await gateway.post("/v1/messages", anthropic_request(), script="ok")
    await gateway.writer.drain()
    request_id = proxied.headers["x-headroom-request-id"]

    response = await gateway.admin("GET", f"/admin/usage/{request_id}")

    assert response.status_code == 200
    assert response.json()["request_id"] == request_id


async def test_an_unknown_request_id_is_a_404_in_headrooms_own_envelope(
    gateway: GatewayHarness,
) -> None:
    response = await gateway.admin("GET", "/admin/usage/hr_nope")

    assert response.status_code == 404
    assert response.json()["headroom"]["reason"] == "ledger_row_not_found"


# --- totals -------------------------------------------------------------------------------


async def test_totals_aggregate_a_tenants_spend(gateway: GatewayHarness) -> None:
    await seed(gateway, count=3)

    totals = (await gateway.admin("GET", "/admin/usage/totals")).json()

    assert len(totals) == 1
    assert totals[0]["tenant_id"] == gateway.tenant.id
    assert totals[0]["requests"] == 3
    assert Decimal(totals[0]["usd_cost"]) == Decimal("0.0000345")
    assert totals[0]["input_tokens"] == 33
    assert totals[0]["output_tokens"] == 21


async def test_totals_split_by_model_on_request(gateway: GatewayHarness) -> None:
    await seed(gateway)
    gateway.book.set("reasoning", MockScript(chunks=openai_reasoning_stream_chunks()))
    await gateway.post(
        "/v1/chat/completions",
        openai_request(model=REASONING_MODEL, stream=True),
        script="reasoning",
    )
    await gateway.writer.drain()

    totals = (await gateway.admin("GET", "/admin/usage/totals", params={"by_model": "true"})).json()

    by_model = {total["model"]: total for total in totals}
    assert set(by_model) == {"mock-model-1", REASONING_MODEL}
    assert by_model[REASONING_MODEL]["reasoning_tokens"] == 57


async def test_a_total_publishes_what_it_could_not_price(gateway: GatewayHarness) -> None:
    """The dashboard is told how much of the picture is missing, not just the sum."""
    gateway.book.set("no_usage", MockScript.openai_stream("hi", include_usage=False))
    await gateway.post("/v1/chat/completions", openai_request(stream=True), script="no_usage")
    await gateway.writer.drain()

    totals = (await gateway.admin("GET", "/admin/usage/totals")).json()

    assert totals[0]["requests"] == 1
    assert totals[0]["unpriced_requests"] == 1
    assert Decimal(totals[0]["usd_cost"]) == Decimal(0)


async def test_a_zero_cost_is_digits_not_exponent_notation(gateway: GatewayHarness) -> None:
    """``"0E-12"`` is what ``str(Decimal)`` gives and not what a dashboard should get."""
    await gateway.post("/v1/messages", anthropic_request(model="gpt-5"))
    await gateway.writer.drain()

    row = (await gateway.admin("GET", "/admin/usage")).json()[0]

    assert row["usd_cost"] == "0.000000000000"
    assert "E" not in row["usd_cost"]


async def test_a_total_counts_errors_separately(gateway: GatewayHarness) -> None:
    gateway.book.set("boom", MockScript.error(429))
    await gateway.post("/v1/messages", anthropic_request(), script="boom")
    await gateway.writer.drain()

    totals = (await gateway.admin("GET", "/admin/usage/totals")).json()

    assert totals[0]["errored_requests"] == 1


async def test_totals_of_nothing_are_empty(gateway: GatewayHarness) -> None:
    response = await gateway.admin("GET", "/admin/usage/totals")

    assert response.status_code == 200
    assert response.json() == []


async def test_the_response_is_json_a_dashboard_can_parse(gateway: GatewayHarness) -> None:
    await seed(gateway)

    response = await gateway.admin("GET", "/admin/usage")

    assert response.headers["content-type"].startswith("application/json")
    assert isinstance(json.loads(response.content), list)


# --- what the dashboard needed (Phase 7) --------------------------------------------------
#
# Three additions, and all three are reads of columns that already existed. The rule the
# phase held itself to is in the module docstring above: the console is a client of
# `/admin/*` and nothing else, so a view that needs a number either finds it here or the
# number gets published here properly — never fetched from the database behind the API's
# back (H-054).


async def test_a_hits_row_carries_what_it_saved_and_where_it_came_from(
    gateway: GatewayHarness,
) -> None:
    """The three Phase 5 columns the explorer's detail panel reads.

    ``cache_source_request_id`` is the one that matters: it is what turns "this was a
    semantic hit" into "…and here is the request whose answer you were served", which is
    the only way a human can audit a similarity score after the fact.
    """
    await gateway.set_cache("exact")
    gateway.book.set("ok", MockScript.anthropic_message("hi"))
    first = await gateway.post("/v1/messages", anthropic_request(), script="ok")
    await gateway.post("/v1/messages", anthropic_request(), script="ok")
    await gateway.writer.drain()

    rows = (await gateway.admin("GET", "/admin/usage")).json()
    hit = rows[0]

    assert hit["cache_disposition"] == "cache_hit_exact"
    assert hit["cache_avoided_usd"] == "0.000011500000"
    assert hit["cache_source_request_id"] == first.headers["x-headroom-request-id"]
    # Not a JSON number, for `usd_cost`'s reason: §P8.H1 sweeps thresholds off this column.
    assert hit["cache_similarity"] is None, "an exact hit has no similarity to report"


async def test_a_semantic_hits_similarity_leaves_as_a_string(gateway: GatewayHarness) -> None:
    """Against the committed corpus, so the number is one the real model produced."""
    corpus = load_corpus()
    question = corpus.question("streaming_revenue:radiohead")
    paraphrase = next(row for row in corpus.probes if row.source == question.id)
    answer = question.answer or ""

    await gateway.set_cache("semantic")
    gateway.book.set(answer, MockScript.anthropic_message(answer))
    await gateway.post("/v1/messages", anthropic_request(text=question.text), script=answer)
    await gateway.post("/v1/messages", anthropic_request(text=paraphrase.text), script=answer)
    await gateway.writer.drain()

    hit = (await gateway.admin("GET", "/admin/usage")).json()[0]

    assert hit["cache_disposition"] == "cache_hit_semantic"
    assert isinstance(hit["cache_similarity"], str)
    assert 0.9 <= float(hit["cache_similarity"]) <= 1.0


async def test_totals_count_every_cache_disposition_and_the_savings(
    gateway: GatewayHarness,
) -> None:
    """The Overview's savings counter and the Cache view's breakdown, in one call."""
    await gateway.set_cache("exact")
    gateway.book.set("ok", MockScript.anthropic_message("hi"))
    await gateway.post("/v1/messages", anthropic_request(), script="ok")  # miss
    await gateway.post("/v1/messages", anthropic_request(), script="ok")  # hit
    await gateway.post(  # bypass: a request that merely declares tools is ineligible
        "/v1/messages", anthropic_request(tools=ANTHROPIC_TOOLS), script="ok"
    )
    await gateway.writer.drain()

    totals = (await gateway.admin("GET", "/admin/usage/totals")).json()[0]

    assert totals["cache_misses"] == 1
    assert totals["cache_hits_exact"] == 1
    assert totals["cache_hits_semantic"] == 0
    assert totals["cache_bypasses"] == 1
    assert totals["cache_disabled"] == 0
    assert totals["cache_avoided_usd"] == "0.000011500000"


async def test_totals_count_the_requests_that_failed_over(gateway: GatewayHarness) -> None:
    """The number the kill demo makes move, published where the Overview can read it."""
    await seed(gateway, count=2)

    totals = (await gateway.admin("GET", "/admin/usage/totals")).json()[0]

    assert totals["failover_requests"] == 0


async def test_a_disabled_tenants_requests_are_counted_as_such(
    gateway: GatewayHarness,
) -> None:
    """ "Off" and "on but never applicable" are different rows, not one merged number."""
    await seed(gateway, count=2)

    totals = (await gateway.admin("GET", "/admin/usage/totals")).json()[0]

    assert totals["cache_disabled"] == 2
    assert totals["cache_misses"] == 0
    assert totals["cache_avoided_usd"] == "0"


# --- the series ----------------------------------------------------------------------------


async def test_the_series_needs_the_root_admin_token(gateway: GatewayHarness) -> None:
    response = await gateway.admin("GET", "/admin/usage/series", authenticate=False)

    assert response.status_code == 401


async def test_the_series_route_is_not_shadowed_by_the_request_id_route(
    gateway: GatewayHarness,
) -> None:
    """A literal path declared after a parameterised one is a route that never runs.

    Without the declaration order in ``usage.py`` this would be a 404 for a ledger row
    whose request id happened to be ``series`` — which is to say, always.
    """
    response = await gateway.admin("GET", "/admin/usage/series")

    assert response.status_code == 200
    assert response.json() == []


async def test_the_series_buckets_traffic_over_time(gateway: GatewayHarness) -> None:
    await seed(gateway, count=3)

    points = (await gateway.admin("GET", "/admin/usage/series", params={"bucket": "minute"})).json()

    assert len(points) >= 1
    assert sum(point["requests"] for point in points) == 3
    assert Decimal(points[-1]["usd_cost"]) > 0
    assert points == sorted(points, key=lambda point: point["bucket_start"])


async def test_the_series_speaks_money_as_a_string_too(gateway: GatewayHarness) -> None:
    await seed(gateway)

    point = (await gateway.admin("GET", "/admin/usage/series")).json()[0]

    assert point["usd_cost"] == "0.000011500000"
    assert point["cache_avoided_usd"] == "0"


async def test_an_unknown_bucket_is_a_422_naming_the_ones_that_work(
    gateway: GatewayHarness,
) -> None:
    response = await gateway.admin("GET", "/admin/usage/series", params={"bucket": "week"})

    assert response.status_code == 422
    body = response.json()
    assert body["headroom"]["reason"] == "unknown_bucket"
    assert "minute, hour, day" in body["error"]["message"]


async def test_the_series_bucket_count_is_bounded(gateway: GatewayHarness) -> None:
    """A `?bucket=minute` over a year must not ask for half a million groups."""
    response = await gateway.admin(
        "GET", "/admin/usage/series", params={"limit": str(MAX_BUCKETS + 1)}
    )

    assert response.status_code == 422


async def test_the_series_filters_by_tenant_like_everything_else(
    gateway: GatewayHarness,
) -> None:
    await seed(gateway, count=2)

    mine = (
        await gateway.admin("GET", "/admin/usage/series", params={"tenant_id": gateway.tenant.id})
    ).json()
    someone_else = (
        await gateway.admin(
            "GET",
            "/admin/usage/series",
            params={"tenant_id": "00000000-0000-4000-8000-000000000009"},
        )
    ).json()

    assert sum(point["requests"] for point in mine) == 2
    assert someone_else == []


# --- the daily rollups (Phase 9) ------------------------------------------------------


async def test_the_rollups_route_is_not_swallowed_by_the_request_id_route(
    gateway: GatewayHarness,
) -> None:
    """FastAPI matches in declaration order, so `/rollups` has to be declared above
    `/{request_id}` — otherwise it is a route that never runs and `rollups` is read as a
    request id nobody has. The failure looks like a 404 from the ledger, which is exactly
    the wrong place to go looking."""
    response = await gateway.admin("GET", "/admin/usage/rollups")

    assert response.status_code == 200
    assert isinstance(response.json(), list)


async def test_the_rollups_route_needs_the_root_admin_token(gateway: GatewayHarness) -> None:
    response = await gateway.admin("GET", "/admin/usage/rollups", authenticate=False)

    assert response.status_code == 401


async def test_there_is_no_route_that_fires_the_rollup(gateway: GatewayHarness) -> None:
    """Read-only like everything else here, and deliberately so.

    The schedule is EventBridge's and a manual run is `aws lambda invoke`. A POST would be
    a way to make the gateway's own database do arbitrary aggregate work from the internet
    side of a load balancer — which is the thing `ui/lib/proxy.ts` names as its example of
    what the console must never relay.
    """
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        response = await gateway.admin(method, "/admin/usage/rollups")
        assert response.status_code == 405, method


async def test_a_rollup_row_is_a_days_total_with_the_stamp_that_says_how_fresh_it_is(
    gateway: GatewayHarness,
) -> None:
    await seed(gateway, count=3)
    rows = await gateway.ledger.list_entries(LedgerQuery())
    [written] = await gateway.ledger.write_daily_rollup(rows[0].started_at.date())

    [row] = (await gateway.admin("GET", "/admin/usage/rollups")).json()

    assert row["day"] == written.day.isoformat()
    assert row["tenant_id"] == gateway.tenant.id
    assert row["requests"] == 3
    # Money as a string, here as everywhere else it leaves the process.
    assert Decimal(row["usd_cost"]) == Decimal("0.0000345")
    assert row["computed_at"] is not None
    # The counts that say how much of the picture the sums are missing (H-025's rule).
    assert row["unpriced_requests"] == 0
    assert row["cache_avoided_unknown"] == 0


async def test_a_rollup_window_bounded_beyond_a_year_is_refused(
    gateway: GatewayHarness,
) -> None:
    """`limit` is days, not rows, and a year plus a leap day is the published ceiling."""
    ok = await gateway.admin("GET", "/admin/usage/rollups", params={"limit": str(MAX_ROLLUP_DAYS)})
    too_many = await gateway.admin(
        "GET", "/admin/usage/rollups", params={"limit": str(MAX_ROLLUP_DAYS + 1)}
    )

    assert ok.status_code == 200
    assert too_many.status_code == 422
