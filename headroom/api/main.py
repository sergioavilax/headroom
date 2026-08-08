"""The gateway ASGI application.

Phase 0 deliberately exposes exactly one route — ``GET /healthz`` — so that compose
has a real service to bring up and CI can prove the container is healthy. The proxy
routes (``POST /v1/messages``, ``POST /v1/chat/completions``) arrive in Phase 1 as
routers included here; this module is extended, never rewritten (BUILD_PLAN §0.2
invariant 7).

Run it with ``uvicorn headroom.api.main:app``.
"""

from fastapi import FastAPI

app = FastAPI(
    title="Headroom",
    version="0.1.0",
    description="An LLM gateway and control plane.",
)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness: the process is up and serving.

    Deliberately dependency-free. A readiness endpoint that checks Postgres and
    DynamoDB belongs with the code that uses them (Phase 2 onward) — a health check
    that lies about dependencies it never touches is worse than none.
    """
    return {"status": "ok"}
