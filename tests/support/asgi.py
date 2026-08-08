"""A raw ASGI driver, because every HTTP test client buffers the response body.

Starlette's ``TestClient`` and httpx's ``ASGITransport`` both collect the whole body
before handing back a response. That is fine for asserting *what* the client received
and useless for asserting *when* — and "never buffers the whole response" is a Phase 1
non-negotiable, so it needs a proof that a buffering client cannot give.

This driver keeps every ASGI ``send`` message on a queue as the app produces it, so a
test can pull ``http.response.start``, then the first ``http.response.body``, and check
what the world looks like at that instant. Paired with :class:`MockScript`'s gate, that
turns "does it stream?" into a causal question with a deterministic answer: the client
has bytes *while the upstream is still blocked*, so no buffer could exist. No sleeps,
no timing thresholds, nothing that gets flaky on a loaded CI runner.

It also drives the client-disconnect path, which no HTTP test client exposes at all.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from starlette.types import ASGIApp, Message, Scope

__all__ = ["ASGIRun", "ContextRecorder", "start_request"]


@dataclass
class ASGIRun:
    """One in-flight request: its scope, its outgoing messages, and its task."""

    scope: Scope
    messages: asyncio.Queue[Message]
    task: asyncio.Task[None]
    _disconnected: asyncio.Event = field(default_factory=asyncio.Event)

    async def next_message(self, timeout: float = 5.0) -> Message:
        """The next ASGI message the app sent. Times out rather than hanging a suite."""
        return await asyncio.wait_for(self.messages.get(), timeout)

    async def next_body(self, timeout: float = 5.0) -> bytes:
        """The next non-empty body chunk."""
        while True:
            message = await self.next_message(timeout)
            if message["type"] == "http.response.body" and message.get("body"):
                body: bytes = message["body"]
                return body

    def disconnect(self) -> None:
        """Hang up, as a real client does when the user closes the tab."""
        self._disconnected.set()

    async def drain(self, timeout: float = 5.0) -> bytes:
        """Everything left of the body, until the app signals the end."""
        chunks: list[bytes] = []
        while True:
            message = await self.next_message(timeout)
            if message["type"] != "http.response.body":
                continue
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                return b"".join(chunks)

    async def finish(self, timeout: float = 5.0) -> None:
        await asyncio.wait_for(self.task, timeout)


def start_request(
    app: ASGIApp,
    *,
    path: str,
    body: Any,
    headers: dict[str, str] | None = None,
    method: str = "POST",
) -> ASGIRun:
    """Send a request into an ASGI app and return a handle to it, still running."""
    payload = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
    raw_headers = [(b"content-type", b"application/json")]
    raw_headers += [
        (name.lower().encode("ascii"), value.encode("utf-8"))
        for name, value in (headers or {}).items()
    ]

    scope: Scope = {
        "type": "http",
        # spec_version 2.3 is what uvicorn reports, and it is the version where
        # Starlette listens for `http.disconnect` on a separate task — i.e. the code
        # path that actually runs in production, and the one the disconnect test needs.
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "root_path": "",
        "headers": raw_headers,
        "client": ("test-client", 12345),
        "server": ("gateway", 80),
        "state": {},
    }

    messages: asyncio.Queue[Message] = asyncio.Queue()
    disconnected = asyncio.Event()
    body_sent = False

    async def receive() -> Message:
        nonlocal body_sent
        if not body_sent:
            body_sent = True
            return {"type": "http.request", "body": payload, "more_body": False}
        # Block until the test hangs up. Returning `http.disconnect` eagerly would
        # tear down every streaming response the instant it started.
        await disconnected.wait()
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        await messages.put(message)

    async def run() -> None:
        await app(scope, receive, send)

    task = asyncio.create_task(run())
    return ASGIRun(scope=scope, messages=messages, task=task, _disconnected=disconnected)


class ContextRecorder:
    """Outer ASGI wrapper that keeps each request's ``RequestContext``.

    It sits *outside* the gateway's own middleware and reads the context back off the
    scope afterwards — the scope dict is shared, so the inner middleware's write is
    visible here. That keeps the assertion "a context exists on every path" honest:
    nothing in the test suite creates one.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.contexts: list[Any] = []

    async def __call__(self, scope: Scope, receive: Any, send: Any) -> None:
        try:
            await self.app(scope, receive, send)
        finally:
            if scope["type"] == "http":
                ctx = scope.get("state", {}).get("ctx")
                if ctx is not None:
                    self.contexts.append(ctx)
