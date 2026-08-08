# Decisions

ADR-style log, numbered `H-NNN`, **append-only**. Every judgment call not already
settled by [BUILD_PLAN.md](../BUILD_PLAN.md) §0.1 gets an entry — including the ones
that felt obvious at the time, because the reason an obvious choice was obvious is
exactly what a reader six months later has lost.

**Entry format** (same structure as Backline's D-log):

```markdown
## H-NNN — Short title (Phase N)

**Status**: accepted | superseded by H-MMM · **Date**: YYYY-MM-DD

**Context.** What forced a choice.

**Decision.** What was chosen, precisely enough to act on.

**Alternatives considered.** What else was on the table and why it lost.

**Consequences.** What this costs, what it now constrains, what has to stay true.
```

An entry is never edited to change its meaning; it is superseded by a later entry
that says so in both directions.

---

## H-000 — Bootstrap stack and repo shape (Phase 0)

**Status**: accepted · **Date**: 2026-08-08

**Context.** BUILD_PLAN L1 locks Python 3.12 + FastAPI + asyncio, uv, ruff, and
mypy --strict. What it does not settle is the shape of the scaffold around them: how
the package is laid out, how the app is built, how tests are configured, and where
the container definition lives. Those choices are load-bearing because invariant 7
says later phases extend this skeleton and never rewrite it.

**Decision.**

- **Package per concern, matching BUILD_PLAN §0.5 exactly**: `headroom/{api,dialects,
  providers,policy,cache,metering,core,db}`. Every package ships at Phase 0 with a
  docstring stating its charter and the phase that fills it, so a later session finds
  a labelled home rather than a naming decision.
- **A module-level `app` in `headroom/api/main.py`**, not an app factory. Uvicorn and
  the test client both want an importable ASGI object; Phase 1 adds routers via
  `app.include_router`, which is additive. A factory buys per-test configuration that
  nothing yet needs.
- **`/healthz` is liveness only** — no dependency probes. A readiness endpoint that
  claims to check Postgres and DynamoDB belongs with the code that actually uses
  them (Phase 2 onward). A health check that lies is worse than no health check.
- **`hatchling` build backend, wheel packages = `["headroom"]`.** The project is
  installed into its own venv by `uv sync`, so `headroom` imports the same way in
  tests, the container, and CI.
- **A single root `Dockerfile`** rather than Backline's `docker/*.Dockerfile` layout.
  There is exactly one image today; the deploy-specific image lands in Phase 9 and
  can introduce `docker/` then, when there is a second thing to name.
- **`pytest-asyncio` in `asyncio_mode = "auto"`** — the whole codebase is async and
  decorating every test is noise.
- **A root `conftest.py` enabling the `pytester` plugin**, so the keyless-by-default
  policy can be proved behaviourally (see `tests/test_pytest_policy.py`) rather than
  merely asserted about the config.

**Alternatives considered.** A flat `headroom/` module tree with files instead of
packages (loses the §0.5 map the plan already committed to); an app factory (premature);
`src/` layout (adds a path level for no benefit with uv's editable installs); Poetry
or pip-tools (two tools, slower, and the operator's toolchain is uv).

**Consequences.** The eight packages are empty scaffolding at Phase 0 — that is
deliberate, and their docstrings carry the contract. Anything a later phase adds must
fit one of these homes or amend §0.5 in the PR that needs a new one.

---

## H-001 — `pgvector/pgvector:pg16` as the Postgres image (Phase 0)

**Status**: accepted · **Date**: 2026-08-08

**Context.** BUILD_PLAN L2 puts config, virtual keys, the cost ledger, the request
log, **and the semantic cache** in one Postgres. The semantic cache needs the
`vector` extension available from the first boot of the stack, well before Phase 5
writes a line of cache code — otherwise the compose file changes underneath a phase
that should only be adding application code.

**Decision.** Pin the database service to `pgvector/pgvector:pg16` — the pgvector
project's own image, Debian-based, tracking upstream `postgres:16`. Same image in
compose and in the CI service container, so local and CI agree by construction.
`CREATE EXTENSION vector` is left to the migration that first needs it (Phase 5); the
image only guarantees availability, which `tests/test_services.py` asserts by querying
`pg_available_extensions`.

**Alternatives considered.** `postgres:16` plus a build step or an init script that
compiles pgvector (slow cold boot, one more thing to get wrong on every machine);
`ankane/pgvector` (the original, now superseded by the project-owned image);
Postgres 17 (RDS support and pgvector packaging are less boring, and L2 says 16);
a separate vector store such as Qdrant (rejected by L2 — one datastore, deliberately).

**Consequences.** The Phase 9 RDS instance must be Postgres 16 with the `vector`
extension enabled from the RDS-supported list, which it is. If a later phase needs a
Postgres feature newer than 16, this pin is the thing to revisit, in a new entry.

---

## H-002 — DynamoDB Local: pinned tag, `-inMemory -sharedDb`, HTTP-400 healthcheck (Phase 0)

**Status**: accepted · **Date**: 2026-08-08

**Context.** L2 puts token buckets and budget reservations on DynamoDB conditional
writes, with `amazon/dynamodb-local` in compose so the P4 code path is identical to
the P9 cloud path (assumption A1). Compose needs a healthcheck on it — the gateway
must not start against a container that is still unpacking a JVM — and DynamoDB Local
has no health endpoint.

**Decision.**

- **Pin `amazon/dynamodb-local:3.1.0`**, not `latest`. A silently moving datastore
  under a concurrency experiment (P4's hammer test) is exactly the kind of drift that
  makes a result unreproducible.
- **Run it `-inMemory -sharedDb`.** `-sharedDb` makes every credential/region pair
  address one dataset, so a table created by the gateway is visible to a test process
  using different dummy credentials — the alternative is a debugging session about an
  empty table that exists. `-inMemory` keeps the container disposable; DynamoDB Local
  state is never meant to survive a restart, and P4's tables are created by code.
- **Healthcheck: an unsigned `GET /` must return HTTP 400.** A malformed DynamoDB
  request is still an answered request, and 400 proves the listener is serving rather
  than merely holding a port open. Verified against the image: it ships `curl`, so
  `curl -s -o /dev/null -w '%{http_code}' … | grep -q 400` runs in-container.
- **CI proves readiness differently**: GitHub service containers cannot override a
  `CMD`, and quoting a piped `--health-cmd` through the runner's option parsing is
  fragile, so the workflow waits on the same HTTP-400 signal from the runner side and
  the service tests then exercise the endpoint.

**Alternatives considered.** `latest` (drift); a TCP-connect healthcheck (a listening
socket is not a serving process); no healthcheck plus `depends_on: service_started`
(loses the ordering guarantee that makes `make up --wait` a real gate); LocalStack
(a much larger surface to boot for one primitive); `-sharedDb` off (surprising
per-credential datasets); on-disk persistence via a volume (state that outlives a
restart is a liability for a reservation ledger used in experiments).

**Consequences.** DynamoDB state resets on `docker compose restart dynamodb` — Phase 4
must create its tables idempotently at startup rather than assuming they exist, which
is also what the real AWS path needs. The tag pin is a thing to bump deliberately,
with a note in PHASE_LOG.

---

## H-003 — Raw-SQL migration runner over Alembic (Phase 0)

**Status**: accepted · **Date**: 2026-08-08

**Context.** The schema is hand-written SQL (a cost ledger with NUMERIC money columns,
a pgvector index whose parameters matter, request-log indexes tuned for the dashboard's
filters). Something has to apply it in order, exactly once, in compose, in CI, and on
RDS.

**Decision.** A ~80-line runner, `headroom/db/migrate.py`: `migrations/*.sql` sorted by
filename, versions recorded in `schema_migrations`, one transaction per file, connect
with a short retry so it can race a fresh Postgres boot. Ported from Backline (D-000),
where it ran through eleven phases and a cloud deploy without incident. Two Headroom
additions: it reads `DATABASE_URL` from the environment with the compose URL as the
default, and it **returns without opening a connection when there are no migration
files**, which is what makes `make migrate` honest on a fresh clone at Phase 0.

**Alternatives considered.** Alembic (autogenerate contributes nothing without
SQLAlchemy models, and it adds a dependency plus a revision graph to a schema that is
append-only by policy); `psql -f` in a shell loop (no ledger, no exactly-once); an ORM
with migrations (the plan's queries are hand-written by design).

**Consequences.** No down-migrations — the local reset path is `docker compose down -v`,
and production forward-fixes. Applied migrations are immutable by convention, enforced
socially (and by the review of every PR) rather than by a checksum column; that trade
is documented in `migrations/README.md` and can be tightened later without a schema
change.

---

## H-004 — Keyless CI shape and caching (Phase 0)

**Status**: accepted · **Date**: 2026-08-08

**Context.** Invariant 4 says CI is fully keyless. The Phase 0 gate also says CI must
prove the service containers come up healthy — a compose file that is merely *valid*
has proved nothing.

**Decision.** Three jobs, no secrets read by any of them:

- `lint-type` — `uv sync --frozen`, `ruff check`, `ruff format --check`, `mypy`, and
  `docker compose config --quiet` so a broken compose file fails in CI.
- `test` — a `pgvector/pgvector:pg16` service container (gated by `pg_isready` health
  options) and an `amazon/dynamodb-local:3.1.0` service container (gated by an explicit
  HTTP-400 wait step), `DATABASE_URL` and `DYNAMODB_ENDPOINT_URL` exported so the
  service tests *run* rather than skip, then `pytest -v` and a `make migrate`-equivalent
  no-op check.
- `image` — build the gateway `Dockerfile` and smoke `/healthz` inside the running
  container. This is the only place the image is proved to actually serve before the
  operator's machine sees it.

Caching is `astral-sh/setup-uv@v5` with `enable-cache: true`, plus `--frozen` so a
lockfile drift fails the build instead of silently resolving something new. The
`embed` extra is deliberately not installed in CI: torch is resolved from the
PyTorch CPU index (H-005) but a ~200 MB download per job buys nothing until Phase 5.

**Alternatives considered.** `docker compose up` in CI instead of service containers
(slower, and service containers are the idiom GitHub actually supports); running
Postgres via `actions/setup-postgres`-style installs (no pgvector); skipping the image
job (leaves the Dockerfile unproven until `make up`); a matrix over Python versions
(L1 locks 3.12 — a matrix would test a configuration nothing ships).

**Consequences.** CI takes three jobs' worth of runner time on every PR, including a
Docker build. In exchange, the Phase 0 gate ("`make up && make test` green on a fresh
clone") has a CI-side analogue that catches breakage before the operator does. If the
service tests ever start skipping in CI, that is a bug in the workflow env, and it will
show as a skip count in `pytest -v` output.

---

## H-005 — The CPU-torch index pin is live from Phase 0 (Phase 0)

**Status**: accepted · **Date**: 2026-08-08

**Context.** The semantic cache embeds with `BAAI/bge-small-en-v1.5` on CPU (L6). On
PyPI, the Linux `torch` wheels are CUDA builds — roughly 5 GB of GPU runtime nobody
here will execute. Backline solved this with `[tool.uv.sources]` pointing torch at
`https://download.pytorch.org/whl/cpu`, but had to **ship the block commented out**:
its build sandbox could not reach `download.pytorch.org` (403 host_not_allowed), and an
index pin the environment cannot fetch breaks every implicit `uv lock`.

**Decision.** Ship the pin **active**. Headroom is built in the local CLI on the
operator's machine (invariant 1), where the index is reachable — verified at Phase 0
before committing: `download.pytorch.org/whl/cpu` returned 200. `torch` is declared
explicitly in the `embed` extra so the pin binds it, and `uv.lock` therefore resolves
CPU wheels for anyone who installs `--extra embed`.

**Alternatives considered.** Keeping Backline's commented block (leaves a trap for
Phase 5, which would have to discover the problem again); no extra at all, torch as a
hard dependency (a 5 GB CI install for a feature that arrives in Phase 5); a separate
requirements file for embeddings (splits the lockfile's guarantees).

**Consequences.** `uv lock` now needs `download.pytorch.org` reachable — a network
that blocks it cannot re-lock this project, which is a real constraint for any future
cloud-sandbox session and one more reason invariant 1 exists. Default installs
(`uv sync`) are unaffected: the extra is opt-in, so keyless CI never touches torch.

---

## H-006 — Host ports 5433 / 8080 / 8001, not 5432 / 8000 (Phase 0)

**Status**: accepted · **Date**: 2026-08-08

**Context.** The first `make up` failed on this machine: `Bind for 0.0.0.0:5432
failed: port is already allocated`. Backline's stack — the sibling portfolio repo,
and the measuring instrument Phase 8 points at this gateway — was running and holds
5432 (Postgres), 8000 (API), and 3000 (UI). Phase 8's H2 requires **both** stacks up
at once: Backline's suite runs against Headroom as its provider base URL. Two projects
that cannot boot simultaneously would make the headline experiment impossible.

**Decision.** Container-internal ports stay canonical (Postgres 5432, gateway 8000,
DynamoDB Local 8000). **Host-side defaults move**: `DB_PORT=5433`,
`GATEWAY_PORT=8080`, `DYNAMODB_PORT=8001`. All three remain `.env`-overridable, and
`headroom/db/migrate.py`'s fallback URL matches the compose default so `make migrate`
works from the host with no configuration.

**Alternatives considered.** Keep 5432/8000 and expect the operator to stop Backline
first (breaks P8.H2 by construction, and the failure mode is a confusing bind error
rather than a message); publish no host ports at all and work inside the compose
network (kills `psql` from the host, the dashboard, and every ad-hoc curl); a compose
profile that toggles ports (configuration for a problem a different default solves).

**Consequences.** Every doc, test invocation, and future runbook must say 5433/8080 —
copy-pasting a `psql -p 5432` from Backline's docs will silently talk to the wrong
database, which is precisely the kind of foot-gun a single fixed convention avoids.
The P9 AWS deployment is unaffected (ALB listeners and RDS have their own addressing).
