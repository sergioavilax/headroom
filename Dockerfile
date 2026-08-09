# The gateway image. Build context: repo root.
#
# Slim + uv, two dependency layers so a code change does not re-resolve the
# environment. The `embed` extra (sentence-transformers/torch) is NOT installed
# here — the semantic cache arrives in Phase 5 and will add it, along with baked
# model weights, at that point (BUILD_PLAN L6).
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependency layer — cached until the lockfile changes.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# Project code. `config/` carries the routing table the gateway reads at startup —
# no secret is in it (BUILD_PLAN §0.2 invariant 3), only the names of the environment
# variables that hold them.
COPY headroom ./headroom
COPY migrations ./migrations
COPY config ./config
RUN uv sync --frozen --no-dev

EXPOSE 8000
CMD ["uv", "run", "--no-sync", "uvicorn", "headroom.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
