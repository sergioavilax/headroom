"""Shared machinery for the two real providers: httpx, streamed, never buffered.

The whole streaming story is one call: ``client.send(request, stream=True)`` returns
once the response *headers* arrive, and the body is pulled chunk by chunk from there.
Anything that reads the body to completion first — ``client.post``, a convenience
wrapper, an over-eager log line — turns a streaming gateway into a batching one and
throws away the product (first-token latency), silently, with every test still green.
That is the mistake this module exists to make impossible: no code path here touches
the body.

Failures are mapped to the taxonomy in ``headroom.core.errors`` **at the boundary**,
and the mapping depends on where they happen:

* before the response — ``ProviderTimeout`` / ``ProviderUnavailable``; the caller can
  still get an honest status, and Phase 6 can still fail over;
* during the body — ``UpstreamStreamCut``; the status line is spent, so the caller is
  told inside the stream instead.

httpx's ``read`` timeout is per read operation rather than per request, so it doubles
as a stall detector: an upstream that accepts the connection and then goes quiet
mid-answer trips it and surfaces as a cut, which is exactly right.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping

import httpx

from headroom.core.context import RequestContext
from headroom.core.errors import ProviderTimeout, ProviderUnavailable, UpstreamStreamCut
from headroom.providers.base import Provider, UpstreamRequest, UpstreamResponse

__all__ = ["HttpProvider", "normalize_base_url"]

DEFAULT_CONNECT_TIMEOUT_S = 10.0
DEFAULT_READ_TIMEOUT_S = 60.0


def normalize_base_url(base_url: str) -> str:
    """Trim a base URL to the server root the dialect paths hang off.

    Both dialect paths already start with ``/v1``, while half the world writes a vLLM
    endpoint as ``http://box:8000/v1`` (the OpenAI SDK wants it that way) and the other
    half as ``http://box:8000``. Naively joining the first form yields
    ``/v1/v1/chat/completions`` and a 404 that looks like a routing bug at the far end.
    Accepting both spellings costs one line and removes a foot-gun from the live smoke
    the operator runs by hand (docs/DECISIONS.md H-011).
    """
    trimmed = base_url.rstrip("/")
    if trimmed.endswith("/v1"):
        trimmed = trimmed[: -len("/v1")]
    return trimmed


class HttpProvider(Provider):
    """A provider that speaks to an HTTP endpoint. Subclasses supply auth."""

    def __init__(
        self,
        name: str,
        base_url: str,
        *,
        read_timeout_s: float = DEFAULT_READ_TIMEOUT_S,
        connect_timeout_s: float = DEFAULT_CONNECT_TIMEOUT_S,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.name = name
        self.base_url = normalize_base_url(base_url)
        self._timeout = httpx.Timeout(
            read_timeout_s, connect=connect_timeout_s, write=connect_timeout_s
        )
        # Injecting a transport is how CI exercises this class for real without a
        # network or a key (invariant 4): `httpx.MockTransport` answers in-process, so
        # the request-building, auth, streaming, and error mapping below are all under
        # test rather than merely under review.
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    # --- subclass hooks -----------------------------------------------------------

    def auth_headers(self) -> Mapping[str, str]:
        """Provider credentials. Raises ``ConfigurationError`` if required and unset."""
        return {}

    # --- lifecycle ----------------------------------------------------------------

    def _client_or_create(self) -> httpx.AsyncClient:
        # No lock needed: there is no await between the check and the assignment, so
        # concurrent tasks on one event loop cannot interleave here.
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url, timeout=self._timeout, transport=self._transport
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # --- the request --------------------------------------------------------------

    async def open(self, request: UpstreamRequest, ctx: RequestContext) -> UpstreamResponse:
        client = self._client_or_create()
        headers = dict(request.headers)
        headers.update(self.auth_headers())
        upstream = client.build_request(
            "POST",
            request.path,
            content=request.body,
            headers=headers,
            timeout=self._timeout if request.timeout_s is None else request.timeout_s,
        )
        try:
            response = await client.send(upstream, stream=True)
        except httpx.TimeoutException as exc:
            raise ProviderTimeout(f"{self.name}: upstream timed out ({exc})") from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"{self.name}: upstream unreachable ({exc})") from exc
        return HttpUpstreamResponse(response, ctx)


class HttpUpstreamResponse(UpstreamResponse):
    """An open httpx streaming response, adapted to the provider contract."""

    def __init__(self, response: httpx.Response, ctx: RequestContext) -> None:
        self._response = response
        self._ctx = ctx

    @property
    def status_code(self) -> int:
        return self._response.status_code

    @property
    def headers(self) -> Mapping[str, str]:
        # httpx joins repeated headers with ", ", which is standard HTTP semantics and
        # adequate here: neither dialect sends a header that must stay split.
        return self._response.headers

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        try:
            async for chunk in self._response.aiter_bytes():
                self._ctx.mark_first_upstream_byte()
                yield chunk
        except httpx.TimeoutException as exc:
            raise UpstreamStreamCut(f"upstream stalled mid-stream ({exc})") from exc
        except httpx.HTTPError as exc:
            raise UpstreamStreamCut(f"upstream stream failed ({exc})") from exc

    async def aclose(self) -> None:
        await self._response.aclose()
