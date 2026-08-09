"""The OpenAI-compatible provider — the vLLM path (BUILD_PLAN L5).

One meaningful difference from the Anthropic provider: **a missing API key is not an
error here.** The operator's two vLLM instances serve on the home network with no
auth, which is the normal way a self-hosted OpenAI-compatible server runs, so
demanding a credential would break the exact deployment this provider exists for.
When a key *is* configured it is sent as a bearer token, which covers hosted
OpenAI-compatible endpoints (Together, Fireworks, an authenticated vLLM) without a
second provider kind.

Base URLs come from the environment because they are per-machine facts, not repo
facts: ``VLLM_BASE_URL`` names one of the operator's 4090 boxes locally and something
else entirely from a container. Phase 6 registers this kind twice — one instance per
GPU — and the failover chain between them is the zero-cost chaos demo.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

import httpx

from headroom.providers.base import Provider
from headroom.providers.http import HttpProvider
from headroom.providers.registry import register_kind

__all__ = ["OpenAICompatProvider"]

DEFAULT_BASE_URL = "http://localhost:8000"


class OpenAICompatProvider(HttpProvider):
    kind = "openai_compat"

    def __init__(
        self,
        name: str = "openai_compat",
        *,
        base_url: str = DEFAULT_BASE_URL,
        api_key_env: str | None = None,
        read_timeout_s: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(name, base_url, read_timeout_s=read_timeout_s, transport=transport)
        self.api_key_env = api_key_env

    def auth_headers(self) -> Mapping[str, str]:
        if self.api_key_env is None:
            return {}
        api_key = os.environ.get(self.api_key_env)
        # Configured-but-unset is treated as "no auth" rather than as a fault: a local
        # vLLM ignores the header either way, and failing the request would make the
        # keyless path depend on a variable nobody needs to set.
        return {"Authorization": f"Bearer {api_key}"} if api_key else {}


def _build(
    name: str,
    *,
    base_url: str | None = None,
    base_url_env: str | None = None,
    api_key_env: str | None = None,
    read_timeout_s: float | None = None,
    **_ignored: object,
) -> Provider:
    resolved = base_url
    if base_url_env:
        resolved = os.environ.get(base_url_env) or resolved
    return OpenAICompatProvider(
        name,
        base_url=resolved or DEFAULT_BASE_URL,
        api_key_env=api_key_env,
        read_timeout_s=60.0 if read_timeout_s is None else read_timeout_s,
    )


register_kind("openai_compat", _build, dialects=frozenset({"openai"}))
