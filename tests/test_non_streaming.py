"""Non-streaming round trips: the body out is the body in, both ways, both dialects.

Less dramatic than the streaming path and just as load-bearing — most of Backline's
suite runs non-streamed, and Phase 8's H2 points that suite at this code. What is being
checked is fidelity in *both* directions: the client's bytes reach the provider
unchanged, and the provider's bytes reach the client unchanged.
"""

from __future__ import annotations

import json

from headroom.providers.mock import MockScript
from headroom.providers.mock_scripts import anthropic_message_body, openai_completion_body

from .support.fixtures import anthropic_request, openai_request
from .support.harness import GatewayHarness

REPLY = "Streams were up 12% quarter over quarter."


async def test_anthropic_round_trip(gateway: GatewayHarness) -> None:
    gateway.book.set("ok", MockScript.anthropic_message(REPLY))

    response = await gateway.post("/v1/messages", anthropic_request(), script="ok")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.content == anthropic_message_body(REPLY)
    assert response.json()["content"][0]["text"] == REPLY


async def test_openai_round_trip(gateway: GatewayHarness) -> None:
    gateway.book.set("ok", MockScript.openai_completion(REPLY))

    response = await gateway.post("/v1/chat/completions", openai_request(), script="ok")

    assert response.status_code == 200
    assert response.content == openai_completion_body(REPLY)
    assert response.json()["choices"][0]["message"]["content"] == REPLY


async def test_the_request_body_reaches_the_provider_byte_for_byte(gateway: GatewayHarness) -> None:
    """Bytes in, bytes out — the property assumption A5 ultimately rests on.

    The payload below is chosen to break anything that re-serializes: keys are not in
    alphabetical order, there is a non-ASCII character, an escaped quote, a float that
    round-trips badly through some encoders, and significant whitespace inside a string.
    """
    raw = (
        b'{"model":"mock-model-1","max_tokens":64,"temperature":0.30000000000000004,'
        b'"metadata":{"z_last":1,"a_first":2},'
        b'"messages":[{"role":"user","content":"Bj\\u00f6rk said \\"hi\\"  \\n twice"}]}'
    )
    gateway.book.set("ok", MockScript.anthropic_message(REPLY))

    response = await gateway.post("/v1/messages", raw, script="ok")

    assert response.status_code == 200
    assert gateway.last_upstream_request().body == raw


async def test_content_length_is_recomputed_not_forwarded(gateway: GatewayHarness) -> None:
    """The upstream's framing headers describe the upstream's connection, not ours.

    httpx decodes the response body, so forwarding the upstream's ``content-length`` or
    ``content-encoding`` would describe bytes that no longer exist. Both are dropped
    and Starlette recomputes the length from what is actually sent.
    """
    body = anthropic_message_body(REPLY)
    gateway.book.set(
        "ok",
        MockScript(
            body=body,
            headers={
                "content-length": "999999",
                "content-encoding": "gzip",
                "anthropic-ratelimit-requests-remaining": "42",
            },
        ),
    )

    response = await gateway.post("/v1/messages", anthropic_request(), script="ok")

    assert response.content == body
    assert response.headers["content-length"] == str(len(body))
    assert "content-encoding" not in response.headers
    # Useful upstream signal is *not* dropped: a caller that wants to behave well needs
    # the rate-limit headers, and a gateway that swallows them makes its callers worse.
    assert response.headers["anthropic-ratelimit-requests-remaining"] == "42"


async def test_a_default_script_answers_without_any_test_configuration(
    gateway: GatewayHarness,
) -> None:
    """No script named: the mock still answers, deterministically.

    This is the path a bare ``curl`` against a compose-up gateway takes — the keyless
    end-to-end demo — so it has a test rather than only a docstring.
    """
    response = await gateway.post("/v1/messages", anthropic_request())

    assert response.status_code == 200
    payload = json.loads(response.content)
    assert payload["content"][0]["text"] == "mock reply from mock-model-1"


async def test_every_response_carries_a_request_id(gateway: GatewayHarness) -> None:
    gateway.book.set("ok", MockScript.anthropic_message(REPLY))

    response = await gateway.post("/v1/messages", anthropic_request(), script="ok")

    assert response.headers["x-headroom-request-id"] == gateway.last_context().request_id
