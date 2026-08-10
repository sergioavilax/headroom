"""The MockProvider: the load-bearing test double for the entire project.

BUILD_PLAN §0.2 invariant 4 is *keyless by default* — every test runs against this,
CI never holds a credential, and live spend is opt-in behind ``pytest -m live``. That
makes this file's fidelity a structural concern rather than a testing convenience: an
upstream that cannot fail the way real ones fail leaves the interesting code paths
unexercised until a customer finds them.

So it can be made to do all four things real providers do badly:

* **429 and 529** — the two statuses Phase 6 retries with jittered backoff.
* **Timeouts** — raised, never slept. A test that waits for a real timeout is a test
  people delete when the suite gets slow.
* **Mid-stream cuts** — the failure this phase was ordered around (risk register 1).
* **Pathological chunking** — frames split mid-token and mid-UTF-8-sequence, so
  assumption A4's nuance (content equality, never chunk-identity) is actually tested
  rather than assumed.

Faults are per-request: a test names a script with the ``x-headroom-mock-script``
header and drives the whole stack end to end, rather than reaching past the routes to
poke the provider directly. Everything is deterministic — no clocks, no sleeps, no
randomness — because Phase 8 reports numbers from these paths.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Self

from headroom.core.context import RequestContext
from headroom.core.errors import ProviderTimeout, ProviderUnavailable, UpstreamStreamCut
from headroom.dialects.base import dialect_for
from headroom.providers import mock_scripts
from headroom.providers.base import Provider, UpstreamRequest, UpstreamResponse
from headroom.providers.registry import register_kind

__all__ = [
    "BUILTIN_FAULT_PREFIX",
    "CONTROL_SCRIPT",
    "MockProvider",
    "MockScript",
    "MockScriptBook",
    "builtin_script",
]

#: The ``x-headroom-`` control header a test uses to select a script. The proxy strips
#: the prefix, so a request header of ``x-headroom-mock-script`` arrives as this key.
CONTROL_SCRIPT: Final = "mock-script"

#: Script names beginning with this need no book entry — see :func:`builtin_script`.
#: A prefix rather than a bare vocabulary so that a test's own script name can never
#: collide with one by accident.
BUILTIN_FAULT_PREFIX: Final = "fault-"

#: Where ``fault-cut`` severs the stream: after the opening frames and a couple of text
#: deltas, so a caller sees a plausible fragment and then a terminal error event.
BUILTIN_CUT_AFTER: Final = 5

FAULT_TIMEOUT: Final = "timeout"
FAULT_CONNECT_ERROR: Final = "connect_error"


@dataclass(frozen=True, slots=True)
class MockScript:
    """Exactly what the mock upstream will do for one request."""

    #: HTTP status of the upstream response.
    status_code: int = 200
    #: Response headers. Merged over a content-type inferred from the payload shape.
    headers: Mapping[str, str] = field(default_factory=dict)
    #: A whole body, for non-streamed responses and for error payloads.
    body: bytes | None = None
    #: Stream chunks, emitted in order, exactly as given — including deliberately
    #: awful boundaries.
    chunks: Sequence[bytes] = ()

    #: A failure raised *before* any response exists: "timeout" or "connect_error".
    fault: str | None = None
    #: Emit this many chunks, then kill the connection. The mid-stream cut.
    cut_after_chunks: int | None = None

    #: Block before this chunk index until ``gate`` is set. The deterministic way to
    #: prove the gateway does not buffer: hold the upstream open and check the client
    #: already has bytes. No sleeps, no timing heuristics.
    gate: asyncio.Event | None = None
    gate_before_chunk: int | None = None

    # --- builders ----------------------------------------------------------------

    @classmethod
    def anthropic_stream(
        cls,
        text: str,
        *,
        cut_after_chunks: int | None = None,
        chunk_size: int | None = None,
        **kwargs: Any,
    ) -> Self:
        """A full Anthropic stream, optionally re-chopped and optionally cut short."""
        chunks = mock_scripts.anthropic_stream_chunks(text, **kwargs)
        return cls(chunks=_rechunk(chunks, chunk_size), cut_after_chunks=cut_after_chunks)

    @classmethod
    def openai_stream(
        cls,
        text: str,
        *,
        cut_after_chunks: int | None = None,
        chunk_size: int | None = None,
        **kwargs: Any,
    ) -> Self:
        """A full OpenAI stream, optionally re-chopped and optionally cut short."""
        chunks = mock_scripts.openai_stream_chunks(text, **kwargs)
        return cls(chunks=_rechunk(chunks, chunk_size), cut_after_chunks=cut_after_chunks)

    @classmethod
    def openai_reasoning_stream(
        cls,
        *,
        cut_after_chunks: int | None = None,
        chunk_size: int | None = None,
        **kwargs: Any,
    ) -> Self:
        """A reasoning model's stream — chain of thought first, answer second.

        Defaults live in :func:`mock_scripts.openai_reasoning_stream_chunks` rather than
        being repeated here; ``reasoning``, ``text``, ``reasoning_field`` and the token
        counts all pass straight through.
        """
        chunks = mock_scripts.openai_reasoning_stream_chunks(**kwargs)
        return cls(chunks=_rechunk(chunks, chunk_size), cut_after_chunks=cut_after_chunks)

    @classmethod
    def anthropic_message(cls, text: str, **kwargs: Any) -> Self:
        return cls(body=mock_scripts.anthropic_message_body(text, **kwargs))

    @classmethod
    def openai_completion(cls, text: str, **kwargs: Any) -> Self:
        return cls(body=mock_scripts.openai_completion_body(text, **kwargs))

    @classmethod
    def openai_reasoning_completion(cls) -> Self:
        """The non-streamed reasoning reply, byte-literal and adversarially encoded."""
        return cls(body=mock_scripts.openai_reasoning_body())

    @classmethod
    def error(
        cls,
        status_code: int,
        *,
        dialect: str = "anthropic",
        message: str | None = None,
        retry_after: str | None = None,
    ) -> Self:
        """An upstream error, in the upstream's own error format.

        The body is built by the dialect, so what the client receives is what a real
        provider's error looks like — which is the point of the error-mapping tests:
        they assert Headroom *forwards* this, not that it composes something similar.
        """
        spoken = dialect_for(dialect)
        headers = {"retry-after": retry_after} if retry_after else {}
        return cls(
            status_code=status_code,
            headers=headers,
            body=spoken.error_body(
                status_code=status_code,
                reason="upstream_said_so",
                message=message or f"mock upstream returned {status_code}",
                request_id="req_mock_upstream",
            ),
        )

    @classmethod
    def timeout(cls) -> Self:
        """Upstream never answers. Raised instantly — nothing here sleeps."""
        return cls(fault=FAULT_TIMEOUT)

    @classmethod
    def connect_error(cls) -> Self:
        """Upstream cannot be reached at all."""
        return cls(fault=FAULT_CONNECT_ERROR)


def _rechunk(chunks: Sequence[bytes], chunk_size: int | None) -> Sequence[bytes]:
    if chunk_size is None:
        return chunks
    return mock_scripts.split_every(b"".join(chunks), chunk_size)


class MockScriptBook:
    """Named scripts for one gateway instance. A test writes, a request reads."""

    __slots__ = ("_scripts",)

    def __init__(self) -> None:
        self._scripts: dict[str, MockScript] = {}

    def set(self, name: str, script: MockScript) -> MockScript:
        self._scripts[name] = script
        return script

    def get(self, name: str) -> MockScript | None:
        return self._scripts.get(name)

    def clear(self) -> None:
        self._scripts.clear()

    def __contains__(self, name: object) -> bool:
        return name in self._scripts


class MockProvider(Provider):
    """A provider that does whatever the request's script says."""

    kind = "mock"

    def __init__(self, name: str = "mock", book: MockScriptBook | None = None) -> None:
        self.name = name
        self.book = book if book is not None else MockScriptBook()
        #: Every request this provider was handed, in order. The A5 fixture asserts
        #: against these bytes to prove the *client's* body reached the upstream
        #: unchanged — half of "round-trips untouched" lives on the request side.
        self.received: list[UpstreamRequest] = []
        #: Every response it opened. The client-disconnect test reads ``.closed`` off
        #: the last one: a gateway that leaks upstream connections when callers hang up
        #: dies under exactly the load Phase 6 measures.
        self.opened: list[_MockUpstreamResponse] = []

    async def open(self, request: UpstreamRequest, ctx: RequestContext) -> UpstreamResponse:
        self.received.append(request)
        script = self.script_for(request)
        if script.fault == FAULT_TIMEOUT:
            raise ProviderTimeout(f"mock provider {self.name!r} timed out")
        if script.fault == FAULT_CONNECT_ERROR:
            raise ProviderUnavailable(f"mock provider {self.name!r} could not be reached")
        response = _MockUpstreamResponse(script, ctx)
        self.opened.append(response)
        return response

    def script_for(self, request: UpstreamRequest) -> MockScript:
        """The named script if the request asked for one, else a canned reply.

        Resolution order, most specific first:

        1. ``book["<name>@<provider>"]`` — **a script specialised to one instance.**
           Phase 6 needs exactly that: one request, one script name, two providers that
           must behave *differently* (A returns 529, B serves the answer). The
           alternative — a book per provider — would make a failover test set up two
           books to say one thing.
        2. ``book["<name>"]`` — every script written before this phase, unchanged.
        3. **A built-in fault** (:func:`builtin_script`), which needs no book at all and
           is therefore the only one a *running container* can reach.
        4. Otherwise ``KeyError``, because a typo'd script name must not quietly become
           a happy path in a fault-injection suite.
        """
        name = request.control.get(CONTROL_SCRIPT)
        if name is None:
            return _default_script(request)
        script = self.book.get(f"{name}@{self.name}") or self.book.get(name)
        if script is not None:
            return script
        builtin = builtin_script(name, request, self.name)
        if builtin is not None:
            return builtin
        raise KeyError(f"no mock script named {name!r} (provider {self.name!r})")


def builtin_script(name: str, request: UpstreamRequest, provider: str) -> MockScript | None:
    """A fault a **running gateway** can be asked for, with no test process involved.

    Phase 1 made the mock fault-injectable per request so that *tests* could drive the
    whole stack. Phase 6 needs the same thing one layer out: `make up` plus two curls
    should demonstrate a failover with no key, no network, no GPU, and no spend — and a
    script book only exists inside a test process. So a handful of faults are built in,
    addressable over HTTP by name:

    ``x-headroom-mock-script: fault-529`` (any status), ``fault-timeout``,
    ``fault-connect``, ``fault-cut``.

    **A fault may be aimed at one instance**, with the same ``@`` suffix the book uses:
    ``fault-529@mock`` breaks the chain's primary and leaves its fallback answering
    normally, which is the whole demo in one header. Every other instance falls through
    to its ordinary reply.

    Returns ``None`` for anything not in this vocabulary, so a mistyped script name still
    raises rather than quietly becoming a happy path.
    """
    base, _, target = name.partition("@")
    if not base.startswith(BUILTIN_FAULT_PREFIX):
        return None
    kind = base[len(BUILTIN_FAULT_PREFIX) :]
    if not (kind.isdigit() or kind in {"timeout", "connect", "cut"}):
        return None
    if target and target != provider:
        return _default_script(request)
    if kind.isdigit():
        return MockScript.error(int(kind), dialect=request.dialect)
    if kind == "timeout":
        return MockScript.timeout()
    if kind == "connect":
        return MockScript.connect_error()
    # A mid-stream cut: the fault that must *not* fail over, and therefore the one worth
    # being able to show a caller from the outside.
    cut = _default_script(_streaming(request))
    return MockScript(chunks=cut.chunks, cut_after_chunks=BUILTIN_CUT_AFTER)


def _streaming(request: UpstreamRequest) -> UpstreamRequest:
    """The same request as a streamed one, so ``fault-cut`` has a stream to cut."""
    return UpstreamRequest(
        dialect=request.dialect,
        path=request.path,
        model=request.model,
        body=request.body,
        stream=True,
        headers=request.headers,
        control=request.control,
    )


def _default_script(request: UpstreamRequest) -> MockScript:
    """A deterministic reply for a request that named no script.

    Derived from the model so that a smoke call through a running gateway echoes
    something recognisable — ``make up`` plus a curl at ``mock-echo`` is a working
    demo of the whole path with no key and no network.
    """
    text = f"mock reply from {request.model}"
    if request.dialect == "anthropic":
        return (
            MockScript.anthropic_stream(text)
            if request.stream
            else MockScript.anthropic_message(text)
        )
    return MockScript.openai_stream(text) if request.stream else MockScript.openai_completion(text)


class _MockUpstreamResponse(UpstreamResponse):
    """One scripted response in flight."""

    def __init__(self, script: MockScript, ctx: RequestContext) -> None:
        self._script = script
        self._ctx = ctx
        #: Whether the proxy released this response. Asserted by the client-disconnect
        #: test: a gateway that leaks upstream connections on disconnect is a gateway
        #: that falls over under exactly the load Phase 6 measures.
        self.closed = False

    @property
    def status_code(self) -> int:
        return self._script.status_code

    @property
    def headers(self) -> Mapping[str, str]:
        inferred = "text/event-stream" if self._script.chunks else "application/json"
        headers = {"content-type": inferred}
        headers.update({key.lower(): value for key, value in self._script.headers.items()})
        return headers

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        script = self._script
        if not script.chunks:
            if script.body:
                self._ctx.mark_first_upstream_byte()
                yield script.body
            return

        for index, chunk in enumerate(script.chunks):
            if script.cut_after_chunks is not None and index >= script.cut_after_chunks:
                raise UpstreamStreamCut(
                    f"mock upstream closed the connection after {script.cut_after_chunks} chunk(s)"
                )
            if script.gate is not None and index == script.gate_before_chunk:
                await script.gate.wait()
            self._ctx.mark_first_upstream_byte()
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


def _build_mock_provider(name: str, **_: object) -> Provider:
    return MockProvider(name=name)


# Both dialects, because the mock genuinely speaks both — which is also what lets a
# keyless failover chain be built out of two of them (BUILD_PLAN L4, H-049).
register_kind("mock", _build_mock_provider, dialects=frozenset({"anthropic", "openai"}))
