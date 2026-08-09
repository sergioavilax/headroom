"""Request-context middleware — pure ASGI, deliberately not ``BaseHTTPMiddleware``.

Starlette's ``BaseHTTPMiddleware`` is the ergonomic choice and the wrong one here: it
runs the downstream app in a separate task and pumps the response body through a
memory-object stream, which adds a hop between every upstream chunk and the client and
muddies backpressure. On a gateway whose entire product is first-token latency, that is
not a detail. A pure ASGI middleware sees the same three message types and adds
nothing to the path.

Its jobs are small and all of them are about not losing information:

* mint the :class:`RequestContext` before anything else touches the request, so no code
  path can run without one;
* stamp ``x-headroom-request-id`` on every response, so a caller can quote an id;
* close the context and log it — including when the app raised, which is exactly when
  a missing log line hurts most.
"""

from __future__ import annotations

from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from headroom.core.context import (
    OUTCOME_ERROR,
    OUTCOME_OK,
    RequestContext,
    bind_context,
)
from headroom.core.errors import SOURCE_GATEWAY
from headroom.core.log import log_request

__all__ = ["REQUEST_ID_HEADER", "RequestContextMiddleware", "context_of"]

REQUEST_ID_HEADER = b"x-headroom-request-id"


class RequestContextMiddleware:
    """Gives every HTTP request a context, and every response its id."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        ctx = RequestContext(route=scope.get("path", ""), method=scope.get("method", "POST"))
        bind_context(ctx)
        state: dict[str, Any] = scope.setdefault("state", {})
        state["ctx"] = ctx

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                ctx.status_code = message["status"]
                headers = list(message.get("headers", []))
                headers.append((REQUEST_ID_HEADER, ctx.request_id.encode("ascii")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except BaseException:
            # `complete` is first-call-wins, so a diagnosis the proxy already recorded
            # survives; this only fills in for failures nothing else saw.
            ctx.complete(OUTCOME_ERROR, error_source=SOURCE_GATEWAY)
            log_request(ctx)
            raise
        status = ctx.status_code if ctx.status_code is not None else 200
        ctx.complete(OUTCOME_OK if status < 400 else OUTCOME_ERROR)
        log_request(ctx)


def context_of(scope_or_request: Any) -> RequestContext:
    """The context for a request, from a Starlette ``Request`` or a raw ASGI scope.

    Explicit threading is still the rule — the context is passed into providers as an
    argument — but the route handlers need a way in from the framework side, and this
    is it.
    """
    scope = getattr(scope_or_request, "scope", scope_or_request)
    ctx = scope.get("state", {}).get("ctx")
    if not isinstance(ctx, RequestContext):  # pragma: no cover - middleware always runs
        raise RuntimeError("no RequestContext on this request; is the middleware installed?")
    return ctx
