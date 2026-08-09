"""The *real* provider clients, under test, keylessly.

BUILD_PLAN §0.2 invariant 4 makes CI keyless, which is easy to satisfy by simply never
testing the code that talks to real providers — and then discovering at the first live
smoke that the auth header is misspelled or the URL has a doubled path segment. So the
Anthropic and OpenAI-compatible clients run here against ``httpx.MockTransport``: a
real ``AsyncClient``, real request building, real streaming, real error mapping, an
in-process responder instead of a network. Everything up to the socket is exercised;
nothing costs a cent.

The live smoke the operator runs by hand then has one job — confirming that the far end
is what we think it is — instead of being the first test of our own code.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from headroom.core.context import RequestContext
from headroom.core.errors import (
    ConfigurationError,
    ProviderTimeout,
    ProviderUnavailable,
    UpstreamStreamCut,
)
from headroom.providers.anthropic import AnthropicProvider
from headroom.providers.base import UpstreamRequest
from headroom.providers.http import normalize_base_url
from headroom.providers.openai_compat import OpenAICompatProvider

BODY = b'{"model":"claude-haiku-4-5","max_tokens":8,"messages":[{"role":"user","content":"hi"}]}'


def anthropic_upstream_request(**overrides: object) -> UpstreamRequest:
    defaults: dict[str, object] = {
        "dialect": "anthropic",
        "path": "/v1/messages",
        "model": "claude-haiku-4-5",
        "body": BODY,
        "stream": False,
        "headers": {},
    }
    defaults.update(overrides)
    return UpstreamRequest(**defaults)  # type: ignore[arg-type]


def recording_transport(
    seen: list[httpx.Request], response: httpx.Response | None = None
) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return response if response is not None else httpx.Response(200, json={"ok": True})

    return httpx.MockTransport(handle)


# --------------------------------------------------------------------------------
# Anthropic
# --------------------------------------------------------------------------------


async def test_anthropic_sends_the_key_and_a_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-a-real-key")
    seen: list[httpx.Request] = []
    provider = AnthropicProvider(transport=recording_transport(seen))

    response = await provider.open(anthropic_upstream_request(), RequestContext())
    await response.aclose()
    await provider.aclose()

    request = seen[0]
    assert str(request.url) == "https://api.anthropic.com/v1/messages"
    assert request.headers["x-api-key"] == "sk-ant-test-not-a-real-key"
    assert request.headers["anthropic-version"] == "2023-06-01"
    assert request.content == BODY, "the body was rewritten on its way upstream"


async def test_anthropic_respects_a_version_the_caller_pinned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller pinning ``anthropic-version`` did so deliberately; the default is a
    default, not an override."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    seen: list[httpx.Request] = []
    provider = AnthropicProvider(transport=recording_transport(seen))

    response = await provider.open(
        anthropic_upstream_request(headers={"anthropic-version": "2023-01-01"}),
        RequestContext(),
    )
    await response.aclose()
    await provider.aclose()

    assert seen[0].headers["anthropic-version"] == "2023-01-01"


async def test_anthropic_without_a_key_names_the_variable_to_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The keyless-boot rule's other half: the failure lands on the request that needed
    the key, and it says which environment variable is missing. A 500 that names the
    knob is not the generic 500 the plan forbids."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    provider = AnthropicProvider(transport=recording_transport([]))

    with pytest.raises(ConfigurationError, match="ANTHROPIC_API_KEY"):
        await provider.open(anthropic_upstream_request(), RequestContext())
    await provider.aclose()


async def test_anthropic_streams_chunks_as_they_arrive() -> None:
    """The streamed path through a real ``AsyncClient``: headers first, body in pieces."""
    chunks = [b"event: message_start\ndata: {}\n\n", b"event: message_stop\ndata: {}\n\n"]

    async def stream() -> AsyncIterator[bytes]:
        for chunk in chunks:
            yield chunk

    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=stream()
        )
    )
    provider = AnthropicProvider(api_key_env="UNUSED_KEY_ENV", transport=transport)
    provider.auth_headers = lambda: {}  # type: ignore[method-assign]
    ctx = RequestContext()

    response = await provider.open(anthropic_upstream_request(stream=True), ctx)
    received = [chunk async for chunk in response.aiter_bytes()]
    await response.aclose()
    await provider.aclose()

    assert b"".join(received) == b"".join(chunks)
    assert ctx.first_upstream_byte_at is not None


async def test_an_upstream_error_status_is_returned_not_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 429 is an *answer*. Raising here would throw away the body and the
    ``retry-after`` the caller needs, and Phase 6 needs the status to decide on a
    retry."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    error_body = b'{"type":"error","error":{"type":"rate_limit_error","message":"slow down"}}'
    transport = httpx.MockTransport(
        lambda _: httpx.Response(429, headers={"retry-after": "9"}, content=error_body)
    )
    provider = AnthropicProvider(transport=transport)

    response = await provider.open(anthropic_upstream_request(), RequestContext())
    body = await response.aread()
    await response.aclose()
    await provider.aclose()

    assert response.status_code == 429
    assert response.headers["retry-after"] == "9"
    assert body == error_body


# --------------------------------------------------------------------------------
# Transport failures
# --------------------------------------------------------------------------------


def raising_transport(exc: Exception) -> httpx.MockTransport:
    def handle(_: httpx.Request) -> httpx.Response:
        raise exc

    return httpx.MockTransport(handle)


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (httpx.ConnectTimeout("connect timed out"), ProviderTimeout),
        (httpx.ReadTimeout("read timed out"), ProviderTimeout),
        (httpx.ConnectError("connection refused"), ProviderUnavailable),
        (httpx.RemoteProtocolError("server disconnected"), ProviderUnavailable),
    ],
)
async def test_pre_response_failures_map_to_the_right_gateway_error(
    monkeypatch: pytest.MonkeyPatch, failure: Exception, expected: type[Exception]
) -> None:
    """Timeout means 504, unreachable means 502 — the distinction Phase 6 acts on."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    provider = AnthropicProvider(transport=raising_transport(failure))

    with pytest.raises(expected):
        await provider.open(anthropic_upstream_request(), RequestContext())
    await provider.aclose()


async def test_a_failure_during_the_body_is_a_stream_cut(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The line the whole resilience design rests on.

    The same underlying network fault is a retryable ``ProviderUnavailable`` before the
    response and an unrecoverable ``UpstreamStreamCut`` after it — because once bytes
    are on the wire, Phase 6 must not splice a second provider's answer onto the first
    one's fragment.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    async def dying_stream() -> AsyncIterator[bytes]:
        yield b"event: message_start\ndata: {}\n\n"
        raise httpx.ReadError("connection reset by peer")

    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=dying_stream()
        )
    )
    provider = AnthropicProvider(transport=transport)

    response = await provider.open(anthropic_upstream_request(stream=True), RequestContext())
    with pytest.raises(UpstreamStreamCut):
        async for _ in response.aiter_bytes():
            pass
    await response.aclose()
    await provider.aclose()


# --------------------------------------------------------------------------------
# OpenAI-compatible (the vLLM path)
# --------------------------------------------------------------------------------


async def test_openai_compat_sends_no_auth_header_when_none_is_configured() -> None:
    """A local vLLM has no auth, and demanding one would break the deployment this
    provider exists for (BUILD_PLAN L5)."""
    seen: list[httpx.Request] = []
    provider = OpenAICompatProvider(
        "vllm_a", base_url="http://gpu-a:8000", transport=recording_transport(seen)
    )

    response = await provider.open(
        anthropic_upstream_request(dialect="openai", path="/v1/chat/completions"),
        RequestContext(),
    )
    await response.aclose()
    await provider.aclose()

    assert "authorization" not in seen[0].headers
    assert str(seen[0].url) == "http://gpu-a:8000/v1/chat/completions"


async def test_openai_compat_sends_a_bearer_token_when_one_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VLLM_API_KEY", "vllm-secret")
    seen: list[httpx.Request] = []
    provider = OpenAICompatProvider(
        "vllm_a",
        base_url="http://gpu-a:8000",
        api_key_env="VLLM_API_KEY",
        transport=recording_transport(seen),
    )

    response = await provider.open(
        anthropic_upstream_request(dialect="openai", path="/v1/chat/completions"),
        RequestContext(),
    )
    await response.aclose()
    await provider.aclose()

    assert seen[0].headers["authorization"] == "Bearer vllm-secret"


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("http://gpu-a:8000", "http://gpu-a:8000"),
        ("http://gpu-a:8000/", "http://gpu-a:8000"),
        ("http://gpu-a:8000/v1", "http://gpu-a:8000"),
        ("http://gpu-a:8000/v1/", "http://gpu-a:8000"),
        ("https://api.anthropic.com", "https://api.anthropic.com"),
        ("http://host/prefix/v1", "http://host/prefix"),
    ],
)
def test_both_spellings_of_a_vllm_base_url_work(configured: str, expected: str) -> None:
    """``VLLM_BASE_URL`` is written both ways in the wild — the OpenAI SDK wants the
    ``/v1`` suffix, so half the world has it. Joining the suffixed form naively yields
    ``/v1/v1/chat/completions`` and a 404 that looks like a problem at the far end.
    """
    assert normalize_base_url(configured) == expected


async def test_a_normalized_base_url_produces_the_right_upstream_path() -> None:
    """The foot-gun above, checked end to end rather than only on the helper."""
    seen: list[httpx.Request] = []
    provider = OpenAICompatProvider(
        "vllm_a", base_url="http://gpu-a:8000/v1", transport=recording_transport(seen)
    )

    response = await provider.open(
        anthropic_upstream_request(dialect="openai", path="/v1/chat/completions"),
        RequestContext(),
    )
    await response.aclose()
    await provider.aclose()

    assert str(seen[0].url) == "http://gpu-a:8000/v1/chat/completions"
