"""Proof that the gateway streams, rather than collects-then-sends.

*"SSE passthrough that never buffers the whole response — first-token latency is the
product."* That is a Phase 1 non-negotiable, and it is the one claim in the phase that
an ordinary HTTP test cannot check: ``TestClient`` and ``httpx.ASGITransport`` both
gather the full body before returning, so a gateway that buffered everything would pass
every other test in this suite unchanged.

So this file asserts **causally**, not by timing. The upstream is held open mid-stream
with a gate the test controls; the test then checks the client already has bytes *while
that gate is still shut*. If any buffer existed, those bytes could not exist yet. No
sleeps, no thresholds, nothing that turns flaky on a loaded CI runner.
"""

from __future__ import annotations

import asyncio

from headroom.providers.mock import MockScript

from .support.fixtures import anthropic_request, openai_request
from .support.harness import GatewayHarness

REPLY = "The quick brown fox jumps over the lazy dog"


async def test_bytes_reach_the_client_while_the_upstream_is_still_blocked(
    gateway: GatewayHarness,
) -> None:
    gate = asyncio.Event()
    chunks = list(MockScript.anthropic_stream(REPLY).chunks)
    gateway.book.set("gated", MockScript(chunks=chunks, gate=gate, gate_before_chunk=2))

    run = gateway.start(
        "/v1/messages",
        anthropic_request(stream=True),
        script="gated",
    )

    start = await run.next_message()
    assert start["type"] == "http.response.start"
    assert start["status"] == 200

    delivered = await run.next_body()

    # The heart of it: the client is holding response bytes, and the upstream has not
    # been allowed to produce chunk 2 yet — so there is nothing anywhere that could
    # have held the whole answer.
    assert not gate.is_set()
    assert delivered
    whole = b"".join(chunks)
    assert whole.startswith(delivered)
    assert len(delivered) < len(whole)

    gate.set()
    rest = await run.drain()
    await run.finish()
    assert delivered + rest == whole


async def test_the_openai_dialect_streams_the_same_way(gateway: GatewayHarness) -> None:
    gate = asyncio.Event()
    chunks = list(MockScript.openai_stream(REPLY).chunks)
    gateway.book.set("gated", MockScript(chunks=chunks, gate=gate, gate_before_chunk=2))

    run = gateway.start(
        "/v1/chat/completions",
        openai_request(stream=True),
        script="gated",
    )

    assert (await run.next_message())["type"] == "http.response.start"
    delivered = await run.next_body()
    assert not gate.is_set()
    assert delivered

    gate.set()
    rest = await run.drain()
    await run.finish()
    assert delivered + rest == b"".join(chunks)


async def test_first_token_out_is_marked_before_the_stream_completes(
    gateway: GatewayHarness,
) -> None:
    """``ctx.first_token_out_at`` means what it says.

    Phase 3 meters off this and Phase 8's H2 reports gateway overhead from it. If it
    were stamped when the response *finished*, every latency number the project
    publishes would be wrong in the flattering direction — so it is checked here, while
    the stream is provably still open.
    """
    gate = asyncio.Event()
    chunks = list(MockScript.anthropic_stream(REPLY).chunks)
    gateway.book.set("gated", MockScript(chunks=chunks, gate=gate, gate_before_chunk=2))

    run = gateway.start(
        "/v1/messages",
        anthropic_request(stream=True),
        script="gated",
    )
    await run.next_message()
    await run.next_body()

    ctx = run.scope["state"]["ctx"]
    assert ctx.first_upstream_byte_at is not None
    assert ctx.first_token_out_at is not None
    assert ctx.completed_at is None, "the request is still in flight"

    gate.set()
    await run.drain()
    await run.finish()
    assert ctx.completed_at is not None


async def test_a_client_that_hangs_up_releases_the_upstream_connection(
    gateway: GatewayHarness,
) -> None:
    """Disconnect mid-stream: the upstream is closed and the outcome is recorded.

    Phase 4 settles budget reservations in a ``finally`` on exactly this path, so the
    path has to work — and be observable — before there is anything to settle. An
    upstream connection that survives its caller is also how a gateway runs out of
    sockets under the chaos load Phase 6 applies deliberately.
    """
    gate = asyncio.Event()  # never set: the upstream stays open until the client quits
    chunks = list(MockScript.anthropic_stream(REPLY).chunks)
    gateway.book.set("gated", MockScript(chunks=chunks, gate=gate, gate_before_chunk=2))

    run = gateway.start(
        "/v1/messages",
        anthropic_request(stream=True),
        script="gated",
    )
    await run.next_message()
    await run.next_body()

    run.disconnect()
    await run.finish()

    ctx = run.scope["state"]["ctx"]
    assert ctx.outcome == "client_disconnect"
    assert ctx.completed_at is not None
    assert gateway.provider.opened[-1].closed, "the upstream response was never released"
