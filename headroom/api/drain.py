"""Lame-duck drain — retiring a pod's keep-alive connections before it stops being one.

Phase 10 §8 measured a rolling `helm upgrade` and found exactly one dropped request per
replaced pod: ``RemoteProtocolError: Server disconnected without sending a response``.
Raising the preStop sleep from 5s to 15s did not change it, and that is the whole
diagnosis in one observation (docs/DECISIONS.md H-091).

The preStop sleep exists because a pod is sent SIGTERM and removed from Endpoints at the
same instant, and kube-proxy takes a beat to notice. It fixes the *new connection* race
and nothing else. A client that already holds an established keep-alive connection to the
doomed pod keeps using it: conntrack pins the flow to that pod, Endpoints has no say, and
so the sleep is spent serving the very requests that are about to be broken. When SIGTERM
finally arrives, uvicorn closes those connections — and any request the client had already
written onto one is answered by a FIN. No amount of sleeping helps, because the sleep is
not where the problem is.

The fix is to stop the connections from being keep-alive *first*. While a pod is draining,
every response carries ``Connection: close``; the client reads it, retires that connection
itself, and opens the next one — which Endpoints has by then pointed at a healthy pod. By
the time SIGTERM lands there is nothing left to break. `preStop` touches the sentinel file
and *then* sleeps, so the sleep is finally doing work: it is the window in which clients
retire their connections one response at a time.

``Connection`` is a hop-by-hop header and an ASGI app is not generally supposed to set
one. uvicorn is explicit about this one: both its HTTP implementations read the app's
response headers and set ``keep_alive = False`` on ``connection: close``
(``uvicorn/protocols/http/httptools_impl.py``). It is the supported way to say this from
inside the application, and the alternative — a second HTTP server on a second port
whose only job is to be shut down early — is a great deal of machinery for one header.

A sentinel file rather than an admin route or a signal:

* a route would need a credential, and an uncredentialled localhost-only route is a new
  piece of security surface for a `touch`;
* SIGUSR1 would have to survive `uv run`'s signal forwarding, which is one more thing to
  be sure of on a cluster we are about to tear down;
* a file is what `preStop` can already write with the shell it already runs.

The residual, stated honestly: a connection that sits idle in a client's pool for the
whole drain window and is first reused in the milliseconds after SIGTERM is still broken
by this design, because nothing ever handed it a ``Connection: close`` to act on. Busy
pools do not have such connections — every one of them gets a response, and every response
retires it — but the race is narrowed, not closed, and no server-side change can close it.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path

from starlette.types import ASGIApp, Message, Receive, Scope, Send

__all__ = [
    "CONNECTION_HEADER",
    "DRAIN_FILE_ENV",
    "POLL_INTERVAL_S",
    "DrainMiddleware",
    "DrainSwitch",
]

#: Where `preStop` writes the sentinel. Unset means the switch is wired to nothing and
#: `draining` is permanently false — which is what every local run and every test that
#: does not care about shutdown wants.
DRAIN_FILE_ENV = "HEADROOM_DRAIN_FILE"

CONNECTION_HEADER = b"connection"

#: How stale the answer to "are we draining?" is allowed to be. A `stat` per request would
#: also be fine at this gateway's latencies, but there is no reason to pay it: 250 ms of
#: lag at the start of a drain window measured in seconds costs nothing, and the check
#: disappears entirely once the switch has latched.
POLL_INTERVAL_S = 0.25


class DrainSwitch:
    """Reads "is this pod draining?" off the filesystem, at most every ``poll_interval_s``.

    Latching, deliberately: once the sentinel has been seen the answer is yes forever.
    A pod that has entered its preStop hook is not coming back, and a switch that could
    flip off would turn a deleted file into silently resumed keep-alives.
    """

    def __init__(
        self,
        path: str | os.PathLike[str] | None,
        *,
        poll_interval_s: float = POLL_INTERVAL_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._path = Path(path) if path else None
        self._poll_interval_s = poll_interval_s
        self._clock = clock
        self._draining = False
        self._checked_at: float | None = None

    @classmethod
    def from_env(cls) -> DrainSwitch:
        """The wiring the container uses: the path in :data:`DRAIN_FILE_ENV`, or nothing."""
        return cls(os.environ.get(DRAIN_FILE_ENV))

    @property
    def draining(self) -> bool:
        if self._draining or self._path is None:
            return self._draining
        now = self._clock()
        if self._checked_at is not None and now - self._checked_at < self._poll_interval_s:
            return False
        self._checked_at = now
        self._draining = self._path.exists()
        return self._draining


class DrainMiddleware:
    """Stamps ``Connection: close`` on every response while the pod is draining."""

    def __init__(self, app: ASGIApp, *, switch: DrainSwitch | None = None) -> None:
        self.app = app
        self.switch = switch if switch is not None else DrainSwitch.from_env()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Read once, at the top: a request that started while the pod was still serving
        # should not get a half-drained answer if the switch flips mid-response.
        if scope["type"] != "http" or not self.switch.draining:
            await self.app(scope, receive, send)
            return

        async def send_with_close(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() != CONNECTION_HEADER
                ]
                headers.append((CONNECTION_HEADER, b"close"))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_close)
