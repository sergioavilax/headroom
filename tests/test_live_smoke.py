"""The two live smokes — real providers, real money, opt-in only.

Excluded from every default collection by the ``live`` marker (BUILD_PLAN §0.2
invariant 4), so CI never runs them and never needs a credential. The operator runs
them by hand, once, after CI is green:

    uv run pytest -m live -v                    # both, if both are configured
    uv run pytest -m live -k anthropic -v       # just the paid one

**Setup is the compose stack and one env var.** ``make up`` (the migrations are applied
by the test itself, idempotently) plus ``ANTHROPIC_API_KEY`` or ``VLLM_BASE_URL``.
Nothing else: since Phase 2 every ``/v1/*`` request needs a virtual key, and each test
provisions its own — a ``live-smoke`` tenant, reused across runs, and a key minted fresh
for this one and revoked on the way out (``tests/support/live.py``). These tests sent no
key at all between Phase 2's merge and 2026-08-09, failed 401 ``missing_api_key`` on the
first live run after it, and nothing caught it because nothing keyless runs them; the
keyless half that now guards this is ``tests/test_live_smoke_wiring.py``.

**Spend.** The Anthropic smoke is one streamed request to ``claude-haiku-4-5``
($1/MTok in, $5/MTok out) with ``max_tokens=16``: roughly twenty input tokens and
sixteen output, about **$0.0001** — three orders of magnitude under the $0.05 ceiling
this phase was given, and charged against the $3 P1-P7 bucket in §0.6. The vLLM smoke
runs on the operator's own GPUs and costs nothing — which is why it can afford the
larger ``max_tokens`` a reasoning model needs, and the paid one cannot.

**Each smoke ends at the ledger, not at the stream.** The row is asserted to exist and
to be attributed to the smoke tenant, and the request id, tenant id, tokens and cost are
printed — because the reason to spend real money here is to compare Headroom's
accounting against the provider's own, and that comparison starts with an id the
operator can paste into ``/admin/usage``. Read it back *before* the next ``make test``:
the Postgres half of the tenant-store contract suite truncates the control plane, and
``usage_ledger`` references it, so the row goes with it.

Each test skips loudly when its configuration is absent, rather than failing: not
having a key is a normal state for this repo, and a red suite that means "you didn't
opt in" trains people to ignore red suites.
"""

from __future__ import annotations

import os

import httpx
import pytest

from .support.live import LiveGateway, live_gateway, report
from .support.streams import anthropic_text, event_pairs, openai_finish_reasons, openai_text

pytestmark = pytest.mark.live


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY unset — set it in the gitignored .env to run the paid smoke",
)
async def test_a_real_anthropic_call_streams_through_the_gateway(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """One streamed Messages request to the cheapest current model. ~$0.0001."""
    async with live_gateway("anthropic-live-smoke") as live:
        response = await live.post(
            "/v1/messages",
            {
                "model": "claude-haiku-4-5",
                "max_tokens": 16,
                "stream": True,
                "messages": [{"role": "user", "content": "Reply with exactly: headroom ok"}],
            },
        )

        assert response.status_code == 200, response.text
        names = [name for name, _ in event_pairs(response.content)]
        assert "error" not in names, response.text
        assert names[-1] == "message_stop", "the stream did not complete"
        assert anthropic_text(response.content).strip(), "no text came back"

        await assert_billed_to_the_smoke_tenant(capsys, live, response)


@pytest.mark.skipif(
    not os.environ.get("VLLM_BASE_URL"),
    reason="VLLM_BASE_URL unset — point it at a local vLLM instance to run this smoke",
)
async def test_a_real_vllm_call_streams_through_the_gateway(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The OpenAI-dialect path against the operator's own hardware. $0.00.

    The model id is discovered from the instance rather than hard-coded: whichever
    checkpoint is loaded is the one to ask for, and a wrong guess would surface as a
    404 that looks like a gateway bug.

    ``max_tokens`` is 256 where the paid smoke uses 16, because the checkpoint on the
    other end may be a **reasoning** model — the operator's is (Qwen3.6 served with
    ``--reasoning-parser qwen3``). Such a model spends its budget on reasoning deltas
    *before* it emits its first content delta, so a 16-token ceiling ends the stream
    with ``finish_reason: "length"`` and no content whatsoever: well-formed, complete
    as SSE, and empty. Buying the headroom costs nothing here — these are the
    operator's own GPUs.

    The assertion follows from that. This smoke exists to prove the gateway carries a
    real provider's stream end to end, so it asserts the outcome — the completion
    ended on its own terms *and* text reached the client — rather than reading an
    exhausted token budget as a missing answer.
    """
    base_url = os.environ["VLLM_BASE_URL"].rstrip("/").removesuffix("/v1")
    model = os.environ.get("VLLM_MODEL")
    if not model:
        async with httpx.AsyncClient(timeout=10.0) as probe:
            listing = await probe.get(f"{base_url}/v1/models")
        listing.raise_for_status()
        model = listing.json()["data"][0]["id"]

    async with live_gateway("vllm-live-smoke") as live:
        response = await live.post(
            "/v1/chat/completions",
            {
                "model": model,
                "max_tokens": 256,
                "stream": True,
                "stream_options": {"include_usage": True},
                "messages": [{"role": "user", "content": "Reply with exactly: headroom ok"}],
            },
        )

        assert response.status_code == 200, response.text
        data = [payload for _, payload in event_pairs(response.content)]
        assert "[DONE]" in data, "the stream did not reach its terminal marker"
        assert not any('"error"' in payload for payload in data), response.text

        reasons = openai_finish_reasons(response.content)
        assert reasons == ["stop"], (
            f"the completion ended on {reasons or 'no finish_reason at all'}, not 'stop' — "
            "'length' means max_tokens ran out before the model was done (raise it if this "
            "checkpoint reasons at length); anything else is the model or the gateway"
        )
        assert openai_text(response.content).strip(), "no content text came back"

        await assert_billed_to_the_smoke_tenant(capsys, live, response)


async def assert_billed_to_the_smoke_tenant(
    capsys: pytest.CaptureFixture[str],
    live: LiveGateway,
    response: httpx.Response,
) -> None:
    """The half of a live smoke that outlives the process: the row in the ledger.

    Reported before it is asserted, so a run that spent money and then failed here
    still hands the operator the request id to go and look at.
    """
    request_id = live.request_id(response)
    row = await live.ledger_row(response)
    report(capsys, live=live, request_id=request_id, row=row)

    assert row is not None, (
        f"the request succeeded but no ledger row landed for {request_id} — "
        "the gateway billed nobody for a request a provider was paid for"
    )
    assert row.tenant_id == live.identity.tenant.id, (
        f"row {request_id} is attributed to tenant {row.tenant_id}, "
        f"not to the smoke tenant {live.identity.tenant.id}"
    )
    assert row.key_id == live.identity.key.id
