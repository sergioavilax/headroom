# Phase Log

Append-only. One entry per phase, written at the end of the session that built it.

**Entry format:**

```markdown
## Phase N — Title (YYYY-MM-DD)

**Shipped** — what actually landed, specific enough to review against the plan.

**Deferred** — anything in the phase's scope that did not land, and when it will.

**Deviations** — every difference from BUILD_PLAN's text, with the reason. A
deviation is never silent; if it changes the plan, the amendment rides in this PR.

**Gate** — the phase's gate condition, and the command output **verbatim**. Not a
summary of the output: the output.
```

---

## Phase 0 — Bootstrap (2026-08-08)

**Shipped**

- **uv project**, Python 3.12 (`.python-version` pins it; `uv.lock` committed):
  `fastapi`, `uvicorn[standard]`, `httpx`, `asyncpg`, `pydantic` v2, `boto3`.
  `sentence-transformers` + `torch` behind an `embed` extra, with the CPU-wheel index
  pin (`[tool.uv.sources]` → `download.pytorch.org/whl/cpu`) **active** rather than
  commented out as in Backline — H-005.
- **ruff** (lint + format, line length 100, E/W/F/I/UP/B/SIM/RUF) and **mypy
  `--strict`** over `headroom`, `tests`, and `conftest.py`. Both green on the
  committed code.
- **pytest** with `asyncio_mode = "auto"`, a registered `live` marker, and
  `addopts = -m "not live"` so every default collection is keyless (invariant 4).
- **Package skeleton** exactly matching BUILD_PLAN §0.5:
  `headroom/{api,dialects,providers,policy,cache,metering,core,db}/`, each `__init__.py`
  carrying its charter and the phase that fills it. `headroom/api/main.py` exposes
  `GET /healthz` → `{"status":"ok"}` and nothing else.
- **`Dockerfile`** — `python:3.12-slim` + uv, two dependency layers, runs uvicorn.
- **`docker-compose.yml`** — `pgvector/pgvector:pg16`, `amazon/dynamodb-local:3.1.0`
  (`-inMemory -sharedDb`), and the gateway built from the Dockerfile. **Healthchecks on
  all three**, `depends_on: service_healthy` for the gateway. No secret anywhere in it;
  local config via a gitignored `.env` with a committed `.env.example`.
- **`migrations/`** with a README documenting the raw-SQL, filename-ordered convention
  (immutability of applied files, one transaction per file, no down-migrations), plus
  `headroom/db/migrate.py` — the runner, wired to `make migrate`, and a genuine no-op
  at Phase 0: with zero migration files it returns without opening a connection.
- **`Makefile`**: `up` (waits for healthy), `down`, `logs`, `ps`, `test`, `lint`,
  `fmt`, `typecheck`, `migrate`.
- **CI** (`.github/workflows/ci.yml`), fully keyless, three jobs: `lint-type` (ruff,
  ruff format, mypy, `docker compose config`), `test` (Postgres+pgvector and
  dynamodb-local service containers, both endpoints exported so the service tests run
  instead of skipping), and `image` (builds the Dockerfile and smokes `/healthz` in the
  running container).
- **Tests** — 9, all keyless: `/healthz` via the FastAPI test client; three guards on
  the keyless-by-default policy (marker registered, addopts exclude it, and a
  `pytester` run proving a `live`-marked test is really deselected under the committed
  config); three on the migration runner (filename ordering, self-documenting
  directory, no-connection short-circuit); two service proofs (Postgres reachable
  **and** offering pgvector; DynamoDB Local answering).
- **Docs**: `CLAUDE.md` (invariant 1 quoted first and prominently, then all nine §0.2
  invariants verbatim, the §0.3 session protocol, the operator's standing
  preferences); `docs/DECISIONS.md` with the H-NNN format documented and entries
  H-000…H-006; this file; `README.md` stub with MIT badge; `LICENSE` (MIT);
  `.gitignore` (env, python, node, terraform/kubeconfig state, `.claude/`);
  `.gitattributes` forcing LF.

**Deferred** — nothing from the Phase 0 scope.

**Deviations**

1. **`BUILD_PLAN` → `BUILD_PLAN.md`.** The operator committed the plan at the repo
   root without a file extension (its own H1 already reads "BUILD_PLAN.md", and every
   cross-reference in the plan and in Backline's convention names the `.md` path).
   Renamed with `git mv` in this PR. Content untouched.
2. **Host ports are 5433 / 8080 / 8001, not the canonical 5432 / 8000** (H-006). The
   first `make up` failed with `Bind for 0.0.0.0:5432 failed: port is already
   allocated` — Backline's stack was running. Phase 8's H2 needs both stacks up
   simultaneously, so coexistence is a requirement, not a convenience.
   Container-internal ports are unchanged.
3. **Additions the plan's Phase 0 text does not enumerate**, all additive:
   `LICENSE` (the README carries an MIT badge; a badge without the file is a lie),
   `.gitattributes` (LF enforcement — the repo is developed under WSL2 with Windows
   tooling in reach), `.dockerignore`, `.python-version`, and the `image` CI job that
   proves the gateway container actually serves.
4. **The CPU-torch index pin ships active**, where Backline had to comment it out
   (H-005). Verified before committing: `download.pytorch.org/whl/cpu` → HTTP 200 from
   the build machine, and `uv lock` resolved cleanly.
5. **CI proves DynamoDB Local's readiness from the runner**, not with a service-container
   `--health-cmd` (H-002): GitHub service containers cannot override `CMD`, and quoting
   a piped health command through the runner's option parser is fragile. Compose does
   use an in-container healthcheck, which is where the deliverable asked for one.

**Gate** — *fresh clone + `make up && make test` green and keyless on the operator's
machine; CI green on PR-0.*

Run against a **fresh clone** of this branch (`git clone ~/code/headroom /tmp/hr-gate`),
with no `.env`, no pre-built venv, and no exported configuration:

```
$ make up
docker compose up -d --build --wait
 Container hr-gate-db-1 Healthy
 Container hr-gate-dynamodb-1 Healthy
 Container hr-gate-gateway-1 Healthy
docker compose ps
NAME                 IMAGE                         COMMAND                  SERVICE    CREATED          STATUS                    PORTS
hr-gate-db-1         pgvector/pgvector:pg16        "docker-entrypoint.s…"   db         11 seconds ago   Up 11 seconds (healthy)   0.0.0.0:5433->5432/tcp, [::]:5433->5432/tcp
hr-gate-dynamodb-1   amazon/dynamodb-local:3.1.0   "java -jar DynamoDBL…"   dynamodb   11 seconds ago   Up 11 seconds (healthy)   0.0.0.0:8001->8000/tcp, [::]:8001->8000/tcp
hr-gate-gateway-1    hr-gate-gateway               "uv run --no-sync uv…"   gateway    11 seconds ago   Up 5 seconds (healthy)    0.0.0.0:8080->8000/tcp, [::]:8080->8000/tcp

$ make test        # bare and keyless — the two service tests skip without endpoints
=================== 7 passed, 2 skipped, 1 warning in 0.55s ====================

$ DATABASE_URL=postgresql://headroom:headroom@localhost:5433/headroom \
  DYNAMODB_ENDPOINT_URL=http://localhost:8001 make test
========================= 9 passed, 1 warning in 0.39s =========================

$ curl -sS localhost:8080/healthz
{"status":"ok"}

$ make lint
uv run ruff check .
All checks passed!
uv run ruff format --check .
16 files already formatted

$ make typecheck
uv run mypy
Success: no issues found in 16 source files

$ make migrate
uv run python -m headroom.db.migrate
migrations: none on disk yet (the first one lands in Phase 2) — nothing to do
```

Per-test detail from the same suite (in-repo run, `pytest -v`):

```
tests/test_healthz.py::test_healthz_returns_ok PASSED                    [ 11%]
tests/test_migrations.py::test_repo_migrations_dir_exists_and_documents_itself PASSED [ 22%]
tests/test_migrations.py::test_discovery_is_filename_ordered_and_sql_only PASSED [ 33%]
tests/test_migrations.py::test_run_migrations_never_connects_when_there_is_nothing_to_apply PASSED [ 44%]
tests/test_pytest_policy.py::test_live_marker_is_registered PASSED       [ 55%]
tests/test_pytest_policy.py::test_default_addopts_deselect_live PASSED   [ 66%]
tests/test_pytest_policy.py::test_live_test_is_deselected_under_the_committed_config PASSED [ 77%]
tests/test_services.py::test_postgres_is_reachable_and_offers_pgvector PASSED [ 88%]
tests/test_services.py::test_dynamodb_local_is_listening PASSED          [100%]

$ uv run pytest -m live -q
9 deselected, 1 warning in 0.17s
```

CI on PR-0 ([run 31281919485](https://github.com/sergioavilax/headroom/actions/runs/31281919485)),
all three jobs green, no annotations:

```
$ gh run view 31281919485 --json conclusion,jobs
success
lint + typecheck: success
gateway image builds and serves: success
pytest (postgres + dynamodb-local service containers): success

lint + typecheck | All checks passed!
lint + typecheck | Success: no issues found in 16 source files
gateway image builds and serves | gateway healthy
pytest (…service containers) | dynamodb-local answering (HTTP 400 to an unsigned GET)
pytest (…service containers) | ========================= 9 passed, 1 warning in 1.14s ===
```

**9 passed, 0 skipped in CI** — the service tests executed against the containers
rather than skipping, which is the half of the gate a green pytest alone would not
prove.

The first PR-0 run was also green but carried a deprecation annotation
(`actions/checkout@v4` and `astral-sh/setup-uv@v5` target Node 20, force-run on Node
24). Fixed in-branch: checkout `v7`, setup-uv `v9.0.0` — pinned to the exact release
because astral-sh publishes floating major tags only through `v7`, and an assumed `v9`
alias failed to resolve on the intervening run.

The one warning is `StarletteDeprecationWarning: Using httpx with starlette.testclient
is deprecated; install httpx2 instead` — emitted by `fastapi.testclient` under
Starlette 1.6, not by repo code. Left visible rather than filtered; Phase 1 decides
the httpx line when it builds the streaming proxy that depends on it.

**Assumed-facts register (§0.4)** — nothing was due for conversion at this gate. Two
adjacent facts were verified early, in passing: `amazon/dynamodb-local:3.1.0` boots
and answers HTTP in compose (the transport half of **A1**; the conditional-write half
belongs to P4), and `pgvector/pgvector:pg16` really does offer the `vector` extension
(asserted by `tests/test_services.py`).

**Spend** — $0.00. No provider API was called in this phase.

---

## Phase 1 — The proxy core: two dialects, real streaming (2026-08-08)

**Shipped**

- **Two proxy routes**, one pipeline. `POST /v1/messages` (Anthropic dialect) and
  `POST /v1/chat/completions` (OpenAI dialect), streaming and non-streaming, sharing
  `headroom/api/proxy.py` so that streaming fidelity, tool fidelity, error honesty, and
  timings are one implementation rather than two that drift.
- **The mid-stream cut, handled first.** `tests/test_mid_stream_cut.py` was written
  before the happy path, per risk register item 1. A stream that dies mid-answer — or
  simply stops without its terminal marker — ends in a terminal error event in the
  caller's own dialect (`event: error` for Anthropic, an `error`-bearing `data:` frame
  and **no `[DONE]`** for OpenAI), never a silent truncation. H-008.
- **SSE passthrough that observes rather than re-frames** (`headroom/core/sse.py`,
  H-007): upstream bytes go downstream unchanged while a copy feeds an incremental
  parser that tells the gateway whether the stream completed. Phase 3's usage extraction
  and Phase 5's stop-reason check plug into the same tap.
- **Nothing buffers.** `client.send(stream=True)` → `StreamingResponse`, proved
  *causally* in `tests/test_no_buffering.py`: the upstream is held at a gate and the
  client is checked to already have bytes while that gate is shut. No sleeps, no timing
  thresholds. The client-disconnect path is covered there too — outcome recorded, the
  upstream response released.
- **MockProvider** (`headroom/providers/mock.py` + `mock_scripts.py`) — the load-bearing
  test double. Both dialects, scripted responses, scripted usage blocks, scripted
  chunking (including a re-chopper that splits every SSE frame, JSON token, and UTF-8
  sequence at one byte), and scripted faults: `429`, `529`, timeout, connect error, and
  mid-stream cut. Fault-injectable per request via `x-headroom-mock-script`, so tests
  drive the whole stack rather than poking internals. Deterministic throughout: no
  clock, no randomness, no sleeps.
- **Real provider clients**, exercised keylessly. `AnthropicProvider` (`x-api-key`,
  `anthropic-version` defaulted but never overridden) and `OpenAICompatProvider` (bearer
  token when configured, no auth when not — the local-vLLM case), on a shared
  `HttpProvider` that maps transport failures to the error taxonomy at the boundary.
  `tests/test_provider_clients.py` runs them against `httpx.MockTransport`: real client,
  real request building, real streaming, real error mapping, in-process responder.
- **Honest error mapping** (H-009). Upstream status, body, and `retry-after` forwarded
  verbatim; where there is no upstream answer the gateway invents a *specific* status
  (504 timeout, 502 unreachable, 404 unroutable model, 400 unparseable body, 500
  misconfiguration-naming-the-variable), in the dialect's own error shape, with a stable
  `headroom.reason` and `x-headroom-error-source`.
- **`RequestContext` on every path** (`headroom/core/context.py`): request id, tenant
  placeholder, dialect, model, provider, stream flag, outcome, and four monotonic marks
  (received / first-upstream-byte / first-token-out / complete) plus the derived
  durations Phase 3 will meter and Phase 8's H2 will report. Threaded explicitly into
  providers, created by pure-ASGI middleware (H-015), and closed first-call-wins so a
  backstop can never overwrite a diagnosis.
- **Provider registry + routing table** (H-013): kinds self-register implementations,
  instances are named providers, routes resolve `(dialect, model)` → instance *name*
  with longest-prefix-wins. Shaped so Phase 6 widens a rule instead of rewriting the
  proxy.
- **`config/routing.yaml`** (H-014), Pydantic-validated, secret-free by schema —
  providers name the *environment variable* holding their credential and `extra="forbid"`
  makes a literal `api_key:` fail to load. The shipped config routes `mock-` models to
  the MockProvider, so `make up` plus one curl is a working end-to-end demo with no key,
  no network, and no spend.
- **Header policy** (H-010): deny-list both directions; the caller's credentials never
  reach a provider (which is what stops Phase 2's virtual keys leaking), framing headers
  are dropped, and useful upstream signal — rate limits, `retry-after`, request ids — is
  preserved.
- **One structured JSON log line per request** (`headroom/core/log.py`), with logging
  configured explicitly at startup so it actually emits under uvicorn.
- **Tests**: 127 keyless, 2 live-marked and deselected by default. New files —
  `test_mid_stream_cut` (6), `test_streaming_passthrough` (14), `test_no_buffering` (4),
  `test_non_streaming` (6), `test_tool_blocks` (5), `test_error_mapping` (14),
  `test_request_context` (11), `test_routing` (17), `test_sse` (17),
  `test_provider_clients` (19), `test_logging` (5) — plus the Phase 0 nine, all still
  green.
- **Docs**: H-007…H-015 in `docs/DECISIONS.md`; `.env.example` documents
  `ANTHROPIC_API_KEY`, `VLLM_BASE_URL`, `VLLM_MODEL`, `VLLM_API_KEY`,
  `HEADROOM_ROUTING_CONFIG`, `HEADROOM_LOG_LEVEL` — placeholder names only.

**Deferred**

- Nothing from the Phase 1 scope. Explicitly *not* built, per the plan: auth (P2),
  metering and usage extraction (P3), retries and failover (P6). The seams for all three
  exist — `ctx.tenant_id`, the SSE observer, the pre-first-byte/post-first-byte
  distinction in the provider interface — and are documented where a later phase will
  look for them.

**Deviations**

1. **A second config file: `config/routing.yaml`** (H-014). §0.5 named only
   `config/models.yaml`, labelled Phase 3. Routes are policy and change when a provider
   or a GPU does; model metadata and prices are reference data and change when a vendor
   publishes. Splitting them keeps Phase 3 additive. **§0.5's repo map is amended in this
   PR** to list both files.
2. **New dependency: `pyyaml`** (plus `types-PyYAML` in the dev group). Both config files
   are meant to be read and edited by a human and both earn their comments. `uv.lock`
   regenerated.
3. **Service-backed tests now find the compose stack themselves** (H-012) — the fold-in
   from the P0 review. This changes documented behaviour and amends the `CLAUDE.md` line
   about not inventing a fallback: the fallback is the documented compose stack, an
   *inferred* endpoint that is unreachable still skips loudly, and an *explicit* one that
   is unreachable now **fails** — so CI cannot silently stop testing its own containers.
   `make up && make test` reports 127 passed, 0 skipped.
4. **Additions the plan's Phase 1 text does not enumerate**, all additive: `tests/` became
   a package with a `tests/support/` helper layer (a raw ASGI driver — no HTTP test client
   can prove non-buffering, since they all buffer); a structured request log with explicit
   logging configuration (H-015); `x-headroom-request-id` on every response; and
   `config/` added to the container image.
5. **An ordering bug was found by its own test and fixed, not worked around.**
   `_error_response` stamped `complete()` before `mark_first_token_out()`, so on the
   timeout / unroutable / malformed-body paths the request's end preceded its first
   output byte. `tests/test_request_context.py::assert_ordered` caught it on first run.
   Recorded here because the timing marks are what Phase 8's H2 publishes.

**Gate** — *keyless tests: streamed content equality vs mock fixtures across both
dialects (A4's nuance: content/event equality, not chunk-identical); a tool-call
round-trip fixture; a mid-stream-cut fixture proving the client sees a terminal error
event, never a silent truncation.*

Run on the operator's machine with the compose stack up and **nothing exported**:

```
$ make up
docker compose up -d --build --wait
 Container headroom-db-1 Healthy
 Container headroom-dynamodb-1 Healthy
 Container headroom-gateway-1 Healthy
docker compose ps
NAME                  IMAGE                         COMMAND                  SERVICE    CREATED          STATUS                    PORTS
headroom-db-1         pgvector/pgvector:pg16        "docker-entrypoint.s…"   db         12 seconds ago   Up 11 seconds (healthy)   0.0.0.0:5433->5432/tcp, [::]:5433->5432/tcp
headroom-dynamodb-1   amazon/dynamodb-local:3.1.0   "java -jar DynamoDBL…"   dynamodb   12 seconds ago   Up 11 seconds (healthy)   0.0.0.0:8001->8000/tcp, [::]:8001->8000/tcp
headroom-gateway-1    headroom-gateway              "uv run --no-sync uv…"   gateway    12 seconds ago   Up 5 seconds (healthy)    0.0.0.0:8080->8000/tcp, [::]:8080->8000/tcp

$ make test        # bare — no DATABASE_URL, no DYNAMODB_ENDPOINT_URL, no key
================= 127 passed, 2 deselected, 1 warning in 0.39s =================

$ make lint
uv run ruff check .
All checks passed!
uv run ruff format --check .
57 files already formatted

$ make typecheck
uv run mypy
Success: no issues found in 57 source files

$ uv run pytest -m live -q --collect-only
2/129 tests collected (127 deselected) in 0.04s
```

The two live-marked tests are the only ones that can spend money, and they are deselected
by every default collection (invariant 4).

Per-file counts from the same run:

```
19 tests/test_provider_clients.py     6 tests/test_non_streaming.py
17 tests/test_sse.py                  6 tests/test_mid_stream_cut.py
17 tests/test_routing.py              5 tests/test_tool_blocks.py
14 tests/test_streaming_passthrough.py 5 tests/test_logging.py
14 tests/test_error_mapping.py        4 tests/test_no_buffering.py
11 tests/test_request_context.py      3 tests/test_pytest_policy.py
                                      3 tests/test_migrations.py
                                      2 tests/test_services.py
                                      1 tests/test_healthz.py
```

**The fixture that governed the design.** Written before any implementation existed, and
run against nothing:

```
$ uv run pytest tests/test_mid_stream_cut.py -q
tests/test_mid_stream_cut.py:23: in <module>
    from headroom.core.sse import iter_sse_events
E   ModuleNotFoundError: No module named 'headroom.core.sse'
1 error in 0.05s
```

and after the passthrough was built to satisfy it — first run, no iteration:

```
$ uv run pytest tests/test_mid_stream_cut.py -q
......                                                                   [100%]
6 passed in 0.02s
```

**End to end through the real container**, keyless, no configuration:

```
$ curl -sS -D- -X POST localhost:8080/v1/messages -H 'content-type: application/json' \
    -d '{"model":"mock-model-1","max_tokens":32,"messages":[{"role":"user","content":"hi"}]}'
HTTP/1.1 200 OK
x-headroom-request-id: hr_394571b79b95474583f2aab71f490bc9
{"id":"msg_mock_6ca20aa0926a68c6","type":"message","role":"assistant","model":"mock-model-1","content":[{"type":"text","text":"mock reply from mock-model-1"}],"stop_reason":"end_turn","stop_sequence":null,"usage":{"input_tokens":11,"output_tokens":7}}

$ curl -sS -N -X POST localhost:8080/v1/messages -H 'content-type: application/json' \
    -d '{"model":"mock-model-1","max_tokens":32,"stream":true,"messages":[{"role":"user","content":"hi"}]}' | head -8
event: message_start
data: {"type":"message_start","message":{"id":"msg_mock_6ca20aa0926a68c6","type":"message","role":"assistant","model":"mock-model-1","content":[],"stop_reason":null,"stop_sequence":null,"usage":{"input_tokens":11,"output_tokens":0}}}

event: content_block_start
data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}

event: ping
data: {"type":"ping"}

$ docker compose logs gateway --no-log-prefix | grep request_id | tail -2
{"request_id":"hr_32d01df5debb490683225efa960d46a9","route":"/v1/messages","dialect":"anthropic","tenant_id":null,"model":"mock-model-1","provider":"mock","stream":false,"outcome":"ok","status":200,"upstream_status":200,"error_source":null,"error_reason":null,"upstream_latency_ms":4.126,"ttft_ms":4.132,"passthrough_overhead_ms":0.006,"total_ms":4.134}
{"request_id":"hr_8ba6b0abece44a4997e6b6fff2f90c7c","route":"/v1/messages","dialect":"anthropic","tenant_id":null,"model":"nope","provider":null,"stream":false,"outcome":"model_not_routed","status":404,"upstream_status":null,"error_source":"gateway","error_reason":"model_not_routed","upstream_latency_ms":null,"ttft_ms":0.366,"passthrough_overhead_ms":null,"total_ms":0.368}
```

That `passthrough_overhead_ms: 0.006` is the gateway's own cost between receiving the
first upstream byte and releasing the first downstream byte — 6 microseconds against the
mock. It is not the answer to "what does a gateway in Python cost" (Phase 8's H2 gets
that by running the same suite with and without the hop), but it is the component
Headroom can measure about itself, and it is now in the ledger's shape from day one.

**Assumed-facts register (§0.4)**

- **A4 — VERIFIED.** *SSE passthrough via httpx streaming + FastAPI `StreamingResponse`
  preserves event order and content; chunk boundaries are NOT guaranteed identical.* Both
  halves hold. `tests/test_streaming_passthrough.py` asserts event-sequence and content
  equality against the mock's emitted bytes for both dialects, and re-chops the same
  stream at 1, 2, 3, 7, 13, 64, 512-byte boundaries — splitting SSE frames mid-field,
  JSON mid-token, and UTF-8 mid-character (the fixture text carries an emoji, `日本語`,
  and `𝄞`) — with byte-identical reassembly every time. The nuance is honoured: no test
  asserts chunk identity. Completion detection survives the same treatment: a
  `message_stop` delivered one byte at a time is still recognised, so the truncation
  guard does not become a source of false alarms.
- **A5 — VERIFIED.** *Tool-use blocks round-trip through Anthropic-dialect passthrough
  untouched.* `tests/test_tool_blocks.py` asserts **byte** equality in both directions on
  payloads built to break re-serialization: non-alphabetical key order, a `ö` escape,
  an escaped quote inside a string, nested objects and arrays. The reply's `tool_use`
  block reaches the client byte-for-byte; the follow-up turn's `tool_use` + `tool_result`
  pair reaches the provider byte-for-byte; `ö` stays `ö` and a literal `ö` stays
  literal. Streamed `input_json_delta` fragments reassemble into the exact input object,
  and a cut mid-tool-call produces a terminal error rather than half a tool call. The
  property is structural, not careful: the proxy reads the body as bytes and sends those
  bytes, so no code exists that could re-encode them. H2's pre-flight still runs the
  $0.50 smoke against the real API.
- **A2 — untouched but unblocked.** Nothing here requires an SDK `base_url` override yet;
  the live smoke exercises the same path by hand.
- **A1, A3, A6, A7** — not due at this gate.

**Live smoke** — not yet run. It is operator-run, after CI is green, and the commands are
in the PR description. Budgeted at well under $0.05 against the $3 P1–P7 bucket.

**Spend** — $0.00 so far in this phase. Every test above ran on the MockProvider.
