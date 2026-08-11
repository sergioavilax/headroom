"""The lame-duck drain: the header, the latch, and the two files that have to agree.

Phase 10 §8 dropped one request per replaced pod and raising the preStop sleep did not
change it, because the sleep has no reach over a connection that already exists (H-091).
The fix is one header — a draining pod answers `Connection: close`, so a client retires the
connection itself while there is still a healthy pod to open the next one against.

What can be tested keylessly is exactly the mechanism, and it is worth being precise about
what that is and is not. These tests pin:

* **the header appears only while draining**, because a gateway that always sent it would
  open a TCP connection per request and nobody would notice until the latency graph;
* **the switch latches**, because a pod in its preStop hook is not coming back and a switch
  that could flip off would turn a deleted sentinel into silently resumed keep-alives;
* **the poll is bounded**, because this sits on the hot path of every request;
* **`RequestContextMiddleware` is still outermost**, which `headroom/api/middleware.py`'s
  docstring requires and which adding a second middleware is exactly how you break;
* **the chart's hook and the container's environment name the same file**, which is the
  failure that would leave a pod that drains nothing and reports no error at all.

What they cannot test is the race itself — that needs two pods, a load balancer and a
network round trip, and it is measured rather than asserted by `scripts/rollout_repro.sh`
(three arms each way, in H-091).
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from starlette.responses import PlainTextResponse

from headroom.api.drain import DRAIN_FILE_ENV, DrainMiddleware, DrainSwitch
from headroom.api.main import app as real_app
from headroom.api.middleware import RequestContextMiddleware

# --- the switch ---------------------------------------------------------------------------


def test_no_sentinel_configured_is_never_draining(tmp_path: Path) -> None:
    """The local and CI case. An unset variable must not be a half-configured drain."""
    assert DrainSwitch(None).draining is False
    assert DrainSwitch("").draining is False


def test_the_switch_follows_the_sentinel_into_existence(tmp_path: Path) -> None:
    sentinel = tmp_path / "draining"
    switch = DrainSwitch(sentinel, poll_interval_s=0.0)
    assert switch.draining is False
    sentinel.touch()
    assert switch.draining is True


def test_the_switch_latches(tmp_path: Path) -> None:
    """Once seen, forever. A pod in its preStop hook is not coming back, and a sentinel
    that could be un-written would resume keep-alives on a pod about to be SIGTERMed."""
    sentinel = tmp_path / "draining"
    switch = DrainSwitch(sentinel, poll_interval_s=0.0)
    sentinel.touch()
    assert switch.draining is True
    sentinel.unlink()
    assert switch.draining is True


def test_the_sentinel_is_not_stat_ed_on_every_request(tmp_path: Path) -> None:
    """This runs in front of every response, so the check is rate-limited — and once the
    switch has latched it stops touching the filesystem at all."""
    sentinel = tmp_path / "draining"
    now = [0.0]
    switch = DrainSwitch(sentinel, poll_interval_s=0.25, clock=lambda: now[0])

    assert switch.draining is False
    sentinel.touch()
    now[0] = 0.1
    assert switch.draining is False, "re-stat inside the poll interval"
    now[0] = 0.3
    assert switch.draining is True

    sentinel.unlink()
    now[0] = 99.0
    assert switch.draining is True, "a latched switch should not be reading the disk"


def test_the_switch_reads_the_variable_the_chart_sets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sentinel = tmp_path / "draining"
    monkeypatch.setenv(DRAIN_FILE_ENV, str(sentinel))
    switch = DrainSwitch.from_env()
    assert switch.draining is False
    sentinel.touch()
    switch = DrainSwitch.from_env()
    assert switch.draining is True


# --- the header ---------------------------------------------------------------------------


def _app(switch: DrainSwitch) -> FastAPI:
    app = FastAPI()

    @app.get("/thing")
    async def thing() -> PlainTextResponse:
        return PlainTextResponse("ok")

    @app.get("/already-closing")
    async def already_closing() -> PlainTextResponse:
        return PlainTextResponse("ok", headers={"Connection": "keep-alive"})

    app.add_middleware(DrainMiddleware, switch=switch)
    return app


async def _get(app: FastAPI, path: str = "/thing") -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        return await client.get(path)


async def test_a_serving_pod_says_nothing_about_the_connection(tmp_path: Path) -> None:
    """Keep-alive is the whole reason this gateway's overhead is measured in milliseconds.
    The header must be absent when the pod is not going anywhere."""
    response = await _get(_app(DrainSwitch(tmp_path / "absent", poll_interval_s=0.0)))
    assert response.status_code == 200
    assert "connection" not in {k.lower() for k in response.headers}


async def test_a_draining_pod_asks_the_client_to_retire_the_connection(tmp_path: Path) -> None:
    sentinel = tmp_path / "draining"
    sentinel.touch()
    response = await _get(_app(DrainSwitch(sentinel, poll_interval_s=0.0)))
    assert response.status_code == 200
    assert response.headers["connection"] == "close"


async def test_a_draining_pod_overrides_a_response_that_asked_to_stay(tmp_path: Path) -> None:
    """Appending would leave `keep-alive, close` on the wire, which is a contradiction the
    client resolves however it likes. The existing header is replaced, not joined."""
    sentinel = tmp_path / "draining"
    sentinel.touch()
    response = await _get(_app(DrainSwitch(sentinel, poll_interval_s=0.0)), "/already-closing")
    assert response.headers.get_list("connection") == ["close"]


# --- the wiring ---------------------------------------------------------------------------


def test_the_request_context_middleware_is_still_the_outermost_one() -> None:
    """`add_middleware` inserts at the front of the list and the front of the list is the
    *outside* of the stack, so adding a middleware moves the previous outermost one in.
    `headroom/api/middleware.py`'s docstring says it has to see every request including the
    ones that fail before a route — so the drain middleware is added first, on purpose, and
    this is the test that notices when somebody reorders the two."""
    # By name, because Starlette types `Middleware.cls` as a `_MiddlewareFactory` protocol
    # and mypy will not let a concrete class be compared against it.
    names = [getattr(m.cls, "__name__", repr(m.cls)) for m in real_app.user_middleware]
    assert names[0] == RequestContextMiddleware.__name__
    assert DrainMiddleware.__name__ in names
    assert names.index(DrainMiddleware.__name__) > names.index(RequestContextMiddleware.__name__)
