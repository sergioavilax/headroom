"""The two live smokes — real providers, real money, opt-in only.

Excluded from every default collection by the ``live`` marker (BUILD_PLAN §0.2
invariant 4), so CI never runs them and never needs a credential. The operator runs
them by hand, once, after CI is green:

    uv run pytest -m live -v                    # both, if both are configured
    uv run pytest -m live -k anthropic -v       # just the paid one

**Spend.** The Anthropic smoke is one streamed request to ``claude-haiku-4-5``
($1/MTok in, $5/MTok out) with ``max_tokens=16``: roughly twenty input tokens and
sixteen output, about **$0.0001** — three orders of magnitude under the $0.05 ceiling
this phase was given, and charged against the $3 P1-P7 bucket in §0.6. The vLLM smoke
runs on the operator's own GPUs and costs nothing.

Each test skips loudly when its configuration is absent, rather than failing: not
having a key is a normal state for this repo, and a red suite that means "you didn't
opt in" trains people to ignore red suites.
"""

from __future__ import annotations

import os

import httpx
import pytest

from headroom.api.gateway import build_gateway
from headroom.api.main import app as headroom_app

from .support.streams import anthropic_text, event_pairs, openai_text

pytestmark = pytest.mark.live


async def gateway_client() -> tuple[httpx.AsyncClient, object]:
    """The real gateway — the committed routing table, real provider clients."""
    instance = build_gateway()
    headroom_app.state.gateway = instance
    transport = httpx.ASGITransport(app=headroom_app)
    return httpx.AsyncClient(transport=transport, base_url="http://gateway", timeout=60.0), instance


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY unset — set it in the gitignored .env to run the paid smoke",
)
async def test_a_real_anthropic_call_streams_through_the_gateway() -> None:
    """One streamed Messages request to the cheapest current model. ~$0.0001."""
    client, instance = await gateway_client()
    try:
        response = await client.post(
            "/v1/messages",
            json={
                "model": "claude-haiku-4-5",
                "max_tokens": 16,
                "stream": True,
                "messages": [{"role": "user", "content": "Reply with exactly: headroom ok"}],
            },
        )
    finally:
        await client.aclose()
        await instance.aclose()  # type: ignore[attr-defined]

    assert response.status_code == 200, response.text
    names = [name for name, _ in event_pairs(response.content)]
    assert "error" not in names, response.text
    assert names[-1] == "message_stop", "the stream did not complete"
    assert anthropic_text(response.content).strip(), "no text came back"


@pytest.mark.skipif(
    not os.environ.get("VLLM_BASE_URL"),
    reason="VLLM_BASE_URL unset — point it at a local vLLM instance to run this smoke",
)
async def test_a_real_vllm_call_streams_through_the_gateway() -> None:
    """The OpenAI-dialect path against the operator's own hardware. $0.00.

    The model id is discovered from the instance rather than hard-coded: whichever
    checkpoint is loaded is the one to ask for, and a wrong guess would surface as a
    404 that looks like a gateway bug.
    """
    base_url = os.environ["VLLM_BASE_URL"].rstrip("/").removesuffix("/v1")
    model = os.environ.get("VLLM_MODEL")
    if not model:
        async with httpx.AsyncClient(timeout=10.0) as probe:
            listing = await probe.get(f"{base_url}/v1/models")
        listing.raise_for_status()
        model = listing.json()["data"][0]["id"]

    client, instance = await gateway_client()
    try:
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": model,
                "max_tokens": 16,
                "stream": True,
                "stream_options": {"include_usage": True},
                "messages": [{"role": "user", "content": "Reply with exactly: headroom ok"}],
            },
        )
    finally:
        await client.aclose()
        await instance.aclose()  # type: ignore[attr-defined]

    assert response.status_code == 200, response.text
    data = [payload for _, payload in event_pairs(response.content)]
    assert "[DONE]" in data, "the stream did not reach its terminal marker"
    assert not any('"error"' in payload for payload in data), response.text
    assert openai_text(response.content).strip(), "no text came back"
