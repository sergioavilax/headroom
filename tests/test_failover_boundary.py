"""The streaming boundary, from both sides — and the splice it exists to prevent.

This is the decision Phase 6 turns on (docs/DECISIONS.md H-048). A request may be
retried against another provider for exactly as long as **nothing about the response has
been committed to the client**. One byte later, retrying would join two providers'
answers into one response, and the thing that makes that catastrophic rather than merely
untidy is that *it looks fine*: the frames are well formed, the stream ends properly,
and every SDK on the far end returns a single complete message. Nothing in the transcript
says two models wrote it.

So this file has three parts.

1. **Before the byte** — the same fault, one instant earlier, fails over cleanly.
2. **After the byte** — the shipped gateway refuses to retry and falls back on H-008's
   terminal error event, which is a *visible* failure the caller's SDK raises on.
3. **The sabotage** — the naive implementation, executed, so the horror is a measurement.
   ``_spliced_passthrough`` below is not shipped code and never was; it is what a
   reasonable person writes when they decide the mid-stream cut "should also fail over",
   and it is mounted over the real ``_passthrough`` so the rest of the stack is genuine.

The two answers, for the identical fault, on the identical fixtures:

    shipped     "The capital of France is " + event: error(upstream_stream_cut)
    spliced     "The capital of France is The capital of Germany is Berlin."
                …with two message_start frames and one message_stop, presented as one
                complete answer with stop_reason "end_turn" and no error anywhere in it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from headroom.api import proxy as proxy_module
from headroom.api.gateway import Gateway
from headroom.core.context import OUTCOME_OK, RequestContext
from headroom.core.errors import ProviderError, ProviderTimeout, UpstreamStreamCut
from headroom.dialects.anthropic import ANTHROPIC
from headroom.dialects.base import Dialect
from headroom.policy.failover import Failover
from headroom.providers.base import UpstreamRequest, UpstreamResponse
from headroom.providers.mock import MockScript

from .support.fixtures import anthropic_request
from .support.harness import GatewayHarness
from .support.streams import anthropic_text, event_pairs

FRANCE = "The capital of France is Paris."
GERMANY = "The capital of Germany is Berlin."


def _cut_a_serve_b(chain: GatewayHarness, *, after: int = 8) -> None:
    """A starts answering about France and dies; B would answer about Germany.

    Two *different* answers on purpose. A splice of two identical answers reads as a
    stutter and could be mistaken for a chunking artefact; a splice of two different ones
    is unmistakable, and it is what a real failover would produce, because the fallback
    is answering the same question from scratch with its own sampling.
    """
    chain.book.set("cut@mock_a", MockScript.anthropic_stream(FRANCE, cut_after_chunks=after))
    chain.book.set("cut@mock_b", MockScript.anthropic_stream(GERMANY))


# --------------------------------------------------------------------------------
# 1. Before the first downstream byte: failover is free
# --------------------------------------------------------------------------------


async def test_a_fault_before_the_first_byte_fails_over_silently(
    chain: GatewayHarness,
) -> None:
    """The safe side of the line, and the reason the line is worth drawing carefully.

    ``mock_a`` never produces a byte — it times out while opening — so the caller is
    served entirely by ``mock_b`` and sees one complete, coherent answer.
    """
    chain.book.set("cut@mock_a", MockScript.timeout())
    chain.book.set("cut@mock_b", MockScript.anthropic_stream(GERMANY))

    response = await chain.post("/v1/messages", anthropic_request(stream=True), script="cut")

    assert response.status_code == 200
    assert anthropic_text(response.content) == GERMANY
    assert [name for name, _ in event_pairs(response.content)].count("message_start") == 1
    assert chain.last_context().failover_hops == 1
    assert chain.last_context().outcome == OUTCOME_OK


async def test_a_non_streamed_body_that_dies_mid_read_still_fails_over(
    chain: GatewayHarness,
) -> None:
    """The subtle half of "before the first byte", and worth the machinery it costs.

    A non-streamed request reads the whole upstream body before anything is sent, so a
    connection that dies *during* that read has still committed nothing downstream — and
    is therefore still safe to retry. That is exactly what killing a container while a
    request is in flight produces, so it is the difference between the live demo losing
    a request and not losing one.

    The executor gets this by reading the body *inside* the retry loop rather than after
    it, which is the entire reason ``BufferedUpstreamResponse`` exists.
    """
    cut = MockScript.anthropic_stream(FRANCE, cut_after_chunks=2)
    chain.book.set("cut@mock_a", MockScript(chunks=cut.chunks, cut_after_chunks=2))
    chain.book.set("cut@mock_b", MockScript.anthropic_message(GERMANY))

    response = await chain.post("/v1/messages", anthropic_request(), script="cut")

    assert response.status_code == 200
    assert GERMANY in response.text
    ctx = chain.last_context()
    assert (ctx.failover_hops, ctx.failover_error) == (1, UpstreamStreamCut.reason)


# --------------------------------------------------------------------------------
# 2. After the first downstream byte: the shipped answer
# --------------------------------------------------------------------------------


async def test_a_cut_after_the_first_byte_is_a_terminal_error_not_a_hop(
    chain: GatewayHarness,
) -> None:
    """The load-bearing assertion of the phase.

    ``mock_b`` is scripted, healthy, and one line away — and is never called. What the
    caller receives is the fragment plus H-008's terminal error event, which is the
    Phase 1 discipline arriving unchanged: a stream that ended early says so, in the
    caller's own dialect, and the caller's SDK raises.
    """
    _cut_a_serve_b(chain)

    response = await chain.post("/v1/messages", anthropic_request(stream=True), script="cut")

    text = anthropic_text(response.content)
    events = [name for name, _ in event_pairs(response.content)]
    assert chain.providers["mock_b"].received == []
    assert GERMANY not in text
    assert events.count("message_start") == 1
    assert events[-1] == "error"
    assert b"message_stop" not in response.content
    assert chain.last_context().outcome == UpstreamStreamCut.reason
    assert chain.last_context().failover_hops == 0


async def test_the_fragment_the_caller_keeps_is_only_the_first_providers(
    chain: GatewayHarness,
) -> None:
    """Whatever survives a cut is one provider's prose, entire and unmixed.

    Stated separately from the test above because it is the property, and the events are
    only the evidence for it: the bytes a client accumulated before the failure are a
    prefix of what ``mock_a`` was saying, and contain nothing ``mock_b`` would have said.
    """
    _cut_a_serve_b(chain)

    response = await chain.post("/v1/messages", anthropic_request(stream=True), script="cut")

    text = anthropic_text(response.content)
    assert text and FRANCE.startswith(text)
    assert not any(word in text for word in ("Germany", "Berlin"))


async def test_the_executor_itself_refuses_to_retry_once_a_byte_is_out(
    chain: GatewayHarness,
) -> None:
    """The guard, tested directly rather than inferred from the proxy's shape.

    In the shipped gateway this is unreachable: the executor returns before any code
    that can yield. That is a property of *today's call sites*, and call sites change —
    so the executor checks, and this is the test that keeps the check honest. A context
    that has already released a byte cannot be given a second provider, whatever the
    chain says.
    """
    failover: Failover = chain.failover
    chain.book.set("gone@mock_a", MockScript.timeout())
    chain.book.set("gone@mock_b", MockScript.anthropic_message(GERMANY))
    ctx = RequestContext()
    ctx.mark_first_token_out()  # a byte has been committed downstream

    with pytest.raises(ProviderTimeout):
        await failover.open(
            ("mock_a", "mock_b"),
            UpstreamRequest(
                dialect="anthropic",
                path=ANTHROPIC.upstream_path,
                model="mock-model-1",
                body=b"{}",
                stream=True,
                control={"mock-script": "gone"},
            ),
            ctx,
        )

    assert chain.providers["mock_b"].received == []
    assert ctx.failover_hops == 0


async def test_without_the_byte_the_same_call_does_fail_over(chain: GatewayHarness) -> None:
    """The control for the test above: the only difference is the mark."""
    failover: Failover = chain.failover
    chain.book.set("gone@mock_a", MockScript.timeout())
    chain.book.set("gone@mock_b", MockScript.anthropic_message(GERMANY))
    ctx = RequestContext()

    upstream = await failover.open(
        ("mock_a", "mock_b"),
        UpstreamRequest(
            dialect="anthropic",
            path=ANTHROPIC.upstream_path,
            model="mock-model-1",
            body=b"{}",
            stream=False,
            control={"mock-script": "gone"},
        ),
        ctx,
    )

    assert upstream.status_code == 200
    assert GERMANY in (await upstream.aread()).decode()
    assert ctx.failover_hops == 1


# --------------------------------------------------------------------------------
# 3. THE SPLICE SABOTAGE — the implementation this policy exists to forbid
# --------------------------------------------------------------------------------


async def _sabotaged_passthrough(
    dialect: Dialect, ctx: RequestContext, upstream: UpstreamResponse, gateway: Gateway
) -> AsyncIterator[bytes]:
    """**NOT SHIPPED CODE.** The mid-stream cut, "fixed" by failing over.

    This is what a well-meaning change looks like: the stream died, there is a healthy
    fallback right there, and the caller is waiting — so open the fallback and keep
    yielding. Fifteen lines, every one of them locally reasonable, and it produces the
    response ``test_the_sabotage_serves_a_frankenstein_answer`` measures.

    It is deliberately *minimal*: no attempt to strip the second stream's opening frames,
    because that is precisely the point. A gateway doing this "properly" would have to
    parse, re-frame, and re-emit somebody else's SSE — which H-007 refuses even on the
    happy path, and which cannot be made safe here anyway, since the second provider is
    answering a question the caller has already been handed half an answer to.
    """
    try:
        async for chunk in upstream.aiter_bytes():
            ctx.mark_first_token_out()
            yield chunk
    except ProviderError:
        # "It only failed halfway through — just carry on with the other provider."
        fallback = gateway.registry.get("mock_b")
        second = await fallback.open(
            UpstreamRequest(
                dialect=dialect.name,
                path=dialect.upstream_path,
                model=ctx.model or "",
                body=b"{}",
                stream=True,
                control={"mock-script": "cut"},
            ),
            ctx,
        )
        async for chunk in second.aiter_bytes():
            yield chunk
        await second.aclose()
    finally:
        await upstream.aclose()
    ctx.complete(OUTCOME_OK)
    gateway.meter.record(ctx, dialect.usage_observer().usage)


async def test_the_sabotage_serves_a_frankenstein_answer(
    chain: GatewayHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run the forbidden implementation and measure exactly what it hands the caller.

    Everything here is real except ``_passthrough``: real routes, real auth, real
    providers, real SSE. The result is the reason H-048 is a decision rather than a
    preference — and note which assertion is the frightening one. It is not that the
    text is wrong. It is that the stream is **well formed and terminates normally**, so
    nothing downstream has any way to know.
    """
    _cut_a_serve_b(chain)
    monkeypatch.setattr(proxy_module, "_passthrough", _sabotaged_passthrough)

    response = await chain.post("/v1/messages", anthropic_request(stream=True), script="cut")
    text = anthropic_text(response.content)
    events = [name for name, _ in event_pairs(response.content)]

    # Two answers, welded end to end, to two different questions.
    assert "France" in text and "Berlin" in text
    assert text == "The capital of France is " + GERMANY
    assert text.startswith("The capital of France is ")
    # Two openings and one ending, which is not a shape any provider ever emits…
    assert events.count("message_start") == 2
    assert events.count("message_stop") == 1
    # …and yet the stream terminates cleanly, with no error event anywhere in it. An SDK
    # parses this as one complete message and returns it. THAT is the horror.
    assert "error" not in events
    assert response.status_code == 200


async def test_the_shipped_gateway_refuses_the_same_splice(chain: GatewayHarness) -> None:
    """The pair to the sabotage, on the identical fixtures, with nothing patched.

    Same fault, same providers, same scripts. The difference is that the caller is told,
    and being told is the entire product: a visible failure they can retry is worth more
    than an invisible answer they cannot check.
    """
    _cut_a_serve_b(chain)

    response = await chain.post("/v1/messages", anthropic_request(stream=True), script="cut")
    text = anthropic_text(response.content)
    events = [name for name, _ in event_pairs(response.content)]

    assert "Berlin" not in text
    assert events.count("message_start") == 1
    assert events.count("message_stop") == 0
    assert events[-1] == "error"
