"""The gateway ASGI application.

Phase 0 shipped exactly one route so compose had a service to bring up. Phase 1 adds
the two proxy routes, the request-context middleware, and a lifespan that builds the
gateway once at startup — all additions (BUILD_PLAN §0.2 invariant 7); ``/healthz`` and
the module-level ``app`` are untouched.

``/healthz`` stays liveness-only even now that there are real dependencies. It reports
that the process is serving, nothing more. A readiness probe that claims to have
checked Postgres, DynamoDB, and three providers belongs with the code that uses them —
and a health check that lies is worse than no health check (H-000).

Run it with ``uvicorn headroom.api.main:app``.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from headroom.api import proxy
from headroom.api.gateway import build_gateway
from headroom.api.middleware import RequestContextMiddleware
from headroom.core.log import configure_logging


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Build the gateway at startup; release its upstream connections at shutdown.

    Configuration is read exactly once, here. A request handler that re-read config or
    rebuilt a client would be paying for it on the hot path — and would make "what is
    this gateway routing right now" a question with a time-varying answer.
    """
    configure_logging()
    app.state.gateway = build_gateway()
    try:
        yield
    finally:
        await app.state.gateway.aclose()


app = FastAPI(
    title="Headroom",
    version="0.1.0",
    description="An LLM gateway and control plane.",
    lifespan=lifespan,
)

# Outermost, so every response carries a request id and no code path can run without a
# RequestContext — including the ones that fail before reaching a route.
app.add_middleware(RequestContextMiddleware)
app.include_router(proxy.router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness: the process is up and serving."""
    return {"status": "ok"}
