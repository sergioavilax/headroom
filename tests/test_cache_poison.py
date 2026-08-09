"""D-021, attempted. Every ineligible path is driven at the cache; nothing lands.

BUILD_PLAN §0.2 invariant 6 exists because of Backline's D-021, where a cache served
content that did not belong to the question asked. The Phase 5 form is worse than the
original — *"a semantic cache that stores an amputated answer poisons every future hit"*
— because one bad write is served forever, to every paraphrase, silently.

So this file does not test a predicate. It drives **the real gateway**, through the real
proxy exits, with caching switched on and the request eligible, and tries to make each
failure mode leave something behind:

* a stream cut after three chunks;
* a stream that simply stops without its terminal marker (the quiet half);
* a *complete stream* of a *truncated answer* (``stop_reason: max_tokens`` /
  ``finish_reason: length``) — the case that looks perfectly healthy from the outside;
* an upstream 429 and an upstream 500;
* a timeout, where the provider may well have generated an answer and there is not one
  byte of it here;
* a response carrying a tool call;
* a request that arrives with tools declared;
* a request the tenant is over budget for.

Each one asserts the same two things: the caller was told the truth, and the cache is
still empty. The second assertion is the one that matters — and it is only meaningful
because ``test_cache_gate.py`` proves the identical setup *does* store on the happy path.
"""

from __future__ import annotations

import json

import pytest

from headroom.cache.eligibility import (
    MAX_CACHEABLE_BODY_BYTES,
    REASON_INCOMPLETE,
    REASON_TOOL_OUTPUT,
    REASON_TOOLS,
    REASON_UPSTREAM_ERROR,
)
from headroom.core.cache import CACHE_SEMANTIC, DISPOSITION_BYPASS
from headroom.core.sse import iter_sse_events
from headroom.providers import mock_scripts
from headroom.providers.mock import MockScript
from tests.support.fixtures import ANTHROPIC_TOOLS, anthropic_request, openai_request
from tests.support.harness import GatewayHarness


async def enabled(gateway: GatewayHarness) -> None:
    """Caching fully on. Every test below starts from the state most likely to store."""
    await gateway.set_cache(CACHE_SEMANTIC)


async def assert_nothing_landed(gateway: GatewayHarness) -> None:
    await gateway.writer.drain()
    assert await gateway.cache_entries() == 0, "an ineligible response reached the cache"


# --- the two ways a stream ends early -----------------------------------------------------


async def test_a_cut_stream_is_never_cached(gateway: GatewayHarness) -> None:
    await enabled(gateway)
    gateway.book.set("cut", MockScript.anthropic_stream("The quick brown fox", cut_after_chunks=3))

    response = await gateway.post("/v1/messages", anthropic_request(stream=True), script="cut")

    events = [(event.event, event.data) for event in iter_sse_events(response.content)]
    assert events[-1][0] == "error"
    assert gateway.last_context().outcome == "upstream_stream_cut"
    assert gateway.last_context().cache_reason == REASON_INCOMPLETE
    await assert_nothing_landed(gateway)


async def test_a_stream_that_simply_stops_is_never_cached(gateway: GatewayHarness) -> None:
    """The quiet half: no exception anywhere, the bytes just end.

    A guard against *exceptions* alone misses this one entirely, which is why H-008 tracks
    the terminal marker rather than trusting the transport.
    """
    await enabled(gateway)
    chunks = mock_scripts.anthropic_stream_chunks("a complete-looking answer")
    gateway.book.set("short", MockScript(chunks=chunks[:-1]))

    await gateway.post("/v1/messages", anthropic_request(stream=True), script="short")

    assert gateway.last_context().outcome == "upstream_stream_incomplete"
    await assert_nothing_landed(gateway)


# --- the case that looks healthy -------------------------------------------------------------


async def test_an_answer_truncated_by_max_tokens_is_never_cached(
    gateway: GatewayHarness,
) -> None:
    """**The headline poison attempt.**

    Every signal here says success: 200, a well-formed body, ``message_stop`` present, no
    error anywhere. The only thing wrong with it is that the model was cut off
    mid-sentence — a *complete stream* of a *truncated answer*, exactly the distinction
    H-008 drew. A cache that stores this serves half an answer forever.
    """
    await enabled(gateway)
    gateway.book.set(
        "truncated",
        MockScript.anthropic_message("the answer begins and then", stop_reason="max_tokens"),
    )

    response = await gateway.post("/v1/messages", anthropic_request(), script="truncated")

    assert response.status_code == 200
    assert json.loads(response.content)["stop_reason"] == "max_tokens"
    assert gateway.last_context().outcome == "ok"
    assert gateway.last_context().cache_reason == REASON_INCOMPLETE
    await assert_nothing_landed(gateway)


async def test_a_streamed_answer_truncated_by_max_tokens_is_never_cached(
    gateway: GatewayHarness,
) -> None:
    await enabled(gateway)
    gateway.book.set(
        "truncated",
        MockScript.anthropic_stream("cut short by the budget", stop_reason="max_tokens"),
    )

    await gateway.post("/v1/messages", anthropic_request(stream=True), script="truncated")

    assert gateway.last_context().outcome == "ok"
    await assert_nothing_landed(gateway)


async def test_the_openai_length_finish_reason_is_never_cached(
    gateway: GatewayHarness,
) -> None:
    """The Phase 1 live-smoke shape: a reasoning model that spent its whole budget
    thinking. A complete stream, ``[DONE]`` present, no error — and no answer."""
    await enabled(gateway)
    gateway.book.set(
        "exhausted",
        MockScript(
            chunks=mock_scripts.openai_reasoning_stream_chunks(text="", finish_reason="length")
        ),
    )

    await gateway.post("/v1/chat/completions", openai_request(stream=True), script="exhausted")

    assert gateway.last_context().outcome == "ok"
    assert gateway.last_context().stop_reason == "length"
    await assert_nothing_landed(gateway)


# --- upstream failures ------------------------------------------------------------------------


@pytest.mark.parametrize("status", [429, 500, 529, 400])
async def test_an_upstream_error_body_is_never_cached(gateway: GatewayHarness, status: int) -> None:
    """The forwarded error body is a perfectly well-formed JSON document. It is also not
    an answer, and a cache that stored it would serve a 200 carrying a 429's text."""
    await enabled(gateway)
    gateway.book.set("boom", MockScript.error(status))

    response = await gateway.post("/v1/messages", anthropic_request(), script="boom")

    assert response.status_code == status
    assert gateway.last_context().cache_reason == REASON_UPSTREAM_ERROR
    await assert_nothing_landed(gateway)


async def test_a_timeout_is_never_cached(gateway: GatewayHarness) -> None:
    """The provider may well have generated an answer, and there is not one byte of it
    here. Both the empty body and the non-``ok`` outcome must refuse — either alone would
    be one accident away from storing an empty response as a real one."""
    await enabled(gateway)
    gateway.book.set("slow", MockScript.timeout())

    response = await gateway.post("/v1/messages", anthropic_request(), script="slow")

    assert response.status_code == 504
    await assert_nothing_landed(gateway)


async def test_a_connect_failure_is_never_cached(gateway: GatewayHarness) -> None:
    await enabled(gateway)
    gateway.book.set("down", MockScript.connect_error())

    assert (
        await gateway.post("/v1/messages", anthropic_request(), script="down")
    ).status_code == 502
    await assert_nothing_landed(gateway)


# --- tools ---------------------------------------------------------------------------------------


async def test_a_response_containing_a_tool_call_is_never_cached(
    gateway: GatewayHarness,
) -> None:
    """Reachable only if a model invents a tool call unprompted — which is why the check
    is here as well as on the request. Belt, and braces."""
    await enabled(gateway)
    gateway.book.set("tooled", MockScript(body=mock_scripts.anthropic_tool_use_body()))

    await gateway.post("/v1/messages", anthropic_request(), script="tooled")

    assert gateway.last_context().cache_reason in {REASON_TOOL_OUTPUT, REASON_INCOMPLETE}
    await assert_nothing_landed(gateway)


async def test_a_request_declaring_tools_neither_reads_nor_writes(
    gateway: GatewayHarness,
) -> None:
    await enabled(gateway)
    gateway.book.set("ok", MockScript.anthropic_message("a fine answer"))

    body = anthropic_request()
    body["tools"] = ANTHROPIC_TOOLS
    await gateway.post("/v1/messages", body, script="ok")

    ctx = gateway.last_context()
    assert ctx.cache_disposition == DISPOSITION_BYPASS
    assert ctx.cache_reason == REASON_TOOLS
    await assert_nothing_landed(gateway)


async def test_a_tool_result_conversation_is_never_cached(gateway: GatewayHarness) -> None:
    """The A5 shape — the conversation Backline's agents actually send."""
    from tests.support.fixtures import anthropic_tool_result_request

    await enabled(gateway)
    gateway.book.set("ok", MockScript.anthropic_message("a fine answer"))

    await gateway.post("/v1/messages", anthropic_tool_result_request(), script="ok")

    assert gateway.last_context().cache_disposition == DISPOSITION_BYPASS
    await assert_nothing_landed(gateway)


# --- refusals ---------------------------------------------------------------------------------


async def test_a_budget_refusal_is_never_cached(gateway: GatewayHarness) -> None:
    """A 402's body is the gateway's own error document. It is also the *only* record the
    request happened — and it must not become somebody's cached answer."""
    await enabled(gateway)
    gateway.book.set("ok", MockScript.anthropic_message("a fine answer"))
    await gateway.set_budget("0.000000000001")

    response = await gateway.post("/v1/messages", anthropic_request(), script="ok")

    assert response.status_code == 402
    await assert_nothing_landed(gateway)


async def test_a_response_over_the_size_bound_is_never_cached(
    gateway: GatewayHarness,
) -> None:
    await enabled(gateway)
    gateway.book.set("huge", MockScript.anthropic_message("x" * (MAX_CACHEABLE_BODY_BYTES + 1000)))

    response = await gateway.post("/v1/messages", anthropic_request(), script="huge")

    assert response.status_code == 200
    await assert_nothing_landed(gateway)


async def test_a_streamed_response_over_the_size_bound_stops_being_recorded(
    gateway: GatewayHarness,
) -> None:
    """The copy is abandoned mid-stream and the request is unaffected — the client still
    gets every byte."""
    await enabled(gateway)
    text = "y" * (MAX_CACHEABLE_BODY_BYTES + 1000)
    gateway.book.set("huge", MockScript.anthropic_stream(text))

    response = await gateway.post("/v1/messages", anthropic_request(stream=True), script="huge")

    assert response.status_code == 200
    # Every byte still reached the client: only the *copy* was abandoned.
    assert len(response.content) > MAX_CACHEABLE_BODY_BYTES
    assert gateway.last_context().outcome == "ok"
    await assert_nothing_landed(gateway)


# --- and the control -----------------------------------------------------------------------------


async def test_the_happy_path_really_does_store(gateway: GatewayHarness) -> None:
    """The control for every assertion above.

    Without this, "the cache is empty" would be satisfied by a cache that never stores
    anything at all — which is the way a poison-attempt suite passes for the wrong reason.
    """
    await enabled(gateway)
    gateway.book.set("ok", MockScript.anthropic_message("a complete answer"))

    await gateway.post("/v1/messages", anthropic_request(), script="ok")

    assert await gateway.cache_entries() == 1
