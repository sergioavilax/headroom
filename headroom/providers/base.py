"""The provider contract: open an upstream response, hand back its bytes.

Deliberately small. A provider does **not** parse the response, does not know about
dialects beyond the path it posts to, and never buffers a stream — it opens the
upstream call and exposes the bytes as they arrive. Everything interesting (completion
detection, error mapping, timing) happens above it, in one place, so a new provider
cannot get those wrong differently from the existing ones.

Two shapes of failure a provider must distinguish, because the gateway responds to
them very differently:

* raised from ``open`` — nothing was sent downstream yet, so the caller can still be
  given an honest status code, and Phase 6 can still retry against a fallback;
* raised from ``aiter_bytes`` — the status line is long gone and bytes are already on
  the wire, so the only honest move is a terminal error event, and Phase 6 must
  **not** splice a second provider's answer onto the first one's fragment.

That line — *before or after the first byte* — is the one the resilience phase is
built on, so it is drawn here, in the interface, rather than left to each provider.
Phase 6 cashed it: ``headroom/policy/failover.py`` refuses to retry once
``RequestContext.first_token_out_at`` is set, and ``tests/test_failover_boundary.py``
executes the splice that would otherwise happen so the horror is a measurement rather
than a warning (docs/DECISIONS.md H-048).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field

from headroom.core.context import RequestContext

__all__ = ["BufferedUpstreamResponse", "Provider", "UpstreamRequest", "UpstreamResponse"]


@dataclass(frozen=True, slots=True)
class UpstreamRequest:
    """What to send upstream. ``body`` is the caller's bytes, untouched."""

    dialect: str
    path: str
    model: str
    body: bytes
    stream: bool
    #: Client headers, already stripped of credentials and hop-by-hop fields by the
    #: proxy. A provider adds its own auth on top; it never forwards the caller's.
    headers: Mapping[str, str] = field(default_factory=dict)
    #: ``x-headroom-*`` headers, split out because they steer the *gateway* and must
    #: never reach a real provider. The MockProvider reads its script name from here.
    control: Mapping[str, str] = field(default_factory=dict)
    timeout_s: float | None = None


class UpstreamResponse(ABC):
    """An open upstream response. Owns a live connection until ``aclose``."""

    @property
    @abstractmethod
    def status_code(self) -> int: ...

    @property
    @abstractmethod
    def headers(self) -> Mapping[str, str]: ...

    @abstractmethod
    def aiter_bytes(self) -> AsyncIterator[bytes]:
        """Body bytes in arrival order.

        Chunk boundaries are whatever the network produced and are **not** meaningful
        (assumption A4): the gate asserts content and event-sequence equality, never
        identical chunking. Raises ``UpstreamStreamCut`` if the connection dies here.
        """

    async def aread(self) -> bytes:
        """Drain the body. For the non-streaming path, which wants it whole."""
        return b"".join([chunk async for chunk in self.aiter_bytes()])

    @abstractmethod
    async def aclose(self) -> None:
        """Release the connection. Always called, including on client disconnect."""


class BufferedUpstreamResponse(UpstreamResponse):
    """A response whose body is already in hand, wearing the streaming interface.

    Phase 6 needs this for one reason: **the failover executor has to read some bodies
    before it can decide whether the request is finished.** A 529 must be read (and its
    connection released) before the next provider is tried, and a non-streamed 200 must
    be read *inside* the retry loop so that a connection dying mid-body can still fail
    over — nothing has gone downstream yet, so nothing can be spliced.

    Materialising the result as an ``UpstreamResponse`` rather than as a bare
    ``(status, headers, body)`` triple is what keeps ``headroom/api/proxy.py`` unchanged:
    ``_buffered_response`` calls ``aread()`` exactly as it always has, and on one of
    these that call is a return statement. Nothing above the executor learns that
    retries exist.

    ``aclose`` is a genuine no-op — the connection this came from was released by
    whoever built it, which is the executor, in the one place that owns retries.
    """

    __slots__ = ("_body", "_ctx", "_headers", "_status_code")

    def __init__(
        self,
        status_code: int,
        headers: Mapping[str, str],
        body: bytes,
        ctx: RequestContext | None = None,
    ) -> None:
        self._status_code = status_code
        self._headers = dict(headers)
        self._body = body
        self._ctx = ctx

    @property
    def status_code(self) -> int:
        return self._status_code

    @property
    def headers(self) -> Mapping[str, str]:
        return self._headers

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        if self._ctx is not None:
            self._ctx.mark_first_upstream_byte()
        yield self._body

    async def aread(self) -> bytes:
        if self._ctx is not None:
            self._ctx.mark_first_upstream_byte()
        return self._body

    async def aclose(self) -> None:
        return None


class Provider(ABC):
    """One configured upstream. Instances are shared across requests and long-lived."""

    #: The name this provider is registered and routed under, e.g. ``"vllm_a"``.
    name: str
    #: Which implementation it is, e.g. ``"anthropic"``. Phase 6 groups health by it.
    kind: str

    @abstractmethod
    async def open(self, request: UpstreamRequest, ctx: RequestContext) -> UpstreamResponse:
        """Send the request and return once the response *headers* are in.

        Returning early — before the body — is what makes streaming possible at all,
        and what lets ``ctx.first_upstream_byte_at`` mean the first byte rather than
        the last.
        """

    async def aclose(self) -> None:
        """Release any pooled connections. Called once, at application shutdown.

        Concrete rather than abstract, and a genuine no-op by default: a provider that
        holds nothing (the mock) should not be forced to write an empty override, and
        forgetting one on a provider that *does* hold connections is a leak this
        default cannot cause — the HTTP base class implements it.
        """
        return None
