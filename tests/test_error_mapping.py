"""Honest errors: the upstream's status and body survive; invented ones are specific.

BUILD_PLAN Phase 1: *"upstream errors map to honest downstream errors with the
upstream's status preserved."* Two distinct obligations hide in that sentence, and both
are tested here.

**When the upstream answered, forward its answer.** A 429 with a ``retry-after``, a 400
naming the offending field, a 529 — all of them reach the caller as themselves. This is
not politeness: an SDK picks its retry behaviour from the status, and Phase 6's failover
and Phase 8's H3 are *measured* on the difference between a 429 and a 529. Flattening
them would not merely inconvenience callers, it would destroy the experiment.

**When there was no answer, say precisely what went wrong.** A timeout is a 504, an
unreachable provider is a 502, an unroutable model is a 404 — each with the dialect's
own error body so the caller's SDK raises the right exception type, and each carrying a
stable ``headroom.reason`` that an HTTP status is too coarse to express. Never the
generic 500 the plan forbids.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest

from headroom.providers.mock import MockScript

from .support.fixtures import anthropic_request, openai_request
from .support.harness import GatewayHarness

# --------------------------------------------------------------------------------
# The upstream answered — forward it
# --------------------------------------------------------------------------------


async def test_a_429_is_forwarded_with_its_body_and_retry_after(gateway: GatewayHarness) -> None:
    script = gateway.book.set(
        "throttled",
        MockScript.error(
            429,
            dialect="anthropic",
            message="Number of requests has exceeded your rate limit",
            retry_after="17",
        ),
    )

    response = await gateway.post("/v1/messages", anthropic_request(), script="throttled")

    assert response.status_code == 429
    assert response.content == script.body, "the upstream's error body was rewritten"
    assert response.headers["retry-after"] == "17"
    assert response.headers["x-headroom-error-source"] == "upstream"
    assert json.loads(response.content)["error"]["type"] == "rate_limit_error"


async def test_a_529_is_forwarded_as_529(gateway: GatewayHarness) -> None:
    """529 is Anthropic's "overloaded". It is retryable; 500 is not the same thing."""
    gateway.book.set("overloaded", MockScript.error(529, dialect="anthropic"))

    response = await gateway.post("/v1/messages", anthropic_request(), script="overloaded")

    assert response.status_code == 529
    assert json.loads(response.content)["error"]["type"] == "overloaded_error"


async def test_an_upstream_400_reaches_the_caller_verbatim(gateway: GatewayHarness) -> None:
    """The case where forwarding matters most: only the provider knows what was wrong.

    A gateway that replaced this with "bad request" would delete the one piece of
    information the caller needs to fix their request.
    """
    script = gateway.book.set(
        "bad",
        MockScript.error(
            400,
            dialect="anthropic",
            message="messages.0.content.0.type: Input tag 'txt' is not valid",
        ),
    )

    response = await gateway.post("/v1/messages", anthropic_request(), script="bad")

    assert response.status_code == 400
    assert response.content == script.body
    assert "Input tag 'txt' is not valid" in response.text


async def test_openai_dialect_errors_keep_their_own_shape(gateway: GatewayHarness) -> None:
    script = gateway.book.set(
        "throttled",
        MockScript.error(429, dialect="openai", message="Rate limit reached", retry_after="3"),
    )

    response = await gateway.post("/v1/chat/completions", openai_request(), script="throttled")

    assert response.status_code == 429
    assert response.content == script.body
    assert response.headers["retry-after"] == "3"
    assert json.loads(response.content)["error"]["message"] == "Rate limit reached"


async def test_a_failed_streaming_request_answers_with_a_status_not_a_stream(
    gateway: GatewayHarness,
) -> None:
    """Asking for a stream does not turn a 429 into a 200 with an error inside it.

    Both providers answer a failed streaming request with a plain JSON error and the
    real status, and so does Headroom — a 200 whose body says "actually, 429" is the
    kind of thing that makes retry logic silently stop working.
    """
    gateway.book.set("throttled", MockScript.error(429, retry_after="1"))

    response = await gateway.post(
        "/v1/messages", anthropic_request(stream=True), script="throttled"
    )

    assert response.status_code == 429
    assert not response.headers["content-type"].startswith("text/event-stream")
    assert gateway.last_context().outcome == "upstream_error"


# --------------------------------------------------------------------------------
# There was no answer — invent a specific one
# --------------------------------------------------------------------------------


async def test_a_timeout_is_a_504_that_says_so(gateway: GatewayHarness) -> None:
    gateway.book.set("slow", MockScript.timeout())

    response = await gateway.post("/v1/messages", anthropic_request(), script="slow")

    assert response.status_code == 504
    payload = json.loads(response.content)
    assert payload["type"] == "error"
    assert payload["headroom"]["reason"] == "upstream_timeout"
    assert payload["headroom"]["request_id"] == gateway.last_context().request_id
    assert response.headers["x-headroom-error-source"] == "upstream"
    assert gateway.last_context().outcome == "upstream_timeout"


async def test_an_unreachable_provider_is_a_502(gateway: GatewayHarness) -> None:
    gateway.book.set("down", MockScript.connect_error())

    response = await gateway.post("/v1/messages", anthropic_request(), script="down")

    assert response.status_code == 502
    assert json.loads(response.content)["headroom"]["reason"] == "upstream_unavailable"


@pytest.mark.parametrize(
    ("path", "request_body"),
    [("/v1/messages", anthropic_request), ("/v1/chat/completions", openai_request)],
)
async def test_a_timeout_is_spoken_in_the_callers_dialect(
    gateway: GatewayHarness, path: str, request_body: Callable[..., dict[str, Any]]
) -> None:
    """The status is the same; the body shape is not — and the SDK cares.

    ``anthropic-python`` looks for ``{"type": "error", "error": {...}}``;
    ``openai-python`` looks for ``{"error": {...}}``. An error the client's SDK cannot
    parse is barely better than no error at all.
    """
    gateway.book.set("slow", MockScript.timeout())

    response = await gateway.post(path, request_body(), script="slow")

    assert response.status_code == 504
    payload = json.loads(response.content)
    assert payload["error"]["message"]
    assert payload["headroom"]["reason"] == "upstream_timeout"
    if path == "/v1/messages":
        assert payload["type"] == "error"
    else:
        assert payload["error"]["code"] == "upstream_timeout"


async def test_an_unroutable_model_is_a_404_naming_the_model(gateway: GatewayHarness) -> None:
    """404 is what both providers return for an unknown model, so an SDK's
    ``NotFoundError`` means the same thing with or without Headroom in the path."""
    response = await gateway.post("/v1/messages", anthropic_request(model="gpt-4o"))

    assert response.status_code == 404
    payload = json.loads(response.content)
    assert payload["headroom"]["reason"] == "model_not_routed"
    assert "gpt-4o" in payload["error"]["message"]
    assert response.headers["x-headroom-error-source"] == "gateway"


async def test_a_body_that_is_not_json_is_a_400(gateway: GatewayHarness) -> None:
    response = await gateway.post("/v1/messages", b"{not json at all")

    assert response.status_code == 400
    assert json.loads(response.content)["headroom"]["reason"] == "invalid_request_body"


async def test_a_body_without_a_model_is_a_400(gateway: GatewayHarness) -> None:
    response = await gateway.post("/v1/messages", {"max_tokens": 8, "messages": []})

    assert response.status_code == 400
    assert "model" in json.loads(response.content)["error"]["message"]


async def test_a_json_array_body_is_a_400_not_a_crash(gateway: GatewayHarness) -> None:
    response = await gateway.post("/v1/messages", b'["not", "an", "object"]')

    assert response.status_code == 400
    assert json.loads(response.content)["headroom"]["reason"] == "invalid_request_body"


async def test_error_responses_still_carry_a_request_id_header(gateway: GatewayHarness) -> None:
    """The id is how a caller's screenshot becomes a ledger row. It cannot be optional
    on exactly the responses people screenshot."""
    gateway.book.set("slow", MockScript.timeout())

    response = await gateway.post("/v1/messages", anthropic_request(), script="slow")

    assert response.headers["x-headroom-request-id"].startswith("hr_")
    assert response.headers["x-headroom-request-id"] == gateway.last_context().request_id
