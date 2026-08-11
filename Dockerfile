# The gateway image. Build context: repo root.
#
# Slim + uv, two dependency layers so a code change does not re-resolve the
# environment.
#
# ── One Dockerfile, two images ─────────────────────────────────────────────────
# `WITH_EMBED=1` adds the `embed` extra (sentence-transformers on CPU torch) and **bakes
# `BAAI/bge-small-en-v1.5`'s weights into the layer**, which is BUILD_PLAN L6's
# requirement for the deploy image and the pattern Backline proved with a 42-second
# Fargate pull. Off by default, so `make up`, CI's image job, and every local build stay
# at ~200 MB and take seconds; on for `deploy/aws/README.md` §2's push, which is ~1.5 GB
# and the slowest step in the runbook.
#
# H-000 said Phase 9 could introduce a `docker/` directory "when there is a second thing
# to name". It turned out not to be a second thing: the deploy image differs from this
# one by an extra and a download, and a second Dockerfile would be a second copy of the
# base image, the layer order, and the entrypoint — three things that must not drift and
# that nothing would notice drifting. A build argument cannot drift (H-076).
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /uvx /bin/

ARG WITH_EMBED=0

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

# Where the baked weights live, set before anything can download them so the cache lands
# in this layer rather than in a home directory the runtime user may not have.
# `HF_HUB_OFFLINE` is deliberately *not* set here — it would break the download below —
# and is set by the task definition instead, so a deployed task that somehow missed its
# cache fails loudly rather than fetching 130 MB from HuggingFace mid-request.
ENV HF_HOME=/opt/hf

WORKDIR /app

# Dependency layer — cached until the lockfile changes.
COPY pyproject.toml uv.lock README.md ./
RUN if [ "$WITH_EMBED" = "1" ]; then EXTRA="--extra embed"; else EXTRA=""; fi; \
    uv sync --frozen --no-dev --no-install-project $EXTRA

# The weights, in their own layer so a code change never re-downloads them.
#
# It is this call and not an import, for the reason Phase 5's container run found the
# hard way: constructing Headroom's own `BGEEmbedder` touches no weight file, which is
# why `PUT /admin/cache {"mode":"semantic"}` used to answer 200 on an image with no
# model in it. `SentenceTransformer(...)` is the line that really fetches, so it is the
# line that really bakes.
RUN if [ "$WITH_EMBED" = "1" ]; then \
        /app/.venv/bin/python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-small-en-v1.5')"; \
    fi

# Project code. `config/` carries the routing table the gateway reads at startup —
# no secret is in it (BUILD_PLAN §0.2 invariant 3), only the names of the environment
# variables that hold them.
COPY headroom ./headroom
COPY migrations ./migrations
COPY config ./config
RUN if [ "$WITH_EMBED" = "1" ]; then EXTRA="--extra embed"; else EXTRA=""; fi; \
    uv sync --frozen --no-dev $EXTRA

EXPOSE 8000
CMD ["uv", "run", "--no-sync", "uvicorn", "headroom.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
