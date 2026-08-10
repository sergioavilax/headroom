"""One request, one row, one reservation — however many providers it took.

Two claims, and they are the same claim from two sides.

**Money.** A request that hops must not double-reserve or double-bill. In the shipped
pipeline that holds *structurally*: the budget reservation is taken before the failover
executor exists in the call path and settled after it has finished, so every hop lives
inside one admission. Structural or not, it is the arithmetic a tenant is charged, so it
is measured here rather than reasoned about — the counters are read back off the store
and compared to the cost of the response that was actually served.

**Truth.** The ledger has to say what happened. ``provider`` moves to whoever served;
``failover_hops`` counts what was passed over; ``failover_from`` and ``failover_error``
name the first candidate that did not serve and why. Together with ``error_reason`` and
``upstream_status`` — which describe the *last* thing that happened — a two-link chain is
described completely by one row (docs/DECISIONS.md H-051, migrations/0006).

The rate limiter is here too, for the question it raises: a hop costs a second upstream
call, so should it cost a second bucket unit? No — the limiter meters *client requests*,
not upstream attempts, and a tenant should not be charged rate for the gateway's own
retry decision.
"""

from __future__ import annotations

from decimal import Decimal

from headroom.core.context import RequestContext
from headroom.core.errors import ProviderTimeout
from headroom.core.ledger import LedgerQuery
from headroom.metering.cost import COST_NOT_BILLABLE, COST_PRICED, COST_USAGE_UNKNOWN
from headroom.policy.failover import Failover
from headroom.providers.base import UpstreamRequest
from headroom.providers.mock import MockScript

from .support.fixtures import anthropic_request
from .support.harness import GatewayHarness

#: The canonical fixture's exact price: 11 prompt tokens at $0.25/MTok plus 7 generated
#: at $1.25/MTok. A terminating decimal, so nothing here needs a tolerance (H-023).
ONE_CALL_USD = Decimal("0.0000115")


def _a_fails_b_serves(chain: GatewayHarness, failure: MockScript) -> None:
    chain.book.set("fault@mock_a", failure)
    chain.book.set("fault@mock_b", MockScript.anthropic_message("served by mock_b"))


# --------------------------------------------------------------------------------
# The ledger row
# --------------------------------------------------------------------------------


async def test_a_failover_writes_exactly_one_row(chain: GatewayHarness) -> None:
    """Two upstream calls, one invoice line. The row count is the request count."""
    _a_fails_b_serves(chain, MockScript.error(529, dialect="anthropic"))

    await chain.post("/v1/messages", anthropic_request(), script="fault")
    await chain.writer.drain()

    rows = await chain.ledger.list_entries(LedgerQuery(limit=100))
    assert len(rows) == 1
    assert len(chain.providers["mock_a"].received) == 1
    assert len(chain.providers["mock_b"].received) == 1


async def test_the_row_names_who_served_and_who_failed(chain: GatewayHarness) -> None:
    """The gate's ledger-truth clause, in one assertion block."""
    _a_fails_b_serves(chain, MockScript.error(529, dialect="anthropic"))

    await chain.post("/v1/messages", anthropic_request(), script="fault")
    row = await chain.ledger_row()

    assert row.provider == "mock_b"  # who ultimately served
    assert row.failover_hops == 1  # how many were passed over
    assert row.failover_from == "mock_a"  # which one, first
    assert row.failover_error == "upstream_status_529"  # and why
    assert row.upstream_status == 200  # the *served* response, not the failed one
    assert row.outcome == "ok"
    assert row.cost_status == COST_PRICED
    assert row.usd_cost == ONE_CALL_USD


async def test_a_row_the_primary_served_says_nothing_about_failover(
    chain: GatewayHarness,
) -> None:
    """NULL and zero on the common path — which is what makes the partial index useful."""
    chain.book.set("ok", MockScript.anthropic_message("served"))

    await chain.post("/v1/messages", anthropic_request(), script="ok")
    row = await chain.ledger_row()

    assert (row.failover_hops, row.failover_from, row.failover_error) == (0, None, None)
    assert row.provider == "mock_a"


async def test_an_exhausted_chain_records_the_first_failure_and_the_last(
    chain: GatewayHarness,
) -> None:
    """Both ends of the story on one row, which is migration 0006's whole argument.

    ``failover_from``/``failover_error`` are the *first* failure — why the request left
    where it was routed. ``error_reason``/``upstream_status`` are the *last* — what the
    caller was eventually handed. Neither could be derived from the other.
    """
    chain.book.set("down@mock_a", MockScript.error(429, dialect="anthropic"))
    chain.book.set("down@mock_b", MockScript.error(503, dialect="anthropic"))

    await chain.post("/v1/messages", anthropic_request(), script="down")
    row = await chain.ledger_row()

    assert (row.failover_from, row.failover_error) == ("mock_a", "upstream_status_429")
    assert (row.provider, row.upstream_status) == ("mock_b", 503)
    assert row.error_reason == "upstream_status_503"
    assert row.failover_hops == 1
    # An upstream that answered ≥400 billed nobody, and that zero is a measurement
    # rather than a default — H-025, unchanged by this phase.
    assert row.cost_status == COST_NOT_BILLABLE
    assert row.usd_cost == Decimal(0)


async def test_the_overhead_column_measures_the_serving_provider_not_the_chain(
    chain: GatewayHarness,
) -> None:
    """``passthrough_overhead_ms`` is the column §P8.H2 publishes, so it must stay clean.

    A failed attempt stamps ``first_upstream_byte_at`` when its error body is read. Left
    in place, the derived overhead — *first upstream byte → first byte out* — would
    silently include the entire failover sequence and turn a gateway-cost metric into a
    failover-duration metric. ``restart_upstream_timing`` is why it does not.

    ``ttft_ms`` deliberately keeps the whole wait, because that is what the caller
    experienced, and the two numbers being different is the point.
    """
    _a_fails_b_serves(chain, MockScript.error(529, dialect="anthropic"))

    await chain.post("/v1/messages", anthropic_request(), script="fault")
    row = await chain.ledger_row()

    assert row.passthrough_overhead_ms is not None
    assert row.upstream_latency_ms is not None
    assert row.ttft_ms is not None
    # The gateway's own cost is a slice of the request, not the request.
    assert row.passthrough_overhead_ms <= row.ttft_ms
    assert row.upstream_latency_ms <= row.ttft_ms


async def test_the_upstream_mark_is_rewound_between_attempts(chain: GatewayHarness) -> None:
    """The mechanism behind the test above, asserted where a mock's microseconds cannot hide it.

    Every mark on a ``RequestContext`` is idempotent — only the *first* call records
    anything — so a failed attempt's stamp survives forever unless something clears it.
    The context is seeded with a value no real ``perf_counter`` mark can produce, and the
    assertion is simply that it is gone: if ``restart_upstream_timing`` stopped being
    called, the sentinel would still be sitting there when the response was served, and
    every derived timing on that row would be measured from the wrong attempt.
    """
    failover: Failover = chain.failover
    chain.book.set("fault@mock_a", MockScript.error(529, dialect="anthropic"))
    chain.book.set("fault@mock_b", MockScript.anthropic_message("served by mock_b"))
    ctx = RequestContext()
    ctx.first_upstream_byte_at = -1.0  # a sentinel: no real mark is ever negative

    await failover.open(
        ("mock_a", "mock_b"),
        UpstreamRequest(
            dialect="anthropic",
            path="/v1/messages",
            model="mock-model-1",
            body=b"{}",
            stream=False,
            control={"mock-script": "fault"},
        ),
        ctx,
    )

    assert ctx.first_upstream_byte_at is not None
    assert ctx.first_upstream_byte_at > 0.0
    assert ctx.failover_hops == 1


# --------------------------------------------------------------------------------
# The budget: one reservation, one settlement
# --------------------------------------------------------------------------------


async def test_a_failover_reserves_once_and_settles_once(chain: GatewayHarness) -> None:
    """The no-double-billing claim, as arithmetic on the tenant's own counters.

    Admission happens above the executor and settlement below it, so the number of hops
    is invisible to the budget by construction. Measured anyway: settled spend is exactly
    one call's cost, and nothing is left held.
    """
    await chain.set_budget("1.00")
    _a_fails_b_serves(chain, MockScript.error(529, dialect="anthropic"))

    await chain.post("/v1/messages", anthropic_request(), script="fault")
    budget = await chain.budget()

    assert budget.spent == ONE_CALL_USD
    assert budget.reserved == Decimal(0)
    assert budget.reservations == 0
    row = await chain.ledger_row()
    assert row.budget_settled_usd == ONE_CALL_USD
    assert row.budget_status == "reserved"


async def test_two_hops_cost_the_same_as_none(chain: GatewayHarness) -> None:
    """The comparison that makes the claim legible: same question, same bill.

    One tenant, two identical requests — the first served by the primary, the second only
    after a hop. The tenant is charged twice for two answers, not three times for two
    answers and a failure.
    """
    await chain.set_budget("1.00")
    chain.book.set("clean", MockScript.anthropic_message("served by mock_a"))
    await chain.post("/v1/messages", anthropic_request(), script="clean")
    after_one = (await chain.budget()).spent

    _a_fails_b_serves(chain, MockScript.error(529, dialect="anthropic"))
    await chain.post("/v1/messages", anthropic_request(), script="fault")
    after_two = (await chain.budget()).spent

    assert after_one == ONE_CALL_USD
    assert after_two == ONE_CALL_USD * 2


async def test_an_exhausted_chain_releases_the_hold(chain: GatewayHarness) -> None:
    """Nobody billed, so nothing is charged — H-025/H-031 unchanged under failover.

    Every provider answered ≥400, which means no model ran anywhere in the chain. The
    settlement follows the cost, the cost is a measured zero, and the tenant's cap is
    exactly where it started.
    """
    await chain.set_budget("1.00")
    chain.book.set("down@mock_a", MockScript.error(529, dialect="anthropic"))
    chain.book.set("down@mock_b", MockScript.error(529, dialect="anthropic"))

    response = await chain.post("/v1/messages", anthropic_request(), script="down")
    budget = await chain.budget()

    assert response.status_code == 529
    assert budget.spent == Decimal(0)
    assert budget.reserved == Decimal(0)


async def test_a_chain_that_times_out_everywhere_settles_at_the_estimate(
    chain: GatewayHarness,
) -> None:
    """H-031's hardest row, under failover: a timeout may have been billed upstream.

    Two providers were *sent* the request and neither answered. Releasing the hold would
    be a cheerful guess that it cost nothing; the only defensible number is the bound
    already reserved. The ledger still writes NULL — it is an invoice and states facts —
    and the two figures sitting on one row is the disagreement H-031 documents.
    """
    await chain.set_budget("1.00")
    chain.book.set("gone@mock_a", MockScript.timeout())
    chain.book.set("gone@mock_b", MockScript.timeout())

    response = await chain.post("/v1/messages", anthropic_request(), script="gone")
    row = await chain.ledger_row()
    budget = await chain.budget()

    assert response.status_code == 504
    assert row.cost_status == COST_USAGE_UNKNOWN
    assert row.usd_cost is None
    assert row.budget_settled_usd == row.budget_reserved_usd
    assert budget.spent == row.budget_reserved_usd
    assert budget.reserved == Decimal(0)
    assert row.error_reason == ProviderTimeout.reason


# --------------------------------------------------------------------------------
# Rate limits: a hop is not a second request
# --------------------------------------------------------------------------------


async def test_a_hop_does_not_consume_a_second_bucket_unit(chain: GatewayHarness) -> None:
    """The limiter meters client requests, not upstream attempts.

    A hop is the gateway's own decision about how to serve one request, and charging the
    tenant rate for it would make a provider outage look like the tenant misbehaving —
    exactly when their traffic most needs to get through.
    """
    await chain.set_limits(requests_per_min=10)
    _a_fails_b_serves(chain, MockScript.error(529, dialect="anthropic"))

    await chain.post("/v1/messages", anthropic_request(), script="fault")
    bucket = await chain.bucket("requests", limit_per_min=10)

    assert bucket.available == 9
    assert chain.last_context().failover_hops == 1
