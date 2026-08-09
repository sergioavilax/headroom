"""The rate limiter through the whole stack: a real burst, a real 429, a real ledger row.

Everything here drives the gateway over HTTP rather than calling the store, because the
questions this file answers are about the *pipeline*: does a refusal really stop before
the provider, does the 429 arrive in the caller's own dialect with a `retry-after` on it,
does the gate really sit where H-039 says it does, and does the row say what happened.

The store underneath is the in-memory one — these are assertions about wiring and
semantics, not about concurrency. The concurrency claim has its own file, and it runs
against DynamoDB Local because a dict cannot be raced.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from headroom.core.errors import (
    RATELIMIT_LIMIT_HEADER,
    RATELIMIT_REMAINING_HEADER,
    RATELIMIT_RESET_HEADER,
    RATELIMIT_SCOPE_HEADER,
)
from headroom.core.limits import DIM_REQUESTS, SCOPE_KEY, SCOPE_TENANT
from headroom.dialects.anthropic import ANTHROPIC
from headroom.metering.prices import load_price_book
from headroom.policy.budgets import Estimate, estimate_usd
from headroom.policy.limits import token_cost
from headroom.providers.mock import MockScript

from .support.fixtures import DEFAULT_MODEL, anthropic_request, openai_request
from .support.harness import GatewayHarness

#: The canonical mock reply's settled cost: 11 prompt tokens, 7 generated.
FIXTURE_COST = Decimal("0.0000115")

#: Exact bytes, for the one test whose arithmetic has to be exact. Everything else posts
#: the fixture dict and lets the client serialize it.
BODY = anthropic_request()
RAW_BODY = json.dumps(BODY, separators=(",", ":")).encode()


def fixture_estimate() -> Estimate:
    """The bound both gates measure :data:`RAW_BODY` against — the gateway's own function."""
    return estimate_usd(
        ANTHROPIC,
        BODY,
        RAW_BODY,
        model=DEFAULT_MODEL,
        when=datetime.now(UTC),
        prices=load_price_book(),
    )


async def burst(gateway: GatewayHarness, count: int, *, script: str = "ok") -> list[int]:
    """``count`` sequential requests; the status of each, in order."""
    statuses = []
    for _ in range(count):
        response = await gateway.post("/v1/messages", anthropic_request(), script=script)
        statuses.append(response.status_code)
    return statuses


# --- the limit holds --------------------------------------------------------------------


async def test_a_requests_per_minute_limit_admits_exactly_its_capacity(
    gateway: GatewayHarness,
) -> None:
    """Three, then 429 — through the whole gateway, in one wall-clock instant.

    Sequential rather than concurrent on purpose: this is the *semantic* claim, and it
    has to hold before the concurrency claim is worth making.
    """
    gateway.book.set("ok", MockScript.anthropic_message("hello"))
    await gateway.set_limits(requests_per_min=3)

    statuses = await burst(gateway, 6)

    assert statuses == [200, 200, 200, 429, 429, 429]
    assert len(gateway.provider.received) == 3, "a refusal must never reach the provider"


async def test_an_uncapped_tenant_touches_no_bucket_at_all(gateway: GatewayHarness) -> None:
    """The reason every test written before this phase did not have to change.

    An unconfigured scope is not "a limit of infinity" that gets consumed and always
    passes — it is skipped entirely, so a deployment that sets no limits does no extra
    work on the request path and writes nothing anywhere.
    """
    gateway.book.set("ok", MockScript.anthropic_message("hello"))

    assert await burst(gateway, 5) == [200] * 5

    ctx = gateway.last_context()
    assert ctx.rate_limit_status is None
    assert ctx.rate_limit_scope is None
    bucket = gateway.bucket_key(DIM_REQUESTS)
    assert await gateway.limits.store.clear(bucket) is False, "nothing was ever written"


async def test_a_tokens_per_minute_limit_measures_the_same_bound_the_budget_reserves(
    gateway: GatewayHarness,
) -> None:
    """One estimate, two gates (H-034, H-039).

    The tokens charged here are exactly ``estimate.input_tokens + estimate.output_tokens``
    — the same number the budget gate turns into dollars — so a tenant cannot be measured
    as large by one gate and small by the other. The test computes that number with the
    gateway's own function rather than hard-coding it, and sizes the bucket at
    ``2 * cost - 1``: exactly one request fits, and the second is short by one token.
    """
    gateway.book.set("ok", MockScript.anthropic_message("hello"))
    cost = token_cost(fixture_estimate())
    await gateway.set_limits(tokens_per_min=cost * 2 - 1)

    first = await gateway.post("/v1/messages", RAW_BODY, script="ok")
    assert first.status_code == 200

    second = await gateway.post("/v1/messages", RAW_BODY, script="ok")
    assert second.status_code == 429
    assert second.headers[RATELIMIT_SCOPE_HEADER] == "tenant:tokens"
    assert int(second.headers[RATELIMIT_LIMIT_HEADER]) == cost * 2 - 1
    assert int(second.headers[RATELIMIT_REMAINING_HEADER]) == cost - 1


async def test_a_request_bigger_than_the_whole_bucket_is_refused_without_a_retry_after(
    gateway: GatewayHarness,
) -> None:
    """H-038's honest header is the absent one: waiting will never make this fit."""
    gateway.book.set("ok", MockScript.anthropic_message("hello"))
    await gateway.set_limits(tokens_per_min=10)

    response = await gateway.post("/v1/messages", anthropic_request(), script="ok")

    assert response.status_code == 429
    assert "retry-after" not in response.headers
    assert RATELIMIT_RESET_HEADER not in response.headers
    body = response.json()
    assert body["headroom"]["reason"] == "rate_limit_exceeds_capacity"
    assert "waiting will not help" in body["error"]["message"]
    assert not gateway.provider.received


# --- the two scopes ------------------------------------------------------------------


async def test_a_key_has_its_own_bucket_and_it_is_consumed_first(
    gateway: GatewayHarness,
) -> None:
    """BUILD_PLAN's *"per key and per tenant"*, and the order H-036 argues for.

    The key's limit is the tighter one, so it is what refuses — and the refusal names
    the key's bucket, not the tenant's.
    """
    gateway.book.set("ok", MockScript.anthropic_message("hello"))
    await gateway.set_limits(requests_per_min=10, scope=SCOPE_TENANT)
    await gateway.set_limits(requests_per_min=2, scope=SCOPE_KEY)

    statuses = await burst(gateway, 4)

    assert statuses == [200, 200, 429, 429]
    assert gateway.last_context().rate_limit_scope == "key:requests"
    # The tenant's bucket paid for the two that got through and nothing more: a request
    # the key refused never reached the shared bucket at all.
    tenant_bucket = await gateway.bucket(DIM_REQUESTS, limit_per_min=10, scope=SCOPE_TENANT)
    assert tenant_bucket.available == 8


async def test_the_tenant_bucket_refuses_a_key_that_is_within_its_own_limit(
    gateway: GatewayHarness,
) -> None:
    """The shared ceiling still applies to a well-behaved key.

    And here the key's bucket *is* over-consumed relative to what was served — two
    requests were admitted by the key and refused by the tenant. That is the documented
    non-refund (H-036): the limiter errs strict, and the key's bucket refills it back
    within one emission interval.
    """
    gateway.book.set("ok", MockScript.anthropic_message("hello"))
    await gateway.set_limits(requests_per_min=2, scope=SCOPE_TENANT)
    await gateway.set_limits(requests_per_min=100, scope=SCOPE_KEY)

    statuses = await burst(gateway, 4)

    assert statuses == [200, 200, 429, 429]
    assert gateway.last_context().rate_limit_scope == "tenant:requests"
    key_bucket = await gateway.bucket(DIM_REQUESTS, limit_per_min=100, scope=SCOPE_KEY)
    assert key_bucket.available == 96, "all four were charged to the key, including the two refused"


# --- the refusal, in the caller's dialect --------------------------------------------


@pytest.mark.parametrize("dialect", ["anthropic", "openai"])
async def test_the_429_speaks_the_callers_dialect(gateway: GatewayHarness, dialect: str) -> None:
    """The SDK on the other end has to raise something it knows (H-009).

    Both dialects spell a rate limit ``rate_limit_error``, which is a real value in both
    vocabularies; the precision — *which* bucket, and whether waiting helps — travels in
    ``headroom.reason`` and the headers, where no SDK can be surprised by it.
    """
    gateway.book.set("ok", MockScript.anthropic_message("hello"))
    await gateway.set_limits(requests_per_min=1)

    path = "/v1/messages" if dialect == "anthropic" else "/v1/chat/completions"
    body = anthropic_request() if dialect == "anthropic" else openai_request()
    await gateway.post(path, body, script="ok")
    response = await gateway.post(path, body, script="ok")

    assert response.status_code == 429
    payload = response.json()
    if dialect == "anthropic":
        assert payload["type"] == "error"
        assert payload["error"]["type"] == "rate_limit_error"
    else:
        assert payload["error"]["type"] == "rate_limit_error"
        assert payload["error"]["code"] == "rate_limited"
    assert payload["headroom"]["reason"] == "rate_limited"
    assert payload["headroom"]["request_id"] == gateway.last_context().request_id


async def test_the_refusal_carries_a_retry_after_a_client_can_act_on(
    gateway: GatewayHarness,
) -> None:
    """A limit of 4/min emits one unit every 15 seconds, and the header says so."""
    gateway.book.set("ok", MockScript.anthropic_message("hello"))
    await gateway.set_limits(requests_per_min=4)

    await burst(gateway, 4)
    response = await gateway.post("/v1/messages", anthropic_request(), script="ok")

    assert response.status_code == 429
    assert response.headers["retry-after"] == "15"
    assert response.headers[RATELIMIT_RESET_HEADER] == "15"
    assert response.headers[RATELIMIT_SCOPE_HEADER] == "tenant:requests"
    assert response.headers[RATELIMIT_LIMIT_HEADER] == "4"
    assert response.headers[RATELIMIT_REMAINING_HEADER] == "0"


async def test_the_message_quotes_the_callers_own_numbers(gateway: GatewayHarness) -> None:
    """The most common support question ("why 429?") answers itself.

    Every figure is the caller's own — their limit, their bucket, their request's size —
    so quoting it discloses nothing they do not already own.
    """
    gateway.book.set("ok", MockScript.anthropic_message("hello"))
    await gateway.set_limits(requests_per_min=2)

    await burst(gateway, 2)
    response = await gateway.post("/v1/messages", anthropic_request(), script="ok")

    message = response.json()["error"]["message"]
    assert "rate limit of 2 requests per minute" in message
    assert "0 are available" in message
    assert "retry in 30s" in message


# --- the order of the gates (H-039) ----------------------------------------------------


async def test_an_out_of_scope_key_is_told_that_rather_than_told_to_slow_down(
    gateway: GatewayHarness,
) -> None:
    """403 before 429: the scope checks run first, and a burst cannot mask a permission
    problem behind a rate limit the caller would waste time backing off from."""
    gateway.book.set("ok", MockScript.anthropic_message("hello"))
    await gateway.set_limits(requests_per_min=1)
    scoped = await gateway.store.update_key(gateway.key.id, allowed_models=["other-*"])
    assert scoped is not None
    gateway.authenticator.cache.invalidate_key(gateway.key.id)

    for _ in range(3):
        response = await gateway.post("/v1/messages", anthropic_request(), script="ok")
        assert response.status_code == 403

    # And the bucket was never touched, so a 403 storm cannot exhaust a tenant's limit.
    bucket = await gateway.bucket(DIM_REQUESTS, limit_per_min=1)
    assert bucket.available == 1


async def test_an_anonymous_request_never_reaches_the_limiter(gateway: GatewayHarness) -> None:
    """401 before 429, for the same reason: an unidentified caller has no bucket."""
    await gateway.set_limits(requests_per_min=1)

    response = await gateway.post("/v1/messages", anthropic_request(), authenticate=False)

    assert response.status_code == 401
    assert (await gateway.bucket(DIM_REQUESTS, limit_per_min=1)).available == 1


async def test_a_rate_limited_request_never_reserves_budget(gateway: GatewayHarness) -> None:
    """**The reason the limiter runs first** (H-039).

    A request refused for rate must not have taken a budget hold it then hands straight
    back: a compensating release on the hot path is exactly the shape this phase refuses
    to add, and it is where D-019 grows back.
    """
    gateway.book.set("ok", MockScript.anthropic_message("hello"))
    await gateway.set_budget("1.00")
    await gateway.set_limits(requests_per_min=1)

    assert (await gateway.post("/v1/messages", anthropic_request(), script="ok")).status_code == 200
    refused = await gateway.post("/v1/messages", anthropic_request(), script="ok")

    assert refused.status_code == 429
    budget = await gateway.budget()
    assert budget.spent == FIXTURE_COST, "only the served request was charged"
    assert budget.reserved == 0
    assert budget.reservations == 0
    assert budget.identity_holds
    # And the refused request's context never got as far as the budget gate.
    ctx = gateway.last_context()
    assert ctx.budget_status is None
    assert ctx.budget_reserved_usd is None


async def test_a_request_over_both_limits_answers_429_and_402_on_the_retry(
    gateway: GatewayHarness,
) -> None:
    """Deliberate, and stated so it cannot be mistaken for an accident.

    "Slow down" is advice a client can act on immediately; one wasted round trip is
    cheaper than hammering the single budget item to discover the cap is gone too.
    """
    gateway.book.set("ok", MockScript.anthropic_message("hello"))
    await gateway.set_budget("0.000001")  # far below one request's estimate
    await gateway.set_limits(requests_per_min=1)

    first = await gateway.post("/v1/messages", anthropic_request(), script="ok")
    second = await gateway.post("/v1/messages", anthropic_request(), script="ok")

    assert first.status_code == 402, "the first request is inside its limit and over its cap"
    assert second.status_code == 429, "the second is over its limit, and never reaches the cap"
    assert not gateway.provider.received


# --- what a refusal leaves behind -------------------------------------------------------


async def test_a_refusal_writes_a_ledger_row_and_calls_no_provider(
    gateway: GatewayHarness,
) -> None:
    """Same discipline as a budget refusal: a 429 is the only record the request existed,
    since nothing upstream ever saw it."""
    gateway.book.set("ok", MockScript.anthropic_message("hello"))
    await gateway.set_limits(requests_per_min=1)

    await gateway.post("/v1/messages", anthropic_request(), script="ok")
    refused = await gateway.post("/v1/messages", anthropic_request(), script="ok")
    assert refused.status_code == 429

    row = await gateway.ledger_row()
    assert row.outcome == "rate_limited"
    assert row.status_code == 429
    assert row.upstream_status is None
    assert row.usd_cost == 0, "a zero that is a measurement: no model ran"
    assert row.cost_status == "not_billable"
    assert row.error_source == "gateway"
    assert row.error_reason == "rate_limited"
    assert len(gateway.provider.received) == 1


async def test_the_log_line_names_the_bucket_that_refused(gateway: GatewayHarness) -> None:
    """Four buckets can refuse one request, and an HTTP status names none of them."""
    gateway.book.set("ok", MockScript.anthropic_message("hello"))
    await gateway.set_limits(requests_per_min=1, tokens_per_min=100_000)

    await gateway.post("/v1/messages", anthropic_request(), script="ok")
    served = gateway.last_context().as_log_fields()
    await gateway.post("/v1/messages", anthropic_request(), script="ok")
    refused = gateway.last_context().as_log_fields()

    assert served["rate_limit_status"] == "ok"
    assert served["rate_limit_scope"] is None
    assert refused["rate_limit_status"] == "limited"
    assert refused["rate_limit_scope"] == "tenant:requests"


async def test_an_upstream_failure_does_not_hand_the_bucket_unit_back(
    gateway: GatewayHarness,
) -> None:
    """The other half of "a rate limit never settles".

    A request that reached the provider and failed still *used* the rate: it occupied a
    connection, it may have cost the provider work, and the whole point of a flow limit is
    to bound how often that can happen. There is deliberately no code that gives the unit
    back — which is exactly the asymmetry with the budget gate, where the same failure
    releases the hold to $0.
    """
    gateway.book.set("boom", MockScript.error(500))
    await gateway.set_limits(requests_per_min=10)

    response = await gateway.post("/v1/messages", anthropic_request(), script="boom")

    assert response.status_code == 500
    assert (await gateway.bucket(DIM_REQUESTS, limit_per_min=10)).available == 9


async def test_a_limit_change_bites_on_the_very_next_request(gateway: GatewayHarness) -> None:
    """The limits ride the ``Principal``, so a change has to invalidate the auth cache.

    Without that invalidation a tightened limit would take up to ``AUTH_CACHE_TTL_S``
    seconds to apply in the process that made the change — which is not what an operator
    reaching for `/admin/limits` during an incident means (H-037).
    """
    gateway.book.set("ok", MockScript.anthropic_message("hello"))

    assert await burst(gateway, 2) == [200, 200]
    await gateway.set_limits(requests_per_min=1)
    assert await burst(gateway, 2) == [200, 429]
