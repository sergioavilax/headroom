"""Metering end to end: a request goes in, a priced ledger row comes out.

Everything here drives the whole stack — middleware, auth, routing, proxy, dialect,
meter, writer, store — because that is the only way to know the seams line up. The
unit-level properties live next door in ``test_prices.py``, ``test_cost.py`` and
``test_usage_extraction.py``; this file is about the request.

Four claims are load-bearing and each has a test written to fail if it stops holding:

* the cost of a known request is **exact**, in both dialects and both transports;
* a **failure** is a row too, with the cost semantics its failure implies;
* a **price change never reprices history** — the D-017 test, at the ledger this time;
* metering **changed nothing** the client receives, which is the whole passthrough
  design (H-007) meeting a feature that has every reason to want to rewrite bytes.
"""

from __future__ import annotations

import inspect
import json
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from headroom.core.context import RequestContext
from headroom.core.ledger import LedgerQuery, format_usd
from headroom.metering.cost import (
    COST_NOT_BILLABLE,
    COST_PARTIAL,
    COST_PRICED,
    COST_UNPRICED_MODEL,
    COST_USAGE_UNKNOWN,
)
from headroom.metering.meter import Meter
from headroom.metering.prices import ModelPrices, PriceBook, PriceRow
from headroom.metering.usage import Usage
from headroom.providers.mock import MockScript
from headroom.providers.mock_scripts import REASONING_MODEL, openai_reasoning_stream_chunks

from .support.fixtures import anthropic_request, openai_request
from .support.harness import GatewayHarness
from .support.streams import event_pairs, openai_text

#: 11 prompt + 7 generated tokens at the shipped mock rates ($0.25 / $1.25 per MTok).
#: Written out rather than computed so a changed rate fails here with a number a human
#: can check by hand, instead of quietly agreeing with itself.
CANONICAL_COST = Decimal("0.0000115")


# --- the happy paths, priced exactly -------------------------------------------------


async def test_a_non_streamed_anthropic_request_writes_an_exactly_priced_row(
    gateway: GatewayHarness,
) -> None:
    gateway.book.set("ok", MockScript.anthropic_message("hello"))

    await gateway.post("/v1/messages", anthropic_request(), script="ok")

    row = await gateway.ledger_row()
    assert (row.input_tokens, row.output_tokens) == (11, 7)
    assert row.usd_cost == CANONICAL_COST
    assert row.cost_status == COST_PRICED
    assert row.outcome == "ok"
    assert row.streamed is False
    assert row.stop_reason == "end_turn"


async def test_a_streamed_anthropic_request_prices_identically(
    gateway: GatewayHarness,
) -> None:
    """Same request, streamed. A price that depended on the transport would be a bug."""
    gateway.book.set("ok", MockScript.anthropic_stream("hello"))

    await gateway.post("/v1/messages", anthropic_request(stream=True), script="ok")

    row = await gateway.ledger_row()
    assert (row.input_tokens, row.output_tokens) == (11, 7)
    assert row.usd_cost == CANONICAL_COST
    assert row.streamed is True


async def test_a_non_streamed_openai_request_writes_an_exactly_priced_row(
    gateway: GatewayHarness,
) -> None:
    gateway.book.set("ok", MockScript.openai_completion("hello"))

    await gateway.post("/v1/chat/completions", openai_request(), script="ok")

    row = await gateway.ledger_row()
    assert (row.input_tokens, row.output_tokens) == (11, 7)
    assert row.usd_cost == CANONICAL_COST
    assert row.dialect == "openai"
    assert row.stop_reason == "stop"


async def test_a_streamed_openai_request_prices_identically(gateway: GatewayHarness) -> None:
    """The usage chunk arrives *after* the finish frame; the meter has to keep reading."""
    gateway.book.set("ok", MockScript.openai_stream("hello"))

    await gateway.post("/v1/chat/completions", openai_request(stream=True), script="ok")

    row = await gateway.ledger_row()
    assert (row.input_tokens, row.output_tokens) == (11, 7)
    assert row.usd_cost == CANONICAL_COST


async def test_scripted_token_counts_produce_a_hand_checkable_figure(
    gateway: GatewayHarness,
) -> None:
    """1,000,000 in and 1,000,000 out at $0.25 / $1.25 is $1.50. No decimals to trust."""
    gateway.book.set(
        "big",
        MockScript.anthropic_message("hello", input_tokens=1_000_000, output_tokens=1_000_000),
    )

    await gateway.post("/v1/messages", anthropic_request(), script="big")

    row = await gateway.ledger_row()
    assert row.usd_cost == Decimal("1.50")


# --- the reasoning row: the phase's founding observation ------------------------------


async def test_a_reasoning_request_is_billed_on_completion_tokens_not_visible_text(
    gateway: GatewayHarness,
) -> None:
    """**The ledger row that proves the rule.**

    The Phase 1 live smoke found this on the operator's GPU and
    ``tests/test_reasoning_passthrough.py`` pinned it keylessly: 11 visible characters,
    63 completion tokens, 57 of them chain of thought that never appears in
    ``delta.content``. Here it becomes money — the row is billed on 63, not on the
    six-ish tokens a text-counting meter would have found, and the ledger says so in
    the same row that records the visible answer's length.
    """
    gateway.book.set("reasoning", MockScript(chunks=openai_reasoning_stream_chunks()))

    response = await gateway.post(
        "/v1/chat/completions",
        openai_request(model=REASONING_MODEL, stream=True),
        script="reasoning",
    )

    row = await gateway.ledger_row()
    assert row.output_tokens == 63, "billed on the usage block"
    assert row.reasoning_tokens == 57, "of which this much was never visible"
    assert row.input_tokens == 21
    # 21 * 0.25/1e6 + 63 * 1.25/1e6
    assert row.usd_cost == Decimal("0.000084")
    assert row.cost_status == COST_PRICED
    # And the thing a text-counting meter would have measured instead:
    assert len(openai_text(response.content)) == 11


async def test_an_exhausted_reasoning_budget_is_a_complete_billable_request(
    gateway: GatewayHarness,
) -> None:
    """Empty answer, ``finish_reason: length``, 63 tokens generated — and billed.

    The live smoke's original failure, priced. The caller got nothing useful; the
    provider still did the work, and the ledger is not the place to be charitable.
    """
    gateway.book.set(
        "spent",
        MockScript(chunks=openai_reasoning_stream_chunks(text="", finish_reason="length")),
    )

    await gateway.post(
        "/v1/chat/completions",
        openai_request(model=REASONING_MODEL, stream=True),
        script="spent",
    )

    row = await gateway.ledger_row()
    assert row.outcome == "ok"
    assert row.stop_reason == "length"
    assert row.usd_cost == Decimal("0.000084")


# --- attribution ---------------------------------------------------------------------


async def test_a_row_carries_the_tenant_and_key_that_authenticated(
    gateway: GatewayHarness,
) -> None:
    gateway.book.set("ok", MockScript.anthropic_message("hi"))

    await gateway.post("/v1/messages", anthropic_request(), script="ok")

    row = await gateway.ledger_row()
    assert row.tenant_id == gateway.tenant.id
    assert row.key_id == gateway.key.id
    assert row.model == "mock-model-1"
    assert row.provider == "mock"
    assert row.route == "/v1/messages"


async def test_a_row_carries_the_timings_phase_8_reports(gateway: GatewayHarness) -> None:
    """H2's pre-registered p50 overhead target is read off this column."""
    gateway.book.set("ok", MockScript.anthropic_stream("hi"))

    await gateway.post("/v1/messages", anthropic_request(stream=True), script="ok")

    row = await gateway.ledger_row()
    assert row.ttft_ms is not None and row.ttft_ms >= 0
    assert row.total_ms is not None and row.total_ms >= 0
    assert row.passthrough_overhead_ms is not None


async def test_the_request_id_links_the_response_header_to_the_row(
    gateway: GatewayHarness,
) -> None:
    """A caller's screenshot leads to a cost, which is the id's whole purpose."""
    gateway.book.set("ok", MockScript.anthropic_message("hi"))

    response = await gateway.post("/v1/messages", anthropic_request(), script="ok")

    row = await gateway.ledger_row(response.headers["x-headroom-request-id"])
    assert row.request_id == response.headers["x-headroom-request-id"]


# --- error accounting ------------------------------------------------------------------


async def test_an_upstream_429_is_a_row_that_provably_cost_nothing(
    gateway: GatewayHarness,
) -> None:
    """A rejected request is not billed by any provider, so ``0`` is a measurement."""
    gateway.book.set("rate_limited", MockScript.error(429, retry_after="2"))

    response = await gateway.post("/v1/messages", anthropic_request(), script="rate_limited")
    assert response.status_code == 429

    row = await gateway.ledger_row()
    assert row.outcome == "upstream_error"
    assert row.upstream_status == 429
    assert row.usd_cost == Decimal(0)
    assert row.cost_status == COST_NOT_BILLABLE
    assert row.input_tokens is None, "there was no usage block to read"


async def test_a_mid_stream_cut_is_a_row_whose_cost_is_unknown_not_zero(
    gateway: GatewayHarness,
) -> None:
    """**The honest half of the pair above.**

    The connection died after some answer was generated. The provider *will* bill for
    it and Headroom cannot know how much — so the row records the outcome, records the
    prompt count that did arrive, and leaves the money NULL. Writing ``0`` here would
    make a truncation look free; writing an estimate would make it look known.
    """
    gateway.book.set("cut", MockScript.anthropic_stream("hello there", cut_after_chunks=4))

    await gateway.post("/v1/messages", anthropic_request(stream=True), script="cut")

    row = await gateway.ledger_row()
    assert row.outcome == "upstream_stream_cut"
    assert row.usd_cost is None
    assert row.cost_status == COST_USAGE_UNKNOWN
    assert row.input_tokens == 11, "message_start arrived before the cut"
    assert row.output_tokens is None, "message_delta never did"


async def test_a_timeout_is_unknown_because_the_provider_may_have_billed_it(
    gateway: GatewayHarness,
) -> None:
    """We sent it and got no answer. "Free" would be a guess; NULL is the truth."""
    gateway.book.set("slow", MockScript.timeout())

    await gateway.post("/v1/messages", anthropic_request(), script="slow")

    row = await gateway.ledger_row()
    assert row.outcome == "upstream_timeout"
    assert row.usd_cost is None
    assert row.cost_status == COST_USAGE_UNKNOWN


async def test_an_unreachable_provider_provably_generated_nothing(
    gateway: GatewayHarness,
) -> None:
    """A connection that never opened is the one no-answer case that *is* free."""
    gateway.book.set("down", MockScript.connect_error())

    await gateway.post("/v1/messages", anthropic_request(), script="down")

    row = await gateway.ledger_row()
    assert row.outcome == "upstream_unavailable"
    assert row.usd_cost == Decimal(0)
    assert row.cost_status == COST_NOT_BILLABLE


async def test_an_unroutable_model_is_a_row_with_no_provider(
    gateway: GatewayHarness,
) -> None:
    """Authenticated, so attributable — and Phase 8 counts these as error accounting."""
    await gateway.post("/v1/messages", anthropic_request(model="gpt-4o"))

    row = await gateway.ledger_row()
    assert row.outcome == "model_not_routed"
    assert row.provider is None
    assert row.model == "gpt-4o"
    assert row.usd_cost == Decimal(0)


async def test_a_scope_refusal_is_attributed_to_the_key_that_was_refused(
    gateway: GatewayHarness,
) -> None:
    narrowed = await gateway.admin(
        "PATCH", f"/admin/keys/{gateway.key.id}", json={"allowed_models": ["nothing-*"]}
    )
    assert narrowed.status_code == 200

    response = await gateway.post("/v1/messages", anthropic_request())
    assert response.status_code == 403

    row = await gateway.ledger_row()
    assert row.outcome == "model_out_of_scope"
    assert row.key_id == gateway.key.id
    assert row.usd_cost == Decimal(0)


async def test_an_anonymous_request_writes_no_row_at_all(gateway: GatewayHarness) -> None:
    """**The one refusal the ledger declines**, and it is deliberate (H-025).

    A request that never identified itself has no tenant and no key. A row for it
    would be unattributable in a table whose entire purpose is attribution — and the
    structured log line already records the 401, so nothing is lost but the noise.
    """
    response = await gateway.post("/v1/messages", anthropic_request(), authenticate=False)
    assert response.status_code == 401

    assert await gateway.ledger_row_or_none() is None


async def test_a_malformed_body_from_a_known_tenant_is_still_not_a_row(
    gateway: GatewayHarness,
) -> None:
    """It authenticated, but it never named a model — so there was nothing to price."""
    response = await gateway.post("/v1/messages", b"{oh no")
    assert response.status_code == 400

    assert await gateway.ledger_row_or_none() is None


# --- the unmeterable request, recorded as unmeterable ----------------------------------


async def test_a_stream_without_include_usage_is_recorded_as_unknown(
    gateway: GatewayHarness,
) -> None:
    """H-028's consequence, visible in the ledger rather than papered over.

    The caller did not ask for usage, so no provider ever sends any. Headroom does not
    inject the option on their behalf — see the fidelity test below for why — and the
    row says ``usage_unknown`` instead of an estimate nobody could audit.
    """
    gateway.book.set("no_usage", MockScript.openai_stream("hello", include_usage=False))

    await gateway.post("/v1/chat/completions", openai_request(stream=True), script="no_usage")

    row = await gateway.ledger_row()
    assert row.outcome == "ok"
    assert row.cost_status == COST_USAGE_UNKNOWN
    assert row.usd_cost is None
    assert row.stop_reason == "stop", "the finish reason still arrived"


async def test_an_unpriced_model_still_records_its_tokens(gateway: GatewayHarness) -> None:
    """Tokens known, money unknown. The two are recorded separately for this reason."""
    gateway.meter.prices = PriceBook([])
    gateway.book.set("ok", MockScript.anthropic_message("hi"))

    await gateway.post("/v1/messages", anthropic_request(), script="ok")

    row = await gateway.ledger_row()
    assert (row.input_tokens, row.output_tokens) == (11, 7)
    assert row.cost_status == COST_UNPRICED_MODEL
    assert row.usd_cost is None


# --- D-017, at the ledger --------------------------------------------------------------


def _book(*rows: tuple[str, str, str], model: str = "mock-model-1") -> PriceBook:
    return PriceBook(
        [
            ModelPrices(
                model=model,
                dialects=("anthropic",),
                context_window=None,
                rows=tuple(
                    PriceRow(
                        effective_from=date.fromisoformat(when),
                        usd_per_mtok_in=Decimal(price_in),
                        usd_per_mtok_out=Decimal(price_out),
                    )
                    for when, price_in, price_out in rows
                ),
            )
        ]
    )


def _context(when: datetime) -> RequestContext:
    """A finished, successful request that arrived at ``when``."""
    ctx = RequestContext(route="/v1/messages")
    ctx.tenant_id = "11111111-1111-4111-8111-111111111111"
    ctx.key_id = "22222222-2222-4222-8222-222222222222"
    ctx.dialect = "anthropic"
    ctx.model = "mock-model-1"
    ctx.upstream_status = 200
    ctx.started_at = when
    ctx.complete("ok", status_code=200)
    return ctx


@pytest.mark.parametrize(
    ("when", "expected"),
    [
        # $1/Mtok in, $2/Mtok out until the boundary; $10/$20 from it.
        (datetime(2026, 5, 31, 23, 59, tzinfo=UTC), Decimal("0.000024")),
        (datetime(2026, 6, 1, 0, 0, tzinfo=UTC), Decimal("0.00024")),
    ],
)
def test_the_same_request_on_either_side_of_a_boundary_gets_each_price(
    when: datetime, expected: Decimal
) -> None:
    """The dated-price gate: one model, two rows, two answers.

    Priced from the request's own ``started_at`` rather than from wall-clock time, so
    a queued write, a retry, or a replayed fixture cannot drift across the boundary.
    """
    meter = Meter(prices=_book(("2026-01-01", "1", "2"), ("2026-06-01", "10", "20")))

    result = meter.record(_context(when), Usage(input_tokens=4, output_tokens=10))

    assert result.usd_cost == expected


async def test_a_price_change_never_reprices_a_row_that_already_landed(
    gateway: GatewayHarness,
) -> None:
    """**THE D-017 TEST.**

    Backline's scar was a meter that kept billing at stale prices; the mirror-image
    failure is a meter that retroactively re-bills history when prices change. Both
    come from the same mistake — treating the price as a property of the model rather
    than of the transaction — and the fix is the same: the row copies the rates it used.

    So: bill a request, then replace the entire price book with rates a thousand times
    higher, and read the row back. It must be unmoved, down to the effective date.
    """
    gateway.book.set("ok", MockScript.anthropic_message("hi"))
    await gateway.post("/v1/messages", anthropic_request(), script="ok")
    before = await gateway.ledger_row()

    gateway.meter.prices = _book(("1970-01-01", "250", "1250"))

    after = await gateway.ledger.get(before.request_id)
    assert after is not None
    assert after.usd_cost == CANONICAL_COST
    assert after.usd_per_mtok_in == Decimal("0.25")
    assert after.usd_per_mtok_out == Decimal("1.25")
    assert after.price_effective_from == date(1970, 1, 1)


async def test_the_new_price_does_apply_to_the_next_request(
    gateway: GatewayHarness,
) -> None:
    """The other half: history is immutable, the future is not."""
    gateway.book.set("ok", MockScript.anthropic_message("hi"))
    await gateway.post("/v1/messages", anthropic_request(), script="ok")

    gateway.meter.prices = _book(("1970-01-01", "250", "1250"))
    await gateway.post("/v1/messages", anthropic_request(), script="ok")
    await gateway.writer.drain()

    rows = await gateway.ledger.list_entries(LedgerQuery(limit=100))
    costs = {row.usd_cost for row in rows}
    assert costs == {CANONICAL_COST, Decimal("0.0115")}


# --- provider cache tiers --------------------------------------------------------------


async def test_a_reply_reporting_cache_tiers_is_marked_partial(
    gateway: GatewayHarness,
) -> None:
    """The cost is a bound and the row says so, rather than looking like a total."""
    gateway.book.set(
        "cached",
        MockScript(
            body=b'{"type":"message","stop_reason":"end_turn","usage":'
            b'{"input_tokens":11,"output_tokens":7,"cache_read_input_tokens":4000}}'
        ),
    )

    await gateway.post("/v1/messages", anthropic_request(), script="cached")

    row = await gateway.ledger_row()
    assert row.cache_read_tokens == 4_000
    assert row.cost_status == COST_PARTIAL
    assert row.usd_cost == CANONICAL_COST


# --- metering changed nothing the caller sees ------------------------------------------


async def test_the_gateway_does_not_add_stream_options_to_the_callers_body(
    gateway: GatewayHarness,
) -> None:
    """**Why H-028 chose not to inject.**

    Injecting ``stream_options`` upstream would close the usage gap — and would mean
    re-serializing the request body, which is exactly the thing the whole proxy is
    built never to do (H-007, and the A5 tool-block guarantee that rests on it).
    Stripping the extra chunk on the way back would break the other half. So the bytes
    the provider receives are the bytes the caller sent, and a request without usage
    stays without usage.
    """
    body = b'{"model":"mock-model-1","stream":true,"messages":[{"role":"user","content":"hi"}]}'
    gateway.book.set("ok", MockScript.openai_stream("hi", include_usage=False))

    await gateway.post("/v1/chat/completions", body, script="ok")

    assert gateway.last_upstream_request().body == body
    assert b"stream_options" not in gateway.last_upstream_request().body


async def test_metering_a_stream_does_not_disturb_one_byte_of_it(
    gateway: GatewayHarness,
) -> None:
    """The observer is a tap, and now two taps read the same events.

    Phase 3 made the passthrough loop keep feeding its parser past the terminal
    marker. If that had turned the tap into a filter, this is where it would show.
    """
    chunks = openai_reasoning_stream_chunks()
    gateway.book.set("reasoning", MockScript(chunks=chunks))

    response = await gateway.post(
        "/v1/chat/completions",
        openai_request(model=REASONING_MODEL, stream=True),
        script="reasoning",
    )

    assert response.content == b"".join(chunks)
    assert event_pairs(response.content) == event_pairs(b"".join(chunks))


def test_the_metering_call_on_the_request_path_cannot_await_anything() -> None:
    """The non-blocking guarantee, asserted structurally rather than by a stopwatch.

    ``Meter.record`` and ``LedgerWriter.submit`` are ordinary functions, not
    coroutines. There is therefore no point on the request path — including inside the
    streaming generator's last frames — where a slow database could suspend the
    response, because there is nothing to suspend on. A timing test would only tell us
    the database was fast today.
    """
    from headroom.metering.writer import LedgerWriter

    assert not inspect.iscoroutinefunction(Meter.record)
    assert not inspect.iscoroutinefunction(LedgerWriter.submit)


async def test_the_row_lands_after_the_response_not_before_it(
    gateway: GatewayHarness,
) -> None:
    """And the queued row does arrive, once the writer is given a turn."""
    gateway.book.set("ok", MockScript.anthropic_message("hi"))

    response = await gateway.post("/v1/messages", anthropic_request(), script="ok")
    assert response.status_code == 200

    request_id = response.headers["x-headroom-request-id"]
    await gateway.writer.drain()
    assert await gateway.ledger.get(request_id) is not None


# --- the log line, which is the fallback when a row is lost ----------------------------


async def test_the_log_line_carries_the_same_figures_as_the_row(
    gateway: GatewayHarness,
) -> None:
    """H-027's second leg: a lost row is reconstructible from stdout."""
    gateway.book.set("ok", MockScript.anthropic_message("hi"))

    await gateway.post("/v1/messages", anthropic_request(), script="ok")

    fields = gateway.last_context().as_log_fields()
    row = await gateway.ledger_row()
    assert fields["input_tokens"] == row.input_tokens
    assert fields["output_tokens"] == row.output_tokens
    assert fields["cost_status"] == row.cost_status
    assert row.usd_cost is not None
    assert fields["usd_cost"] == str(row.usd_cost)


async def test_the_logged_cost_is_a_string_not_a_json_number(
    gateway: GatewayHarness,
) -> None:
    """JSON numbers are doubles. Serializing money as one would undo the whole point.

    Round-tripped through ``json`` rather than string-matched, so what is asserted is
    the *type on the wire*: a reader gets the digits back verbatim, not a float that
    happens to print the same today.
    """
    gateway.book.set("ok", MockScript.anthropic_message("hi"))
    await gateway.post("/v1/messages", anthropic_request(), script="ok")

    logged = json.loads(json.dumps(gateway.last_context().as_log_fields()))
    assert logged["usd_cost"] == "0.000011500000"
    assert isinstance(logged["usd_cost"], str)


async def test_a_zero_cost_serializes_as_digits_not_exponent_notation(
    gateway: GatewayHarness,
) -> None:
    """``str(Decimal("0.000000000000"))`` is ``"0E-12"`` — correct, and a surprise.

    A dashboard rendering a spend column should not have to know that. Plain decimal
    notation is what leaves the process, at both ends of the range.
    """
    await gateway.post("/v1/messages", anthropic_request(model="gpt-4o"))

    row = await gateway.ledger_row()
    assert row.usd_cost == Decimal(0)
    assert format_usd(row.usd_cost) == "0.000000000000"
    assert gateway.last_context().as_log_fields()["usd_cost"] == "0.000000000000"
