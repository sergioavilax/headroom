"""The gate through the whole stack: a real request, a real refusal, a real ledger row.

Everything here drives the gateway over HTTP rather than calling the store, because the
questions this file answers are about the *pipeline*: does a refusal really stop before
the provider, does the 402 arrive in the caller's own dialect, does the hold really come
back when a request fails, and does the row say what happened.

The store underneath is the in-memory one — these are assertions about wiring and
semantics, not about concurrency. The concurrency claim has its own file, and it runs
against DynamoDB Local because a dict cannot be raced.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from headroom.core.budgets import RESERVATION_TTL_S, BudgetScope
from headroom.providers.mock import MockScript

from .support.fixtures import anthropic_request, openai_request
from .support.harness import GatewayHarness
from .support.streams import openai_text

#: The canonical mock reply: 11 prompt tokens, 7 generated, at $0.25/$1.25 per MTok.
FIXTURE_COST = Decimal("0.0000115")


# --- the happy path -------------------------------------------------------------------


async def test_a_request_under_the_cap_settles_to_what_it_actually_cost(
    gateway: GatewayHarness,
) -> None:
    """Reserve conservatively, settle exactly, hand the difference back."""
    await gateway.set_budget("1.00")
    gateway.book.set("ok", MockScript.anthropic_message("hello"))

    response = await gateway.post("/v1/messages", anthropic_request(), script="ok")

    assert response.status_code == 200
    budget = await gateway.budget()
    assert budget.spent == FIXTURE_COST
    assert budget.reserved == 0, "the hold is released the moment the cost is known"
    assert budget.remaining == Decimal("1.00") - FIXTURE_COST
    assert budget.reservations == 0
    assert budget.identity_holds


async def test_a_streamed_request_settles_the_same_way(gateway: GatewayHarness) -> None:
    """The settlement rides the metering exits, so the transport cannot change it."""
    await gateway.set_budget("1.00")
    gateway.book.set("ok", MockScript.anthropic_stream("hello"))

    response = await gateway.post("/v1/messages", anthropic_request(stream=True), script="ok")

    assert response.status_code == 200
    budget = await gateway.budget()
    assert budget.spent == FIXTURE_COST
    assert budget.reserved == 0
    assert budget.identity_holds


async def test_the_hold_is_visible_while_the_request_is_in_flight(
    gateway: GatewayHarness,
) -> None:
    """Not a detail: it is the entire mechanism. Between admission and settlement the
    budget is encumbered, which is what stops the next concurrent request."""
    await gateway.set_budget("1.00")
    gate = asyncio.Event()
    gateway.book.set(
        "held",
        MockScript(
            chunks=list(MockScript.anthropic_stream("hello").chunks),
            gate=gate,
            gate_before_chunk=2,
        ),
    )

    run = gateway.start("/v1/messages", anthropic_request(stream=True), script="held")
    try:
        await run.next_message()
        await run.next_body()
        mid_flight = await gateway.budgets.store.get(gateway.scope, when=datetime.now(UTC))
        assert not gate.is_set(), "the upstream is provably still open"
        assert mid_flight is not None
        assert mid_flight.reserved > 0
        assert mid_flight.spent == 0
        assert mid_flight.reservations == 1
    finally:
        gate.set()
        await run.drain()
        await run.finish()

    settled = await gateway.budget()
    assert settled.reserved == 0
    assert settled.spent == FIXTURE_COST


async def test_an_uncapped_tenant_is_admitted_and_nothing_is_held(
    gateway: GatewayHarness,
) -> None:
    """The default for every deployment that has not configured a budget — and the
    reason all 446 tests written before this phase did not have to change."""
    gateway.book.set("ok", MockScript.anthropic_message("hello"))

    response = await gateway.post("/v1/messages", anthropic_request(), script="ok")

    row = await gateway.ledger_row()
    assert response.status_code == 200
    assert row.budget_status == "no_budget"
    assert row.budget_reserved_usd is None
    assert row.budget_settled_usd is None


# --- the refusal ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "body", "error_type"),
    [
        ("/v1/messages", anthropic_request(), "billing_error"),
        ("/v1/chat/completions", openai_request(), "insufficient_quota"),
    ],
    ids=["anthropic", "openai"],
)
async def test_an_exhausted_budget_is_402_in_the_callers_own_dialect(
    gateway: GatewayHarness, path: str, body: dict[str, object], error_type: str
) -> None:
    """402 Payment Required, and an error type the caller's own SDK understands."""
    await gateway.set_budget("0.000001")

    response = await gateway.post(path, body)

    assert response.status_code == 402
    payload = response.json()
    assert payload["error"]["type"] == error_type
    assert payload["headroom"]["reason"] == "budget_exceeded"
    assert payload["headroom"]["request_id"] == gateway.last_context().request_id
    assert response.headers["x-headroom-error-source"] == "gateway"


async def test_a_refusal_never_reaches_a_provider(gateway: GatewayHarness) -> None:
    """The property the whole phase is for: refused means *not spent*, not *refunded*.

    Asserted against the MockProvider's own record of what it was handed, so it holds
    however the pipeline is later rearranged — there is no path from the raise to
    ``provider.open``.
    """
    await gateway.set_budget("0.000001")

    response = await gateway.post("/v1/messages", anthropic_request())

    assert response.status_code == 402
    assert gateway.provider.received == [], "the upstream was called for a refused request"
    assert gateway.provider.opened == []


async def test_a_refusal_writes_a_ledger_row(gateway: GatewayHarness) -> None:
    """The 402's row is the only surviving record that the request happened — nothing
    upstream saw it, so if this row were missing the request would be invisible."""
    await gateway.set_budget("0.000001")

    await gateway.post("/v1/messages", anthropic_request())

    row = await gateway.ledger_row()
    assert row.budget_status == "exceeded"
    assert row.status_code == 402
    assert row.outcome == "budget_exceeded"
    assert row.error_reason == "budget_exceeded"
    assert row.provider == "mock"
    # Zero, and that zero is a measurement: no model ran, so nothing was billable.
    assert row.usd_cost == 0
    assert row.cost_status == "not_billable"
    # What it *wanted* is recorded even though nothing was held.
    assert row.budget_reserved_usd is not None
    assert row.budget_reserved_usd > 0
    assert row.budget_settled_usd is None


async def test_the_refusal_message_quotes_the_numbers_that_explain_it(
    gateway: GatewayHarness,
) -> None:
    """A 402 a developer cannot act on is a support ticket. Every figure is the
    tenant's own, so quoting them discloses nothing they do not already have."""
    await gateway.set_budget("0.000001")

    response = await gateway.post("/v1/messages", anthropic_request())

    message = response.json()["error"]["message"]
    assert "monthly" in message
    assert "0.000001" in message
    assert "reserves" in message


async def test_the_budget_is_checked_after_scope_so_a_403_still_wins(
    gateway: GatewayHarness,
) -> None:
    """Ordering, deliberately: a key reaching past its scope is told so, whatever the
    tenant's balance. Otherwise a misconfigured client would chase a 402 that is not
    the actual problem."""
    await gateway.set_budget("0.000001")
    await gateway.store.update_key(gateway.key.id, allowed_models=["nothing-*"])
    gateway.authenticator.cache.invalidate_key(gateway.key.id)

    response = await gateway.post("/v1/messages", anthropic_request())

    assert response.status_code == 403


async def test_a_budget_refusal_does_not_consume_the_budget(gateway: GatewayHarness) -> None:
    await gateway.set_budget("0.000001")

    for _ in range(3):
        assert (await gateway.post("/v1/messages", anthropic_request())).status_code == 402

    budget = await gateway.budget()
    assert budget.spent == 0
    assert budget.reserved == 0
    assert budget.reservations == 0
    assert budget.identity_holds


# --- what a failure settles at ---------------------------------------------------------


async def test_an_upstream_error_hands_the_whole_hold_back(gateway: GatewayHarness) -> None:
    """Providers do not bill a rejected request, so neither does this."""
    await gateway.set_budget("1.00")
    gateway.book.set("boom", MockScript.error(429))

    response = await gateway.post("/v1/messages", anthropic_request(), script="boom")

    assert response.status_code == 429
    budget = await gateway.budget()
    assert budget.spent == 0
    assert budget.reserved == 0
    assert budget.remaining == Decimal("1.00")
    row = await gateway.ledger_row()
    assert row.budget_status == "reserved"
    assert row.budget_settled_usd == 0


async def test_a_timeout_settles_at_the_estimate_because_the_provider_may_have_billed_it(
    gateway: GatewayHarness,
) -> None:
    """The one place the budget and the invoice deliberately disagree (H-031).

    The ledger writes NULL — it states facts, and the fact is that nobody knows. The
    budget cannot say "unknown"; it has to hold a number, and the only defensible one
    is the bound already reserved. Releasing it would be a cheerful guess that a
    request which reached a model cost nothing.
    """
    await gateway.set_budget("1.00")
    gateway.book.set("slow", MockScript.timeout())

    response = await gateway.post("/v1/messages", anthropic_request(), script="slow")

    assert response.status_code == 504
    row = await gateway.ledger_row()
    assert row.cost_status == "usage_unknown"
    assert row.usd_cost is None, "the invoice says it does not know"
    assert row.budget_settled_usd == row.budget_reserved_usd, "the budget keeps its bound"

    budget = await gateway.budget()
    assert budget.reserved == 0
    assert budget.spent == row.budget_reserved_usd
    assert budget.identity_holds


async def test_a_mid_stream_cut_settles_at_the_estimate(gateway: GatewayHarness) -> None:
    """Tokens flowed; how many is unknowable. Same rule as the timeout."""
    await gateway.set_budget("1.00")
    gateway.book.set("cut", MockScript.anthropic_stream("hello", cut_after_chunks=3))

    response = await gateway.post("/v1/messages", anthropic_request(stream=True), script="cut")

    assert response.status_code == 200  # the failure is inside the stream (H-008)
    row = await gateway.ledger_row()
    assert row.outcome == "upstream_stream_cut"
    assert row.usd_cost is None
    assert row.budget_settled_usd == row.budget_reserved_usd

    budget = await gateway.budget()
    assert budget.spent > 0
    assert budget.reserved == 0
    assert budget.identity_holds


async def test_an_unroutable_model_never_takes_a_hold_at_all(
    gateway: GatewayHarness,
) -> None:
    """Routing fails before admission, so there is nothing to release."""
    await gateway.set_budget("1.00")

    response = await gateway.post("/v1/messages", anthropic_request(model="gpt-5"))

    assert response.status_code == 404
    budget = await gateway.budget()
    assert budget.spent == 0
    assert budget.reserved == 0
    row = await gateway.ledger_row()
    assert row.budget_status is None


async def test_a_client_disconnect_still_settles(gateway: GatewayHarness) -> None:
    """The hardest path: the settlement runs inside a cancellation.

    It is shielded, so it may finish after the request is gone — ``harness.budget()``
    drains it rather than sleeping. And if it never finished at all, the hold would
    expire and the sweeper would release it, which is what the expiry is for.
    """
    await gateway.set_budget("1.00")
    gate = asyncio.Event()  # never set: the upstream stays open until the client quits
    gateway.book.set(
        "hang",
        MockScript(
            chunks=list(MockScript.anthropic_stream("hello").chunks),
            gate=gate,
            gate_before_chunk=2,
        ),
    )

    run = gateway.start("/v1/messages", anthropic_request(stream=True), script="hang")
    await run.next_message()
    await run.next_body()
    run.disconnect()
    await run.finish()

    assert run.scope["state"]["ctx"].outcome == "client_disconnect"
    budget = await gateway.budget()
    assert budget.reserved == 0, "a hung-up caller must not leave budget encumbered"
    assert budget.reservations == 0
    assert budget.identity_holds


# --- the leak, end to end ---------------------------------------------------------------


async def test_a_stranded_hold_does_not_refuse_a_live_request(
    gateway: GatewayHarness,
) -> None:
    """A process died mid-request fifteen minutes ago and never settled.

    Its hold is still on the item and it covers the entire cap. The next real request
    through the gateway must still be served, because the refusal path sweeps before it
    refuses — which is the whole reason the sweep lives there rather than on a timer.
    """
    await gateway.set_budget("0.001")
    stale = datetime.now(UTC) - timedelta(seconds=RESERVATION_TTL_S + 60)
    await gateway.budgets.store.reserve(
        BudgetScope.tenant(gateway.tenant.id),
        request_id="hr_from_a_process_that_died",
        usd=Decimal("0.001"),
        when=stale,
    )
    gateway.book.set("ok", MockScript.anthropic_message("hello"))

    response = await gateway.post("/v1/messages", anthropic_request(), script="ok")

    assert response.status_code == 200
    budget = await gateway.budget()
    assert budget.expired_releases == 1
    assert budget.expired_released == Decimal("0.001")
    assert budget.spent == FIXTURE_COST
    assert budget.identity_holds


# --- the OpenAI dialect's unmeterable case ------------------------------------------------


async def test_a_stream_with_no_usage_block_settles_at_the_estimate(
    gateway: GatewayHarness,
) -> None:
    """H-028 leaves these requests unmetered on purpose — the gateway will not rewrite a
    caller's body to make them meterable. The budget still has to hold *something*, and
    the estimate is the only honest candidate."""
    await gateway.set_budget("1.00")
    gateway.book.set("no-usage", MockScript.openai_stream("hello", include_usage=False))
    body = openai_request(stream=True, max_tokens=32)
    body.pop("stream_options")

    response = await gateway.post("/v1/chat/completions", body, script="no-usage")

    assert response.status_code == 200
    assert openai_text(response.content) == "hello", "the answer is served, just unpriced"
    row = await gateway.ledger_row()
    assert row.cost_status == "usage_unknown"
    assert row.usd_cost is None
    assert row.budget_settled_usd == row.budget_reserved_usd
