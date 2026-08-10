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

---

## H-040 — Two mechanisms keep tenants apart, and one function is both of them (Phase 5)

**Status**: accepted · **Date**: 2026-08-09

**Context.** The session brief states the requirement without qualification: *"no cache
entry ever serves across tenants — exact or semantic."* That is easy to satisfy and hard
to *prove*, because the failure is silent: a cross-tenant hit returns a 200 carrying a
complete, well-formed answer belonging to somebody else, and nothing in the response says
so. Backline's **D-021** is the same shape one layer up, which is why invariant 6 exists
at all.

A single mechanism is not enough, and the reason is specific rather than superstitious.
If isolation lived only in a SQL predicate, that predicate is one refactor away from being
"optimised" — the exact layer looks up by a 64-character hash, and *"the hash is unique
anyway, drop the tenant from the WHERE"* is a change a reviewer would wave through. If it
lived only in the hash, a future query written by hand (an admin listing, a Phase 7
dashboard panel, Phase 9's rollup Lambda) reaches the table without going through it.

**Decision.** Isolation is **a value, not a habit**: `CacheNamespace`
(`headroom/core/cache.py`) is the only address the cache has, every store method takes
one, and there is no method on `ResponseCacheStore` that can be called without naming a
tenant. That one value then does two jobs:

- it **salts the exact key** — `request_hash` is SHA-256 over `namespace.salt` and the
  canonicalised request, so two tenants asking the byte-identical question produce
  different digests;
- it **is the query** — every statement in `headroom/db/cache.py` leads with
  `tenant_id = $1`, against a unique index that leads with the same column, and the
  semantic search filters the whole namespace before pgvector is allowed to order
  anything.

Both are downstream of `namespace_for`, which is called from exactly one place.

**The sabotage is what makes this more than a claim.** `tests/test_cache_isolation.py`
patches `namespace_for` so the namespace no longer carries the tenant — the realistic
version of the bug, which is not "somebody deleted a WHERE clause" but "somebody decided
the namespace did not need the tenant in it" — and that single patch removes both
mechanisms at once. Under it, tenant B is served tenant A's answer, on the exact layer
*and* on a paraphrase through the semantic layer. Restored, B gets its own upstream answer
and the disposition is `cache_miss`. A leak test that could only defeat one of two defences
would prove nothing about the other, which is why the sabotage lives permanently in the
suite rather than being a one-off run recorded in a log.

**Alternatives considered.** *The predicate alone* — the conventional answer, and one
refactor from silence. *The hash alone* — leaves every hand-written query outside the
guarantee, including ones later phases will write. *A table per tenant* — real isolation,
traded for schema management, connection-pool pressure, and a `Scan` for every admin
listing. *Row-level security in Postgres* — genuinely stronger, and it binds the guarantee
to one datastore's feature at exactly the moment `InMemoryResponseCacheStore` stops being
able to reproduce it, which would take the contract suite (H-021) down with it.

**Consequences.** Adding a way to read the cache means adding a `CacheNamespace` parameter,
which is the intended friction. The namespace also carries dialect, model, and transport,
so "which requests can possibly share an answer" is one type rather than four conventions.
And because the tenant is inside the digest, a database dump's `request_hash` column is not
a stable identifier for "this question" across tenants — worth knowing before anyone tries
to use it for analytics.

---

## H-041 — Eligibility: single-turn, no tools at all, and a temperature bound (Phase 5)

**Status**: accepted · **Date**: 2026-08-09

**Context.** BUILD_PLAN §P5 fixes the shape — *"eligibility rules are conservative and
documented: single-turn user content, no tool_use in the conversation, temperature ≤ a
bound, per-tenant + per-model namespacing, TTLs"* — and the brief names three adjacent
cases the plan does not settle: tools **present but unused**, streamed versus
non-streamed, and reasoning models. The first is decided here; the second is H-043 and the
third is H-044.

**Decision.**

- **A request that declares `tools` is ineligible even when nothing has been called.**
  This is the case worth arguing. There is one user turn, no `tool_use` block anywhere,
  and the letter of the plan's rule is satisfied — and it is refused anyway, for two
  reasons. The same words with tools available may legitimately produce a *tool call*
  rather than prose, so answering with a cached paragraph is D-021's shape exactly. And
  the tools array is part of the prompt: a semantic probe that embeds only the user's
  question would ignore it entirely, so a request with tools and one without could match
  each other on the strength of identical words.
- **The tool scan is structural and deliberately over-broad** (`declares_tools`). It walks
  the whole parsed body rather than checking the two or three places tools are *supposed*
  to appear, because the failure mode of a targeted check is silent: a content-block type
  or vendor extension nobody anticipated slips past and is discovered as a wrong answer.
  It inspects object **keys** and typed markers — `tools`, `tool_choice`, `tool_calls`,
  the legacy `functions`/`function_call`, a `type` of `tool_use`/`tool_result`/…, a `role`
  of `tool` — and never free text, so a user asking a question *about* tool use is
  unaffected. A false positive costs one cache miss.
- **Single-turn, for both layers, following the plan literally.** Multi-turn *exact*
  caching would in fact be safe: the hash covers every byte, so two identical conversations
  get the same answer with no more risk than a single turn carries. It is not done anyway.
  The value is low (verbatim repeats of a long conversation are rare), and widening the
  blast radius of any future normalisation bug from one question to a whole conversation is
  not a trade this phase should make on its own authority against the plan's stated rule.
  The alternative is recorded here so a later phase can take it deliberately.
- **`temperature > 0.2` is ineligible.** A caller asking for variety is asking for the
  opposite of a cache. 0.2 rather than 0.0 because a great deal of production traffic sets
  a small non-zero temperature without wanting different answers, and refusing all of it
  would make the feature useless on real workloads. Note this is *not* a correctness
  mechanism: temperature is inside the exact key and inside `context_hash` regardless, so
  two temperatures never share an entry. The bound is about whether caching is the right
  behaviour at all. It is a module constant rather than a per-tenant knob — the plan makes
  the *similarity threshold* configurable and says nothing about this one, and one new dial
  per phase is enough.
- **`n > 1` is ineligible**: the caller asked for several answers and a cache has one.
- **A response over 1 MiB is not stored.** A bound on what one entry can cost the table,
  not a correctness rule. On the streaming path the copy is abandoned mid-response and the
  request itself is unaffected.

**Alternatives considered.** *Allow tools when none have been called* — the permissive
reading, and the one that produces a wrong answer rather than a missing feature. *A
targeted tool check* — cheaper, and silently wrong the first time a vendor ships a new
block type. *Multi-turn exact caching* — see above; deferred rather than rejected.
*Temperature as a per-tenant setting* — a second dial for a rule nobody has asked to
change. *No temperature rule at all, relying on the key* — technically safe and
behaviourally wrong: it would return one fixed answer to a caller who explicitly asked for
sampling.

**Consequences.** The ten reasons (`tools_present`, `temperature_above_bound`,
`multiple_completions`, `not_single_turn`, `incomplete_response`, `upstream_error`,
`empty_body`, `body_too_large`, `tool_output`, `reasoning_response`) are stable identifiers
from here: they reach the log line as `cache_reason` and Phase 7 will chart them.
Backline's own agent traffic is tool-heavy and therefore almost entirely uncacheable, which
is the correct outcome and worth stating before §P8.H2 wonders why its hit rate is zero —
H2 is a *passthrough overhead* experiment and runs with caching off anyway (H-047).

---

## H-042 — The exact key is a canonical hash with nothing dropped (Phase 5)

**Status**: accepted · **Date**: 2026-08-09

**Context.** BUILD_PLAN §P5 says *"normalized-request hash → stored response"*. What
"normalized" means is the whole decision, because every field a normaliser discards is a
field two different requests can now share an answer through.

There is also a rule this appears to break and does not. H-028 refuses to rewrite a request
body even to close a real metering gap, and H-007/H-016 rest on the fact that *no code
exists which could rebuild a request*. Canonicalisation parses and re-serialises.

**Decision.**

- **Canonicalising is not rewriting, and the distinction is enforced by where the output
  goes.** `canonical_json` produces bytes that are hashed and thrown away. Nothing it
  produces is ever sent, stored, or returned. The proxy still forwards the caller's bytes
  verbatim; if a canonical form ever reached a provider, that would be the bug.
- **The only normalisation is key order and whitespace.** Not one field is dropped — not
  `metadata`, not `user`, not a field this gateway has never heard of. A caller who sends
  an extra key gets their own entry. That is a deliberate hit-rate cost paid against a
  zero-length list of fields anybody can be *sure* cannot change a response, and widening
  it is an argument someone has to make in a new entry.
- **`ensure_ascii=False`, so a literal `ö` and the six-character JSON escape that denotes
  the same character canonicalise identically.** This is H-016 pointed the other way: on
  the wire that difference is load-bearing and the proxy preserves it byte for byte; in a
  *key* it is noise, and two clients whose JSON encoders disagree about non-ASCII must not
  miss each other's entries.
- **The digest is length-prefixed over its parts**, so no concatenation of namespace and
  body can be ambiguous. Contrived, and free to make impossible.
- **`context_hash` is the same canonical hash over the request with the user's question
  replaced by a sentinel**, domain-separated from the exact key by a marker. This is the
  semantic layer's guard rail and the reason its blast radius is exactly one field: a
  semantic hit requires an identical system prompt, temperature, `max_tokens`, and
  everything else, and allows similarity to move only what the user asked.
- **Which part *is* the question is a dialect method** (`Dialect.cache_probe`), because
  Anthropic keeps `system` as a top-level field while the OpenAI dialect keeps it in
  `messages`. A shared implementation that blanked "the messages" would drop the OpenAI
  system prompt out of the context entirely and let two different system prompts share an
  entry — which `tests/test_cache_keys.py` asserts against directly.

**Alternatives considered.** *Drop fields that "obviously" do not affect the answer*
(`metadata`, `user`, request ids) — a higher hit rate, and the list is exactly the kind
that grows by one plausible entry at a time until something in it does matter. *Hash the
raw bytes with no canonicalisation* — maximally safe, and it would miss on whitespace,
which is the one difference reliably produced by clients pretty-printing prompts.
*`ensure_ascii=True`* — matches the repo's other serializer and would split one question
into two entries depending on the client's encoder. *Embed the whole request rather than
just the question* — makes similarity a function of JSON scaffolding.

**Consequences.** Hit rates are lower than a field-dropping normaliser would produce, and
that is the intended trade. `request_hash` is not a stable identifier for "this question"
across tenants (H-040), and it is not stable across a change to the canonicalisation rules
either — such a change silently invalidates every entry, which is safe (a miss) and should
still be treated as a schema change.

---

## H-043 — The transport is part of the key; an entry is replayed, never converted (Phase 5)

**Status**: accepted · **Date**: 2026-08-09

**Context.** BUILD_PLAN §P5 asks for *"cached responses replay as a simulated stream when
the caller asked for streaming (first token effectively instant — a demo moment)"*, and the
brief asks for the choice to be logged either way: A4-grade fidelity tests if responses
replay as streams, or a stated reason if caching is non-streamed-only.

The obvious design is one canonical entry per question, rendered into whichever transport
the caller wants. It hits more often. It also needs two pieces of machinery this repo has
spent four phases arguing against: an **assembler** that rebuilds a JSON message from SSE
frames, and a **synthesiser** that emits frames no provider ever sent.

**Decision.** **Neither. The transport is part of the cache key, an entry stores the
upstream's bytes verbatim, and a replay yields exactly those bytes.**

- A streaming caller is served by an entry a streaming request populated; a non-streaming
  caller by one a non-streaming request populated. Neither is converted into the other, and
  a transport mismatch is a miss.
- The demo moment survives intact: stream a question twice and the second one's first token
  is already there, because a streaming client populates its own entry on its first
  request.
- The fidelity claim gets *stronger* than the live path's. A4 fixed that chunk boundaries
  are never meaningful and content equality is the bar; a replay is **byte-identical**,
  which is a strict superset. `tests/test_cache_replay.py` asserts it on both dialects,
  including the H-016 fixtures carrying literal 2-, 3-, and 4-byte UTF-8 sequences — the
  only fixtures in the repo that can *see* a re-encode — and including streams recorded
  from 1-byte chunking.
- The stored bytes are emitted in **one chunk**. Re-chopping them into plausible-looking
  pieces would be inventing a shape, which is the thing this decision exists not to do.

**Upstream response headers are not stored and not replayed.** A cached `retry-after` or
`anthropic-ratelimit-requests-remaining` describes a call that did not happen, and a
replayed provider request id would send an operator chasing the wrong trace. A replay
carries Headroom's own namespace instead — `x-headroom-cache`, `-source`, `-similarity`,
`-age` — which since H-038 is stripped from every upstream response and can therefore only
have been written by this process.

**Alternatives considered.** *One canonical entry, rendered per transport* — the higher hit
rate, bought with an assembler and a synthesiser on the serving path; H-016 proved by
sabotage that a single re-encode is invisible to every test that does not compare bytes,
and a cache's output is served forever rather than once. *Store only non-streamed
responses* — the brief's explicit alternative, and it forfeits the demo moment and most of
the value, since streaming is what a gateway's callers actually use. *Store the stream and
synthesise a body from it for non-streaming callers* — the same violation in one direction
only.

**Consequences.** A question asked both ways occupies two entries, and the first request of
each transport misses. Worth noting for completeness: `stream` is *also* inside the request
body, so the exact layer would separate the two transports even without this — the
namespace's `transport` is defence in depth, and its value is that a future dialect
signalling streaming out of band (a header, a distinct route) stays separated by
construction. The sabotage run reflects that honestly: removing `transport` from the
namespace fails the key-level tests and not the end-to-end ones.

---

## H-044 — A reasoning response is cacheable exactly and never semantically (Phase 5)

**Status**: accepted · **Date**: 2026-08-09

**Context.** The brief requires the reasoning-model case to be decided and logged: *"the
P1/P3 findings — reasoning deltas and reasoning_tokens — must inform what a cached replay
even means."* Those findings are that reasoning deltas are ordinary bytes in the stream
which the proxy forwards untouched (Phase 1's live smoke, then
`tests/test_reasoning_passthrough.py`), and that `reasoning_tokens` are *inside*
`output_tokens` and knowable only from the usage block (Phase 3).

Two consequences fall out mechanically and need no decision. Replay carries the chain of
thought intact, because byte-identical replay carries everything. And the avoided cost is
computed from the stored usage exactly as the original was priced, because
`reasoning_tokens` were already inside the count that was billed.

What needs a decision is the third thing: **a cached replay of a reasoning model hands the
caller the *original* chain of thought, not a fresh one.**

**Decision.** **A response reporting `reasoning_tokens` is stored *exact-only*: it may be
hit by a byte-identical request and is never embedded, so no paraphrase can ever reach
it.**

For an exact hit this is plainly right — same question, same reasoning, and a cache that
refused it would be refusing the safest hit there is. For a *semantic* hit it would be a
category of wrongness beyond "the answer is wrong": the chain of thought explicitly reasons
about the original question's wording, so a hit across a near-miss would serve a visible
monologue about Radiohead to somebody who asked about Coldplay. Worse for this project's
own purposes, §P8.H1's silent-wrong-answer metric scores the *answer* against the answer
key and would not capture it at all.

Enforcing it costs two lines: the usage block already says whether there were reasoning
tokens, and `StoreDecision` carries `store` and `embed` as separate booleans for exactly
this reason. A provider reporting the field as `0` has told us there was none, and is
treated as an ordinary response.

**Alternatives considered.** *Treat reasoning responses like any other* — the default, and
it makes the most confusing possible failure mode reachable. *Refuse to cache them at all*
— throws away the safest hits to prevent the dangerous ones. *Strip the reasoning from the
stored bytes and replay only the answer* — that is re-serialisation, forbidden by H-043 for
the same reasons, and it would break the byte-identity claim the fidelity tests rest on.
*Embed them but require a much higher threshold* — a second threshold to explain and sweep,
for a case a boolean settles.

**Consequences.** A tenant on a reasoning model gets exact caching and effectively no
semantic caching, which is a real reduction in value and is stated in the README rather
than discovered. `/admin/cache` reports `semantic_entries` beside `entries`, so the gap is
visible. If a later phase wants semantic caching for reasoning models, the honest route is
to store the answer *and* the reasoning separately and replay only what the new request
asked for — a different design, and a new entry.

---

## H-045 — A hit is billed at zero and records the cost it avoided (Phase 5)

**Status**: accepted · **Date**: 2026-08-09

**Context.** BUILD_PLAN §P5's gate: *"ledger rows correctly marked `cache_hit_exact` /
`cache_hit_semantic` with cost $0 and the *avoided* cost recorded (the dashboard's savings
number needs it)."* The brief adds the constraint that matters more: *"a hit's ledger row
must be distinguishable from an upstream call's in every way that matters (no fake
upstream_status, honest timings)"*, and asks for the cost semantics to follow H-026's
recorded-not-priced pattern.

**Decision.**

- **`usd_cost = 0` with `cost_status = not_billable`.** No new status is invented: this is
  precisely H-025's existing rule for a request that never reached a provider — an
  unroutable model, a scope refusal — and a hit is one of those. The zero is a
  *measurement*, which is what `not_billable` means and what distinguishes it from the NULL
  that means "we do not know".
- **The avoided cost is a column of its own**, `cache_avoided_usd`, and it is **the entry's
  own recorded cost** — the figure the request that populated it was actually billed, copied
  in at store time. H-024's rule one table over: a saving should be a fact about an invoice
  line that really happened, not a re-pricing of a hypothetical at today's rates. It is
  NULL when that cost was never known (an unpriced model, an unmeterable stream), so a
  savings total can never quietly add a zero for a figure nobody has.
- **Everything that would imply an upstream call is NULL.** `upstream_status`, `provider`,
  `upstream_latency_ms`, `passthrough_overhead_ms`, `input_tokens`, `output_tokens`,
  `reasoning_tokens`. `provider` is the one worth defending: the route *did* resolve to one
  and it was never called, and the column's readers — the dashboard's per-provider spend,
  Phase 6's failover and health accounting — are asking "which upstream served this". The
  honest answer is none.
- **The token columns stay NULL rather than borrowing the entry's counts.** Nothing was
  generated. Every `SUM(output_tokens)` written before this phase — in `/admin/usage`, in
  Phase 7, in Phase 9's rollup Lambda — keeps meaning "tokens a model produced", with no
  edit anywhere.
- **What a hit *does* carry**: `outcome = ok` and `status_code = 200`, because that is what
  the caller experienced; a real `ttft_ms` and `total_ms`, which are small and are the
  number the demo is about; the `stop_reason`, because the answer really did end that way;
  `cache_similarity` for a semantic hit; and `cache_source_request_id`, the provenance that
  makes a hit auditable and is the answer key §P8.H1 needs.
- **A hit takes no budget reservation and settles nothing** (H-046), so `budget_status` is
  NULL — which the field already documents as "never got as far as the gate".

**Alternatives considered.** *A `cache_hit` cost status* — a sixth value for a case the
fifth already describes exactly. *Copying the entry's token counts onto the row* — makes
the savings visible in existing charts, by making every token total wrong. *Re-pricing the
avoided cost at today's rates* — arguably the more useful counterfactual, and it is a
counterfactual; the recorded figure is a fact, and the source row carries the rates for
anyone who wants the other number. *Keeping `provider` set* — defensible, and it puts
requests no provider saw into per-provider accounting Phase 6 is about to build on.

**Consequences.** `cache_avoided_usd`, `cache_similarity`, and `cache_source_request_id`
are stable columns from here. A tenant's ledger row count is still their request count
(hits are rows), but their *token* totals now under-count what they were served —
correctly, and the savings column is where the difference lives. Phase 7 builds the savings
counter from these three columns and needs no new schema.

---

## H-046 — The cache sits after the rate limiter and before the budget gate (Phase 5)

**Status**: accepted · **Date**: 2026-08-09

**Context.** H-039 closed by naming this decision and declining to make it: *"Phase 5's
cache will have to choose a position in this sequence too — a cache hit costs no provider
call, so should it consume a bucket? — and this entry is where that argument starts."*

**Decision — the order is: authenticate (401) → read the body → model scope (403) → route →
provider scope (403) → rate limit (429) → cache → budget reservation (402) → open the
upstream.**

**After the rate limiter, and the units are not refunded on a hit.** A hit costs no
provider work, but it is not free to *this* process: it costs a connection, a pgvector
search, and on the semantic path a CPU embedding — the most expensive thing the gateway
does to a request it never forwards. A tenant able to serve unlimited traffic as long as it
repeated itself would have a denial of service for the asking. H-036's no-refund rule then
applies unchanged: a bucket consumption settles never, and a hit is not an exception.
Placing the cache after the limiter also means a burst is shed before it can reach the
embedder — the same argument H-039 made for shedding before the budget item.

**Before the budget gate, and a hit takes no reservation at all.** This is the sharper
half. A hit spends nothing, so there is nothing to bound; reserving and settling to zero
would put two DynamoDB round trips on the one path whose entire product is that the first
token is already there. The consequence is stated rather than discovered: **a tenant over
its cap still gets its cached answers.** A budget bounds *spend*, and a hit does not spend.
The tenant is degraded — every miss is a 402 — rather than dead, and abuse is bounded by
the limiter one step earlier. `tests/test_cache_gate.py` asserts exactly that pair on one
exhausted tenant: a hit at 200 and a miss at 402.

**The cost of that placement, named.** A *miss* by an out-of-budget tenant pays for its
cache lookup — including an embedding — before the 402. That is a bounded waste on a path
the rate limiter already guards, and the alternative (budget first) would put a
compensating release on the hot path for every hit, which is the one shape Phase 4 refused
to add.

**A refusal earlier in the chain never reaches the cache.** A 403 or a 429 leaves
`cache_disposition` NULL, which is how the log line distinguishes "the cache said nothing"
from "the cache was never asked".

**Alternatives considered.** *Cache before the rate limiter* — a hit becomes free to the
tenant and the gateway becomes free to abuse. *Cache after the budget gate* — two DynamoDB
round trips per hit and a reservation that always settles to zero. *Refuse hits for tenants
over budget* — consistent-sounding, and it refuses a request that costs nothing to serve
for a reason that does not apply to it. *Charge a hit a reduced budget amount for the
gateway resources it used* — inventing a price for something no provider billed, which is
the whole class of thing this repo's metering refuses to do.

**Consequences.** The order is pinned by tests rather than by reading the proxy, and the
sabotage run confirms the pin fires: moving the cache above the limiter fails
`test_a_hit_still_consumes_its_rate_limit` and
`test_a_rate_limited_request_never_reaches_the_cache`. Phase 6 inherits one more line in the
same sequence and the same question — a failover hop happens *after* all of this, so a
cache hit can never trigger one.

---

## H-047 — H2 runs with caching disabled, and the plan says so before the data exists (Phase 5)

**Status**: accepted · **Date**: 2026-08-09

**Context.** BUILD_PLAN §P8.H2 measures per-request gateway overhead against a
pre-registered p50 < 50 ms and reports the real number either way — it is the figure that
answers "why is a gateway in Python defensible". Phase 5 has just made that measurement
corruptible: a cached hit answers in microseconds without touching a provider, so a suite
run against a cache-enabled tenant would report an overhead figure that is really a
*hit-rate* figure, flattering the gateway by exactly the amount Backline's 133 questions
happen to repeat themselves.

Invariant 8 decides *when* this has to be settled: every Phase 8 experiment's hypothesis,
metrics, and conditions are written **before data exists**. Phase 5 is the phase that makes
the amendment executable, so Phase 5 is where it is made.

**Decision.** BUILD_PLAN §P8.H2 is amended in this PR with: *"H2 runs against a tenant with
caching disabled entirely; overhead is measured on pure passthrough."* Three things make it
more than a sentence:

- **It is the shipped default.** `cache_mode` is `NOT NULL DEFAULT 'disabled'`, so the H2
  tenant is correct by construction and the amendment is a statement of what not to change
  rather than a step somebody has to remember.
- **The pre-flight asserts it.** `experiments/` checks `GET /admin/cache/{tenant}` before
  spending the $10.
- **The ledger makes it checkable afterwards.** Every row carries `cache_disposition`, so
  the H2 report states the count of rows that are not `cache_disabled`, and that count must
  be zero. A run that accidentally had caching on cannot be reported as if it did not.

Measuring what the cache *saves* is §P8.H1's job, on a different tenant, with the threshold
sweep this phase built the config surface for.

**Alternatives considered.** *Leave it implicit because the default is already disabled* —
defaults change, and an unstated assumption is the one nobody checks when a number looks
surprising. *Run H2 with caching on and report the hit rate beside the overhead* — two
confounded variables in the number the writeup is built on. *Run it both ways* — a second
$10 run to answer a question §P8.H1 answers better and for free.

**Consequences.** The H2 tenant now has a documented configuration requirement and the
report has one more line in it. §P8.H1 keeps a separate tenant, which is also what stops
its seeded corpus from polluting H2's ledger rows.

---

## H-048 — Failover stops at the first downstream byte, and the guard is checked (Phase 6)

**Status**: accepted · **Date**: 2026-08-09

**Context.** BUILD_PLAN §P6 states the rule and asks for the decision to be logged: *"if
the primary fails **before first token**, the request replays against the fallback
transparently; if a stream dies **after first token**, the caller gets a terminal error
event and failover does *not* silently splice mid-answer — that semantic is documented as
a decision (H-xxx), because splicing is how gateways serve Frankenstein responses."*

What makes splicing worse than an ordinary bug is that it is *invisible*. Two providers'
answers welded end to end produce a stream whose frames are well formed, whose terminal
marker is present, and which every SDK on the far end returns as one complete message.
Nothing in the transcript says two models wrote it, and nothing downstream can check.
H-008 drew the same line a phase earlier for a different reason ("a stream that ends early
ends in a terminal error event"); this is that line made load-bearing.

**Decision.** **A request may be retried for exactly as long as nothing about its response
has been committed to the client — and the predicate for that is
`RequestContext.first_token_out_at is None`.**

Three things make it a design rather than a rule:

- **The executor owns everything up to the commit point and nothing after it.**
  `Failover.open` is called on the line that used to read `provider.open`, and it returns
  the response that will be served. There is no path in `headroom/api/proxy.py` from a
  forwarded byte back into it, so in the shipped gateway the boundary holds
  *structurally*.
- **It is checked anyway, on every retry.** "Structurally unreachable" is a property of
  today's call sites, and call sites change. When the guard sees that a byte has gone out
  it stops and raises the last failure. `tests/test_failover_boundary.py` drives the
  executor directly with the mark set, because that is the only way to test something the
  proxy cannot reach.
- **The commit point is later than it looks, and that is worth having.** A *non-streamed*
  response is read in full before anything is sent, so a connection that dies mid-body has
  still committed nothing — and is still safe to fail over. The executor therefore reads
  non-streamed bodies **inside** the retry loop rather than after it, which is the whole
  reason `BufferedUpstreamResponse` exists. Operationally this is the difference between
  killing a container mid-request and losing that request, or not.

After the boundary, H-008's discipline is unchanged: a terminal error event in the
caller's own dialect, no `message_stop`, no `[DONE]`, and an honest `upstream_stream_cut`.

**The sabotage is executed rather than described.**
`tests/test_failover_boundary.py::test_the_sabotage_serves_a_frankenstein_answer` mounts a
naive `_passthrough` — fifteen lines, each locally reasonable, that opens the fallback when
the stream dies and keeps yielding — over the real one, and measures what the caller gets:

```
shipped   "The capital of France is " + event: error(upstream_stream_cut)
spliced   "The capital of France is The capital of Germany is Berlin."
          two message_start frames, one message_stop, no error event anywhere,
          HTTP 200, stop_reason "end_turn"
```

The frightening assertion in that test is not that the text is wrong. It is
`assert "error" not in events`.

**Alternatives considered.** *Splice and re-frame properly* — strip the second stream's
opening frames, renumber indices, emit one coherent message. It requires parsing and
re-emitting somebody else's SSE, which H-007 refuses even on the happy path, and it cannot
be made correct anyway: the fallback is answering a question the caller has already been
handed half an answer to, and no amount of re-framing turns half of one answer plus all of
another into an answer. *Buffer every streamed response so a retry is always safe* —
correct, and it throws away first-token latency, which is the product (H-008 rejected the
same trade). *Retry after the first byte only while no content delta has been emitted* — a
narrower window that is genuinely safe, and it makes the boundary a function of dialect
semantics rather than of transport, so every new content-block type is a fresh chance to
get it wrong. The predicate stays "has any byte left".

**Consequences.** A mid-stream fault is caller-visible and always will be — §P8.H3
pre-registers exactly that, and the number it reports is "terminal error events: 100%". The
gateway therefore cannot promise zero caller-visible failures, only zero *silent* ones, and
the README says so. `first_token_out_at` is now a load-bearing field rather than a timing
convenience: anything that set it early would disable failover, and anything that set it
late would enable a splice.

---

## H-049 — What triggers a hop, what never does, and how a chain is configured (Phase 6)

**Status**: accepted · **Date**: 2026-08-09

**Context.** BUILD_PLAN §P6 names the triggers in five words — *"on 429/5xx"* — plus the
transport faults the MockProvider injects. It says nothing about the exclusions, and the
exclusions are where a failover phase does damage: this gateway issues its own 429 (rate
limit, H-038) and its own 402 (budget, H-032), and *failing over on either would be the
precise inversion of what they are for*. A rate limit exists to shed load; moving the
excess to a second provider is not shedding it. A budget refusal exists to stop spending;
spending it somewhere else is not stopping.

**Decision — the trigger set is closed and small.**

| Situation | Hop? | Why |
|---|---|---|
| `ProviderTimeout`, `ProviderUnavailable` | **yes** | no answer at all; another box may have one |
| body dies mid-read, non-streamed | **yes** | nothing committed downstream yet (H-048) |
| upstream **429** | **yes** | that provider is shedding; another may not be |
| upstream **5xx** (500…599, incl. 529) | **yes** | BUILD_PLAN's own list |
| upstream 4xx other than 429 | **no** | describes the *request*; the next provider says the same thing one round trip later |
| gateway 402 / 429 / 403 / 404 | **no** | see below |
| `ConfigurationError` (a missing key) | **no** | routing around one's own misconfiguration is how it stays undiscovered for a month |
| mid-stream cut | **no** | H-048 |

**The gateway's own refusals are excluded structurally, not by a condition.** They are
raised by `gateway.limits` / `gateway.budgets` **before** the executor exists in the call
path, and none of them is a `ProviderError` — which is the only exception class
`headroom/policy/failover.py` catches. So there is no `if` in that file that could get the
distinction wrong, and the test asserts the strong form: not "the header says gateway" but
**no provider in the chain was called at all**. H-038 built the three distinguishability
markers for this phase to read; what this phase actually needed was for the question never
to arise.

**Decision — chains are per-route configuration, and failover is opt-in.**

```yaml
routes:
  openai:
    - prefix: ""
      provider: vllm_a
      fallbacks: [vllm_b]
      max_attempts: 3        # optional, 1..5
```

`fallbacks` defaults to empty and `max_attempts` to one attempt per candidate, so **a rule
that mentions neither behaves exactly as it did in Phase 5** — one call, no retry, no
backoff, no breaker on its path. That is what makes the phase additive for the `claude-`
route that spends real money and for every deployment that has configured nothing. The
attempt sequence wraps when `max_attempts` exceeds the chain (`a, b, a`), so a
single-provider route can ask to be *retried* rather than abandoned; a repeat inside
`fallbacks` itself is refused, because it would make `failover_hops` count a hop that never
left the provider.

**Decision — BUILD_PLAN L4 stops being structural and becomes checked.** Routing is per
dialect, so a chain lives inside one dialect's rules and is same-dialect by construction —
but nothing structural stops `fallbacks: [anthropic]` under an `openai:` route, which would
hand a chat-completions body to the Messages API *on exactly the day the primary went
down*. That is the cross-dialect translation L4 puts permanently out of scope, arriving
through a config file instead of through a translation layer. So `register_kind` now
requires each kind to declare which dialects it speaks, and `build_gateway` refuses to
start on a rule that crosses one — primaries included, because a rule that applied only to
the new feature would be a rule the old feature can still break.

**Decision — a scope narrows a chain and can never widen it.** A key scoped to `vllm_a` and
not `vllm_b` is not served by `vllm_b` when `vllm_a` fails. Authorization outranks
availability: an outage must not be able to widen a permission. The primary is still checked
by `Principal.require_provider` and still answers 403, so filtering the rest can only narrow
something already authorised.

**Alternatives considered.** *Retry every 5xx **and** every 4xx* — a 400 retried against a
second provider is a second 400 and one more round trip of latency, sold as resilience.
*Treat a gateway 429 as retryable against a different provider* — the inversion above; worth
naming because a library that only sees status codes would do it. *Fail over on a missing
credential* — defensible for a rotated key, and it means a broken provider serves zero
traffic while looking configured. *Chains as a top-level `chains:` block referenced by name*
— tidier when many routes share one chain, and it puts the answer to "where does this model
go when it fails" one indirection away from the rule that routes it. *No per-route
`max_attempts`* — then "retry with backoff" is only reachable by adding a second provider,
which is not a knob, it is a purchase.

**Consequences.** `MAX_ATTEMPT_LIMIT = 5` is a published ceiling: an unbounded retry budget
on the first-token path is a self-inflicted denial of service, and a number an operator can
raise without a code change is a number somebody eventually sets to 50 during an incident.
`register_kind` gained a required keyword, a deliberate tightening of a registration
contract with three in-repo call sites. And a fallback naming an undefined provider now
fails at load — the worst typo to find late, because it is invisible until the primary is
down.

---

## H-050 — Backoff is paid to a provider that already failed, not to a fresh one (Phase 6)

**Status**: accepted · **Date**: 2026-08-09

**Context.** BUILD_PLAN §P6 asks for *"retry with jittered exponential backoff"* and names
no parameters. Two things therefore have to be decided: the curve, and — much more
consequentially — *when the gateway sleeps at all*.

Almost every retry library answers the second question with "before every attempt after the
first", because almost every retry library was written for **one** endpoint. Applied to a
failover chain that is simply wrong: nothing about `vllm_a` being down suggests `vllm_b`
needs a moment to collect itself, and on a gateway whose entire product is first-token
latency an unnecessary 50 ms is 50 ms of the thing being sold.

**Decision — the delay is a function of how many times *this* provider has failed *this*
request.** Moving to a fresh candidate costs nothing. Coming back to one that has already
failed here pays `uniform(0, min(cap, base · 2^k))`, with `k` counting that provider's prior
failures in this request.

**Decision — the parameters, published rather than tuned in place:** `base_s = 0.05`,
`multiplier = 2.0`, `cap_s = 2.0`, **full jitter**. Full jitter rather than a fixed delay
because the failure being prevented is a *synchronised* retry — a burst that all failed at
the same instant would otherwise all come back at the same instant, and a fixed delay merely
moves the stampede. `BackoffPolicy.worst_case_s` publishes the bound: three attempts against
one provider sleep at most **150 ms** in total, five attempts at most 750 ms, and with full
jitter the expectation is half of that.

**Decision — `sleep` and `jitter` are injected.** A backoff verified by actually waiting is
a test somebody deletes the week the suite gets slow; a jittered one verified against a real
RNG is a test that flakes. CI passes a recorder that appends the requested duration and
returns immediately, and asserts the exact schedule. The curve is tested separately, against
`BackoffPolicy`, with the jitter supplied as a number.

**Decision — an upstream's own `retry-after` is forwarded and never slept on.** A provider
that answers 429 with `retry-after: 30` is telling the *caller* something useful, and that
header still crosses untouched (H-010). What the gateway must not do is honour it itself: a
30-second sleep inside a request is an upstream being allowed to stall this gateway, and the
correct response to "come back in 30 seconds" is to go somewhere else now.

**Alternatives considered.** *Sleep before every retry including the first fallback* — the
conventional implementation, and the one the sabotage run executes; it fails
`test_moving_to_a_fresh_provider_costs_no_delay` and adds latency to precisely the case
failover exists to make fast. *Decorrelated jitter* (AWS's other recommendation) — better
under sustained contention, and it needs state across attempts for a chain that is at most
five attempts long. *No jitter, exponential only* — simpler, and it synchronises retries,
which is the failure mode. *A total deadline instead of a per-retry cap* — a better
guarantee, and it wants a clock inside the executor; the arithmetic bound above is exact
without one, and is asserted.

**Consequences.** The three numbers are published and pinned by
`tests/test_failover_backoff.py`, so changing one is a diff with a justification attached.
An emergent behaviour worth recording, because it falls out of this entry plus H-052 and
would otherwise be rediscovered: once a breaker has tripped, a single-provider route with
`max_attempts: 3` stops paying its retry budget — the first two slots are skipped and only
the final one, which a breaker may never skip, is attempted. Retries against a provider we
already know is down are exactly the retries worth not making.

---

## H-051 — What a ledger row says about a hop (Phase 6)

**Status**: accepted · **Date**: 2026-08-09

**Context.** `failover_hops` has been a column since `migrations/0002` (H-024), reserved for
this phase. The session brief asks for more than a count: *"the row records which provider
ultimately served and which failed (decide the shape for intermediate failures; log it)"*.
Three facts are in play and no single column carries them: who served, who was passed over,
and what the caller was eventually handed.

**Decision — three columns, and they answer different questions.**

- **`provider`** moves with the executor and names **who served**. On an exhausted chain it
  names the last candidate, because that is whose answer the caller received — which keeps
  it consistent with `upstream_status` on the same row.
- **`failover_hops`** counts **slots that did not serve**. Zero means the primary served it,
  which is what the column has claimed since `0002`.
- **`failover_from` / `failover_error`** (new, `migrations/0006`) name the **first**
  candidate passed over and why.

The first/last split is the load-bearing part. `error_reason` and `upstream_status` already
describe the *last* thing that happened — the failure a caller was handed when a chain ran
out. What no existing column could say is why the request left where it was routed, and that
is the operational question: *we are serving from `vllm_b`; what happened to `vllm_a`?*
Between them, one row describes a two-link chain completely. A longer chain is summarised
here and written out in full on the structured log line, which carries the whole trail as
`["vllm_a:upstream_status_529", "vllm_b:ok"]`.

**Decision — a breaker-skipped candidate counts as a hop.** It was not tried, so it did not
fail; it is still a slot that did not serve, and `failover_hops > 0` means "the primary did
not serve this request". The alternative — counting only real attempts — makes the hop count
drop back to zero the moment an outage becomes *persistent* enough for the breaker to trip,
which is exactly when somebody is reading it. `failover_error` says `breaker_open`, so the
two cases stay distinguishable where it matters.

**Decision — the upstream timing mark is rewound between attempts.** A failed attempt's
error body stamps `first_upstream_byte_at`, and marks are idempotent, so without an explicit
rewind that stamp becomes the *served* response's first byte — and `passthrough_overhead_ms`,
defined as *first upstream byte → first byte out*, would silently include the entire
failover sequence. That column is what §P8.H2 publishes against a pre-registered p50 < 50 ms,
so this is the difference between a gateway-cost metric and a failover-duration metric.
`RequestContext.restart_upstream_timing` is the only place in the project where a mark moves
backwards, and it is a named method rather than an assignment so the one legitimate reason to
do it is written beside it. `ttft_ms` deliberately keeps the whole wait, because that is what
the caller experienced.

**Alternatives considered.** *One `failover_trail TEXT` column holding
`vllm_a:529>vllm_b:ok`* — complete for any chain length, and it encodes structure in a
string, which is the kind of schema that ages into a parser. *A `failover_attempts` table
joined on `request_id`* — fully normalised, and it puts "why did this leave `vllm_a`" one
join away from the row that answers everything else about the request; the same argument
`0003` and `0005` made for widening this table rather than adding another. *Record the last
failure instead of the first* — already on the row, twice, as `error_reason` and
`upstream_status`. *A `CHECK` tying the three columns together* — a constraint spanning three
columns fails a row for a bookkeeping slip and loses the invoice line with it; the writer
keeps them consistent and the tests assert it.

**Consequences.** `failover_from` and `failover_error` are stable columns from here, and
`upstream_status_<code>` / `breaker_open` join the stable identifier set the ledger stores
and Phase 7 charts. A partial index on `failover_hops > 0` serves the dashboard's "show me
the requests that failed over" and §P8.H3's own query without indexing the overwhelming
majority of rows, which have nothing to say. And one property is now asserted rather than
hoped: a request that hops writes exactly **one** row.

---

## H-052 — Provider health is in-process, and a breaker never skips the last candidate (Phase 6)

**Status**: accepted · **Date**: 2026-08-09

**Context.** BUILD_PLAN §P6 asks for *"provider health tracking (rolling error/latency
windows)"* and *"a circuit breaker [that] trips a provider out of rotation after a threshold
and probes it back in"*. Where the evidence lives, and what a breaker is allowed to refuse,
are both left open.

**Decision — health is in memory, per process, and never shared.** Not Postgres, not
DynamoDB. BUILD_PLAN L2 reserves DynamoDB for token buckets and budget reservations *only*,
and the deeper reason is that a breaker is **not a fact about the world**: it is a record of
what *this* process has been able to reach. A Fargate task whose own network path is broken
must trip its own breaker without convincing the other three that Anthropic is down, and a
task that has just started must be free to find out for itself. The cost — a cold process
pays a few failures before it learns — is bounded by `min_samples`, and `/admin/providers`
says plainly that it reports one gateway's experience.

**Decision — the thresholds, published:** a rolling window of **20** observations, a floor of
**5** before anything can trip, a failure ratio of **0.5**, and a **10-second** cooldown. A
ratio over a window rather than a streak, because a provider failing half its requests is
worse than one failing all of them — every failed request costs a timeout and it succeeds
just often enough to look alive, which a consecutive-failures rule never catches. Ten seconds
because it has to be long enough not to hammer a sick provider and short enough that recovery
is visible in a demo somebody is filming.

**Decision — a provider is scored when its response *finishes*, not when it starts.** An
upstream that answers headers and then dies mid-answer is not a healthy upstream, and that is
precisely what `docker kill` on a live vLLM produces. So the executor scores the attempts it
can judge on its own (a transport failure, a retryable status, a body it read to the end) and
says nothing about a live stream; `headroom/api/proxy.py` scores that one when the stream
ends. One attempt, one observation, never two. Two exclusions follow: an upstream **4xx that
is not 429 counts as a success**, because a 400 is a healthy provider correctly refusing a
bad request and counting it would let one tenant's malformed payloads trip a breaker for
everybody else; and a **client disconnect is scored by nobody**, because the caller hanging
up is not evidence about an upstream.

**Decision — the breaker never skips the last remaining candidate.** Tripping is only useful
when there is somewhere else to go. On a single-provider route a breaker that refused would
replace an upstream's 529 — which a caller can read and act on — with a gateway error about a
decision they cannot see: it would convert a provider's outage into *this gateway's*, which
is strictly worse than trying and failing. So the final slot is always attempted, which also
makes it the natural probe.

**Decision — half-open admits exactly one probe, and a probe that succeeds clears the
window.** One probe, because a recovered provider stampeded by everything that queued behind
it is a second outage. Clearing the window, because otherwise the failures that tripped the
breaker are still in it and the very next blip satisfies the ratio again — the bug that turns
a ten-second outage into a ten-minute one. Lifetime counters survive the clear; they are the
record, not the verdict.

**Alternatives considered.** *Share health through DynamoDB so every task benefits* —
contradicts L2, adds a read to the hot path, and makes one task's broken network everybody's
outage. *Consecutive failures as the trip rule* — simpler, and blind to the half-failing
provider. *Latency-based tripping (p95 above a bound)* — genuinely useful, and it needs a
baseline per model and per prompt size, which is a research project; the latency window is
recorded and reported, and nothing trips on it yet. *A background task that reopens breakers
on a timer* — one more thing to run and to fail silently; the transition happens on the
request that finds the cooldown elapsed, which needs no scheduler.

**Consequences.** `HealthTracker.admit` is a query with a side effect — it performs the
open → half-open transition — which is the same trade H-032 took for the budget sweep on
`GET /admin/budgets`, and taken for the same reason: the transition and the decision are the
same event. `/admin/providers` reports state, window, ratio, latency percentiles, and last
error, which is what Phase 7's health tiles read; `DELETE /admin/providers/{name}/health` is
the incident-response route, spelled with `/health` because a `DELETE` on the provider itself
would read as *remove this provider*, which a running gateway can never do.

---

## H-053 — One admission per request, however many providers it takes (Phase 6)

**Status**: accepted · **Date**: 2026-08-09

**Context.** The session brief: *"a failed-then-retried request must not double-reserve or
double-bill. State how the P4 reservation travels across hops; test the arithmetic under a
failover."* Every previous phase put something on the admission path — a bucket consumption
(H-039), a cache lookup (H-046), a budget reservation (H-032) — and a phase that makes one
request touch several providers is exactly the phase that could start running them several
times.

**Decision — the executor sits strictly *inside* one admission, and that is the whole
answer.** The pipeline is unchanged and gains one line in the middle of its last step:

```
authenticate (401) → read the body → model scope (403) → route → provider scope (403)
  → rate limit (429) → cache → budget reservation (402)
  → [ failover executor: attempt … attempt ]
  → settle → one ledger row
```

Admission happens before the executor exists in the call path and settlement after it has
finished, so the number of hops is invisible to money **by construction**. There is no
compensating action, no second reservation, and nothing to keep in sync — the same shape
H-036 and H-039 chose for the same reason: the bug this project keeps refusing to add is *an
operation whose absence breaks an invariant*.

**Decision — a hop does not consume a second rate-limit unit.** The limiter meters *client
requests*, not upstream attempts. A hop is the gateway's own decision about how to serve one
request, and charging a tenant rate for it would make a provider outage look like the tenant
misbehaving — at exactly the moment their traffic most needs to get through.

**Decision — settlement follows the *final* outcome, and H-025/H-031 need no amendment.**
Every provider answered ≥400 → no model ran anywhere → cost is a measured `0` and the hold is
released. Every provider timed out → two providers were *sent* the request and neither
answered → `usage_unknown`, NULL on the invoice, and the hold settles at the **estimate**,
because releasing it would be a cheerful guess that two round trips to a model cost nothing.
The ledger and the budget disagree on that row, visibly, on purpose — H-031's documented
disagreement, reached by a longer road.

Stated as arithmetic, and asserted: **a request that hops costs exactly what a request that
does not hop costs.** Two identical requests, one served by the primary and one only after a
failover, move the tenant's counter by `2 × $0.0000115` and not by three of anything.

**Alternatives considered.** *Reserve per attempt and release the losers* — the natural
reading of "retry", and it puts a compensating release on the hot path for every hop, which
is D-019's shape with a new noun. *Charge a bucket unit per attempt* — defensible as "the
gateway did more work", and it punishes tenants for outages. *Widen the reservation when a
hop happens* — the estimate already bounds one request's worst case, and a hop does not make
that request bigger. *A separate "failover attempts" counter in DynamoDB* — a third store
write on the latency path, for a number the ledger already carries per row.

**Consequences.** `tests/test_failover_ledger.py` asserts the arithmetic on the tenant's own
counters rather than on the code's shape, because "structurally impossible" is a claim about
today's pipeline. The one place a hop *is* invisible to money is the estimate's blind spot,
unchanged from H-034: an unpriced model is invisible to the cap whether it hops or not.

---

## H-054 — The console is a client of `/admin/*`, and has no other way in (Phase 7)

**Status**: accepted · **Date**: 2026-08-09

**Context.** A dashboard that reads a database is the easiest thing in this repo to build
and the worst thing in it to own. The gateway and the console share a machine, a compose
network, and — in Phase 9 — a VPC; nothing physical stops the UI opening its own asyncpg
pool and writing the four `GROUP BY`s it needs. The session brief rules that out in one
line (*"READ THROUGH THE REAL ADMIN API ONLY"*), and this entry records why the rule is
worth more than the convenience it costs.

Three reasons, in ascending order of how much they matter.

1. **A second reader is a second set of bugs.** `usage_ledger` has thirty-nine columns and
   five `cost_status` values whose distinctions are the whole point of Phase 3 — NULL is
   not zero, a `partial` row is a bound, a hit's token counts are absent rather than
   copied. A hand-written dashboard query re-decides all of that, silently, in a language
   with no tests pointed at it.
2. **The admin API is the product surface.** Every figure this console needs is a figure
   an operator with `curl` should be able to get, and a dashboard that reached past the
   API would have removed the pressure to make that true.
3. **Phase 9 splits them.** ECS runs the gateway and the ui as separate tasks; a console
   holding database credentials would need RDS reachability, a second secret, and a second
   thing to rotate — for data it can already ask for over HTTP.

**Decision.** The console talks to `/admin/*` and to nothing else. It has no
`DATABASE_URL`, no `DYNAMODB_ENDPOINT_URL`, and no client for either; `docker-compose.yml`
gives the `ui` service exactly one variable, and it is a URL. **A view that needs a number
the API does not publish causes the API to publish it** — properly, with tests, in the
same PR — rather than causing a query.

That happened three times in this phase, and all three are reads of columns that already
existed:

- **`/admin/usage/series`** (new) — the ledger aggregated into `minute`/`hour`/`day`
  buckets, for the cost-over-time and requests-per-minute charts. Implemented on both
  `LedgerStore`s and asserted by the same contract suite (H-021's shape), because a
  `count(*) FILTER` and a Python `sum(1 for …)` are two sentences about one rule and the
  ways they drift are exactly the ways a chart becomes quietly wrong.
- **Seven counters on `/admin/usage/totals`** — the five cache dispositions, the avoided
  cost, and the failover count. They live on the existing aggregate rather than in a new
  endpoint because the Overview asks one question — *what has this tenant been doing* —
  and answering it with four round trips would let the four answers disagree under live
  traffic.
- **Three cache columns on a ledger row view** — `cache_avoided_usd`, `cache_similarity`,
  `cache_source_request_id`. The last is the important one: it turns "this was a semantic
  hit" into "…and here is the request whose answer you were served", which is the only way
  a human can audit a similarity score after the fact, and the same provenance §P8.H1
  measures silent wrong answers with.

**The seed script holds itself to the same rule.** `scripts/seed_demo.py` configures
through `/admin/*` and generates traffic through `/v1/*`; it writes no SQL. So every
number the demo shows is a number the gateway really computed, and a metering bug shows up
in the demo rather than being papered over by a fixture that wrote what it wanted to see.

**Alternatives considered.** *A read-only database user for the console* — the
conventional answer, and it makes reason 1 worse rather than better: read-only protects
the data and does nothing for the semantics. *A GraphQL or BFF aggregation layer over the
admin API* — a third place for the ledger's rules to be re-decided. *Server components
importing `headroom`'s Python* — not a thing, and the fact that it is not a thing is part
of why the boundary is easy to keep. *Publishing the extra figures as a separate
`/admin/dashboard` endpoint shaped for this UI* — tempting, and it would make the API a
function of one client; `/admin/usage/totals` answers "what has this tenant been doing"
for anybody who asks.

**Consequences.** The console's own tests can therefore be hermetic against a stub of the
admin API rather than against a database, which is what makes the browser smoke run in CI
at all. `MAX_BUCKETS = 1440` and `MAX_LIMIT = 1000` are now published ceilings on what one
request may ask the ledger to aggregate. And the API grew a surface that has exactly one
consumer today — a real cost, accepted, because the alternative is a consumer with no
surface.

---

## H-055 — The admin token is typed in, never deployed; the session is an httpOnly cookie (Phase 7)

**Status**: accepted · **Date**: 2026-08-09

**Context.** The console authenticates with `HEADROOM_ADMIN_TOKEN` — the root credential
that creates tenants and mints keys (H-019). The session brief names the choice and
constrains it: *entered at runtime, or env-provided to the ui service — never baked into a
bundle, never committed*. Both spellings satisfy the letter of invariant 3; they fail
differently, and the difference is what this entry is about.

**Env-provided** means the token is in the ui service's environment. It is then in
`docker compose config` output, in `docker inspect`, in an ECS task definition's
environment or its secrets reference, and in whatever shell exported it — and, most
importantly, **anyone who can reach the console is already the operator**, because the
server will attach that token to any request the page makes. On a laptop behind a firewall
that is fine. On the Phase 9 ALB it is a published, unauthenticated tenant-and-key CRUD
that happens to be behind an IP allow-list.

**Decision — the operator types the token into a sign-in screen, and the console's server
exchanges it for an `httpOnly`, `SameSite=Strict` session cookie.**

- **The `ui` service is handed no secret at all**, not even by reference. Its compose
  environment is one variable and it is a URL. That is a stronger property than "no secret
  in the repo": there is no secret in the *deployment* either, so there is nothing to
  rotate, nothing to leak from an inspect, and nothing for a Phase 9 task definition to
  get wrong.
- **The token never crosses into client code.** `lib/session.ts` and `lib/admin.ts` import
  `server-only`, which makes importing them from a client component a *build* error — the
  same trick `config/routing.yaml` plays with `extra="forbid"` (H-014), enforced by the
  compiler rather than by a reviewer. There is no `NEXT_PUBLIC_*` anything.
- **`httpOnly` means the page's own JavaScript cannot read the token back**, so an
  injected script cannot exfiltrate the credential even while riding a live session.
  `SameSite=Strict` closes the CSRF: nothing should ever navigate into an operator console
  from somewhere else.
- **The token is probed before it is accepted.** `POST /api/session` calls
  `GET /admin/tenants` with it and distinguishes all three answers that matter: 200, 401
  ("the gateway rejected that token"), and 503 ("the gateway's own `HEADROOM_ADMIN_TOKEN`
  is unset, so no token can be right"). A console that stored whatever was typed would
  blame the operator's next action for their previous typo, and would send somebody
  hunting for a better token when the fix is on the other side.
- **Eight hours**, so a demo or an evening never signs itself out mid-thought, and a
  walked-away laptop is not a permanent grant.

**The trade, stated rather than buried.** The token sits in the browser's cookie jar for
the session, and anyone who can reach this console *and* has the token can act as the
operator. That is the same trust boundary the gateway's admin API already has — one root
token, no roles — so the console neither invents a weaker one nor pretends to a stronger
one. What it removes is the class of failure where reaching the console is *sufficient*.

**Alternatives considered.** *Env-provided to the ui service* — the brief's other option;
see above. *The token in `sessionStorage`, sent as a header on each call* — readable by any
script on the page, which is the one property `httpOnly` exists to provide. *A server-side
session store keyed by a random id* — strictly better at hiding the token, and it needs
shared state across the several tasks Phase 9 runs, for a credential that is already the
thing being protected. *Encrypting the cookie under a server key* — a second secret to
provision in order to protect the first, on a single-operator console. *OAuth or a real
identity layer* — the correct answer for a product and scope-lust here; the entry that
adds accounts supersedes this one, and it should supersede H-019 first.

**Consequences.** There is one more step between `make up` and a working dashboard, and
the README says so. The console is useless without the gateway's admin API switched on,
which is correct and is the 503 path above. And `tests/e2e/console.spec.ts` asserts the
properties rather than trusting them: an unauthenticated visitor sees the sign-in screen
and no data, a wrong token is refused, the cookie really is `httpOnly` and `Strict`, and
`document.cookie` cannot see the token.

---

## H-056 — The console polls; it does not stream (Phase 7)

**Status**: accepted · **Date**: 2026-08-09

**Context.** BUILD_PLAN §P7 asks for *"SSE-fed live tiles where it's cheap"*, and this
gateway is very good at SSE — it is the thing Phase 1 was built around. The temptation to
use it here is therefore real and worth answering rather than ignoring.

**Decision — every view polls a `GET`, at one of three intervals: 2 s for the live view
and provider health, 5 s for the overview and the meters, 15 s for the control-plane
tables.**

The reasoning is about what a push channel would actually cost against what it would
actually buy.

- **Every number here is already a `GET` on a tested admin API.** A stream would need a
  second transport on the gateway, a fan-out story for the several Fargate tasks Phase 9
  runs (a tenant's spend changes on whichever task served the request, and a browser is
  connected to one of them), a reconnect-and-backfill dance, and a way to test all of it
  keylessly. That is a phase, not a feature.
- **What it would buy is under two seconds, on a figure a human is watching.** The kill
  demo is legible at a 2 s poll: a request appears, the colour of the stack changes, a
  breaker flips to `open`. Nobody watching that can tell it from a push.
- **A poll degrades correctly.** A gateway restart mid-demo costs one failed request and
  the next tick recovers; a dropped stream costs a reconnect path that is only exercised
  when something is already going wrong.

Three properties make the poll a real design rather than a `setInterval`:

- **A hidden tab does not poll.** A console left open behind an editor for a day would
  otherwise be a slow permanent load against the same ledger §P8 reads.
- **Refetch never flashes a skeleton.** The previous render is held at reduced opacity —
  including across a filter change, where blanking to a skeleton would be a layout jump for
  data about to look almost the same.
- **`fetchedAt` is the clock.** Every window a view draws ends at the moment the data was
  *read*, never at `Date.now()` during render. That keeps rendering a pure function of the
  fetched result, and it is also the honest reading: a chart whose last bucket is "now"
  against four-second-old rows draws a gap that looks like an outage and is not one.

**Alternatives considered.** *SSE for the live tiles only* — the plan's own suggestion, and
the smallest version of the trade; it still needs the transport, the fan-out, and the
reconnect, for the one view where 2 s is already indistinguishable. *Websockets* — the same
costs plus a protocol. *A single adaptive interval that speeds up under traffic* — a clever
thing that makes "how stale is this" unanswerable. *`revalidate` and server components* —
would move the polling to the server and give up the pause-when-hidden property, which is
the one that matters for a console left open.

**Consequences.** The three intervals are published constants (`LIVE_INTERVAL_MS`,
`PAGE_INTERVAL_MS`, `SLOW_INTERVAL_MS`) and appear on screen in each view's badge, so
"how old is this number" is never a guess. The load is bounded and predictable: the live
view is two indexed queries every two seconds. If a later phase does want a stream, it
replaces `usePoll` and nothing else — every view consumes the same `{data, error,
fetchedAt, refreshing}` shape.

---

## H-057 — No charting library; the charts are inline SVG, to the mark spec (Phase 7)

**Status**: accepted · **Date**: 2026-08-09

**Context.** The console needs five chart forms: bars over time stacked by provider, a
sparkline in a stat tile, ranked horizontal bars, a one-row part-to-whole, and the budget
meter §P7 explicitly licenses (*"a tenant's budget as a channel strip with headroom is the
one flourish this UI is allowed"*). Recharts, visx, or Observable Plot would each draw
four of the five.

**Decision.** Hand-rolled inline SVG, in one file, roughly three hundred lines
(`ui/components/charts.tsx`).

- **A library's defaults are the wrong design language, and overriding them is the work.**
  This console is true black, cool zinc, mono numerals, hairline chrome. A charting library
  arrives with its own type scale, its own palette, rounded-both-ends bars, dashed
  gridlines, and a tooltip that looks like somebody else's product — and every one of those
  is an override, which is the same amount of code as drawing the mark, plus a dependency.
- **The mark specs are exact and worth hitting exactly**: bars capped at 24 px with a 4 px
  rounded *data* end and a square baseline, a 2 px surface **gap** between touching fills
  rather than a stroke around them, solid hairline gridlines one step off the surface, no
  number on every point, and text in ink tokens rather than in the series colour. Those are
  a handful of lines each when you own the path and a fight when you do not.
- **The channel strip is bespoke anyway.** It is the flourish the plan allows and the
  metaphor is load-bearing rather than decorative: settled spend and live reservations
  stack from the bottom like signal, the space above them is *headroom*, and the hairline
  across the top is the cap you must not clip. That is BUILD_PLAN §0.2 rule 5 drawn —
  the gate compares **committed** spend against the cap, never landed alone, and a
  dashboard rendering only the landed bar would be D-019 with a nicer font.

**The palette is computed, not chosen.** The five series colours are the validated dark
categorical steps, checked against *this* console's surface (`#131417`) rather than
assumed: five adjacent slots, worst-pair CVD ΔE 8.4, worst-pair normal-vision ΔE 19.3, all
≥ 3:1 against the surface. A colour follows the **entity** — a provider's slot comes from
its index in the gateway's own provider list, so a provider going silent during a kill
demo never repaints the one that took over — and a sixth provider folds into a neutral
rather than being handed a generated hue nobody checked. The four status colours are the
fixed status palette and are never used as a series; each ships with a word beside it, so
no state is ever carried by hue alone.

**One bug this decision caught, recorded because it is the kind that ships.** The plots
stretch to their container with `preserveAspectRatio="none"`, which scales x and y
independently — and any `<text>` inside them is squashed by the same factor. In a
half-width card the axis ticks came out as unreadable glyphs. The fix is that axis labels
are HTML beside the SVG, not SVG text inside it; a library would have had this right, and
a library would also have had to be talked out of dashed gridlines.

**Alternatives considered.** *Recharts* — the default choice, ~500 KB, and its bar/stack
geometry cannot express the surface-gap rule without custom shapes, at which point the
library is drawing rectangles. *visx* — low level enough to comply, and it is a dozen
packages to arrive at the scales three of these charts do not need. *Observable Plot* —
lovely, and it wants a grammar where this needs five shapes. *A canvas renderer* — nothing
here is dense enough to need one, and it would forfeit the accessible-name and `<title>`
hover layer SVG gives for free.

**Consequences.** Five chart forms is the vocabulary; a sixth is a deliberate addition to
one file rather than a config object. There is no zoom, brush, or animated transition, and
none is missed. `ui/tests/unit/series.test.ts` pins the two properties that are correctness
rather than taste — a bar is rounded at the data end and square at the baseline, and a
colour follows its entity across a filter change — because those are the ones a later edit
would break without noticing.

---

## H-058 — The console's tests: Node's own runner, and a stub gateway for the browser (Phase 7)

**Status**: accepted · **Date**: 2026-08-09

**Context.** BUILD_PLAN §P7's gate names a *"Playwright smoke"*, and the session brief adds
the constraint that makes it interesting: browser tests must be *hermetic against the
compose stack or mocked APIs — no live providers*, and CI time must stay sane. Meanwhile
invariant 4 says CI is fully keyless, and H-004's CI is three jobs that a fourth and fifth
should not double.

**Decision — two layers, and neither one needs a database.**

**Unit tests run on `node --test` with native TypeScript stripping (Node 24), so the
console's test layer costs zero dependencies.** No vitest, no jest, no transform step, no
config file. What is tested is what is worth testing without a browser: the proxy's path
allow-list and header construction, the money arithmetic, and the chart layer's geometry
and colour assignment. Twenty-seven tests, a tenth of a second.

The money tests earn their place. The gateway serialises `NUMERIC` as a string precisely so
a double never touches it (H-024), and a console that did `parseFloat` on arrival would
undo six phases of exactness in its first line — invisibly, because a float sum of small
numbers looks entirely reasonable until somebody compares it against `psql`. So every total
is summed in integer picodollars, the same 1e-12 quantum the budget store uses (H-030), and
the display is rounded half-up on the integer rather than through a float. That last part is
not theoretical: `Number(34_500_000n) / 1e12` is a double a hair below 0.0000345, so
`.toFixed(6)` renders `$0.000034` — the same class of last-mile error, arriving in the last
file. `ui/tests/unit/format.test.ts` asserts both the right answer and the wrong one.

**The browser smoke runs against a Node stub of `/admin/*`** (`ui/tests/stub/gateway.mjs`),
not against compose. Seven tests, Chromium only, ~2.5 s of test time on top of a browser
download. Hermetic by construction: no Postgres, no DynamoDB, no provider, no key — which
is exactly what lets it run on every pull request rather than on the operator's desk.

Three things about the stub are deliberate:

- **Its fixtures are interesting.** A tenant at 83% of its cap, a failover with its
  `failover_from` and `failover_error`, a semantic hit with a similarity and a source
  request id, a breaker `open` with a cooldown counting down. A smoke against an empty
  database proves the pages render and nothing about whether they render the things they
  exist for.
- **It checks the credential.** An unauthenticated `/admin/*` call gets a 401, so the smoke
  exercises the console's token path rather than routing around it — which is what makes
  the httpOnly-cookie assertions mean something.
- **Its clock is real.** Timestamps are relative to when the stub started, because every
  window in the console ends at "now"; fixed dates would put the whole fixture outside the
  last ten minutes and the smoke would be asserting against empty charts it believed were
  full. (It was, briefly. That is how this line came to be here.)

**And the smoke runs the server that ships** — `node .next/standalone/server.js`, the
runtime image's own entrypoint, not `next start`. A smoke against a different server than
the one that deploys is a smoke that can pass while the artifact is broken.

**Alternatives considered.** *vitest* — one dependency, a config file, and a watcher nobody
would run; the built-in runner does what is needed. *Playwright against the compose stack* —
the most faithful, and it makes the browser job depend on Postgres, DynamoDB, a migration,
and a seed, any of which failing reads as a UI failure. *Component tests with Testing
Library* — a third layer between the two that exist, testing render output that the browser
smoke already renders for real. *Three browser engines* — triples a job that exists to prove
the console works at all. *Visual regression snapshots* — a genuinely useful thing for a
design this specific, and a stream of false failures from font rendering across machines;
the operator's screenshots in `docs/evidence/p7-dashboard/` are the visual record instead.

**Consequences.** CI gains two jobs (`ui` and `ui-e2e`) and the image job gains a second
build-and-smoke. Neither reads a secret. The stub is now a thing that has to stay in step
with the admin API's response shapes — a real maintenance cost, and the reason it is
written as literal fixtures rather than generated: when a shape changes, the diff is the
list of what the console will now see.

---

## H-059 — H1 seeds the cache from the answer key, not from a model run (Phase 8)

**Status**: accepted · **Date**: 2026-08-10

**Context.** BUILD_PLAN §P8.H1 leaves this open in a parenthesis — *"seed the cache with the
133 canonical entries (reference answers derived from the answer key / **a prior scored
Backline run**)"* — and the slash is doing a great deal of work. The two readings produce
different experiments.

Seeding from a prior scored run is the more *realistic* cache: it holds what a model
actually said, including the ~7% of answers that were wrong. Seeding from the answer key
holds only true answers.

**Decision.** **The answer key.** Every seeded entry is correct for its own question by
construction.

The argument is about what the headline number *means*. H1 reports a **silent-wrong-answer
rate**, and with a model-run seed that rate is a sum of two independent quantities: *the
model was wrong about the question the cache answered*, and *the cache answered the wrong
question*. Only the second is a fact about semantic caching; the first is a fact about
Sonnet on 2026-08-08. They cannot be separated after the fact, because a probe that hits a
wrong entry whose answer happens to be wrong for *both* questions is indistinguishable from
a probe that hit a right entry the model fluffed. So a model-run seed makes the metric a
blend, published under the name of one of its terms.

With a key seed the attribution is exact: **a hit is wrong if and only if the cache resolved
it to the wrong source question.** That is the quantity the experiment is named after, and
it is the quantity nobody currently measures.

Two consequences are accepted rather than hidden. The measured hit rate is *unchanged* by
this choice — hit rate is a function of the embedding space and the threshold, and entry
bodies are never embedded — so nothing about the savings side of the curve depends on it.
And the seeded corpus is a *best-case* cache in one specific respect: a real cache holding a
wrong answer serves that wrong answer even on a correct hit. That is a real effect, it is
the *model's* error rate rather than the cache's, Backline has already published it (93.3),
and the two compose by inspection.

**Alternatives considered.** *A prior scored run* — see above; it also makes the artifact
depend on one dated run of a paid suite, so regenerating the corpus would cost $8 rather
than $0. *Both, as two arms* — doubles every table to separate a term the reader can compose
themselves from a number Backline already publishes. *Synthetic answers from a cheaper
model* — invents a third error source and measures none of them.

**Consequences.** A seeded entry's body is synthesised from the key by code with no model in
it, in Backline's own `ANSWER:` protocol, so the gateway's store and replay path handle a
real Anthropic-dialect body unchanged. The artifact is therefore reproducible offline and
for free, which is what makes a regenerated corpus a reviewable diff rather than a purchase.
H1 measures the cache; it does not measure the model, and REPORT.md says so in the sentence
that states the result.

---

## H-060 — The probe is the prompt as sent, and the answer protocol is never paraphrased (Phase 8)

**Status**: accepted · **Date**: 2026-08-10

**Context.** Backline's prompts are two things joined by a blank line: a question (*"What is
Mariko Chorus's net payable for 2026-02, after recoupment?"*) and a machine-readable
protocol (*"End your reply with a line exactly `ANSWER: $<amount>` (USD)."*). All 133 split
that way, into 133 bodies and **8 distinct tails**. Two decisions fall out and neither is
obvious.

**Decision — the paraphrase rewrites the body and re-attaches the tail byte-for-byte.**

A paraphrase of the tail is not a paraphrase. `ANSWER: $<amount>` is a wire format
Backline's scorer parses; rewording it changes *what was asked* rather than *how*, and a
corpus whose probes ask for a different output shape would measure a scorer, not a cache.
Enforced mechanically: the generator re-attaches the original tail and a check asserts byte
equality, so a model that helpfully rephrased it cannot reach the artifact.

**Decision — the embedded text is the whole prompt, tail included; the body alone is a
secondary sensitivity curve.**

This is the load-bearing half. The shipped gateway embeds the user turn
(`Dialect.cache_probe`), and the user turn is the whole prompt. So the *only* similarity
numbers that describe a cache anyone could deploy in front of this suite are the ones
computed over the full prompt — boilerplate and all.

And the boilerplate is not noise to be cleaned away: eight tails shared across 133 questions
raise the floor under every pairwise similarity, and they raise it *most* between questions
that share an answer type — which is to say, between the near-misses. That is a real
property of this workload and plausibly of every workload where an application templates its
prompts, which is most of them. Removing it would produce a prettier curve about a system
nobody runs.

The question-body-only curve is reported beside it because it costs one more embedding pass
over the same texts and it answers the one objection this design invites. Naming which is
primary **before** either exists is the whole point: after the fact, "the boilerplate
inflated it" and "stripping it was cherry-picking" are both available.

**Alternatives considered.** *Embed the body only* — cleaner numbers about a gateway that
does not exist. *Strip the tail in the gateway before embedding* — a real design, and
plausibly a future feature, but inventing it here would mean measuring an unshipped feature
in the phase whose job is to measure the shipped one. *Paraphrase the tail too* — measures
Backline's answer extractor. *Drop the tails from the corpus entirely* — the prompts would no
longer be Backline's prompts, and the answer key would no longer apply.

**Consequences.** Every probe carries both texts and both vectors, so the artifact is roughly
twice the size it would otherwise be and every table in REPORT.md has two columns. If the
primary curve turns out to be dominated by boilerplate similarity, that is a finding about
prompt templating and it is reported as one — with the secondary curve standing next to it as
the counterfactual.

---

## H-061 — A wrong hit is wrong by Backline's own arithmetic, and a lucky one is not correct (Phase 8)

**Status**: accepted · **Date**: 2026-08-10

**Context.** §P8.H1's metric is *"hit resolved to a different source question — with the
returned answer provably wrong for the asked question, courtesy of the answer key"*. The word
carrying the weight is **provably**, and it rules out the easy implementation.

The easy implementation is `source != probe.source`. It is wrong in a specific and
embarrassing direction: the suite has 10 `abstention` questions whose expected answer is the
same `ABSTAIN` token, so a probe for one abstention question hitting another's entry receives
an answer that **is correct**. Counting that as poison would inflate the headline number with
the corpus's own structure. There are quieter cases too — two `count` questions that both
answer 9.

**Decision — three outcomes, not two, and the middle one is Backline's arithmetic.**

A hit to a different source is a **silent wrong answer** only when the served entry's answer
is *not equivalent* to the probe question's expected answer. When it is equivalent, it is a
**benign collision**: recorded, reported, and never counted as a correct hit.

*Equivalence is not re-implemented.* It is computed over all 133 × 133 ordered pairs at
artifact-build time using `evals.answers` and `evals.scoring` — the code that scores the
suite, with its per-kind parsing, its `money` tolerances, its `set` semantics and its
abstention protocol — and the resulting matrix is committed inside the golden artifact. CI
then replays it with no Backline on the path, no Postgres, and no network. That is the P5
corpus pattern exactly: compute once where the dependencies live, commit the numbers, and let
the keyless suite do arithmetic over them.

**A benign collision is never folded into "correct".** A cache that returns the right answer
because two questions happened to share one has been graded on the corpus rather than on
itself, and the grade would not survive a corpus with fewer ties. Reporting the two columns
separately also makes the abstention structure visible instead of buried.

**Alternatives considered.** *Source-identity alone* — over-counts poison by the corpus's own
tie structure, in the direction that makes the finding look worse than it is while making the
measurement less true. *Re-implement the comparison in `experiments/`* — a second scorer to
keep in step with Backline's, whose drift would be invisible and would move the headline
number. *Import Backline at sweep time* — kills keyless CI and pins the experiment to a
sibling repo's working tree. *Count benign collisions as correct* — see above. *Count them as
poison* — the reverse error, and also wrong: the caller got a right answer.

**Consequences.** The artifact carries a 133 × 133 boolean matrix and the Backline commit
that produced it, so "provably wrong" has a provenance rather than a claim. Regenerating the
artifact requires Backline present; the sweep never does. If Backline's scorer changes, the
matrix is stale until rebuilt — which is why the Backline `suite_hash` and git sha are both
stamped into the artifact and asserted by a test.

---

## H-062 — Two probe families: a paraphrase has a right answer to find; a novel question does not (Phase 8)

**Status**: accepted · **Date**: 2026-08-10

**Context.** §P8.H1's corpus is paraphrases, and a paraphrase is the *favourable* case for a
semantic cache: the question it paraphrases is sitting in the cache, so a right answer is
available and the only question is whether the threshold finds it. A production cache spends
most of its life in the other case — asked something it has never seen, with 132 near-misses
and no right answer anywhere. There, **every hit is wrong by construction** (or lucky, per
H-061).

Measuring only the favourable case would publish a safety curve that omits the situation the
safety question is actually about.

**Decision. Two pre-registered families, with the plan's as primary.**

- **Family A — paraphrase probes** (399). §P8.H1's curve, and the headline.
- **Family B — novel-question probes** (133). Each canonical question probed against a cache
  holding the other 132, leave-one-out. No true match exists.

Family B costs **nothing**: it needs no paraphrases, only the 133 canonical vectors, so it is
producible before a dollar is spent and it is the half of H1 a reader can reproduce from a
clone. It is the floor Family A sits on — the false-positive rate of the embedding space
itself, with the corpus's own hard negatives (templates crossed with 150 artists and 12
periods) doing the work no invented negative could do honestly.

τ₀, the recommended threshold (H-063), is computed over **A ∪ B** rather than over A alone,
because a threshold that is safe only for questions you have already answered is not a safe
threshold.

**Alternatives considered.** *Family A only* — the plan's letter, and it measures the easy
half. *Family B only* — free and reproducible, and it cannot measure hit rate at all, since
there is nothing correct to hit. *A held-out split (seed 100, probe 33)* — a smaller cache
and a smaller answer key for no gain; leave-one-out uses every question as both. *Invented
hard negatives* — the thing this corpus was chosen to avoid: a negative tuned until the test
passed.

**Consequences.** Every table in REPORT.md has a family column, and the two curves must be
read together — quoting Family A's hit rate beside Family B's poison rate would be the most
flattering possible pair and is exactly what the pre-registration forbids. Family B also makes
a partial result possible: it can be measured, and was, before the paraphrase batch existed.

---

## H-063 — The recommended threshold is a rule fixed before the curve, not a reading of it (Phase 8)

**Status**: accepted · **Date**: 2026-08-10

**Context.** §P8.H1 asks for *"the resulting savings-vs-poison curve and its knee"*. "Knee" is
a word, not a definition, and a curve with a free reader is a curve that recommends whatever
the reader wanted. This is the single place in H1 where post-hoc discretion could change the
headline recommendation, so the discretion is spent in advance.

**Decision.**

> **τ₀, the zero-poison floor** — the **lowest** grid threshold at which the
> silent-wrong-answer count over **Family A ∪ Family B** is zero, and stays zero at every
> higher grid point.

Because hit rate is non-increasing in threshold, τ₀ is *also* the highest-savings threshold
among the zero-poison ones, so one rule answers both "where is it safe" and "where is it
best" without a second, negotiable trade-off parameter. The "and stays zero above" clause
matters: the wrong-hit count is **not** monotone — a wrong neighbour can be displaced by a
right one as the bar rises — so the lowest zero point and the point above which zero holds
are different numbers, and picking the first without the second would recommend a threshold
that is unsafe slightly higher up.

If no such threshold exists in `[0.700, 0.990]`, **τ₀ does not exist** and that is reported as
BUILD_PLAN's own second branch — *"semantic caching is unsafe for this workload class, here's
the threshold-by-threshold proof"* — rather than as a softer threshold found by relaxing the
rule.

Reported unconditionally beside it, whatever it says: **where the shipped default 0.90
lands.** `DEFAULT_SIMILARITY_THRESHOLD`'s own docstring already says it was measured on 12
questions and is expected to move when this data exists. If τ₀ is above 0.90, the shipped
default is unsafe on this corpus and the report says so in those words.

**Alternatives considered.** *Maximum-curvature knee detection* — a defensible name for a
choice of smoothing, on a step function where the steps are the result. *A cost-weighted
optimum* (dollars saved per wrong answer) — the honest general answer, and it needs a price
for a wrong answer, which nobody has and which would become the free parameter this entry
exists to remove. *Highest threshold with hit rate above some floor* — encodes a savings
target as if it were a safety finding. *Read the curve and argue* — what everyone does, and
the reason nobody trusts the answer.

**Consequences.** τ₀ is computed by code, not chosen, and the sweep reports the exact
similarity **breakpoints** beside the grid so a reader can see the step the rule landed on
rather than a rounded grid point. Changing the rule after seeing the curve is possible only as
an amendment to `experiments/PRE_REGISTRATION.md` in a later PR, with the original
recommendation still published.

---

## H-064 — H2 compares against the direct-local run, and admits it has no paired control (Phase 8)

**Status**: accepted · **Date**: 2026-08-10

**Context.** §P8.H2 pre-registers *"overall score within the documented ≤3.0 same-model noise
bound of the fresh-run reference points (93.3 / 92.5 / 91.6)"*. Three numbers and one bound is
not yet a criterion: against the *range* it is a 7.7-point window, against the *mean* it is
one interval, against each separately it is three tests with a choice of which to quote
afterwards.

Backline's own §A5.5 answers a stricter question by *pairing* — a fresh control and a fresh
treatment on the same day. That costs another ~$8, against §0.6's $10 for this experiment.

**Decision — the primary criterion is `|overall − 93.3| ≤ 3.0`, and the absence of a paired
control is stated before the result rather than after.**

`93.3` is the **direct-local** fresh run. It is the right comparator for the arithmetic reason
that it differs from this treatment by exactly one thing — the gateway hop — where `92.5` also
differs by a cloud, an RDS, and a Fargate CPU, and `91.6` also differs by a different commit
and a different day. Choosing the *nearest neighbour in configuration space* is the choice
that makes the residual attributable, and it is fixed here so it cannot become the choice that
makes the residual small.

`92.5` and `91.6` are reported as context, with the full per-category table beside all three in
`deploy/aws/README.md`'s shape. `python -m evals gate` is run and reported as the strict
secondary check with its known variance failure modes pre-declared — it failed on *both* runs
of the AWS experiment, in different places, and §A5.5's rule is to report that rather than
re-roll.

**The limitation, named:** without a same-day control, between-day drift — provider-side model
updates, load, sampling — sits inside this experiment's residual and cannot be separated from
the gateway's effect. The claim H2 can support is therefore *"the suite scored within the
documented same-model noise bound of its direct-local reference when run through the gateway"*,
and **not** *"the gateway changed the score by X"*. The second sentence needs the control run,
and the control run needs $8 the plan did not budget.

**Alternatives considered.** *Against the range `[91.6, 93.3] ± 3.0`* — a 7.7-point window
that almost nothing could fail. *Against the mean of the three* — averages three configurations
that differ in three ways. *A paired same-day control* — the correct experiment, and $8 outside
the line item; naming it as the thing not done is more useful than a quiet omission. *Halve
both runs to 66 questions each to afford the pair* — a different suite, not gate-comparable,
and it would forfeit the exact ground truth that makes Backline worth pointing at anything.

**Consequences.** REPORT.md's H2 verdict is a statement about a bound, not an effect size, and
the sentence that states it says so. If a later session has $8 spare, the paired control is the
single highest-value follow-up in this repo and it needs no new code — the runbook's own
commands with `ANTHROPIC_BASE_URL` unset.

---

## H-065 — "Gateway overhead" is three numbers, and the pre-registered one is the weakest (Phase 8)

**Status**: accepted · **Date**: 2026-08-10

**Context.** §P8.H2 promises *"the number that answers 'why is a gateway in Python
defensible'"*, pre-registered as **p50 overhead < 50 ms** and *"measured from Headroom's own
ledger timings"*. H-051 already named the column: `passthrough_overhead_ms`, defined as *first
upstream byte → first byte out*, and it named it in Phase 6, before this data existed.

That column is going to come back in **microseconds**. P1 measured 0.006 ms, P6 measured 0.019
ms. Publishing "p50 overhead: 0.02 ms, target < 50 ms, PASS" is true, pre-registered, and would
be a flattering non-answer to the question the plan actually asked — because
`passthrough_overhead_ms` measures the *forwarding* cost, and the gateway's real cost is mostly
spent *before* the upstream is opened, in authentication, routing, the rate limiter, the cache
lookup and the budget reservation.

And the ledger cannot separate that: `upstream_latency_ms` is *request received → first upstream
byte*, which contains the admission work **and** the provider's own time to first token, with no
mark between them.

**Decision — report three numbers, in this order, with the pre-registered one first and its
weakness stated in the same breath.**

1. **`passthrough_overhead_ms` p50/p95/p99** — the pre-registered metric, H-051's column,
   against the pre-registered `< 50 ms`. Reported first because it is the one that was promised,
   and reported with the sentence *"this is expected to be sub-millisecond, so meeting a 50 ms
   target by four orders of magnitude is a weak test"* attached to it rather than in a footnote.
2. **Admission cost, measured where the provider costs nothing** — `upstream_latency_ms` over
   ≥ 2,000 **MockProvider** requests through the full pipeline. The mock answers in
   microseconds, so what remains is the gateway's own admission work, with real Postgres and
   real DynamoDB Local behind it. Keyless, free, reproducible by a stranger with a clone, and it
   is the honest answer to "what does a gateway in Python cost". Its caveat is recorded with it:
   it excludes TLS and DNS setup to a real upstream, which httpx amortises over a keep-alive
   pool and which a direct caller pays too.
3. **End-to-end latency parity** — Backline's own per-question p50/p95 through the gateway
   against the three reference runs' p50s (12,678 / 12,508 / 13,033 ms). The caller-visible
   answer, and the noisiest: it carries a 12-second provider-side quantity in which a
   millisecond of gateway is invisible, and there is no paired control (H-064).

**Adding a fourth timing mark was considered and rejected.** A `provider_open_at` mark would
separate admission from provider time directly and would be the clean fix. It is a change to
`RequestContext`, to the ledger schema, and to the log line — in the phase whose job is to
*measure the shipped gateway*, using a column H-051 pre-registered two phases ago. Measuring a
gateway that Phase 8 modified in order to measure it is the shape of mistake this plan spends
its invariants avoiding. The mark is the right first change of Phase 9 or 11, and this entry is
the reason.

**Alternatives considered.** *Report only the pre-registered figure* — literal compliance, and
it answers the plan's stated question with a number that cannot fail. *Report only the mock
admission cost* — the useful number, and it quietly replaces a pre-registered metric with a
better one chosen after the fact. *Subtract a modelled provider TTFT from
`upstream_latency_ms`* — invents the missing mark arithmetically, and the model would be fitted
to the data it is used to interpret.

**Consequences.** REPORT.md's overhead section is three rows, not one, and the honest headline
is row 2. The keyless mock measurement runs in the suite, so the number is regenerated on every
change rather than measured once — which also makes it the first thing that would notice a
future phase putting a round trip on the admission path.

---

## H-066 — Phase 8's money stops: whose cap is whose, and why $12 sits inside a $10 line (Phase 8)

**Status**: accepted · **Date**: 2026-08-10

**Context.** §0.6 budgets **$1** for H1's paraphrase generation and **$10** for the H2 run, with
**$6** contingency behind them and a **$20** hard project total. The complication is discovered
by computing, rather than quoting, Backline's own pre-run projection for the 133-question
suite: **$11.27**. Backline's runner refuses to start when its projection exceeds `--budget`
unless `--yes` is passed — and `AWS_DEPLOY_PLAN.md` §9 names the reflex by hand: *"never `--yes`
reflexively"*.

So `--budget 10.00` does not produce a $10 experiment. It produces either a refusal, or a
`--yes` that switches off the projection guard entirely — which is strictly worse than raising
the number, because it removes the check rather than moving it.

**Decision — three stops, at three values, each doing one job.**

| Stop | Value | Job |
|---|---|---|
| `experiments/h1/generate.py`'s internal cap | **$1.00** | §0.6's H1 line, enforced by the harness: the generator refuses to issue the call that would cross it |
| Backline's `--budget` | **$12.00** | the operative stop, above its own $11.27 projection so the guard stays *on* and `--yes` is never needed |
| Headroom's budget gate on the H2 tenant | **$15.00** | the independent backstop |

**The backstop is deliberately the loosest, and that is not a mistake.** A second guard set at
or below the first fires during normal operation, which makes it a source of corrupted runs
rather than a safety net — and a Headroom 402 mid-suite would be scored by Backline as an
errored row, so a backstop that fires *destroys the experiment it was protecting*. At $15 it can
only fire if Backline's own committed-spend accounting is wrong, which is precisely the failure
a second opinion exists for. There is a pleasing symmetry in the experiment's money being
guarded by the product under measurement, and it is only pleasing because the gate reads
*committed* spend — reserved plus landed — which is §0.2 rule 5 and the thing Backline's own
runner does too.

**The arithmetic against §0.6, stated rather than absorbed.** Expected H2 spend is ~$8.09 (three
measured runs: $7.88 / $8.01 / $8.09). The $12 flag is a stop, not a plan. If a run genuinely
lands between $10 and $12 it is drawn from the $6 contingency bucket and recorded in PHASE_LOG's
spend line. Worst case for the phase is $1 + $12 = **$13 against the $20 project cap**, so the
cap holds even in the case where every stop is reached.

**Alternatives considered.** *`--budget 10.00 --yes`* — nominally inside the line item, and it
disables the projection guard to get there; the worst of both. *Raise §0.6's H2 line to $12 by
amendment* — defensible, and it edits a budget table to match a flag when the contingency bucket
exists for exactly this. *Run the gate subset (43 questions, $3.89 projected)* — cheap, and not
comparable to the 133-question reference points, so it would answer a different question for
less money. *No Headroom-side backstop* — leaves one accounting system with no second opinion,
in the repo whose product is second opinions about spend.

**Consequences.** Three numbers now live in `experiments/PRE_REGISTRATION.md` §4 and in the
runbook, and the runbook states each one's expected cost before its command. No command in this
phase that spends money is run by Claude Code; every one is handed over — the invariant-2
discipline the plan applies to `terraform apply`, applied to spend.

---

## H-067 — H3 is adjudicated from the recording that already exists (Phase 8)

**Status**: accepted · **Date**: 2026-08-10

**Context.** §P8.H3 promotes the P6 chaos suite to a reported experiment and adds *"the two-GPU
live kill"*. The session brief asks for the **delta** between what P7 captured and what §P8.H3
specifies, and explicitly: *"If P7's artifacts already satisfy clauses, say so explicitly rather
than re-demanding them."*

The temptation is to specify a fresh recording, because a fresh recording is easy to specify and
costs the *operator* rather than the phase. The check is whether the existing one answers the
pre-registered questions.

**Decision — no new GPU session. The recording exists; Phase 8 adds the adjudication.**

What exists: `tests/test_failover_chaos.py`, green in CI since P6 at three fault intensities;
`docs/evidence/p7-dashboard/` with seven stills, `hero.gif`, a kill timestamp and a psql
cross-check; and — the part that settles it — **492 ledger rows surviving in the compose volume
from the 2026-08-10 run**, 270 of them on the `vllm_a → vllm_b` chain, spanning ~92 minutes under
the runbook's 2-second loop. That is the sustained load §P8.H3 asks for, already recorded, with
per-request provenance.

What was missing was never a picture. It was **numbers**: the psql cross-check committed with P7
is three columns (requests, spend, failed over) and does not state the caller-visible 5xx count,
the `failover_error` breakdown, or the re-admission interval — the three things §P8.H3 actually
adjudicates. Those are five queries over rows that are still there, not another `docker kill` on
a container that spends minutes reloading a 27B checkpoint.

So Phase 8 ships two analysers rather than a demand: one that runs the mock chain at three
intensities and emits a committed results artifact, and one that reads the surviving rows and
adjudicates the three clauses. **If the analysis finds the rows cannot answer a clause, that gap
is reported and a re-record is scheduled** — the decision is "adjudicate first", not "assume it
is fine".

**The recovery bound is arithmetic, not a choice.** H-052 published `COOLDOWN_S = 10` and one
probe per half-open window in Phase 6; the transition happens on the request that finds the
cooldown elapsed, with no scheduler. Under one request every `T` seconds, re-admission is
observed within `10 + T`. The constant is read from `headroom/policy/health.py` rather than typed
into the pre-registration, so the two cannot drift — which also means the bound could not have
been fitted to the data, and `PRE_REGISTRATION.md` §H3.5 says so.

**Alternatives considered.** *Specify a fresh sustained-load recording* — a GPU session, a 27B
reload, and an operator evening, to produce rows materially identical to 492 that already exist.
*Report the clauses from the mock chain only* — reproducible and free, and it drops the half of
§P8.H3 that needed real hardware. *Report from `hero.gif`* — a picture is not a measurement,
which is the whole reason the ledger exists. *Re-record because the rows might be truncated by
the next `make test`* — real (H-029's caveat) and answered by extracting the analysis artifact
**now**, which is what the analyser's committed JSON is.

**Consequences.** H3's live half depends on rows in a local volume, so the extracted artifact is
committed to `docs/evidence/p8-experiments/` in this PR and the raw query output beside it —
after which the volume may be wiped freely. The mock half runs in CI on every pull request and is
reproducible by a stranger; the live half is the operator's desk and is labelled as such, exactly
as P6's two-GPU demo already is.

---

## H-068 — The entity check counted sentence position as entityhood; amended before any measurement (Phase 8)

**Status**: accepted · **Date**: 2026-08-10

**Context.** The operator ran the paid paraphrase batch. It completed **114/130 with 16
UNRESOLVED**, and every one of the 16 failed on the same shape:

```
hand-catalog_lookup-01   lost name: Counting          (candidate 3 also: lost code: EP)
hand-contract_terms-01   lost name: Please
cross_collateral-004     lost name: Across
cross_collateral-005     lost name: Across
hand-cross_collateral-01 lost name: Across
sql_analytics-003        lost name: Summing
sql_analytics-004        lost name: Summing
sql_analytics-008        lost name: Across
hand-sql_analytics-02    lost name: Exactly
hand-reconciliation-01   lost code: ONLY; lost name: Audit
multi_step-006 / -008    lost name: Suppose; lost code: US / lost name: States, Suppose, United
multi_step-007           lost name: Germany, Suppose
multi_step-009 / -010    lost name: Suppose; lost name: Kingdom, Suppose, United
hand-multi_step-02       lost name: Kingdom, Suppose, United
```

`Suppose`, `Please`, `Across`, `Counting`, `Summing`, `Exactly` and `Audit` are not entities.
They are ordinary words that happen to open a sentence, and `_NAME`'s `\b[A-Z][a-z]+\b` cannot
tell a capital that means *proper noun* from a capital that means *this is where the sentence
starts*. No faithful paraphrase can preserve them: rewording a question is exactly the operation
that moves its first word. The failures were **identical across all 3 candidates over all 3
attempts**, which is the diagnostic — a rule no correct answer satisfies is not strictness, it is
a stuck generator burning `MAX_ROUNDS` on every draw.

The module's own docstring argues the asymmetry that made over-broadness the right *default*: a
false positive costs a regeneration, a false negative costs a probe that quietly asks a different
question. That argument holds and is not retracted. What it assumed is that a false positive is
*payable*. For these 16 it is not payable at any price, because the correct answer sits outside
the accepted set.

**This is a pre-measurement amendment (risk register item 3).** The batch is fixed before any
measurement exists: no sweep, figure or analysis has read `h1_paraphrases.json`, the corpus is
not built from it, and no H1 number exists anywhere in the repo. The checker is corrected against
*failures*, never against a curve.

**Decision — three narrow amendments, and the exemptions apply to the body, never to the
candidate.**

1. **Position is not entityhood.** A capitalised word is exempt only when it opens a sentence
   **and** is an ordinary English word (`COMMON_WORDS`). The conjunction is the safety property:
   drop the first clause and the track `"Bones"` stops being required; drop the second and
   `Voltage has more than one agreement` loses its artist. Only spaces and tabs are stepped over
   when deciding "opens a sentence", so `"Bones"` and `(Kinetic Digital` — quoted, bracketed —
   are not sentence openings and keep their capital's meaning. A name is exempt only if *every*
   occurrence is positional; one mid-clause use makes it an entity everywhere in that body.
2. **ALL-CAPS emphasis is not a code.** `report ONLY findings that are genuinely out of
   tolerance` is emphasis; `_CODE` read it as an identifier and three of three candidates dropped
   it. Exempt only at length ≥ 3 and only for words already in the lexicon, which is what keeps
   `US`, `GB`, `DE` and `EP` codes.
3. **A gloss the body writes itself declares an equivalence.** `United States (US)` is one entity
   with two spellings *because the question says so*, so the pair becomes one `AliasGroup`
   satisfied by either. `U.S.` normalises to `US` on both sides of the comparison. Losing **both**
   spellings still fails.

**Per-case adjudication, as asked.**

* **`US` ≡ `U.S.` ≡ `United States`, and `GB` / `DE` / `JP` likewise — canonical-variant
  equivalence, accepted.** Not from a hardcoded world model: the equivalence is read out of the
  question's own text, which writes `in the United States (US)`. A prompt that introduces an
  abbreviation has licensed it. Both directions are legitimate and the run produced both —
  candidates 1 and 3 of `multi_step-006` kept the long form and dropped `(US)`, candidate 2 kept
  `US` and dropped the long form. Failing either is failing a correct answer. Four groups exist
  across the 130 bodies: `United Kingdom (GB)` ×4, `Germany (DE)` ×2, `United States (US)` ×2,
  `Japan (JP)` ×1.
* **`EP` — exact-token survival required.** The opposite call, deliberately. `EP` fixes the
  *scope* of `hand-catalog_lookup-01` ("original album or EP, plus any compilations"); a
  paraphrase that drops it asks a narrower question and the answer key stops applying. The
  question glosses nothing, so there is no in-text licence, and unlike a country it has no
  standard punctuation variant. The run itself decides it: **two of the three candidates kept
  `EP`**. A token two of three drafts preserve unprompted costs nothing to require, so it stays
  exact and the third candidate stays refused.
* **`ONLY` — dropped as a requirement.** Emphasis capitalisation of a word `STOPWORDS` already
  lists as droppable in its title case (`Only`); consistency demanded one answer for both. The
  honest limit: the mechanical layer now protects no spelling of "only" in that question, and a
  paraphrase that drops the restriction changes the task. That was already true — the layer has
  never protected lower-case prose — and it belongs to the operator spot-check, the second clause
  of §P8.H1's QA chain, not to this module.
* **`Germany`, `United`, `Kingdom`, `States` — still required, but satisfiable by their glossed
  code.** They are real entities and their loss must still be caught; the alias group is what
  makes "loss" mean *the referent is gone* rather than *the spelling changed*.

**Alternatives considered.** *Add the seven words to `STOPWORDS`* — one line, and it drops
`Across`, `Counting` and `Audit` everywhere including mid-clause, where a capital is real
evidence; the fix would silently unprotect any future entity sharing those spellings. *Exempt
every sentence-initial capital regardless of the word* — no lexicon to maintain, and it stops
requiring `Voltage`, `Germany` and `Meridian` wherever a question opens with them, a false
negative in exactly the class this module exists to prevent. *Raise `MAX_ROUNDS`, or hand-write
the 16 paraphrases* — treats a broken rule as bad luck, and hand-written probes are not the
instrument the pre-registration describes. *Ship the 114 and report on those* — an uneven corpus
that reweights itself toward whatever the model found easy, which `build.py` already refuses.

**Consequences.**

* **The 114 already committed were re-validated against the corrected checker: 342 paraphrases,
  0 new failures.** Nothing moved to unresolved. Expected, since the amendment only ever removes
  requirements — but run rather than assumed, because "expected" is not a measurement.
* **Blast radius, measured over all 130 bodies:** the amendment stops requiring exactly `Suppose`
  ×6, `Across` ×4, `Summing` ×2, `Counting`, `Please`, `Exactly`, `Audit`, and the code `ONLY`.
  No artist, track, label, ISRC, statement id, figure or period changes status. The 16 pinned
  tests are per-question and assert both halves — spurious token absent, real entities present.
* **The 16 still need regeneration.** The generator never persisted rejected candidates, so the
  drafts that would now pass are gone. `--only` re-runs exactly those ids for about $0.05; the
  command is in `experiments/RUNBOOK.md` and is the operator's to run.
* **Residual, stated rather than hidden.** An alias group is satisfied by either spelling, so a
  paraphrase substituting `United States (GB)` for `United Kingdom (GB)` would keep `GB` and pass
  the mechanical layer. Catching a *substituted* entity rather than a dropped one needs a check
  on the tokens a paraphrase **adds**, and the run's own data says that check is not viable as
  written: 46 of the 114 accepted questions contain a paraphrase introducing a capitalised word
  the body lacks, nearly all sentence-opening verbs (`Identify` ×13, `Examine` ×13, `Review`
  ×12). Substitution stays with the operator spot-check, which exists for precisely this.
* **`Counting Crows` is the shape of the remaining false negative:** a real name whose first word
  is a common word *and* which opens the question. Its second word stays required, so the entity
  is never wholly unprotected. No such name is in this suite; recorded so the next reader need
  not rediscover it.

---

## H-069 — A two-part ask is an entity: the compound-ask check, and `--only` made a redo (Phase 8)

**Status**: accepted · **Date**: 2026-08-10

**Context.** The operator ran the spot-check — the half of §P8.H1's QA chain no checker can do
— and failed `contract_terms-004#p3` for scope drift. The body asks two things:

```
As of 2026-06-30, what royalty rate applies to Paloma & The Effigy's digital streaming
revenue? Cite the governing clause.
```

The candidate asked one:

```
p3  What clause specifies the royalty rate applicable to Paloma & The Effigy's digital
    streaming revenue on 2026-06-30?
```

Answer the rewrite correctly and you have named a clause. Answer the original correctly and you
have named a **rate** *and* a clause. The rate survives in the rewrite only as a modifier inside
the thing being asked for, which is not the same as being asked for. Every mechanical rule
passed it — entity, period, figure, length, no blank line, no protocol token. There was nothing
for the checker to see, because nothing was lost: the ask was *compressed*.

The operator forced a redraw (~$0.001). **The fresh batch reproduced the identical drift in 2 of
3 candidates**, verbatim:

```
p2  Cite the clause that sets Paloma & The Effigy's digital streaming royalty rate as of
    2026-06-30.
p3  Which governing clause specifies the royalty rate applicable to Paloma & The Effigy's
    digital streaming revenue on 2026-06-30?
```

Independent draws at temperature 1.0, the same collapse. That is the diagnostic: not a bad
sample, but `claude-haiku-4-5` systematically compressing *answer-plus-citation* into
*citation*. And the batch's own history shows it was never one probe — the draw the operator
rejected sat beside `Identify the clause that sets …`, which collapses the same way and which
the seeded sample of 20 simply never showed him.

**Decision — make the compound ask survive mechanically, on the same footing as an entity.**

`compound_ask()` reads the shape off the body: an interrogative sentence **plus** a separate
sentence opening with a request verb (`REQUEST_VERBS`). It extracts what each part demands —
the head noun of the instruction's object (`clause`), the head noun of the interrogative's
wh-phrase (`rate`). Nothing is listed: `clause` and `rate` appear nowhere in `checks.py`, and
the rule finds **25 questions across three categories**, not one id.

A candidate satisfies it when the two demands land in **two different asks**. English joins two
demands with a coordinator, a sentence break, or a participial adjunct, so `ask_segments()`
splits on exactly those three and the demands must fall on opposite sides of one. Both inside
one segment is the collapse — *"Cite the clause that sets X's royalty rate"* mentions the rate
without asking for it. All four faithful forms pass and are pinned as tests: coordinated
interrogatives (`…, and which clause establishes it?`), coordinated objects of one verb
(`Identify the governing clause and the royalty rate`), two sentences (`…? Please cite the
relevant clause.`), and the adjunct (`…, citing the controlling clause`). Order is surface, so
the citation may come first.

**Every one of the 25 compound bodies satisfies its own rule**, and that is a test. It is
H-068's diagnostic turned on the amendment that answers H-068: a rule the original text fails
is broken, not strict.

**The rubric was deliberately *not* strengthened.** The obvious second lever is a prompt rule
against compressing a two-part ask. Rejected: `RUBRIC_VERSION` is stamped into the corpus and a
batch generated under one version is never mixed with another (`rubric.py`), so changing the
system prompt forces a redraw of all 130 — 113 of which are fine — and a fresh spot-check of all
390 probes. The evidence says that price buys little: 50 of 75 compound-ask candidates already
pass, so this is a 1-in-3 failure to be retried, not H-068's 0-in-3 rule that admitted no
correct answer. The mechanical check is the durable fix because it holds for every future draw
whatever the model does. **If redraws thrash** — the same ids landing `unresolved` across
repeated `--only` runs — the next lever is `RUBRIC_VERSION = 2` plus a full regeneration, and
that is a costed decision for the operator, not a silent one.

**Alternatives rejected.** *Require the demand nouns to be present* — the collapsed candidates
all keep both words, so presence is exactly what fails to discriminate. *Count ask sites
(wh-heads and request verbs)* — fails `Identify the governing clause and the royalty rate`, one
verb with a coordinated object, which is faithful; a rule that rejects a correct answer is the
H-068 mistake repeated. *Leave it to the spot-check* — it samples 20 of 390 and had already
missed the collapsed neighbour of the probe it caught.

**Consequences.**

* **The accepted batch was audited against the rule: 25 of 75 compound-ask candidates fail,
  across 17 of the 25 questions.** They are recorded in `AWAITING_REDRAW` in
  `tests/test_experiments_h1.py` as a redraw that has not happened yet, never as a tolerated
  exception. `build.py` re-checks every committed paraphrase and will refuse to assemble a
  corpus until they are redrawn — which is the correct state, loudly.
* **The audit set is an upper bound, so it clears itself.** The test asserts *failures ⊆
  `AWAITING_REDRAW`*: green as redraws land, red the moment a collapse — or any other check
  failure — appears anywhere else.
* **Pre-measurement, per risk register item 3.** No sweep has read the current corpus, and the
  checker is corrected against *failures*, never against a curve.
* **Stated limit.** This catches a two-part ask collapsing to one, not every drift. A candidate
  keeping two separate demands but swapping one for something the body never asked would pass
  here; and a body whose parts are all instructions (the reconciliation runs) is out of scope
  deliberately — no collapse has been observed there, and widening a rule to a shape without
  evidence is how H-068's stuck generator happened. The spot-check remains the chain's second
  clause, now with a smaller job.

**And the second finding, which is why the first one cost hand-editing.** `--help` documented
`--only` as "question ids to (re)do"; the implementation skipped any question that already had
paraphrases, so the flag could only *resume*. Forcing the redraw above required deleting the
question's entry out of `h1_paraphrases.json` by hand. **The operator's spot-check rejecting a
mechanically valid paraphrase is a designed-for outcome — it is the whole point of a human
clause in the QA chain — and it must not require surgery on the evidence.** `--only` now redoes:
`select()` is one function shared by the projection and the run, a bare run resumes, `--only`
regenerates the named ids complete or not, and an id not in the suite is an error rather than a
silent no-op. The entry being replaced is dropped only once a call has been paid for, so a
budget stop leaves the question exactly as it was, and a redraw that then fails leaves the id
**absent** and `unresolved` rather than carrying text the operator rejected.

---

## H-070 — The redraws thrashed, so the rubric asks too: `RUBRIC_VERSION` 2 (Phase 8)

**Status**: accepted · **Date**: 2026-08-10

**Context — H-069's own lever, invoked on H-069's own condition.** H-069 added the
mechanical compound-ask check and deliberately did *not* strengthen the prompt, on the
argument that 50 of 75 compound-ask candidates already passed: a 1-in-3 failure is a retry,
not a regeneration. It named the condition that would overturn that: *"if redraws thrash —
the same ids landing `unresolved` across repeated `--only` runs — the next lever is
`RUBRIC_VERSION = 2` plus a full regeneration, and that is a costed decision for the
operator, not a silent one."*

They thrashed. The operator ran three `--only` rounds over the 17 audited ids:

```
17 → 8 → 5 → 5
```

**Five ids are stuck**: `contract_terms-008`, `-012`, `-013`, `-014` and
`hand-contract_terms-04`, each having failed three to four independent rounds — nine to
twelve independent draws apiece, since a round is up to `MAX_ROUNDS = 3` attempts. Every
failure is the same shape, the compound-ask collapse on the *rate-plus-cite* body, verbatim
in the committed `unresolved` block:

```
collapsed the compound ask: 'rate' and 'clause' are asked for separately in the question
and are folded into one here
```

`hand-contract_terms-04` additionally loses the name `Japanese` intermittently — a second,
independent drift on the same body, which is what a model doing badly rather than unluckily
looks like. Redraw spend so far ~**$0.11**.

**The arithmetic says grinding is no longer information.** Reading the per-candidate
collapse rate back out of the round-by-round attrition (9 of 17 cleared, then 3 of 8, then 0
of 5) puts it near **one in two** on these bodies, not H-069's batch-wide one in three — and
attrition selects for the hardest bodies, so it climbs as the set shrinks. All three
candidates must pass in the same round, by design (the model is told the three must differ
from each other, and accumulating passing candidates across calls would trade that diversity
away), so a clean round is roughly `(1/2)³` ≈ **one in eight** for these questions. Another
$0.11 of grinding buys the same distribution, and 0-of-5 in the last round is that
distribution being honest.

**Decision — bump `RUBRIC_VERSION` to 2 and regenerate all 130 under it.**

The amendment is one new rule, stated positively, with a single faithful form shown, and the
rest of the rubric byte-for-byte unchanged:

```
4. KEEP A TWO-PART ASK IN TWO PARTS. When the question asks for a value AND separately
   instructs you to cite, name, report, quote or show something, the rewrite must ask for
   both, as two distinct asks — joined by "and", left as a second sentence, or attached
   as a "…, citing …" clause. Mentioning the second thing inside the first ("cite the
   clause that sets the rate") names it; it does not ask for it.
   Original: What was the closing balance for 2026-05? Cite the statement it comes from.
   Faithful: What closing balance was recorded for 2026-05, and which statement reports it?
```

**The mechanical check stays exactly as it is.** This is belt and braces, not a swap: the
rubric asks and the checker verifies, and a rule that lives only in a prompt is a rule that
holds for whatever the model felt like doing that day. The example is invented rather than
lifted from the suite — the model is being taught a *shape*, not handed a question it will
be asked to rewrite — and `test_the_rubric_teaches_a_form_the_checker_accepts` runs it
through `compound_ask()` and `check_paraphrase()`, so a prompt that taught a form the harness
rejects fails the suite instead of the batch. Teaching an unpassable form is H-068's stuck
generator reached from the other side, and it would burn the whole regeneration.

**"Batches never mix versions" is now code, because otherwise the lever does not fire.**
The rule was documented in `rubric.py` and enforced nowhere. Under it, the honest command
for a full regeneration — a bare `python -m experiments.h1.generate` — would have read the
complete v1 file, found every question complete, and printed `nothing to do`. So:

* `load_batch()` reads the stamped version; a batch from another one (or from none) arrives
  holding **no questions**, and a bare run therefore selects the whole suite. Nothing is
  lost that git does not hold, and nothing is overwritten until a call has been paid for.
* `--only` on a superseded batch is **refused**, nothing sent. Redrawing 5 of 130 under a
  new rubric would write a file stamped v2 containing five questions and 125 absences, and
  the operator would learn that from `build.py` a step later. Refusing beats destroying.
* `build.py` refuses to assemble a corpus from a batch stamped at another version. The
  corpus carries the rubric block as provenance; a corpus whose probes were drawn under a
  rubric the operator never approved is provenance that says the wrong thing.

**Cost, measured rather than guessed.** `--dry-run` (free, sends nothing) projects
`130 to generate · worst case $0.376095` against the unchanged `$1.00` stop. The worst case
prices every prompt at three bytes per token with a full `max_tokens` of output, so the
expected actual is roughly a third of it per round; the first full run's evidence — 114
questions, 163 calls including retries, **$0.19** — puts the realistic figure at the
pre-committed **~$0.30**. A run stopped by the cap is resumable in the ordinary way: what it
wrote is stamped v2, so a bare re-run resumes rather than restarting.

**Alternatives rejected.**

* *Keep grinding `--only`.* The information content of round four is zero, by the arithmetic
  above; the last round returned 0 of 5. This is the condition H-069 pre-committed to.
* *Hand-write the five paraphrases.* They would be human-drawn probes in a corpus whose
  claim is "drawn by `claude-haiku-4-5` under a stated, hashed rubric". The rubric hash would
  stop describing the artifact, and the spot-check would be the operator approving their own
  text.
* *Drop the five questions.* It removes exactly the hardest shape from a corpus that measures
  semantic-cache safety, which is selection on difficulty — the one bias H1 cannot afford.
* *Loosen the checker instead.* H-068's mistake, and the rule is validated against all 25
  compound bodies and four faithful forms. The candidates are collapsing; the rule is right.
* *Amend only the prompt and skip the version bump.* Then a corpus contains probes drawn
  under two different rubrics, and `spot_check.approved_by` means nothing at all.

**Consequences.**

* **All 390 probes are new, so the operator's spot-check is a fresh 20, not a re-read of 2.**
  The seeded sample names the same probe *ids* (the seed is unchanged, deliberately — the
  sample cannot be re-drawn until it flatters), but every text behind them is redrawn. This
  is the price H-069 costed, paid.
* **`AWAITING_REDRAW` now empties all at once** rather than id by id, when the v2 batch is
  built into a new corpus. It stays an upper bound, so it still cannot hide a new collapse.
* **Pre-measurement, per risk register item 3.** No sweep has read the current corpus. The
  curve test stays red until the regeneration, the rebuild, the spot-check and the sweep have
  all run, in that order.
* **The v1 batch is not deleted — it is in git**, at `71afa0e` plus the working-tree round
  the operator has just run, and can be diffed against v2 to see what the rule bought.
* **Stated limit — the two `$1` numbers are not the same number, and this lever fits once.**
  The harness's stop is **per invocation**; §0.6's `$1` for H1 paraphrase generation is
  **whole-project**. Landed so far: $0.19 (first run) + $0.02 (the H-068 redraw) + $0.001
  (the forced redraw) + ~$0.11 (three compound rounds) ≈ **$0.32**, and the artifact's
  `spend` block records only the *last* run, so it under-reports what the corpus cost — the
  cumulative figure lives in `PHASE_LOG.md`. After a ~$0.30 regeneration the §0.6 line is
  roughly two-thirds spent. There is room for one more full regeneration only by exceeding
  it, so a `RUBRIC_VERSION = 3` would be a budget amendment, not a repeat of this decision.
  Making the harness's stop read cumulative landed spend out of the artifact is the obvious
  follow-up; it is deliberately **not** done here, mid-lever, where it could halt the very
  run this entry authorises.
