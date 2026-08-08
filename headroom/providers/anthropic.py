"""The Anthropic provider: ``api.anthropic.com``, or anything that speaks its dialect.

Thin by design — everything interesting is in :mod:`headroom.providers.http`. What is
specific to Anthropic is exactly two headers:

* ``x-api-key`` — the credential, read from the environment at request time and never
  from the repo, a compose file, or a task definition (BUILD_PLAN §0.2 invariant 3).
  The gateway *boots* without it: a deployment routing only mock and vLLM traffic is a
  legitimate deployment, and the error for a missing key belongs on the request that
  needed it, naming the variable to set.
* ``anthropic-version`` — required by the Messages API. Forwarded when the caller sent
  one (they may be pinning a version deliberately) and defaulted when they did not.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import replace

import httpx

from headroom.core.context import RequestContext
from headroom.core.errors import ConfigurationError
from headroom.dialects.anthropic import DEFAULT_API_VERSION
from headroom.providers.base import Provider, UpstreamRequest, UpstreamResponse
from headroom.providers.http import HttpProvider
from headroom.providers.registry import register_kind

__all__ = ["AnthropicProvider"]

DEFAULT_BASE_URL = "https://api.anthropic.com"


class AnthropicProvider(HttpProvider):
    kind = "anthropic"

    def __init__(
        self,
        name: str = "anthropic",
        *,
        base_url: str = DEFAULT_BASE_URL,
        api_key_env: str = "ANTHROPIC_API_KEY",
        api_version: str = DEFAULT_API_VERSION,
        read_timeout_s: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(name, base_url, read_timeout_s=read_timeout_s, transport=transport)
        self.api_key_env = api_key_env
        self.api_version = api_version

    def auth_headers(self) -> Mapping[str, str]:
        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            raise ConfigurationError(
                f"provider {self.name!r} needs an API key: set {self.api_key_env} "
                f"in the environment (locally, in the gitignored .env)"
            )
        return {"x-api-key": api_key}

    async def open(self, request: UpstreamRequest, ctx: RequestContext) -> UpstreamResponse:
        if "anthropic-version" not in request.headers:
            request = replace(
                request, headers={**request.headers, "anthropic-version": self.api_version}
            )
        return await super().open(request, ctx)


def _build(
    name: str,
    *,
    base_url: str | None = None,
    api_key_env: str | None = None,
    api_version: str | None = None,
    read_timeout_s: float | None = None,
    **_ignored: object,
) -> Provider:
    return AnthropicProvider(
        name,
        base_url=base_url or DEFAULT_BASE_URL,
        api_key_env=api_key_env or "ANTHROPIC_API_KEY",
        api_version=api_version or DEFAULT_API_VERSION,
        read_timeout_s=60.0 if read_timeout_s is None else read_timeout_s,
    )


register_kind("anthropic", _build)
