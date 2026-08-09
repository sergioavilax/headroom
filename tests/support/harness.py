"""The gateway harness: a real app, a mock-only gateway, and handles to interrogate it.

Lives in ``support`` rather than ``conftest`` so tests can import the type and annotate
their fixture parameter — ``mypy --strict`` covers the test suite too, and a test file
full of untyped ``gateway`` parameters is a test file mypy cannot check.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from headroom.core.config import GatewayConfig, ProviderSpec, RouteSpec
from headroom.core.context import RequestContext
from headroom.providers.base import UpstreamRequest
from headroom.providers.mock import MockProvider, MockScriptBook

from .asgi import ContextRecorder

__all__ = ["GatewayHarness", "mock_only_config"]


def mock_only_config() -> GatewayConfig:
    """A gateway configuration with one provider and nothing that can spend money.

    Routes are ``mock-`` prefixed rather than catch-all, so "this model is not routed"
    stays a case a test can deliberately ask for.
    """
    return GatewayConfig(
        providers={"mock": ProviderSpec(kind="mock")},
        routes={
            "anthropic": [RouteSpec(prefix="mock-", provider="mock")],
            "openai": [RouteSpec(prefix="mock-", provider="mock")],
        },
    )


@dataclass
class GatewayHarness:
    """A running gateway plus the handles a test needs to interrogate it."""

    app: Any
    book: MockScriptBook
    provider: MockProvider
    client: httpx.AsyncClient
    recorder: ContextRecorder

    async def post(
        self,
        path: str,
        body: Any,
        *,
        script: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        """POST to the gateway, optionally naming a mock script.

        ``body`` may be a dict (encoded as JSON) or raw bytes. Tests that assert
        byte-level fidelity pass bytes, so the exact payload is the test's to control
        rather than the HTTP client's.
        """
        request_headers = {"content-type": "application/json"}
        if script is not None:
            request_headers["x-headroom-mock-script"] = script
        request_headers.update(headers or {})
        if isinstance(body, bytes):
            return await self.client.post(path, content=body, headers=request_headers)
        return await self.client.post(path, json=body, headers=request_headers)

    def last_context(self) -> RequestContext:
        """The ``RequestContext`` of the most recent request."""
        assert self.recorder.contexts, "no request has been made yet"
        ctx: RequestContext = self.recorder.contexts[-1]
        return ctx

    def last_upstream_request(self) -> UpstreamRequest:
        """What the provider was actually handed — the request half of fidelity."""
        assert self.provider.received, "the provider was never called"
        return self.provider.received[-1]
