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

---

## H-007 — SSE passthrough observes the stream; it never re-frames it (Phase 1)

**Status**: accepted · **Date**: 2026-08-08

**Context.** The gateway has to know things about a stream it is forwarding. Phase 1
needs to know whether the upstream reached its dialect's terminal marker — without that,
a truncated answer is indistinguishable from a complete one. Phase 3 needs the usage
payload from `message_delta` / the trailing usage chunk. Phase 5 needs the stop reason
before it may cache. All of that argues for parsing. Meanwhile assumption A4 and the
tool-block requirement (A5) argue for touching nothing.

**Decision.** Forward the upstream's bytes **unchanged** and feed a *copy* of every chunk
to an incremental SSE parser (`headroom/core/sse.py`) used purely as an observer. The
parser's output steers gateway behaviour; it can never alter a byte the client receives,
because the yield and the `feed` are separate statements over the same immutable chunk.

Consequences that fall out for free: chunk boundaries are whatever the network produced
(A4 satisfied by construction), multi-byte characters split mid-sequence pass through
intact because nothing decodes them, and `input_json_delta` fragments — which are *not*
valid JSON documents individually — need no special handling.

**Alternatives considered.** *Parse and re-emit* — the obvious design, and the one that
makes usage extraction tidiest; rejected because re-serializing is exactly the class of
change that breaks A5 silently, and because it puts a JSON encoder on the first-token
path. *Pure byte passthrough with no parser* — cheapest, but then the gateway cannot tell
a finished stream from an amputated one, which is the whole subject of this phase.
*Tail-scanning the raw bytes for `message_stop`* — works until a terminal marker straddles
a chunk boundary, which the `chunk_size=1` fixture does on every run.

**Consequences.** The parser is on the hot path for every streamed chunk, so it is written
to be cheap (byte-level line splitting, no regex, nothing decoded that need not be). Its
correctness is load-bearing in a way a passive component usually is not: a parser that
loses an event appends a spurious error to a good response, and one that hallucinates an
event lets a truncation through. `tests/test_sse.py` therefore tests it at the boundaries
directly, not only through the proxy.

---

## H-008 — A stream that ends early ends in a terminal error event (Phase 1)

**Status**: accepted · **Date**: 2026-08-08

**Context.** BUILD_PLAN's risk register item 1 orders the mid-stream-cut fixture written
*before* the happy path. Once a 200 and some SSE frames are on the wire, the HTTP status
is spent: there is no status code left to say "actually, that failed". The default
behaviour of every naive proxy — let the stream stop — produces a fragment that an SDK
returns as a complete answer, that Phase 3 bills as complete, and that Phase 5 would cache
and serve forever (invariant 6, one layer down).

**Decision.** A streamed response that does not reach its dialect's terminal marker gets
one appended frame, in the caller's own dialect, that the caller's SDK raises on:

- **Anthropic**: `event: error` carrying `{"type":"error","error":{"type":"api_error",…}}`
  — part of the Messages API streaming spec.
- **OpenAI**: a bare `data:` frame whose JSON carries an `error` key, and **no `[DONE]`
  after it** — `openai-python` raises `APIError` on exactly that shape, and `[DONE]` is
  precisely the claim that would make a fragment look finished.

Both carry `headroom.reason`, which distinguishes the two ways a stream ends early:
`upstream_stream_cut` (the connection died — an exception) and
`upstream_stream_incomplete` (the bytes simply stopped — no exception anywhere). The
second is the quieter failure, and the one a guard against *exceptions* alone misses.

**Completion is defined per dialect, with deliberately different strictness.** Anthropic
always sends `message_stop`, so the rule is strict: `message_stop` or an upstream `error`
event, nothing else. The OpenAI dialect's `[DONE]` is convention rather than protocol, so
the rule is lenient: `[DONE]`, **or** a chunk carrying a non-null `finish_reason`, **or**
an error frame. Lenient there avoids crying truncation at a compliant-but-terse backend;
strict here avoids missing a real one. (A `finish_reason` of `length` is a *complete
stream* of a *truncated answer* — a real distinction, and Phase 5's business under
invariant 6. The only question at this layer is whether the stream ended or was cut.)

**Alternatives considered.** *Silently ending the stream* — the failure this decision
exists to prevent. *Killing the TCP connection* so the client sees a transport error —
honest, but it discards the request id and the reason, and behaves differently behind
every load balancer. *Buffering the whole response and returning a 5xx once it turns out
incomplete* — would work, and would throw away first-token latency, which is the product.
*A gateway-specific error envelope* — a shape no SDK parses is barely better than silence.

**Consequences.** The gateway must track completion for every streamed response, which is
what H-007 pays for. The check is gated on a `text/event-stream` content-type, so a
non-SSE 200 streamed through is not falsely accused. Phase 6 inherits the semantic that
makes failover safe — a fault **before** the first byte may be retried against a fallback,
a fault **after** it may not, because splicing is how gateways serve Frankenstein
responses — and that line is drawn in the provider interface rather than in each provider.

---

## H-009 — Upstream errors are forwarded; invented ones are specific (Phase 1)

**Status**: accepted · **Date**: 2026-08-08

**Context.** "Upstream errors map to honest downstream errors with the upstream's status
preserved" is a Phase 1 non-negotiable. Two different situations hide inside it, and they
need opposite treatments.

**Decision.**

- **The upstream answered** (any status ≥ 400): forward its status, its body **verbatim**,
  and its headers. The gateway composes nothing. An upstream 400 naming the offending
  field is more useful than anything Headroom could write, and `retry-after` on a 429 is
  the caller's means of behaving well.
- **There was no answer**: the gateway invents a status, and which one is fixed by the
  error class so the same fault always surfaces the same way — `504` timeout, `502`
  unreachable or cut, `404` unroutable model (matching what both providers return for an
  unknown model, so an SDK's `NotFoundError` means the same thing with or without Headroom
  in the path), `400` unparseable body, `500` gateway misconfiguration.
- Invented bodies use **only documented dialect error types** (`api_error`,
  `rate_limit_error`, `overloaded_error`, …). The exact cause travels in a `headroom`
  block — `{"reason": …, "request_id": …}` — that SDKs ignore as an unknown key.
- Every error response carries `x-headroom-error-source: upstream | gateway`.

**Alternatives considered.** *Normalizing every error into one Headroom shape* —
consistent, and it destroys the information the caller needs; it would also flatten the
429/529 distinction that Phase 6's failover and Phase 8's H3 are *measured* on. *Inventing
more expressive error types* (`timeout_error` for the 504) — more honest at a glance, but a
gateway is a poor place to coin vocabulary an SDK might switch on; `reason` carries that
precision where nothing can trip over it. *Returning 500 for everything with no answer* —
the generic 500 the plan forbids by name.

**Consequences.** The set of invented statuses is now a compatibility surface: changing
what a timeout returns changes caller retry behaviour. `reason` strings are likewise stable
identifiers, and Phase 3 writes them to the ledger, so they are additive-only from here. A
misconfiguration still returns 500 — but one that names the exact environment variable to
set, which is what separates a specific 500 from a generic one.

---

## H-010 — Header policy: a deny-list, and credentials never cross (Phase 1)

**Status**: accepted · **Date**: 2026-08-08

**Context.** A proxy that forwards headers naively breaks in three distinct ways; a proxy
that forwards none makes its callers worse citizens than they were without it.

**Decision.** Both directions use a **deny-list**, not an allow-list.

- **Upstream-bound**: strip the caller's `authorization` / `x-api-key` / `api-key` /
  `cookie`, the hop-by-hop headers, `host`, `content-length`, and `accept-encoding`. Strip
  `x-headroom-*` and hand it to the provider separately as *control* input. Everything else
  is forwarded, and the provider adds its own credential on top.
- **Client-bound**: strip hop-by-hop, `content-length`, and `content-encoding` — httpx has
  already decoded the body, so the upstream's framing describes bytes that no longer exist.
  Everything else is forwarded, including `retry-after` and the provider's rate-limit and
  request-id headers.

The credential rule matters more than it looks today: Phase 2 turns the client's
`authorization` into a **virtual key** — an `hk_…` value meaningful only to Headroom — and a
gateway that forwarded it would leak its own tenants' secrets to Anthropic. Writing the rule
now means Phase 2 adds keys rather than also fixing a leak.

**Alternatives considered.** *An allow-list* — safer against a future framing header, and it
silently discards signal forever; the cost of the deny-list is having to notice one new
header, the cost of the allow-list is invisible degradation. *Forwarding everything* —
produces the classic `content-encoding: gzip` on decoded plaintext. *Forwarding the caller's
auth upstream* — convenient in Phase 1, a security bug in Phase 2.

**Consequences.** Repeated response headers collapse to a single comma-joined value (httpx's
`Headers` semantics); neither dialect sends a header that must stay split, and revisiting
this means changing the `UpstreamResponse.headers` type. A new hop-by-hop or framing header
in some future HTTP revision has to be added to the deny-list by hand.

---

## H-011 — A vLLM base URL is accepted with or without its `/v1` suffix (Phase 1)

**Status**: accepted · **Date**: 2026-08-08

**Context.** Both dialect paths already begin with `/v1`. Meanwhile the OpenAI SDK wants its
`base_url` to *end* with `/v1`, so half the world writes `VLLM_BASE_URL` as
`http://box:8000/v1` and the other half as `http://box:8000`. httpx joins the first form
into `/v1/v1/chat/completions` — a 404 that looks like a problem at the far end, on a path
the operator exercises by hand during the live smoke.

**Decision.** `normalize_base_url` trims trailing slashes and one trailing `/v1` segment, so
both spellings resolve to the same endpoint. Applied to every HTTP provider, not just the
OpenAI-compatible one.

**Alternatives considered.** *Document the required form* — a README line against a
misconfiguration that produces a confusing error is a losing trade. *Make the upstream path
per-provider configuration* — more general, more to get wrong, and nothing needs the
generality yet. *Detect at startup with a probe* — a network call at boot, for this.

**Consequences.** A provider genuinely served under a path ending in `/v1` that is *not* the
API version prefix would be mis-trimmed. No such deployment exists among the launch
providers, and the failure would be immediate and loud rather than subtle.

---

## H-012 — Service-backed tests find the compose stack on their own (Phase 1)

**Status**: accepted · **Date**: 2026-08-08

**Context.** Phase 0 made `tests/test_services.py` skip unless `DATABASE_URL` and
`DYNAMODB_ENDPOINT_URL` were exported by hand. The documented workflow is `make up && make
test`, which therefore ran two fewer tests than the operator thought it did — a skip nobody
reads is a test nobody has. Against that, `CLAUDE.md` says service-backed tests should "skip
loudly on a missing endpoint env var rather than inventing a fallback", and CI's value
depends on those tests *running* rather than skipping.

**Decision.** Fall back to the compose endpoints (the H-006 host ports), keeping the
distinction that actually matters:

- **inferred** endpoint, nothing listening → skip, naming the address it tried and
  suggesting `make up`. A fresh clone with no stack up is not a broken repo.
- **explicit** endpoint, nothing listening → **fail**. Someone stated the store is there;
  CI states it in its workflow env, and a silent skip would make that job a liar about its
  own service containers.

This deliberately amends the `CLAUDE.md` line: the fallback is not invented, it is the
documented compose stack, and it stays loud when absent.

**Alternatives considered.** *Export the defaults from the Makefile* — same effect for `make
test`, no effect for a bare `pytest`, and Python could no longer tell an inferred value from
an operator's. *A `HEADROOM_REQUIRE_SERVICES=1` flag in CI* — one more thing to set and to
forget; explicit-configuration-means-required needs no flag. *Leave Phase 0 behaviour* —
keeps a documented workflow quietly under-testing itself.

**Consequences.** Adding a service-backed test means using `resolve_endpoint` rather than
reading the environment directly. The reachability probe is a 0.4-second TCP connect, so a
fresh clone pays under a second before skipping. `make up && make test` now reports **127
passed, 0 skipped**.

---

## H-013 — Providers register as kinds; routes resolve to instance names (Phase 1)

**Status**: accepted · **Date**: 2026-08-08

**Context.** Phase 6 adds failover chains across the operator's two vLLM boxes, and
invariant 7 says it extends this phase rather than rewriting it. Whether that is possible is
decided now, by how a request finds its upstream.

**Decision.** Two levels. **Kinds** are implementations (`anthropic`, `openai_compat`,
`mock`) that self-register a factory at import time; config names a kind, and a new
implementation becomes available without the loader, the router, or the proxy learning
anything. **Instances** are configured providers with names (`anthropic`, `vllm_a`,
`vllm_b`), held in a per-gateway `ProviderRegistry`. The routing table resolves
`(dialect, model)` to an instance **name**; the registry turns a name into an object.

Routes are per dialect, with **longest prefix wins** and `""` as the catch-all.

**Alternatives considered.** *Routes holding provider objects* — one fewer indirection, and
Phase 6 would have to change every rule's type. *A single flat provider table with no kinds*
— the operator's two GPUs would need two copy-pasted classes. *First-match-wins ordering
from the YAML file* — makes a config file's line order load-bearing, and carving one model
out of a family becomes a game of positioning.

**Consequences.** Phase 6 widens what a rule holds (primary plus same-dialect fallbacks) and
`Gateway.provider_for`; the proxy is untouched, and BUILD_PLAN L4's same-dialect constraint
is enforced by the data structure rather than remembered by a reviewer. Two equally specific
prefixes are ordered alphabetically, so a restart cannot reshuffle routing under an
experiment.

---

## H-014 — Routing lives in its own config file, and PyYAML is a dependency (Phase 1)

**Status**: accepted · **Date**: 2026-08-08

**Context.** BUILD_PLAN §0.5 lists exactly one config file, `config/models.yaml`, labelled
Phase 3 and described as model ids, dialects, context windows, and dated prices. Phase 1
needs somewhere to declare providers and routes.

**Decision.** A second file, `config/routing.yaml`, loaded and validated by Pydantic at
startup. `models.yaml` stays Phase 3's and keeps its stated contents. The split is by
ownership: routes are *policy* (they change when a provider is added or a GPU dies), while
model metadata and prices are *reference data* (they change when a vendor publishes).
`HEADROOM_ROUTING_CONFIG` overrides the path.

Providers name the **environment variable** holding their credential (`api_key_env`), never
the credential — and with `extra="forbid"`, a well-meaning `api_key:` line fails to load
rather than quietly working, which makes invariant 3 structural rather than social.

Validation happens at load (a route naming an undefined provider fails at startup);
credentials resolve at use (a missing `ANTHROPIC_API_KEY` fails on the first request that
needs Anthropic, not at boot). A gateway serving only mock and vLLM traffic must start
without any key, and Phase 9's container must come up healthy before its secret arrives.

**PyYAML** joins the dependencies. Both config files are meant to be read and edited by a
human and both earn their comments, which JSON cannot carry. `types-PyYAML` joins the dev
group because `mypy --strict` will not take `Any` for an answer on a config loader.

**Alternatives considered.** *Put routes in `models.yaml` now* — closer to the letter of
§0.5, and it mixes two things with different change rates and different owners.
*Environment-variable routing* (`HEADROOM_ROUTES="claude-=anthropic,…"`) — no new
dependency, unreadable, uncommentable. *JSON* — no comments, and these files are documents.
*A silent built-in default when the file is missing* — a gateway that invents its own
routing table sends real traffic somewhere nobody chose.

**Consequences.** `config/` is now part of the container image (`Dockerfile`), and Phase 3
adds `models.yaml` beside it rather than restructuring. §0.5's repo map is amended by this
PR to list both files.

---

## H-015 — Pure ASGI middleware, and logging is configured explicitly (Phase 1)

**Status**: accepted · **Date**: 2026-08-08

**Context.** The request context has to be created before anything else touches a request,
and one structured line has to be written after every request — including the ones that
raised. Starlette's `BaseHTTPMiddleware` is the ergonomic way to do both.

**Decision.** Use **pure ASGI middleware** instead. `BaseHTTPMiddleware` runs the downstream
app in a separate task and pumps the response body through a memory-object stream — an extra
hop between every upstream chunk and the client, and muddied backpressure. On a gateway
whose entire product is first-token latency that is not a detail. The pure-ASGI form sees
the same three message types and adds nothing to the path.

Separately: **`configure_logging()` is called at startup.** Python's root level is
`WARNING`, so a request logger that merely exists emits nothing, and the only symptom is a
quiet container — a docstring promising structured logs above a stream of silence. The
`headroom` logger gets an explicit level (`HEADROOM_LOG_LEVEL`, default `INFO`), a stdout
handler emitting bare JSON, and `propagate = False` so uvicorn's root handlers do not print
every line a second time in a different format.

**Alternatives considered.** *`BaseHTTPMiddleware`* — fewer lines, measurable cost on the one
path that matters. *Creating the context inside each route* — misses everything that fails
before routing, which is exactly where a missing log line hurts. *Leaving logging to
uvicorn's config* — works only if every deployment remembers to pass one.

**Consequences.** The middleware manipulates raw ASGI messages, so it is coupled to those
shapes rather than to a friendly API — it is small and commented for that reason.
`ctx.complete()` is first-call-wins so the middleware's backstop cannot overwrite a diagnosis
the proxy already recorded; `tests/test_request_context.py` pins that.

---

## H-016 — The reasoning fixtures serialize non-ASCII literally, unlike every other fixture (Phase 1)

**Status**: accepted · **Date**: 2026-08-08

**Context.** The live vLLM smoke turned up a delta field no module under `headroom/` has
ever heard of (`reasoning_content`), carried to the client intact. Making that a keyless
fixture meant deciding how the mock serializes it — and `mock_scripts._dumps` uses
`json.dumps`'s default `ensure_ascii=True`, so every fixture in the repo ships its
non-ASCII pre-escaped.

That default quietly disarms the assertion. If the fixture's bytes contain no non-ASCII
in the first place, then a proxy that re-encodes the stream with `json.loads`/`json.dumps`
changes *nothing* about it — the escaped form survives an escaping round trip unchanged —
and a byte-equality test passes against a gateway that is actively corrupting bodies.
Worse, a 1-byte re-chop of an all-ASCII payload never splits a character, so the A4
"mid-UTF-8-sequence" claim is not actually exercised by the payload making it.

**Decision.** The reasoning fixtures serialize with `ensure_ascii=False`
(`mock_scripts._dumps_literal`), which is also what vLLM's FastAPI layer really sends. The
trace carries a literal `ö`, `—`, and `𝄞`, so the wire holds 2-, 3-, and 4-byte UTF-8
sequences. The non-streamed `OPENAI_REASONING_BODY` goes further and carries the *same*
character in both forms — literal and escaped — in one string, so a normalization is
caught whichever direction it runs. `_dumps` is left exactly as it was; this is a second
serializer beside it, not a change to the first (invariant 7).

**Alternatives considered.** *Reuse `_dumps` for consistency* — one serializer, and a
fixture that cannot detect the most likely real corruption; consistency bought at the cost
of the property being asserted. *Escape everything and test A4 elsewhere* — the existing
suite already does that, and the sabotage below shows it is not enough. *A raw byte
literal for the stream too* (as the tool-use fixture does) — correct but unreadable across
eighteen frames, and the builder has to stay parameterized so `reasoning_field`, the token
counts, and `finish_reason: "length"` can all vary.

**Consequences.** Two serializers live in `mock_scripts.py` and the difference between them
is load-bearing, so both are documented at the definition. This is now verified rather than
argued: with the proxy temporarily patched to rewrite one literal `ö` as its JSON escape on
the outbound path, `tests/test_reasoning_passthrough.py` fails four ways while
`test_streaming_passthrough` and `test_tool_blocks` both pass — 4 failed, 133 passed. The
new file detects a class of corruption the suite previously could not see. Any future
fixture meant to prove passthrough fidelity should use `_dumps_literal` for the same
reason; `_dumps` remains right for fixtures asserting event sequences rather than bytes.

---

## H-017 — Virtual keys: `hk_` + 256 bits, SHA-256 at rest, an 11-character display prefix (Phase 2)

**Status**: accepted · **Date**: 2026-08-08

**Context.** BUILD_PLAN Phase 2 says keys are `hk_...` strings, hashed at rest. It does not
say *which* hash, and the reflex answer — "passwords need bcrypt/argon2, so credentials
need bcrypt/argon2" — is wrong here for a reason worth writing down, because it will be
questioned by every reader who has been taught the reflex.

A password KDF exists to make guessing **low-entropy, human-chosen** inputs expensive. The
work factor buys time against a dictionary. There is no dictionary for a value drawn from
a CSPRNG: an attacker holding the entire `virtual_keys` table would have to enumerate the
keyspace, and no work factor moves 2^256 anywhere interesting. What a KDF *would* move is
the cost of every authenticated request on a gateway whose entire product is first-token
latency — tens of milliseconds, per request, forever, against a threat that does not exist.

There is a second, structural cost: a salted KDF cannot be looked up. Authentication would
need either a lookup id embedded in the key (a second secret-ish field to get right) or a
scan. A deterministic hash is a unique index.

**Decision.**

- **A key is `hk_` + `secrets.token_urlsafe(32)`** — 256 bits from the OS CSPRNG, 43
  urlsafe characters, 46 total. The `hk_` prefix makes a key recognisable in a log, a bug
  report, and a secret scanner's regex.
- **Stored form: `sha256(key)` hex, in a `UNIQUE`-indexed column.** Authentication is one
  indexed lookup, no per-row work, and `hashlib` is stdlib — no dependency added for
  security theatre.
- **`key_prefix` stores the first 11 characters** (`hk_` plus 8) in the clear, deliberately,
  so a key can be identified in a list and in a 403 message. That withholds 35 of the 43
  secret characters (~208 bits). It is a real disclosure and it is sized to be a
  meaningless one.
- **The plaintext exists in exactly one response**, `POST /admin/keys`, and is
  unrecoverable afterwards. The type system carries this rather than a reviewer:
  `KeyView` has no `key` field, `KeyCreated` is the single model that does, and it is
  returned by one route.
- **Scope matching is exact, with a trailing `*` as the only wildcard.** Empty scope means
  unrestricted.

**Alternatives considered.** *argon2id / bcrypt* — the reflex; costs latency on every
request and forfeits the index, buys nothing against a full-entropy secret. *HMAC-SHA256
under a server-side pepper* — genuinely better if the database can leak while the pepper
does not, and it introduces a second long-lived secret to provision, rotate, and lose;
against a 256-bit random key the marginal benefit is the ability to invalidate every key
at once, which revocation already does. *Storing no prefix at all* — strictly safer, and it
leaves an operator staring at a list of UUIDs during an incident. *Prefix-by-default scope
matching* (like the routing table's) — would silently make `mock-model-1` admit
`mock-model-10`; authorization is the wrong place for a helpful default.

**Consequences.** The security of every key rests on `secrets.token_urlsafe` and on the
`SECRET_BYTES = 32` constant, so both are asserted directly in
`tests/test_virtual_keys.py` — the argument above is void if the entropy is not really
there. The stored prefix is a documented exception to "nothing about the key is stored",
and `tests/test_key_secrecy.py` asserts that the *remainder* appears in no response, no log
record, no field of the store, and (on Postgres) no column of either table. If a future
phase ever needs to display more of a key, this entry is what it has to supersede.

---

## H-018 — Auth decisions are cached for 5 seconds, successes only (Phase 2)

**Status**: accepted · **Date**: 2026-08-08

**Context.** BUILD_PLAN's Phase 2 gate leaves the choice open and constrains the answer:
*"no cache of auth decisions beyond a short TTL, and the TTL is a documented number"*, and
*"a revoked key is dead on the very next request"*. Not caching at all is a legitimate
answer — an indexed lookup on a local Postgres is well under a millisecond, and Phase 8's
H2 target is a p50 gateway overhead under 50 ms, so a round trip would fit. But the lookup
happens before the first upstream byte on every single request, it is the one query the
gateway makes that the caller gains nothing from, and Phase 4 is about to add two more
stores to the admission path.

**Decision.** Cache, with the window drawn tightly and asymmetrically.

- **`AUTH_CACHE_TTL_S = 5.0`**, a named constant in `headroom/policy/auth.py`, quoted in
  this entry and in the README.
- **Successful lookups only.** A failure is never remembered. Caching "unknown" would make
  every freshly minted key dead for up to five seconds — which is the admin API's *first*
  user experience and a bug report reading "sometimes new keys do not work". The cost is
  that a flood of bogus keys reaches the database; that is a rate-limiting problem, and
  rate limiting is Phase 4.
- **Revocation invalidates in-process, immediately.** `DELETE /admin/keys/{id}`,
  `PATCH /admin/keys/{id}` (a narrowed scope has to bite now; a widened one is harmless to
  forget, and one invalidation covers both), and tenant deactivation all drop the affected
  entries before returning. So in the process that revoked, the window is **zero**.
- **The TTL is therefore the *cross-process* bound** — Phase 9 runs several Fargate tasks
  against one RDS and nothing else coordinates them. Stated exactly: *a revoked key is dead
  on the next request in the process that revoked it, and dead within 5 seconds everywhere
  else.*
- **Keyed by the SHA-256 hash, never the plaintext**, so the caller's secret never enters a
  long-lived structure. **Storing a `Principal`, which is frozen**, so a cached permission
  cannot be mutated by whoever holds it.
- **The clock is injected.** `AuthCache(clock=...)` lets the window be tested by advancing
  time rather than by sleeping through it.

**Alternatives considered.** *No cache at all* — simpler, defensible, and the entry would
have said so; rejected because the query is pure overhead on the latency path the project
is measured on, and 5 seconds of staleness on an authorization decision is a smaller cost
than a database round trip per request. *A longer TTL (30–60 s)* — better hit rate, and it
turns revocation into something an operator cannot trust during an incident. *Pub/sub
invalidation across processes* — the correct answer at a scale this is not at, and it
replaces a five-second window with a message bus to operate. *Caching negatives with a
shorter TTL* — two TTLs to explain, for a DoS surface Phase 4 addresses properly.

**Consequences.** The 5.0 is now a load-bearing published number: `tests/test_auth_cache.py`
asserts the constant itself and, separately, the behaviour at both edges of the window. A
deployment wanting the per-request-DB-hit configuration sets `ttl_s=0.0`, which disables the
cache entirely (also tested). Phase 4's budget reservations are **not** allowed to reuse
this cache: a stale authorization is a bounded risk, a stale balance is D-019's scar.

---

## H-019 — The root admin token: environment only, and unset means OFF (Phase 2)

**Status**: accepted · **Date**: 2026-08-08

**Context.** `/admin/tenants` and `/admin/keys` are the endpoints that create tenants and
mint credentials. They need a credential of their own, and BUILD_PLAN §0.2 invariant 3 says
no secret enters the repo, a compose file's committed env, or a task definition's plain
environment. The interesting question is not where the token comes from — it is what
happens when there **isn't** one.

**Decision.**

- **`HEADROOM_ADMIN_TOKEN`, read from the environment**, named (never valued) in
  `.env.example` and referenced through in `docker-compose.yml`. It is resolved once, at
  gateway construction, and held on the `Gateway` object — not re-read per request, and not
  a field any YAML could hold (the H-014 rule, one layer up).
- **Unset means the admin API is off.** Every `/admin` route answers **503** with
  `admin_api_disabled` and a message naming the variable. It never means "no token
  configured, so no check".
- **Compared with `secrets.compare_digest`**, so the check does not leak the token a byte
  at a time to a caller with a stopwatch.
- **A virtual key is never an admin token and vice versa.** They arrive in the same
  `Authorization` header and are validated by different code against different secrets;
  `tests/test_admin_api.py` presents each to the other's endpoint and expects 401.
- **`/admin` is exempt from virtual-key authentication.** The control plane has to stay
  reachable when every key is revoked — which is precisely the moment someone needs it.

**Alternatives considered.** *Unset means open* — the failure this decision exists to
prevent: a fully open tenant-and-key CRUD on the first deployment that forgets one line, and
silent about it. *Unset means the routes 404* — hides an operator's own misconfiguration
behind a plausible answer; 503 with the variable's name is the H-009 rule ("a 500 that names
the knob is not a generic 500") applied one status along. *Refusing to start without a
token* — would stop a mock-and-vLLM-only deployment from booting for a feature it does not
use, and would break CI's image smoke. *Per-admin accounts with roles* — a real answer for a
real product and scope-lust here; one root token, and the entry that widens it can supersede
this one.

**Consequences.** Every environment that wants the admin API must set one more variable, and
`make up` alone no longer gets you a working end-to-end demo — the README now spells out the
four-step version. The token is a single point of compromise with no rotation story yet;
rotating it today means restarting the gateway with a new value, which is acceptable for a
credential only the operator holds and which is worth revisiting before this is ever
multi-operator.

---

## H-020 — 401 before the body, 403 before the route (Phase 2)

**Status**: accepted · **Date**: 2026-08-08

**Context.** "401/403 semantics exact" is a Phase 2 requirement, and the statuses are the
easy half. The hard half is **ordering** — where in the pipeline each check runs — because
that is what decides how much a stranger can learn by probing, and it is invisible in a
status-code table.

**Decision.** The pipeline runs identify → read → scope, and each step is placed where it
is for a stated reason.

1. **Authenticate before the body is parsed.** An anonymous request with malformed JSON
   gets **401**, not 400. A gateway that told a stranger their JSON was wrong would be
   debugging requests for people it has not identified.
2. **401 is "we do not know you"**: missing (`missing_api_key`), unusable
   (`malformed_api_key`), never issued (`unknown_api_key`), revoked (`revoked_api_key`),
   or belonging to a switched-off tenant (`inactive_tenant`). Five distinct `reason`
   values, one status. The reason is for the operator reading a log; the status is all the
   stranger gets.
3. **Inactive tenant is 401, not 403.** The tenant is not being denied a resource; the
   tenant is not currently a tenant. Deactivation has to be as final as revoking every key,
   or it is not usable in an incident.
4. **403 is "we know exactly who you are, and no"**: `model_out_of_scope`,
   `provider_out_of_scope`. Returning 401 here would tell a correctly-configured client to
   go find a better credential, which it cannot.
5. **Model scope is checked before routing, so 403 beats 404.** An out-of-scope model
   answers 403 whether or not this deployment routes it — otherwise a key could enumerate
   the routing table by reading 403 against 404. Provider scope is checked last because it
   is the only one that needs the route resolved.
6. **A control-plane outage is 503** (`control_plane_unavailable`), extending H-009's table
   of invented statuses. The request was well formed and the gateway is correctly
   configured, so it is neither the caller's 4xx nor a permanent 500 — and a gateway that
   cannot authenticate must **not** fail open, which would hand every tenant's budget to
   whoever asked first.
7. **Error bodies are spoken in the caller's dialect** (H-009 unchanged): Anthropic's
   `authentication_error` / `permission_error`, OpenAI's equivalents, with the exact cause
   in `headroom.reason`.

**Alternatives considered.** *Parse the body first so the 400 is more helpful* — helpful to
the wrong audience. *One `auth_failed` reason for all five 401s* — loses the only
information an operator has when a tenant reports "it stopped working". *403 for an
inactive tenant* — reads as a scope problem and invites the client to retry with a
different model. *404 before scope* — leaks the routing table. *`WWW-Authenticate` on the
401* — RFC-correct and neither provider sends it, so it is omitted for wire compatibility
with the SDKs Headroom sits behind.

**Consequences.** The five 401 reasons and two 403 reasons are stable identifiers from
here: Phase 3 writes them to the ledger and Phase 7 charts them, so they are additive-only.
The ordering is asserted directly rather than left as a comment —
`tests/test_auth_matrix.py` checks that an anonymous malformed body is 401, and that an
out-of-scope model returns the same status and reason whether or not the model is routed.

---

## H-021 — One storage interface, two implementations, one contract suite (Phase 2)

**Status**: accepted · **Date**: 2026-08-08

**Context.** BUILD_PLAN §0.5 gives `core/` the storage *interfaces*, and the operator's
standing preference is "interfaces before implementations where a later phase will plug
in". Phase 2 is where that stops being a principle and costs something: the auth matrix,
the admin CRUD surface, and the revocation window are all **logic**, and binding their
tests to a running Postgres would mean a fresh clone silently runs a smaller suite than the
operator believes — the exact failure H-012 was written about.

**Decision.** `TenantStore` in `headroom/core/storage.py`, with two implementations:
`PostgresTenantStore` (`headroom/db/tenants.py`, what the gateway runs) and
`InMemoryTenantStore` (`headroom/db/memory.py`, what most tests run against).

The drift hazard is answered directly, and this is the load-bearing half of the decision:
**`tests/test_tenant_store.py` is one contract suite parametrised over both stores.** Every
behaviour asserted of the in-memory store is asserted of Postgres in the same run, or it is
not asserted at all. The Postgres parameter follows H-012 exactly — it skips when the
compose stack is down and `DATABASE_URL` was merely *inferred*, and it **fails** when
someone stated the database is there, which CI does.

Supporting choices:

- **`build_gateway` always constructs the Postgres store.** No configuration switch can
  select the in-memory one; a deployment cannot lose every tenant to a typo.
- **The pool is lazy** (`headroom/db/pool.py`). Building a gateway never opens a
  connection, so CI's `image` job still smokes `/healthz` with no database anywhere, and
  H-000's "`/healthz` is liveness only" survives contact with real dependencies.
- **Database errors are translated at the boundary**: a missing table becomes a
  `ConfigurationError` naming `make migrate`; anything about reachability becomes
  `ControlPlaneUnavailable` (503). Nothing above `headroom/db/` imports asyncpg.
- **The Postgres fixture truncates the two control-plane tables** before each test, so the
  contract assertions can be exact (`==`, not `in`) and identical for both stores. Stated
  plainly because it means the compose database is a test fixture.

**Alternatives considered.** *Postgres only, everything skips without it* — the honest
minimum, and it makes the keyless gate a fiction. *A mock store with hand-written stubs* —
what "second implementation" usually means, and it asserts nothing about the real one.
*testcontainers* — a container per test run, slower, and it does not remove the need for
the contract suite. *SQLite as the second implementation* — a third SQL dialect to keep
honest, for no benefit over a dict.

**Consequences.** Adding a store method now means adding it in three places (interface, two
implementations) and a contract test, which is the intended friction. Phase 3's ledger and
Phase 5's cache should follow this shape — the ledger especially, because Phase 8 reads it.
`InMemoryTenantStore` is production-shaped code that never runs in production and must stay
that way; if a later phase is tempted to make it selectable, that is a new entry, not a
config flag.

---

## H-022 — The control-plane schema: revocation is a timestamp, nothing is deleted (Phase 2)

**Status**: accepted · **Date**: 2026-08-08

**Context.** `migrations/0001_tenants_and_virtual_keys.sql` is the first real migration —
the file `migrations/README.md` has been describing conventions for since Phase 0. Its
shape is where a handful of small choices get locked in, because H-003 makes applied
migrations immutable: everything here is additive-only from now on.

**Decision.**

- **UUID primary keys** (`gen_random_uuid()`, built into Postgres 13+, no extension). Ids
  appear in URLs, log lines, and Phase 3's ledger; a sequential integer would let anyone
  holding one key id count the deployment's keys.
- **`revoked_at TIMESTAMPTZ` is the state.** `revoked_at IS NULL` means active. No boolean
  beside it, because a boolean and a timestamp maintained separately eventually disagree,
  and the one time anybody reads them is during an incident review.
- **Revocation is idempotent and keeps the first timestamp** —
  `SET revoked_at = COALESCE(revoked_at, now())`. Operators revoke twice; the review needs
  the first time.
- **Nothing is deleted.** `DELETE /admin/keys/{id}` revokes, `DELETE /admin/tenants/{id}`
  deactivates, and the foreign key is `ON DELETE RESTRICT` so the database enforces it
  rather than the application remembering to. Phase 3's ledger attributes every request to
  a key id and a tenant id forever; a row that vanishes turns a historical invoice into an
  orphan.
- **Scopes are `TEXT[] NOT NULL DEFAULT '{}'`.** Empty means unrestricted. Nullable columns
  would make "no restriction" and "restricted to nothing" the same value.
- **`tenants.name` is `UNIQUE`**, and the admin API answers 409. A second "acme" is a
  spend-attribution bug waiting for a quarter to end.
- **`key_hash` is `UNIQUE`** — a collision would be a catastrophe, and the constraint
  supplies the index every authenticated request uses.
- **Partial updates are `COALESCE($n, column)`**, one prepared statement per operation. No
  SQL is built by concatenation anywhere in `headroom/db/`.
- **`updated_at` is set by the statement that changes the row**, not by a trigger — a
  trigger is tidier and invisible in a file review, and this schema is small.

**Alternatives considered.** *`BIGSERIAL` ids* (enumerable), *`active BOOLEAN` on keys*
(two sources of truth), *hard deletes with `ON DELETE CASCADE`* (orphans the ledger,
silently), *a `key_scopes` join table* (normalised, and every read becomes a join for two
short lists that are always read together), *`JSONB` scopes* (no array operators, no reason).

**Consequences.** This file can never be edited (H-003); a change is `0002_*.sql`. Phase 3
adds the ledger with foreign keys onto both tables, which is why RESTRICT matters now
rather than later. The truncate-based test fixture (H-021) must be updated when Phase 3
adds tables that reference these.

---

## H-023 — Prices are a dated history; mock models are flat, deliberately (Phase 3)

**Status**: accepted · **Date**: 2026-08-08

**Context.** BUILD_PLAN Phase 3 says `config/models.yaml` carries dated price schedules
and that the D-017 lesson is "a founding feature here, not a bugfix". Backline's D-017
was a cost meter that kept billing at sticker prices after a vendor published new ones,
and the reason it could is structural: it had a *price* where it needed a *history*. So
the schema is settled first and the arithmetic second.

What the plan does not settle is three things this file had to decide: how a rate is
written down, where a history starts, and whether the models the test suite uses get
histories at all.

**Decision.**

- **A model has an ordered list of `(effective_from, usd_per_mtok_in,
  usd_per_mtok_out)` rows**, and a request resolves to the latest row whose date is on
  or before the request's own date. A price change is an **append**. It cannot reach
  backwards, because a row dated later was never a candidate.
- **Rates are quoted strings, and the loader refuses a float.** An unquoted `3.00` is a
  YAML float, and a float is the one representation of money that is wrong by
  construction. The refusal names the field and says what to write instead, so the rule
  is enforced by the parser rather than by whoever reviews the diff.
- **Mock models are FLAT: exactly one row, effective from the epoch.** This is a
  deliberate asymmetry with the real models and it is the load-bearing half of the
  decision. Every exact-cost assertion in the suite prices against these numbers; a
  second row with a later date would make those assertions start failing on the day it
  took effect, and a suite that goes red on a Tuesday for reasons nobody changed is a
  suite people learn to interrupt. `tests/test_prices.py` asserts the flatness rather
  than trusting it. Rates are $0.25/$1.25 per MTok, chosen so the canonical 11-in/7-out
  fixture lands on `$0.0000115` — a terminating decimal, so no expected cost anywhere in
  the suite needs a tolerance.
- **Real models start their history on 2026-08-08**, the day the rates were read off
  Anthropic's published pricing and written down. Earlier history is **not** modelled: a
  request dated before the first row is `unpriced_model` with a NULL cost. Headroom has
  never billed a request before that date, and inventing a start date is the same class
  of mistake as inventing a rate.
- **`claude-sonnet-5` ships with two rows, and the boundary is real.** Anthropic
  published it at an introductory $2/$10 per MTok **through 2026-08-31**, reverting to
  $3/$15. So the committed config contains a genuine vendor-published price boundary,
  and the identical request costs different money on either side of it. The D-017
  property is therefore exercised against reality, not only against a fixture.
- **The operator's vLLM models are priced at an honest zero**, stated rather than left
  to the unpriced fallback, so a `$0.00` row means "free" and not "we have no idea".
- **Matching is exact-first, then longest prefix** — the routing table's rule (H-013),
  which is what lets one `mock-` entry price a whole family.

**Alternatives considered.** *One current price per model with an `updated_at`* — the
D-017 design, restated. *Giving mock models dated tiers for symmetry* — symmetry bought
with a suite that breaks on a calendar day. *Back-dating the real models to the epoch so
nothing is ever unpriced* — a fabricated claim about what a request cost in 2024, which
is exactly the lie this file exists to prevent. *Numeric YAML rates with a float check
downstream* — the check would live one layer away from the mistake. *A flat file of
rates with no dates at all, deferring dating to a later phase* — the plan names dating as
the founding feature; deferring it would be building the thing that failed.

**Consequences.** `config/` now holds two files and both are loaded at startup (H-014's
split extended: routes are policy, prices are reference data). A model added to routing
and not to prices serves traffic and writes NULL-cost rows — loudly, via `cost_status`,
but it is a real operational trap and the README says so. The mock rates are now a
published number the suite depends on: `tests/test_prices.py` pins them in one place so
a change fails with a message about the rate rather than as arithmetic noise across a
dozen files. When Anthropic's introductory Sonnet 5 window closes on 2026-08-31, no code
changes — the second row is already there, which is the entire point.

---

## H-024 — A ledger row carries the price it was billed at (Phase 3)

**Status**: accepted · **Date**: 2026-08-08

**Context.** H-023 makes a price resolvable by date. That alone does not make a landed
cost safe: if a ledger row referenced its price rather than containing it, then editing
`config/models.yaml` — correcting a typo, adding a model, fixing a start date — would
silently re-bill every historical request that resolved through the edited entry. D-017
and its mirror image are the same mistake seen from two sides: treating the price as a
property of the *model* rather than of the *transaction*.

**Decision.** `migrations/0002_usage_ledger.sql` copies the applied rates into the row:
`price_effective_from`, `usd_per_mtok_in`, `usd_per_mtok_out`, alongside the computed
`usd_cost`. A row is an invoice line and is self-contained; nothing outside it is needed
to explain it, and nothing outside it can change it.

Supporting choices in the same migration:

- **Money is `NUMERIC`, never `DOUBLE PRECISION`.** `usd_cost` is `NUMERIC(24, 12)` — a
  millionth of a cent — sized so the smallest realistic charge is exact with room to
  spare: one output token at $1/MTok is `0.000001`, six places. Rates are
  `NUMERIC(20, 10)`. A float column would reintroduce at the last step the error the
  whole pipeline is arranged to avoid.
- **NULL and 0 are different facts**, and `cost_status` says which: `priced` (exact),
  `partial` (a bound — see H-026), `unpriced_model` (NULL), `usage_unknown` (NULL),
  `not_billable` (0, and that zero is a measurement). A meter that writes `0.00` for
  "we do not know" passes every arithmetic test ever written and is still wrong.
- **`request_id` is UNIQUE**, which is what makes the writer's retry safe (H-027).
- **Foreign keys are `ON DELETE RESTRICT`** onto `tenants` and `virtual_keys`, matching
  H-022. No cascades: a row that vanished would turn a historical invoice into an orphan.
- **`cache_disposition` and `failover_hops` ship empty**, for Phases 5 and 6. The shape
  of a ledger row stops changing after this migration (invariant 7).
- **`started_at` is the request's own arrival time** and is what the price resolves
  against; `created_at` is when the row was written. The gap between them is the
  delivery guarantee, made visible.

**Alternatives considered.** *A `price_id` foreign key onto a prices table* — normalised,
and it makes a landed cost mutable by an `UPDATE` somebody runs at 2 a.m. *Storing only
`usd_cost` and re-deriving the rates for display* — the cost survives, and "why did this
cost that" becomes unanswerable the moment prices move. *`DOUBLE PRECISION` for cost*
("it is only fractions of a cent") — fractions of a cent summed over a quarter is the
number a customer disputes. *Deriving cost at read time from tokens × current price* —
D-017 with extra steps.

**Consequences.** The row is wide, and deliberately: 33 columns, most of them nullable.
Re-pricing history is now impossible *by design*, which also means a genuine mispricing
can only be corrected by a documented, deliberate migration — the right amount of
friction for money. Phase 9's nightly rollup Lambda reads this table and inherits the
same guarantee for free.

---

## H-025 — Which requests get a ledger row, and what a failure costs (Phase 3)

**Status**: accepted · **Date**: 2026-08-08

**Context.** BUILD_PLAN Phase 3 says failed and errored requests are also rows, "zero or
partial cost as honesty dictates — decide and log the H-entry", because P8.H2 publishes
overhead percentiles and error accounting from this table. An error-free ledger would
make both meaningless. But "every request gets a row" and "every row is attributable"
cannot both be true, and the cost of a failure is not one answer.

**Decision.**

**Who gets a row.** Every request that **authenticated and named a model**. That is the
line, and it is drawn where it is because the ledger's entire job is attribution: a row
for a request that never identified itself has no tenant and no key, and `tenant_id` is
`NOT NULL` for exactly that reason. The 401 family is therefore *not* in the ledger — it
is in the structured log line, which already records all five reasons (H-020). A
malformed body from a known tenant is likewise not a row: it never named a model, so
there was never anything to price.

**What a failure costs**, decided from the **upstream status** rather than from the
outcome, because the outcome describes what the caller saw and the status describes what
a provider did:

| Situation | Cost | `cost_status` |
|---|---|---|
| Provider answered < 400 | priced from usage | `priced` / `partial` |
| Provider answered ≥ 400 (429, 5xx) | **0** | `not_billable` |
| Never reached a provider (unroutable, scope refusal, connect failure) | **0** | `not_billable` |
| **Timeout** — sent, no answer | **NULL** | `usage_unknown` |
| Stream cut / client disconnect after tokens flowed | **NULL** unless usage arrived | `usage_unknown` |

The timeout row is the one that earns the table. `ProviderTimeout` already documents the
honest position — "the request may well have been accepted and billed upstream; the
caller is told the truth (we do not know)" — and billing it at zero would be a
comfortable lie in the one place a lie compounds. A connection that never opened, by
contrast, provably generated nothing, so its zero is a measurement.

**Alternatives considered.** *Rows for 401s with a null tenant* — makes the attribution
column nullable, which makes every `GROUP BY tenant` in Phase 7 and every rollup in
Phase 9 quietly wrong-by-omission; and the log line already has them. *No rows for any
failure* — kills H2's error accounting and hides exactly the requests an operator is
looking for. *Billing a timeout at zero* — tidy, and an undercount that grows with
provider flakiness. *Estimating a cut stream's output from the bytes forwarded* — the
Phase 1 reasoning finding says content and billed tokens are not the same quantity;
estimating here would be the very inference this phase forbids.

**Consequences.** A tenant's ledger row count is *not* their request count — anonymous
401s are missing by design, and the README says so. `outcome`, `error_reason`, and the
five `cost_status` values are stable identifiers from here, because Phase 7 charts them
and Phase 8 reports on them. `unpriced_requests` is surfaced beside every total so a sum
can never quietly present itself as complete.

---

## H-026 — Prompt-cache tokens are recorded, not priced (Phase 3)

**Status**: accepted · **Date**: 2026-08-08

**Context.** Anthropic bills prompt-cache reads and cache writes as separate token
classes at rates that are neither the input rate nor the output rate. The phase brief
specifies a price row of `(effective_from, usd_per_mtok_in, usd_per_mtok_out)`, which has
nowhere to put them. Ignoring the usage block's cache fields entirely would leave a
request with heavy prompt caching **under-billed** on the Anthropic dialect (whose
`input_tokens` excludes cached tokens) and **over-billed** on the OpenAI dialect (whose
`prompt_tokens` includes them) — silently, in opposite directions.

**Decision.** Record the counts, do not price them, and **label the row**.
`cache_read_tokens` and `cache_write_tokens` are columns; when either is non-zero on a
model whose rate is not zero, `cost_status` is `partial` and the cost is documented as a
**lower bound rather than a total**. A free model cannot be under-billed, so a local vLLM
reporting prefix-cache hits stays `priced`.

This is invariant 6's instinct applied to money: *a truncated or partial thing is never
recorded as complete.* D-021's scar was an amputated answer billed as whole; this is the
same shape, one layer over.

**Alternatives considered.** *Add cache-tier rate fields now* — the complete answer, and
it widens a schema the brief specified, for a code path no keyless test can exercise and
no current Headroom traffic reaches; a well-marked seam is the better trade.
*Bill cache tokens at the input rate* — over-bills reads by ~10× and under-bills writes,
i.e. wrong twice with no label. *Ignore the fields entirely* — the silent version of the
same error, and the one that would never be noticed. *Refuse to price such a request at
all (`usage_unknown`)* — throws away a figure that is correct as far as it goes.

**Consequences.** A deployment using Anthropic prompt caching will see `partial` rows and
should read them as bounds. The fix is additive — new nullable rate columns and rows in
`config/models.yaml` — but it cannot repair rows already written (H-024), which is
correct and is the trade this entry accepts. The rule is asserted in `tests/test_cost.py`
and end to end in `tests/test_metering.py`.

---

## H-027 — The ledger write is fire-and-forget: at most once, in process (Phase 3)

**Status**: accepted · **Date**: 2026-08-08

**Context.** The phase brief is explicit: the ledger writer must be async enough that a
slow database never blocks or delays the stream to the client, *and* the delivery
guarantee must be decided and documented — specifically, what happens to a row if the
process dies mid-write. First-token latency is the product; a synchronous `INSERT` after
the last byte would put a Postgres round trip on the tail of every request, invisible in
a benchmark and ruinous under the concurrency Phase 8 measures.

**Decision.** A bounded `asyncio.Queue` and one background drain task
(`headroom/metering/writer.py`). `Meter.record` and `LedgerWriter.submit` are **ordinary
synchronous functions** — there is no `await` anywhere on the metering path, so there is
nothing for a slow database to suspend. The guarantee is stated plainly:

> **At most once, in process, best effort.** A row queued when the process dies is lost.

Four things make that a trade rather than a shrug:

1. **A graceful stop loses nothing.** `Gateway.aclose` closes the writer *first*, which
   drains the queue into the store before the store itself is closed. A deploy, a
   scale-in, or a `docker compose down` costs no rows; only a `SIGKILL` does.
2. **A lost row is reconstructible.** The same figures — tokens, cost, cost status,
   timings — go out on the structured request log line, which is written **before** the
   row is queued and lands in the container's stdout. That is why Phase 3 grows the log
   line rather than treating it as superseded by the ledger.
3. **The write is idempotent** (`ON CONFLICT (request_id) DO NOTHING`, H-024), so a
   retry after a crash can never double-bill.
4. **Backpressure is a counted drop, not an unbounded queue.** At 10,000 pending rows
   the writer discards and increments `dropped`, with a JSON warning naming the request
   id. An unbounded queue would trade a reporting gap for an out-of-memory kill, which
   takes the gateway down with it — the ledger's job is not worth the gateway's life. A
   failing store likewise does not kill the drain task; the next request's row is still
   worth having.

**Phase 4 is explicitly not allowed to reuse this path.** A stale ledger row is a
reporting gap; a lost budget reservation is D-019's scar. Budgets settle synchronously on
DynamoDB conditional writes, in a `finally`, and this entry exists partly to make that
distinction impossible to blur later.

**Alternatives considered.** *Synchronous write before responding* — durable, and it
makes the database's p99 the gateway's p99. *A write-ahead log or on-disk spool* — real
durability, real operational surface (a second store to size, rotate, and recover), for
rows a log line already carries. *Unbounded queue* — trades a bounded reporting gap for
an unbounded memory one. *Batched inserts* — a worthwhile optimisation and a strictly
larger loss window per crash; deferrable, and deferred. *Fire-and-forget with a task per
row* — no backpressure signal at all, and unbounded task creation under load.

**Consequences.** `writer.dropped` and `writer.failed` are numbers worth alerting on in
Phase 9; non-zero means the ledger is now an undercount. Tests must `await
writer.drain()` before asserting a row exists — `GatewayHarness.ledger_row` does it, so
no test sleeps and hopes. The worker starts lazily on first submit, for the same reason
the connection pool does: building a gateway must not require a running event loop or a
reachable database (H-021's lazy-pool rule, one module over).

---

## H-028 — The gateway does not inject `stream_options` to make a request meterable (Phase 3)

**Status**: accepted · **Date**: 2026-08-08

**Context.** An OpenAI-dialect **streamed** request carries token counts only when the
caller set `stream_options: {"include_usage": true}`. Without it, no usage chunk is ever
sent and the response is unmeterable. This is the one gap in Phase 3's coverage:
Anthropic reports usage in both modes, and non-streamed OpenAI always carries it. The
phase brief names the choice explicitly — inject the option upstream and strip the extra
chunk downstream, **or** record the tokens as unknown — and constrains it: whatever is
chosen, content equality (A4/A5) must survive it.

**Decision.** **Do not inject.** A request without usage is metered as `usage_unknown`
with a NULL cost, and the gap is reported rather than closed.

The reason is that injection is not a small change; it is the reversal of the property
the whole proxy is built on. Injecting means parsing the caller's JSON body, adding a
field, and **re-serializing it** — and H-007's design, the A5 tool-block guarantee, and
H-016's sabotage-proven fidelity all rest on the fact that *no code exists which could
rebuild a request body*. Stripping the extra chunk on the way back is the same violation
on the response side: it would turn the SSE observer from a tap into a filter. Trading
that for a token count on a minority of requests is a bad trade, and it is one whose
downside is invisible (a re-escaped character in someone else's tool call) while its
upside is a number on a dashboard.

The honest alternative is also the more useful one: the dashboard can show *how many*
requests could not be metered (`unpriced_requests` per total), which is actionable — the
caller adds one field and the gap closes at the source, permanently, for their traffic.

**Alternatives considered.** *Inject and strip* — closes the gap, forfeits the fidelity
invariant. *Inject only when the body is already being parsed anyway* — the proxy parses
a shallow copy and **discards it**; there is no "already". *Estimate tokens from the
forwarded content* — forbidden by this phase's founding observation: 11 visible
characters, 63 billed tokens. *Make injection a per-tenant opt-in flag* — a config
switch that turns off the repo's central guarantee, which is worse than either choice
made outright; if a future phase wants it, it supersedes this entry rather than hiding
behind a default.

**Consequences.** OpenAI-dialect streamed traffic from clients that do not ask for usage
is unmetered, visibly. The README documents the one-line fix for callers. Because the
gate's "usage-injection fidelity" clause is conditional on choosing injection, the proof
obligation here is the inverse and is met directly:
`tests/test_metering.py::test_the_gateway_does_not_add_stream_options_to_the_callers_body`
asserts the provider received the caller's bytes unchanged, and
`test_metering_a_stream_does_not_disturb_one_byte_of_it` asserts the response is
byte-identical to the mock's output while being metered.

---

## H-029 — A live smoke provisions its own identity, in the real control plane (Phase 3, addendum)

**Status**: accepted · **Date**: 2026-08-09

**Context.** Phase 2 made every `/v1/*` request require a virtual key. The keyless suite
moved onto the authenticated path in that PR — `GatewayHarness.post` presents a key by
default — but `tests/test_live_smoke.py` was not touched, because nothing runs it: the
`live` marker deselects it from every CI job and from every `make test`. Both smokes
therefore returned 401 `missing_api_key` from the moment PR-2 merged, and the breakage
surfaced on 2026-08-09, on the first live run after it, a phase and a half later.

Fixing the 401 is one header. The question this entry decides is *where the header's
value comes from*, and the options are not equivalent: a key pasted into the operator's
`.env` is a manual setup step that will rot the same way (a key revoked during an
incident, a volume wiped by `docker compose down -v`), and an in-memory control plane
built inside the test is self-contained but throws away the row.

**Decision.** **Each live smoke provisions its own tenant and key, through the real
`TenantStore`, against the compose Postgres.** `tests/support/live.py` creates — or
reuses — a tenant named `live-smoke`, mints an unrestricted key, drives the request with
it, asserts the resulting ledger row is attributed to that tenant, prints the request
id, and revokes the key on the way out.

Three things fall out of that, and each was the reason for it:

* **The operator's setup does not grow.** `make up` plus `ANTHROPIC_API_KEY` /
  `VLLM_BASE_URL`, exactly as before. Migrations are applied by the helper, idempotently,
  so a smoke on a fresh volume cannot fail on a missing table *after* spending the money.
* **The row outlives the process.** The whole point of a paid smoke is to compare
  Headroom's accounting against the provider's own, and the phase log's verification step
  is a `curl` at `/admin/usage/<request-id>` on the running container. In-memory stores
  would make that impossible, so the smoke uses the stores the gateway actually ships
  with — the first test in the project that does.
* **The tenant is stable, the key never is.** One name to filter the ledger by, run after
  run; a fresh key each time because a plaintext key exists exactly once, in the response
  that created it (H-017), and this store never held it. Revoked on exit: it has served
  its one request, and a credential nobody can reproduce is tidier dead than alive.

**Alternatives considered.** *A key in `.env`* — a manual step that rots silently, and
the failure it produces (401 on a paid run) is the one this entry exists to remove.
*In-memory stores inside the test* — self-contained and unverifiable; deletes the
artefact. *Provision through `POST /admin/keys` rather than the store* — the same result
through one more surface, but it would require `HEADROOM_ADMIN_TOKEN` to be set for a
smoke that otherwise needs no admin credential, which is exactly the kind of extra setup
step being removed. *Leave the key active* — no benefit; its plaintext is gone when the
process exits.

**Consequences.** The live smokes now require a reachable control plane, handled by
H-012's rule (skip when the endpoint was inferred and nothing is listening, fail when
someone stated it was there) rather than by a new one. They leave one revoked key per
run under a permanent `live-smoke` tenant, which is the intended paper trail. And the
`make test` interaction is now documented rather than discovered: the Postgres half of
the tenant-store contract suite runs `TRUNCATE virtual_keys, tenants CASCADE`, and
`usage_ledger` references both, so a `make test` between a live smoke and its `curl`
takes the row with it — the smoke says so on the terminal, in the line after the id.

The mitigation for the *class* of failure is separate and deliberate, because the
obvious one does not apply: import-time bitrot was never the gap. `-m "not live"`
deselects **after** collection, so every default run already imports the live module and
an `ImportError` there fails the suite. What no keyless run could see was *behaviour* —
whether the request the smoke builds still authenticates. So
`tests/test_live_smoke_wiring.py` drives the smokes' own provisioning helper through the
real `Authenticator` on every CI run, and asserts the sabotage (a smoke that sends no
key) is refused with `MissingCredential`. It cannot prove the live smokes pass; it proves
the credential they present is one the gateway accepts, which is the thing that broke.

---

## H-030 — Budgets are integer picodollars on one DynamoDB item (Phase 4)

**Status**: accepted · **Date**: 2026-08-09

**Context.** BUILD_PLAN L2 puts budget reservations on DynamoDB conditional writes, and
the phase brief names the storage question directly: *"Decimal end to end; DynamoDB
stores amounts as strings or cent-integers — decide, justify (DynamoDB Number precision
is a real concern)."* Underneath it sits a harder constraint the brief does not state,
because it is only discovered by trying to write the condition: **DynamoDB conditions do
no arithmetic.** A `ConditionExpression` compares an attribute to a value. There is no
way to express `spent + reserved + estimate <= budget` in one.

**Decision.** Three things, and the third falls out of the first two.

- **The unit is an integer count of picodollars** (1e-12 USD). Not `Decimal`, not a
  string. Strings cannot be added by an `UpdateExpression` at all, which ends that
  option immediately — an atomic increment is the whole mechanism. `Decimal` would
  work: DynamoDB Numbers carry 38 significant digits and boto3 maps them to `Decimal`
  in both directions. It is rejected anyway, because the atomicity argument this phase
  makes rests entirely on *"add this, and only if the result still fits"*, and integers
  are the only numeric domain where that sentence needs no footnote about decimal
  contexts, `Inexact` traps, or which library rounded what. Verified at the gate: 38
  digits accepted, 39 rejected with `ValidationException`. A $1,000,000 cap is 19
  digits.
- **1e-12 is not an arbitrary scale.** It is exactly `metering.cost.USD_QUANTUM`, the
  ledger's own `NUMERIC(24, 12)` precision, so `Decimal → int → Decimal` is lossless
  for every value the meter can produce and the two systems can be compared to the last
  digit. `tests/test_budget_store.py` asserts that equality rather than trusting it. An
  estimate converts with `ROUND_CEILING` (a hold that rounded *down* would admit one
  request too many at the boundary); everything else is already exact at this scale.
- **`remaining` is a stored attribute, not a derived one.** Since the condition cannot
  compute `budget - spent - reserved`, the difference is maintained as an attribute and
  the condition is `remaining_picos >= :estimate`. The item shape is downstream of what
  a condition can express, and this is the line that makes admission a single atomic
  operation.

The bookkeeping identity `remaining == budget - spent - reserved` is therefore
load-bearing rather than incidental. Every mutation moves `remaining` and its components
in the *same* single-item update, so it can only be broken by a wrong expression — and
the contract suite checks it after every operation, against both implementations.

Two findings from the gate belong here because they are facts about the emulator rather
than about the design:

- **`scope` is a DynamoDB reserved word**, and `attribute_exists(scope)` fails with
  `ValidationException`. The partition key is `scope_id`.
- **DynamoDB Local rejects an access key id containing a hyphen** with
  `UnrecognizedClientException: The Access Key ID or security token is invalid`. The
  first version of the emulator credential was `headroom-local`, and every
  DynamoDB-backed test in the suite failed with an error that reads exactly like a real
  AWS authentication failure and sends you looking in entirely the wrong place. It is
  now `headroomlocal`, and `tests/test_dynamo_client.py` pins both the string and the
  container's acceptance of it.

**Alternatives considered.** *Cent-integers* (1e-2) — the brief's own suggestion, and far
too coarse: the canonical mock reply costs $0.0000115 and would round to zero, which
would make every keyless cost assertion in the suite meaningless. *Micro-dollars* (1e-6)
— the same figure lands on 11.5, still not an integer. *`Decimal` through boto3's type
serializer* — defensible, and it puts a rounding context in the middle of the one
argument that has to be airtight. *Strings* — no atomic arithmetic, so no gate at all.
*A separate reservations table* — see H-033.

**Consequences.** Every amount crossing the store boundary is converted at exactly two
functions (`to_picos` / `from_picos`) and nowhere else. The scale is now coupled to
`USD_QUANTUM`: changing the ledger's precision without changing this would be a silent
truncation, which is why the equality is asserted rather than assumed. And `remaining`
may go **negative** — not a bug but the recorded consequence of a request that overran
its estimate (H-031), with the arithmetic staying exact through it.

---

## H-031 — What a hold settles at, and the one place the budget and the invoice disagree (Phase 4)

**Status**: accepted · **Date**: 2026-08-09

**Context.** The phase brief: *"On failure/timeout/cut: RELEASE or settle-partial per the
honest-cost semantics established in H-025/H-026 — decide, log, test."* H-025 already
decided what a failure *costs*: an upstream 4xx/5xx and a request that never reached a
provider cost 0; a timeout costs NULL because it may well have been billed by someone we
cannot ask; a cut stream costs NULL unless its usage arrived. The budget cannot simply
inherit that table, because one of its entries is not a number. A ledger row can say
"unknown". A counter cannot.

**Decision.** The settlement is a function of the meter's `cost_status`:

| `cost_status` | settles at | why |
|---|---|---|
| `priced` | the actual cost | known exactly |
| `partial` | the actual cost | a lower bound, labelled as one on the row (H-026); the budget inherits the same caveat rather than inventing a rate for cache tiers nobody can price |
| `not_billable` | **$0**, released | no model ran — an upstream error, an unroutable model, a scope refusal |
| `unpriced_model` | **$0**, released | there was no price to reserve against either; the estimate was $0 |
| `usage_unknown` | **the estimate** | a model ran and we cannot ask what it charged |

The last row is the decision. A timeout, a mid-stream cut, a client that hung up, an
OpenAI-dialect stream whose caller never asked for usage (H-028) — in every one of them a
provider was reached and generated something. Releasing the hold would be a cheerful
guess that it cost nothing; the only defensible number is the bound already reserved.

**So the budget and the ledger deliberately disagree on exactly these requests**, and the
disagreement is the honest one: `usd_cost` is NULL because a ledger row is an invoice
line and states facts, while `budget_settled_usd` equals the reservation because a budget
is a guard rail and states bounds. Both are on the same row, so the difference is visible
rather than buried.

A **stranded** hold — one whose process died and never settled at all — is the opposite
case, and is **released, not charged** (H-032's sweep). The distinction is what we know:
an unknown *cost* on a request we watched reach a provider is bounded above by its
estimate; an unknown *outcome* on a request we lost track of is not evidence of anything,
and charging on suspicion would let one restart under load quietly eat a tenant's month.
The ledger would not corroborate such a charge either, since a lost request has no row.

**Alternatives considered.** *Release everything unknown* — matches the ledger exactly,
and undercounts every timeout, which is the failure mode that grows with provider
flakiness. *Charge stranded holds too* — symmetrical with the above and much worse: a
`SIGKILL` under load would bill every in-flight tenant for requests that may never have
happened, with no row to explain it. *Settle `partial` at the estimate* — over-counts; the
recorded figure is a bound in the other direction and the row already says so. *A separate
"provisional spend" counter* — a third number to explain, for a distinction `cost_status`
already carries.

**Consequences.** `budget_settled_usd` is not always equal to `usd_cost`, and a reader
comparing the two columns will find rows where they differ — that is intended, and this
entry is what explains it. A tenant with a flaky provider is charged its estimates against
its cap while its invoice says "unknown", which is the conservative direction for a guard
rail and the honest direction for an invoice. Phase 7's dashboard should show both.

---

## H-032 — 402, no `retry-after`, and a sweep on the refusal path (Phase 4)

**Status**: accepted · **Date**: 2026-08-09

**Context.** Two questions the brief leaves open and calls out by name: *"pick the honest
status code and error shape per dialect"*, and *"Reservations must not leak: a crashed
process must not strand reserved budget forever. Decide the mechanism (TTL on reservation
records, sweeper, or settle-on-read)."*

**Decision — the status is 402 Payment Required, in both dialects.**

- **429 is the tempting answer and it is wrong.** It means *slow down*, and every SDK in
  the world answers it by retrying with backoff. A budget refusal does not heal with time
  inside its window, so a 429 would convert one refused request into a retry storm against
  the single item the gate serialises on — the failure this phase exists to prevent,
  arriving through the front door.
- **403 is defensible and loses information.** It is already this gateway's answer for
  "your key is not scoped to that" (H-020), and an operator needs "out of money" and "out
  of scope" to be different bars on a chart.
- **402 says what happened**, no SDK retries it automatically, and each dialect renders it
  in its own vocabulary for insufficient funds: Anthropic's `billing_error`, OpenAI's
  `insufficient_quota`. Both are real values those SDKs already know, which keeps H-009's
  rule intact — a gateway is a bad place to coin vocabulary, and the exact cause travels
  in `headroom.reason` as `budget_exceeded`.
- **No `retry-after`.** The honest value would be "when your window rolls", which for a
  lifetime budget is never, and a header that says *retry* invites the retry this decision
  exists to discourage. The window's reset and the tenant's own figures go in the message
  instead, where a developer can act on them.
- **The check runs after the scope checks and before `provider.open`.** After scope, so a
  key reaching past its permissions is told that rather than sent chasing a balance.
  Before the upstream, so a refusal provably costs nothing — asserted against the
  MockProvider's own record of what it was handed, not reasoned about.

**Decision — leaks are closed by an expiry plus three sweep triggers.**

Every hold carries `expires_at = now + 900s` (`RESERVATION_TTL_S`). Fifteen minutes is
comfortably longer than any single model call and short enough that a `SIGKILL` does not
encumber a cap for the rest of the month. It is an *upper bound on a leak*, not a request
timeout: a live request that outlives it still settles, because a settlement and a sweep
are conditioned on the same hold and only one of them can win.

Three triggers, and the first is the one that matters:

1. **On refusal, before refusing.** When the condition fails, the item comes back with the
   failure (`ReturnValuesOnConditionCheckFailure`, verified working on DynamoDB Local), so
   expired holds are released and the write retried **without a second read**. A dead
   process's hold can therefore never be the reason a live request is turned away — which
   a background timer would only fix eventually, and only if it happened to be running.
2. **On an admin read.** `GET /admin/budgets/{tenant}` sweeps before reporting, so the
   `reserved` figure an operator sees during an incident is live rather than inflated by
   processes that died. A GET with a side effect, taken deliberately: releasing an
   *already expired* hold changes nothing live and is idempotent, and the alternative is a
   number that is quietly wrong exactly when somebody is relying on it. The list route
   does not sweep — a `Scan` that wrote to every item it read would be a surprising thing
   for a listing to do.
3. **`sweep_expired`**, explicitly, for tests and for Phase 9's scheduled runs.

No background task is load-bearing, which is why none is started. DynamoDB's own TTL
feature is deliberately not used for this: it deletes an item, and deleting a reservation
record without decrementing the counter would strand the budget permanently *and* destroy
the evidence.

**Alternatives considered.** *A periodic in-process sweeper* — more moving parts, and it
fixes late what trigger 1 fixes immediately. *DynamoDB TTL on reservation items* — see
above; it is a garbage collector, not a compensating transaction. *No expiry at all,
relying on settlement in a `finally`* — a `finally` does not run through a `SIGKILL`, and
that is the entire scenario. *Charging expired holds instead of releasing them* — see
H-031.

**Consequences.** `budget_exceeded`, and the `no_budget` / `reserved` / `exceeded` status
values, are stable identifiers from here, because the ledger stores them and Phase 7 will
chart them. `expired_releases` and `expired_released_picos` are counters worth alarming on
in Phase 9: non-zero means requests are dying between admission and settlement.

---

## H-033 — Tenant scope only, and the window is the calendar month (Phase 4)

**Status**: accepted · **Date**: 2026-08-09

**Context.** The brief asks two scoping questions: *"per-tenant budgets (and optionally
per-key — decide and log)"*, and *"support at least: total/lifetime and a rolling or
calendar period — decide the window semantics, keep it simple, log the H-entry."*

**Decision — tenant scope only, with the shape for more already in place.**

A budget attaches to a `BudgetScope(kind, id)`, and the only kind Phase 4 enforces is
`tenant`. Per-key budgets are not a matter of adding a row: enforcing two caps on one
request means taking **two holds**, and two conditional writes are not one atomic
operation. Either the second failure has to release the first — a compensating action,
which is a new home for exactly the class of bug this phase exists to eliminate — or both
scopes live in one item, which collapses them back together. Neither is a small change,
and doing it badly would be D-019 with extra steps.

So the scope is a value with a `kind` from the first line, the store is keyed by
`tenant#<uuid>`, and a later phase adds a kind rather than reshaping the table.

**Decision — `monthly` (calendar, UTC) and `total` (lifetime).**

- **Calendar, not rolling.** A rolling 30-day window cannot be a counter: answering "what
  has this tenant spent in the last 30 days" means summing history on every admission,
  which is a query and not a conditional write. This entire phase exists because the
  answer has to be one atomic operation. A calendar month is also how every provider
  invoices and how an operator states a cap.
- **UTC, resolved from the request's own `started_at`** — the same rule the dated price
  schedule follows (H-023). Wall-clock "now" would let a queued settlement or a replayed
  fixture land in the wrong month.
- **There is no reset job.** The item carries `window_id` and `window_expires_at`, and
  `window_expires_at > :now` is part of the admission condition — so the first request of
  a new month fails the condition, and the failure path rolls the counters with a
  compare-and-set on the old `window_id`. Exactly one racer wins the roll; the rest retry
  the ordinary path. A lifetime budget uses a far-future sentinel, so one expression
  serves both window types with no `OR`.
- **A request in flight across a boundary loses its hold to the roll**, and its settlement
  becomes a no-op. Its cost is still in the ledger, which is the invoice; the gate's
  counter for the new month starts at what the new month has spent, which is the only
  number it can honestly hold.

**Alternatives considered.** *Per-key budgets now* — see above; a distributed-transaction
problem wearing a small feature's clothes. *A rolling window* — not expressible as a
counter. *A `daily` window as well* — a third window to test, for a cap nobody asked for.
*A reset job or a scheduled Lambda* — infrastructure to make a key change, and one more
thing that can fail silently on the first of the month.

**Consequences.** `/admin/budgets` reports `window` and `window_id`, and a monthly budget
read on the first of a month reports the *new* window's counters before any request has
rolled them — because that is what the next request will see. Changing a budget's window
type resets its counters, deliberately: a monthly cap and a lifetime cap count different
things, and carrying a total across the change would answer neither question.

---

## H-034 — The estimate: max_tokens, the body's own size, and a stated blind spot (Phase 4)

**Status**: accepted · **Date**: 2026-08-09

**Context.** A reservation-based gate is exactly as trustworthy as the bound it reserves.
If an estimate can be lower than the cost that follows it, then "settled spend never
exceeds the budget" is a hope rather than a property. The brief specifies the shape —
*"from max_tokens (or a documented default when absent) at the model's current dated price
— deliberately conservative; document the formula"* — and leaves the prompt side
unmentioned.

**Decision.**

```
input_tokens  = min(ceil(len(request_body) / 3), context_window)
output_tokens = max_tokens from the body, else 4096
usd           = input_tokens * rate_in / 1e6 + output_tokens * rate_out / 1e6
```

resolved at the price in effect on the request's own date (H-023).

- **The prompt half is included, going beyond the letter of the brief.** Generated tokens
  dominate the *rate* (output runs 4–5× input) but prompt tokens dominate the *count*: a
  long-context request with `max_tokens: 64` can cost far more on its prompt than on its
  answer, and an output-only estimate would wave it straight through a nearly-exhausted
  cap. A bound that omits most of the cost is not a bound.
- **Three bytes per token, not four.** English prose runs about four bytes to the token,
  and the body carries JSON scaffolding the model never sees, so this over-counts by
  roughly a third — the direction that refuses a request rather than the direction that
  lets spend past. The failure modes are asymmetric: an over-estimate produces a visible
  402 the tenant reports immediately; an under-estimate produces an invoice nobody reads
  until later.
- **Capped at the model's `context_window`** when `config/models.yaml` states one. That is
  a real ceiling rather than another heuristic — more input tokens than the window cannot
  exist.
- **4096 when the caller states no ceiling.** Only reachable on the OpenAI dialect (the
  Messages API requires `max_tokens`), and it is a documented assumption rather than a
  guarantee: a model that generates more settles for more, and the overshoot eats the next
  request's headroom (H-030, H-031).
- **An unpriced model estimates $0 and says so.** H-023 refuses to invent a rate, and a
  gate cannot bound an unknown. Such a request is admitted, and its ledger row is
  `unpriced_model` with a NULL cost — the same visible gap `/admin/usage/totals` already
  publishes as `unpriced_requests`. **This is a real operational trap and it is stated
  rather than hidden:** a model added to `config/routing.yaml` and forgotten in
  `config/models.yaml` is invisible to both the invoice and the cap. The fix is the same in
  both cases — price the model — and refusing instead would take a tenant down for a
  config slip.

**The blind spot, named.** The byte heuristic is worst for a request carrying a base64
image, where megabytes of body correspond to a few thousand image tokens. Such a request is
over-reserved, and can be refused against a budget it would in fact have fitted. The
context-window cap bounds the damage; removing the heuristic entirely would mean tokenizing
every request in the gateway, on the first-token path, for a number the settlement corrects
milliseconds later.

**Alternatives considered.** *Output-only, per the brief's letter* — see above. *Tokenizing
the prompt properly* — a per-request CPU cost on the latency path this project publishes
numbers about, and a second tokenizer to keep in sync with two providers'. *Reserving the
model's whole context window* — maximally conservative and useless: at 200k tokens it would
refuse nearly everything. *Refusing unpriced models* — turns a config omission into an
outage.

**Consequences.** `EST_BYTES_PER_TOKEN` and `DEFAULT_MAX_OUTPUT_TOKENS` are published
numbers the suite depends on; `tests/test_budget_estimate.py` checks the formula term by
term against arithmetic done on paper, and asserts the bound really is above what the
canonical fixture costs. Because the estimate is conservative, the stampede's headline
claim holds strictly — and the honest statement of it is: **settled spend cannot exceed the
cap while the estimate bounds the actual, and where it does not, the overshoot is recorded
and the next request is refused.**

---

## H-035 — A token bucket is stored as a time, not as a count (Phase 4b)

**Status**: accepted · **Date**: 2026-08-09

**Context.** BUILD_PLAN Phase 4: *"Token-bucket rate limits (requests/min and tokens/min
per key and per tenant) … enforced on DynamoDB conditional writes (A1)."* The words doing
the work are **enforced on conditional writes**, and they turn out to rule out the way
almost every token bucket in the wild is built.

The obvious item is `tokens` plus `refilled_at`. Admission then needs

```
min(capacity, tokens + (now - refilled_at) * rate) - cost >= 0
```

evaluated at write time — and a DynamoDB `ConditionExpression` compares an attribute to a
value and does **no arithmetic**. So a stored count can only be checked by reading it,
refilling it in application code, deciding, and writing the result back. That is Backline's
**D-019** exactly, in a different noun, and `tests/test_rate_limit_hammer.py` sabotage A is
that implementation failing the hammer by 8x.

**Decision — the bucket is one number: `tat`, the moment it will next be full.**

The GCRA formulation (the leaky-bucket dual used by traffic shapers, and by every rate
limiter that has to be atomic). With `T` the emission interval and `D = T * limit` the
delay tolerance:

```
available at now   = clamp((now + D - tat) / T, 0, limit)      -- derived, never stored
admit cost c       iff tat <= now + D - c*T                    -- a bare comparison
on admit           tat := max(tat, now) + c*T
```

The condition is an attribute compared to a value, which is the only kind DynamoDB has.
Admission is therefore one conditional write, with nothing read and nothing to be stale:

```
ConditionExpression: tat > :now AND tat <= :now + D - :charge
UpdateExpression:    SET tat = tat + :charge
```

**Decision — `max(tat, now)` is recovered with a second conditional write, not a read.**

That clamp is the one term the expression language cannot express, and dropping it is not
cosmetic: without it an idle bucket accumulates unbounded credit, and a bucket untouched
for an hour admits an hour of traffic in one burst (sabotage C measures it: **300 requests
against a five-per-minute limit**). So there are two mutually exclusive branches — *hot*
(`tat > now`, add to it) and *cold* (`tat <= now`, or no item at all, set it to
`now + charge`) — each atomic on its own. Which one to attempt is learned from the item a
failed condition hands back via `ReturnValuesOnConditionCheckFailure`, never from a
separate read. Hot is attempted first, so under load — the only time a limiter's cost
matters — one write is all that happens; an idle bucket costs two, and an idle bucket is by
definition not busy.

**Decision — nanoseconds, integers, and the interval rounds up.** `T = ceil(60e9 / limit)`.
Epoch nanoseconds is a 19-digit integer, comfortably inside DynamoDB's 38 significant
digits even after `+ D`. Rounding up makes the limiter fractionally *stricter* than nominal
— four parts in 10^11 at `limit = 7` — which is the direction the whole phase errs in: a
limiter that leaks is not a limiter.

**Decision — a second table, `headroom_buckets`, carrying a TTL attribute.** Not more items
in the budgets table: the two have opposite retention rules. A budget item must never be
garbage-collected; a bucket item is *safe* to reap, because **an absent bucket and a full
bucket are the same state** to the cold branch. That is also why DynamoDB TTL is right here
and was wrong for reservations — H-032 rejects it there, because deleting a reservation
record without decrementing its counter would strand the budget *and* destroy the evidence.
The `expires_at` attribute is written on every consumption; *enabling* TTL on the table is
Terraform's job in Phase 9, and nothing here depends on the reaper ever running.

**Alternatives considered.** *Stored count plus `refilled_at`* — see above; it is the bug.
*A fixed-window counter* (`SET used = used + :cost` conditioned on the count) — genuinely
atomic, genuinely simple, and it admits **twice the limit across every minute boundary**;
sabotage B measures it at 10 against a limit of 5, and it *passes the hammer*, which is why
it survives review everywhere it is deployed. *A sliding-window log* — a list per bucket,
unbounded item growth, and no conditional expression that can evaluate it. *A
compare-and-set retry loop on a value we read* — correct, but it is a read-then-write with
a condition bolted on, and the phase's whole claim is that there is no read to race.
*Redis* — the better-known answer, and BUILD_PLAN L2 already argues why not: conditional
writes are the correct primitive here and a better interview story than "I used Redis".

**Consequences.** `tat` is the schema — the whole bucket, in one attribute — so a later
phase wanting per-bucket observability has to derive it rather than read it off. A limit
change reprices the future without resetting anything, because `tat` is an absolute time
that means the same thing under any limit. And a bucket's capacity *measured in time* is
always exactly one window whatever the limit is; only the price of a unit changes. That is
counter-intuitive enough that `tests/test_rate_limit_store.py` pins it.

---

## H-036 — Both scopes, four buckets, and no refund (Phase 4b)

**Status**: accepted · **Date**: 2026-08-09

**Context.** BUILD_PLAN says *"per key and per tenant"* and *"requests/min and tokens/min"*,
which is four buckets for one request. H-033 deferred per-key **budgets** on the grounds
that two caps mean two reservations and a compensating release when the second fails. The
obvious question is why that argument does not also defer per-key limits.

**Decision — both scopes are enforced from the first line, and the asymmetry with H-033 is
real rather than inconsistent.**

A budget admission takes a **hold** that must later be settled. Two holds mean a failure
between them has to be compensated, and the compensating release is a new home for exactly
the bug Phase 4 exists to remove. A bucket consumption holds nothing and settles never; two
consumptions are two independent facts, and a failure between them leaves the first
consumed — which is bounded, self-correcting, and in the safe direction.

**Decision — one item per `(scope, dimension)`, consumed key-first and requests-first.**

Four items, up to four conditional writes, and only for dimensions that are actually
configured: an unlimited scope is skipped entirely, so a deployment that caps nobody does
no DynamoDB work at all on this path — which is what keeps this phase additive for every
tenant nobody has limited. The order puts the cheaper and more likely refusal first, so a
request that is going to be refused has consumed as little as possible of anything
*shared*: a key's bucket is private to one credential, the tenant's is shared by all of
them, and one request costs the requests bucket 1 where it can cost the tokens bucket
thousands.

**Decision — consumption is never refunded.**

When the third bucket refuses, the first two stay consumed. This is the decision most worth
arguing with, so the argument in full: **a budget is a stock and a rate limit is a flow.**
An over-charge against a budget persists for the rest of the month and must be corrected,
which is why settlement exists at all. An over-charge against a token bucket is erased by
the bucket's own refill within one emission interval — the refill *is* the compensating
transaction, it runs continuously, and it costs nothing. A refund would be a second
conditional write per bucket on every refusal, repairing an error that time repairs for
free, and it would introduce the one thing this design does not have: **an operation whose
absence breaks an invariant.**

The same rule covers a request that reached a provider and failed. There is deliberately no
code that hands the unit back: the request *used* the rate — it occupied a connection and
may have cost the provider work — and bounding how often that can happen is the whole point
of a flow limit. That is the exact opposite of the budget gate, where the same failure
releases the hold to $0 (H-031), and the two are opposite because they measure different
things.

**Alternatives considered.** *One item per scope holding both dimensions* — makes a scope's
two dimensions atomic together, and costs a four-branch update expression, since each
dimension is independently hot or cold; rejected because it does not remove the property
that has to be documented anyway, given that key and tenant are necessarily two items.
*Check all buckets, then commit all buckets* — two passes, twice the writes, and the first
pass is a read-then-write by another name. *Refund on refusal* — see above. *Tenant-first
ordering* — wastes shared capacity on requests a key's own limit would have refused anyway.

**Consequences.** The limiter is fractionally **stricter** than configured under exactly one
condition: a request refused by its Nth bucket has consumed from the N-1 before it. The
error is bounded by one request's worth per bucket and disappears within one emission
interval. `tests/test_rate_limit_gate.py` asserts it happening rather than pretending it
does not.

---

## H-037 — The limits live in Postgres, on the rows authentication already reads (Phase 4b)

**Status**: accepted · **Date**: 2026-08-09

**Context.** The bucket's *state* has to be in DynamoDB — that is the whole phase. Its
*configuration* has to be somewhere, and the somewhere matters more than it looks, because
the rate limiter runs on the first-token path of every request and must not add a round
trip to it.

**Decision — two nullable columns on `tenants` and on `virtual_keys`
(`migrations/0004_rate_limits.sql`), carried onto the `Principal`, read for free.**

BUILD_PLAN L2 settles the question by itself: *"Postgres … for config, virtual keys, the
cost ledger, request log, and the semantic cache. DynamoDB (conditional writes) for token
buckets and budget reservations **only**."* Limits are config. And the placement pays for
itself twice over: `find_by_hash` already joins both rows on every authentication, so both
scopes' limits arrive with the identity — no second query, no second cache, and no new
staleness bound to explain. They inherit the auth cache's existing one exactly (H-018), so
the guarantee is the same sentence a scope change gets: **a limit change takes effect on
the next request in the process that made it, and within `AUTH_CACHE_TTL_S` (5 s)
everywhere else.** `/admin/limits` invalidates its own process's entry for precisely that
reason.

Note what is *not* cached: the buckets. A stale *balance* is D-019's scar and is never read
at all; a stale *policy* for at most five seconds is the trade Phase 2 already made,
argued, and tested.

**Decision — `NULL` means unlimited, and the setter replaces rather than patches.**

Nullable rather than defaulted to some large number, because "no limit" and "a very high
limit" are different facts and only one of them can be reported honestly.
`set_tenant_limits` / `set_key_limits` are methods of their own rather than four more
`COALESCE` parameters on the existing patchers, because there `None` means *leave alone*
and here it must mean *clear* — `/admin/limits` is a PUT, and an API that can only ever
tighten a limit is a trap during an incident. A `CHECK (… > 0)` in the migration and
`ge=1` in the API keep zero out from both directions: zero would mean "admit nothing",
which already has two better spellings (deactivate the tenant, revoke the key), and it has
no emission interval at all.

**Alternatives considered.** *A `rate_limits` table keyed by scope* — tidier in isolation,
and it costs either a query or a cache on the hot path. *Beside the bucket in DynamoDB* —
contradicts L2, and worse: the limiter would then need the config in order to *build the
condition*, which means reading the item before writing it, which is the bug. *In
`config/routing.yaml`* — limits are per tenant and change at runtime; a committed config
file is neither. *A separate TTL for a limits cache* — a second staleness number to
document and reconcile with H-018's.

**Consequences.** `Tenant` and `VirtualKey` gained a `limits` field, so every code path that
rebuilds one has to carry it — `tests/test_tenant_store.py` asserts that renaming a key,
revoking it, and deactivating a tenant all preserve limits, because uncapping something by
accident is invisible until the traffic arrives. `/admin/tenants` and `/admin/keys`
deliberately do **not** set limits: there is one place to change them, and it is the one
that also shows the buckets.

---

## H-038 — 429 with `retry-after`, and how a caller knows whose it is (Phase 4b)

**Status**: accepted · **Date**: 2026-08-09

**Context.** H-032 chose **402** for a budget refusal and argued explicitly that 429 would
be wrong *there*, because 429 means *slow down*, every SDK retries it, and a budget refusal
does not heal inside its window. A rate-limit refusal is the mirror image and gets the
mirror answer — but it introduces a problem Phase 6 will have to solve on top of it: a
provider's own 429 also reaches the caller, and **failing over on ours would be exactly the
wrong response**, since it would move the excess traffic somewhere else instead of shedding
it.

**Decision — 429, with a `retry-after` computed from the bucket.**

This is the case the status was invented for: it heals with time, and the amount of time is
known exactly. The refusal path already holds the item its condition failed against, so the
value is the true excess of `tat` over the ceiling it had to be under — floored at one
second, because a `retry-after: 0` is an invitation to the retry storm a 429 exists to
damp. The body is the dialect's own `rate_limit_error`, a real value in both vocabularies,
and the precision travels in `headroom.reason` where no SDK can be surprised by it (H-009).

**One refusal carries no `retry-after`, deliberately.** When a request needs more tokens
than the bucket's whole capacity, waiting will never help and every value would be a lie.
It stays a 429 — the request is fine, the *limit* cannot accommodate it — carries
`headroom.reason: rate_limit_exceeds_capacity`, and the message says what to change. Only
reachable on the tokens dimension: a request costs one unit of the requests bucket, and
every limit is at least 1.

**Decision — three independent markers say a 429 is the gateway's, and the namespace they
live in is now closed.**

1. `x-headroom-error-source: gateway` — an upstream error always says `upstream`;
2. `x-headroom-ratelimit-scope: tenant:requests` — which of the four buckets refused,
   beside `-limit`, `-remaining`, and `-reset`;
3. `headroom.reason: rate_limited` in the body.

Any one of them is sufficient. The headers are the load-bearing ones, and they are only
trustworthy because of the change that makes this decision more than a naming convention:
**`forward_response_headers` now strips the whole `x-headroom-*` namespace from every
upstream response**, on the success path as well as the error path. It was already stripped
from *requests* (H-010, so that a provider never sees the headers that steer Headroom).
Without the response half, "no provider currently sends such a header" would be a property
of today's providers rather than of this proxy, and Phase 6 would be trusting it.
`tests/test_429_distinguishability.py` has an upstream forge both markers and asserts they
are gone.

The upstream's *own* rate-limit headers still cross untouched — `retry-after`,
`x-ratelimit-*`, `anthropic-ratelimit-*` — because they are exactly the signal a caller
needs in order to behave well. Only Headroom's own namespace is closed.

**Alternatives considered.** *403* — collapses "too fast" into "not allowed". *402* — the
budget's answer, and wrong here for H-032's reason run backwards: this one *does* heal.
*503 with `retry-after`* — implies the gateway is unwell, which it is not. *Unnamespaced
`x-ratelimit-*` headers* — the conventional spelling, and unusable for the distinction,
because an upstream sends them too. *A dedicated status such as 529* — inventing
vocabulary, which H-009 forbids. *Leaving the response deny-list alone and reading the body
instead* — a gateway that decides "whose failure is this" by parsing somebody else's JSON
is trusting the wrong party. *413 for the too-large case* — honest about the size and
dishonest about the cause, and it muddies the one-line rule P6 needs.

**Consequences.** `rate_limited` and `rate_limit_exceeds_capacity` are stable identifiers
from here: the ledger stores them as outcomes and Phase 7 will chart them. Phase 6's
failover rule is one line — *a 429 with `x-headroom-error-source: gateway` is a local shed,
never a failover* — and `tests/test_429_distinguishability.py` is written now, so that P6
inherits a pinned contract rather than an intention.

---

## H-039 — The rate limiter runs before the budget gate (Phase 4b)

**Status**: accepted · **Date**: 2026-08-09

**Context.** With both halves of Phase 4 landed, a proxied request now passes five policy
checks. Their order is a real decision with real consequences, and the plan does not fix
it.

**Decision — the order is: authenticate (401) → read the body → model scope (403) → route →
provider scope (403) → rate limit (429) → budget reservation (402) → open the upstream.**

- **Identity first**, unchanged from Phase 2: an anonymous caller has no buckets and no
  budget, and a gateway should not debug requests for strangers (H-020).
- **Scope before either gate**, unchanged from H-032: a key reaching past its permissions
  should be told that, not told to slow down or that it is out of money. A 403 storm
  therefore consumes nothing, which `tests/test_rate_limit_gate.py` asserts.
- **Rate limit before budget**, which is the new part, for three reasons:
  1. **No compensating release.** A rate-limited request that had already reserved budget
     would have to hand the hold straight back — a compensating action on the hot path,
     which is the one shape this phase refuses to add (H-036).
  2. **The limiter protects the gate.** A burst is exactly the traffic the budget gate is
     worst at: every request in it serialises on *one* DynamoDB item. Shedding it one step
     earlier is what keeps the cap's latency bounded under the load the limiter exists for.
  3. **It is cheaper.** A bucket consumption contends on a per-scope, per-dimension item; a
     budget admission contends on the single item every request for that tenant shares.
- **Both before `provider.open`**, so neither refusal can reach an upstream — asserted
  against the MockProvider's own record of what it was handed, not reasoned about.
- **One shared estimate.** The limiter needs the request's size in tokens and the gate needs
  it in dollars; they are the same bound from the same formula (H-034), so the proxy
  computes it once and hands it to both. Two independent estimates of one request would be
  two things to keep in sync.

**Consequence, stated so it cannot be mistaken for an accident:** a request that is both
over its limit and over its cap answers **429**, and answers **402** on the retry. "Slow
down" is advice a client can act on immediately, and one wasted round trip is cheaper than
hammering the budget item to discover the cap is gone too.

**Alternatives considered.** *Budget first* — a refused-for-rate request would take and
release a hold, and the burst would hit the contended item anyway; those are reasons 1 and
2 run backwards. *One combined "policy" step* — collapses two gates with different failure
modes, different statuses, and different datastores into one thing nobody can reason about.
*Rate limit before the scope checks* — cheaper still, and it would answer 429 to a key that
is simply not allowed to be there. *Rate limit after `provider.open`* — not a rate limiter.

**Consequences.** The order is now pinned by tests rather than by reading the proxy:
`test_rate_limit_gate.py` asserts 403-before-429, 401-before-429, 429-before-402, and that
a rate-limited request leaves the budget's counters untouched. Phase 5's cache will have to
choose a position in this sequence too — a cache hit costs no provider call, so *should* it
consume a bucket? — and this entry is where that argument starts.
