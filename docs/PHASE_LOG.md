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

CI on PR-1 ([run 31284884898](https://github.com/sergioavilax/headroom/actions/runs/31284884898)),
all three jobs green, no annotations:

```
$ gh run view 31284884898 --json conclusion,jobs
success
lint + typecheck: success
pytest (postgres + dynamodb-local service containers): success
gateway image builds and serves: success

lint + typecheck | All checks passed!
lint + typecheck | Success: no issues found in 57 source files
pytest (…service containers) | ===== 127 passed, 2 deselected, 1 warning in 0.79s =====
gateway image builds and serves | gateway healthy
```

**127 passed, 0 skipped in CI** — the service tests executed against the containers
rather than skipping, which under H-012 is now enforced rather than hoped for: CI sets
`DATABASE_URL` and `DYNAMODB_ENDPOINT_URL` explicitly, and an explicit endpoint that is
unreachable fails instead of skipping.

**Live smoke** — the free half has since been run against the operator's vLLM instance;
what it found, and the test change it forced, are recorded under *Live smoke — first run
(vLLM)* at the end of this entry. The paid Anthropic half is operator-run after CI is
green, with the commands in the PR description, and is budgeted at well under $0.05
against the $3 P1–P7 bucket.

**Spend** — $0.00 so far in this phase. Every test above ran on the MockProvider, and the
live smoke that has run ran on the operator's own GPUs.

---

### Live smoke — first run (vLLM), 2026-08-08

It failed, and the failure was worth more than the pass would have been.

The operator's instance serves **Qwen3.6 with `--reasoning-parser qwen3`**. The test asked
for `max_tokens: 16`. A reasoning model spends its budget on reasoning deltas *before* it
emits its first content delta, so the response came back well-formed — frames in order,
`[DONE]` present, no error event — carrying **no content at all** and `finish_reason:
"length"`. The old assertion (`openai_text(...)` non-empty) reported that as "no text came
back", which names the symptom and buries the cause: at a 16-token ceiling an exhausted
budget and a broken gateway are indistinguishable.

**Fixed in `tests/test_live_smoke.py`, test-only.** `max_tokens` is 256 — that smoke runs
on the operator's own GPUs, so the headroom is free — and the assertion moved to the
outcome it actually cares about: `finish_reason == "stop"` **and** non-empty reassembled
content, with a failure message that tells "ran out of budget" apart from "the gateway
broke". `tests/support/streams.py` grew `openai_finish_reasons()` to read it. The paid
Anthropic smoke is deliberately untouched: `claude-haiku-4-5` is not a reasoning model at
these settings, `max_tokens: 16` is what holds that call at ~$0.0001, and invariant 5
outranks symmetry between the two tests. **No gateway behaviour changed anywhere in this
work** — not by the test fix, and not by the keyless fixture below. The one judgment call
worth recording is how those fixtures serialize non-ASCII, which is **H-016**.

**A5-adjacent evidence.** The half of that run which *did* work is the interesting half.
vLLM's reasoning parser splits the model's chain of thought into a delta field of its own
— a field neither dialect module in this repo has a line of code for, and one no fixture
in the keyless suite produces — and the operator observed those deltas arriving at the
client through the gateway untouched. A5 asserts exactly this shape for Anthropic
`tool_use` blocks and is already VERIFIED keylessly by `tests/test_tool_blocks.py`; this
run is not that proof and does not convert anything. What it is: the first observation on
real hardware that the property generalises past the fields the gateway knows about.
Under H-007 the proxy forwards body bytes it never re-serializes, so no code path exists
that *could* mangle a delta type invented after the passthrough was written — and now
something outside the fixtures has been through it. Phase 3 inherits the consequence:
reasoning tokens are output tokens the meter cannot see in the content stream, so usage
must be read from the usage block, never inferred from the text.

**And then it stopped being an observation.** An observation on hardware CI cannot reach
decays into folklore, so the finding was turned into a keyless fixture in the same PR:
`headroom/providers/mock_scripts.py` gained `openai_reasoning_stream_chunks()` (chain of
thought first, answer second, `completion_tokens_details.reasoning_tokens` in the usage
chunk) and `OPENAI_REASONING_BODY` for the non-streamed form, `MockScript` gained two
builders, and **`tests/test_reasoning_passthrough.py`** — 10 tests — now asserts in CI
what the operator saw once on a GPU:

- reasoning deltas reach the client **byte-for-byte**, parametrized over *both* spellings
  in the wild (`reasoning_content` and `reasoning`) — because the point is not that
  Headroom supports vLLM's field name, it is that no branch exists which could tell them
  apart;
- the chain of thought never leaks into `delta.content`, and arrives strictly before it;
- the token count is knowable **only** from the usage block — 11 visible characters, 63
  completion tokens, 57 of them reasoning. This is the Phase 3 rule, now enforced;
- the whole stream survives `split_every(…, 1)`. The trace carries a literal `ö`, `—`,
  and `𝄞`, so the wire really does hold 2-, 3-, and 4-byte UTF-8 sequences and a 1-byte
  re-chop really does split characters — the existing fixtures ship non-ASCII
  pre-escaped, so they never did;
- **an exhausted budget is a complete stream, not a truncation**: `finish_reason:
  "length"`, `[DONE]` present, no error, `outcome == "ok"`. The live failure, reproduced
  without a GPU, pinning that the gateway was right and the assertion was wrong;
- **a cut during reasoning is still an error**: same empty answer, no `[DONE]`, no
  `finish_reason`, `outcome == "upstream_stream_cut"`. The two must never be confused —
  invariant 6, one layer up.

**The sabotage that proved it covers something.** Green on the first run is when a test
deserves the most suspicion, so the proxy was temporarily patched to re-encode one
character on the outbound byte path — a literal `ö` rewritten as its six-character JSON
escape, which is precisely what one `json.loads`/`json.dumps` round trip with the default
`ensure_ascii` would do. Semantically identical, different bytes, invisible to anything
but a byte comparison. Under that sabotage:

```
$ uv run pytest -q          # with the proxy corrupting the stream
FAILED tests/test_reasoning_passthrough.py::test_reasoning_deltas_reach_the_client_byte_for_byte[reasoning_content]
FAILED tests/test_reasoning_passthrough.py::test_reasoning_deltas_reach_the_client_byte_for_byte[reasoning]
FAILED tests/test_reasoning_passthrough.py::test_a_reasoning_stream_survives_being_chopped_one_byte_at_a_time[3]
FAILED tests/test_reasoning_passthrough.py::test_a_reasoning_stream_survives_being_chopped_one_byte_at_a_time[64]
4 failed, 133 passed, 2 deselected, 1 warning in 0.41s
```

**Only the new file noticed.** `test_streaming_passthrough` and `test_tool_blocks` both
passed against a gateway that was actively corrupting bytes, because every fixture that
predates this one ships its non-ASCII already escaped — there was nothing left for an
`ensure_ascii` re-encode to change. That gap is what the live smoke walked into, and it
is now closed. (`chunk_size=1` also passes under the sabotage, correctly: a per-chunk
mutation cannot see a two-byte sequence split across two one-byte chunks.) The proxy was
restored immediately; `git diff headroom/api/proxy.py` is empty, and the suite is green.

**Not yet re-run.** The fixed *live* test has not been executed against the live instance
— that is the operator's box, and the session that made this change could not reach it.
Everything above is keyless. The re-run is:

```
$ VLLM_BASE_URL=http://<instance>:8000 uv run pytest -m live -k vllm -v
```

Keyless gate over the whole change, on the operator's machine:

```
$ make lint
uv run ruff check .
All checks passed!
uv run ruff format --check .
58 files already formatted

$ make typecheck
uv run mypy
Success: no issues found in 58 source files

$ make test
================= 137 passed, 2 deselected, 1 warning in 0.40s =================
```

**127 → 137.** The Shipped list above records the count at the Phase 1 gate; the ten added
here are `tests/test_reasoning_passthrough.py`. Everything is additive — no existing test,
fixture, or gateway behaviour was changed to make room for them (invariant 7).

---

## Phase 2 — Tenancy: virtual keys and admin surface (2026-08-08)

**Shipped**

- **The first real migration.** `migrations/0001_tenants_and_virtual_keys.sql` — the
  P0 runner finally has work. `tenants` (UUID pk, unique name, `active`, timestamps) and
  `virtual_keys` (UUID pk, `tenant_id` FK `ON DELETE RESTRICT`, `key_hash` UNIQUE,
  `key_prefix`, `allowed_models`/`allowed_providers` as `TEXT[] NOT NULL DEFAULT '{}'`,
  `revoked_at`, timestamps). Revocation is a timestamp, not a boolean; nothing is ever
  deleted; empty scope means unrestricted. **H-022.**
- **Virtual keys** (`headroom/policy/keys.py`): `hk_` + `secrets.token_urlsafe(32)` — 256
  bits, 46 characters. **SHA-256 hex at rest** in a UNIQUE-indexed column, so
  authentication is one indexed lookup and no password KDF sits on the first-token path;
  the entropy that makes that safe is asserted rather than assumed. An 11-character
  display prefix (`hk_` + 8) is stored deliberately, withholding ~208 bits. Scope matching
  is exact, with a trailing `*` as the only wildcard. **H-017.**
- **`TenantStore`, one interface and two implementations** — `PostgresTenantStore`
  (`headroom/db/tenants.py`, what the gateway runs; hand-written SQL, `RETURNING *`,
  `COALESCE($n, column)` partial updates, no concatenated SQL) and `InMemoryTenantStore`
  (`headroom/db/memory.py`, what most tests run against). The drift hazard is answered by
  `tests/test_tenant_store.py`, **one contract suite parametrised over both**. **H-021.**
- **A lazy connection pool** (`headroom/db/pool.py`): building a gateway never opens a
  connection, so CI's `image` job still smokes `/healthz` with no database in sight and
  H-000's liveness-only health check survives having real dependencies. Database failures
  are translated at the boundary — a missing table becomes a `ConfigurationError` naming
  `make migrate`, anything about reachability becomes a 503. Nothing above `headroom/db/`
  imports asyncpg.
- **Proxy authentication, at the P1 seams.** `_proxy` gained three lines:
  `authenticate()` before the body is read, `require_model()` before routing,
  `require_provider()` after it. Everything else in `proxy.py` is untouched, and the
  errors ride the existing `HeadroomError` handler so a 401 arrives in the caller's own
  dialect. `RequestContext.tenant_id` / `key_id` — Phase 1's placeholders — are now
  filled by `Principal.stamp`. **H-020.**
- **Exact 401/403 semantics, and exact ordering.** 401 for missing / malformed / unknown /
  revoked / inactive-tenant (five distinct `headroom.reason` values, one status); 403 for
  out-of-scope model or provider; 503 for a control-plane outage, extending H-009's table
  of invented statuses. An anonymous request with a broken body is **401, not 400**; an
  out-of-scope model is **403 whether or not it routes**, so a key cannot enumerate the
  routing table.
- **Auth decision cache** (`headroom/policy/auth.py`): `AUTH_CACHE_TTL_S = 5.0`, a named
  constant. **Successes only** — a failure is never cached, so a key minted a millisecond
  ago works now. Revocation, tenant deactivation, and a scope patch all invalidate
  in-process immediately, so the window in the revoking process is **zero** and the TTL is
  the *cross-process* bound (Phase 9 runs several tasks against one RDS). Keyed by hash,
  never plaintext; stores a frozen `Principal`; the clock is injected so the window is
  tested by advancing time rather than sleeping through it. **H-018.**
- **Admin API** (`headroom/api/admin.py`), full CRUD behind a root token:
  `POST/GET/PATCH/DELETE /admin/tenants[/{id}]` and `/admin/keys[/{id}]` (+ `?tenant_id=`).
  `DELETE` revokes/deactivates and returns the object in its new state. 409 on a duplicate
  tenant name, 404 on unknown ids *including* ids that are not UUIDs, 422 on an unknown
  field (`extra="forbid"`). Errors carry Headroom's own envelope with the request id —
  the proxy speaks the caller's dialect because an SDK has to parse it; nothing on
  `/admin` is an SDK.
- **`HEADROOM_ADMIN_TOKEN`, environment only** — named but never valued in `.env.example`,
  referenced through `docker-compose.yml`, resolved once at gateway construction, compared
  with `secrets.compare_digest`. **Unset means the admin API is OFF** — 503 on every route,
  naming the variable — never open. `/admin` is exempt from virtual-key auth, so the
  control plane stays reachable when every key is revoked. **H-019.**
- **The plaintext key exists exactly once**, in the `POST /admin/keys` response, enforced
  structurally: `KeyView` has no `key` field, `KeyCreated` is the only model that does, and
  one route returns it.
- **Tests**: 280 keyless (137 → 280), 2 live-marked and deselected by default. New files —
  `test_tenant_store` (44, the contract suite + Postgres-only proofs),
  `test_auth_matrix` (29), `test_virtual_keys` (27), `test_admin_api` (25),
  `test_auth_cache` (10), `test_key_secrecy` (8). Every Phase 0/1 test still green.
- **Docs**: H-017 … H-022 in `docs/DECISIONS.md`; **`docs/vllm.md`** (below); README with
  the four-step keyless demo and the 401/403 rules; `migrations/README.md` status table.

**Additional deliverable — `docs/vllm.md`**

Phase 1's live smoke produced operational facts that lived only in a terminal scrollback.
They are now a document, with every claim tagged **VERIFIED** (run on this machine) or
**UNTESTED**: the known-good launch command (`cyankiwi/Qwen3.6-27B-AWQ-INT4`,
`--gpu-memory-utilization 0.92 --max-model-len 8192 --enforce-eager --tool-call-parser
qwen3_xml --reasoning-parser qwen3 --limit-mm-per-prompt '{"image": 0, "video": 0}'`, host
port 8010, HF cache mount); why each non-obvious flag (hermes fails *silently* on this
family; the text-only multimodal limit skips vision profiling that crawls under eager mode
and hands the encoder's budget to KV; **8010 because 8000 is Backline's**, and P8.H2 needs
both stacks up at once); the `drawais/…` landmine (text-only `config.json`, `Qwen3_5Config`
/ `Qwen3_5TextConfig` `TypeError` on vLLM 0.26 — a traceback that looks like version skew
and is not); that **GPU selection is unreliable here** (`--gpus device=N` *and*
`--gpus device=UUID` both put the model on the wrong physical card), the reliable check
(`nvidia-smi --query-gpu=uuid,memory.used --format=csv`), and the **UNTESTED** candidate
fix (`--gpus all` + `-e CUDA_VISIBLE_DEVICES=<UUID>`) that has to be settled before Phase
6's two-instance demo; sizing against **free** memory because the desktop card holds
~1.2–5 GB of Windows display memory at all times; and the reasoning-model budget behaviour
that broke the Phase 1 smoke.

**Deferred**

- Nothing from the Phase 2 scope. Explicitly *not* built, per the plan: rate limits and
  budgets (P4), the cost ledger (P3), the Tenants & Keys dashboard (P7). The seams exist —
  `ctx.tenant_id` / `ctx.key_id` are in the log line for P3, the `TenantStore` shape is
  what P3's ledger store should copy, and P4's admission checks slot in beside
  `require_model` / `require_provider`.
- **The `CUDA_VISIBLE_DEVICES` GPU-pinning fix in `docs/vllm.md` is UNTESTED** and marked
  as such. It is Phase 6's pre-flight, not this phase's.

**Deviations**

1. **`make up` now applies migrations.** Since the gateway needs a schema to authenticate
   against, a stack that is "up" without one is a stack whose first request 500s. `up`
   runs `docker compose exec -T gateway uv run --no-sync python -m headroom.db.migrate`
   after `--wait`, so it uses the container's `DATABASE_URL` and needs nothing installed on
   the host. `make migrate` is unchanged (host-side, against `DATABASE_URL`).
2. **The keyless demo is no longer one curl.** Phase 1's `make up` + curl became four steps:
   set `HEADROOM_ADMIN_TOKEN`, `make up`, create a tenant, mint a key. That is the cost of
   the phase, and the README now walks it. Deliberately *not* worked around by letting an
   unauthenticated request through in some "dev mode" — a gateway with an off switch for
   authentication is a gateway that ships with it off.
3. **`ADMIN_TOKEN_ENV` lives in `headroom/core/config.py`**, not in `admin.py`, purely to
   break an import cycle (`gateway.py` needs the name; `admin.py` needs `Gateway`). It sits
   beside `CONFIG_PATH_ENV`, which is where environment-variable names already live.
4. **Two Phase 1 tests changed**, both because the placeholder they asserted is now filled:
   `test_request_context.py` asserted `ctx.tenant_id is None` (now the seeded tenant's id)
   and pinned the log-field set (now includes `key_id`). No Phase 1 *behaviour* changed.
5. **The test harness now authenticates by default.** `GatewayHarness.post` presents a
   seeded key unless a test says `authenticate=False`, and `gateway.start(...)` wraps the
   raw-ASGI driver the same way. So all 137 Phase 0/1 tests now exercise the *authenticated*
   path unchanged, rather than being exempted from it.
6. **CI's migration step became a real assertion.** It was "the runner is a clean no-op at
   Phase 0"; it now applies the migration and proves a second run is a no-op.
7. **The Postgres contract fixture truncates `tenants` and `virtual_keys`.** The compose
   database is a test fixture, stated plainly here because it means `make test` wipes the
   local control plane. It truncates on both entry and exit — entry so the contract
   assertions can be exact (`==`, not `in`) and identical for both stores, exit so the
   README's demo still works immediately after a test run.
8. **Additions the plan's Phase 2 text does not enumerate**, all additive: the 503
   `control_plane_unavailable` status (H-020, extending H-009's table); tenant `active`
   state and deactivation; `key_id` in the request log line (P3 needs per-key attribution,
   not only per-tenant); and `docs/vllm.md`, which the session brief mandated.

**Gate** — *auth matrix tests (missing/revoked/wrong-scope keys); admin CRUD; a revoked key
is dead on the very next request (no cache of auth decisions beyond a short TTL, and the TTL
is a documented number).*

Run on the operator's machine with the compose stack up and **nothing exported**:

```
$ make lint
uv run ruff check .
All checks passed!
uv run ruff format --check .
71 files already formatted

$ make typecheck
uv run mypy
Success: no issues found in 71 source files

$ make test
================= 280 passed, 2 deselected, 1 warning in 1.90s =================

$ uv run pytest -m live -q --collect-only
2/282 tests collected (280 deselected) in 0.05s
```

Per-file counts from the same run:

```
44 tests/test_tenant_store.py      10 tests/test_reasoning_passthrough.py
29 tests/test_auth_matrix.py       10 tests/test_auth_cache.py
27 tests/test_virtual_keys.py       8 tests/test_key_secrecy.py
25 tests/test_admin_api.py          6 tests/test_non_streaming.py
19 tests/test_provider_clients.py   6 tests/test_mid_stream_cut.py
17 tests/test_sse.py                5 tests/test_tool_blocks.py
17 tests/test_routing.py            5 tests/test_logging.py
14 tests/test_streaming_passthrough.py  4 tests/test_no_buffering.py
14 tests/test_error_mapping.py      3 tests/test_pytest_policy.py
11 tests/test_request_context.py    3 tests/test_migrations.py
                                    2 tests/test_services.py
                                    1 tests/test_healthz.py
```

The Postgres half of the contract suite **ran** rather than skipped — 18 of
`test_tenant_store.py`'s 44 are the `[postgres]` parameter, plus five Postgres-only proofs:

```
$ uv run pytest tests/test_tenant_store.py -v | grep -c 'postgres.*PASSED'
18
```

**Run again against a fresh clone** (`git clone -b claude/p2-tenancy . /tmp/hr-p2-gate`),
with no venv, no pre-built image, and a `.env` holding only `HEADROOM_ADMIN_TOKEN` — then
the README's demo executed verbatim on it:

```
$ make up
docker compose up -d --build --wait
 Container hr-p2-gate-db-1 Healthy
 Container hr-p2-gate-dynamodb-1 Healthy
 Container hr-p2-gate-gateway-1 Healthy
docker compose exec -T gateway uv run --no-sync python -m headroom.db.migrate
applied 1 migration(s): 0001_tenants_and_virtual_keys

$ make test        # bare and keyless — nothing exported
================= 280 passed, 2 deselected, 1 warning in 2.38s =================

$ curl -sS localhost:8080/healthz
{"status":"ok"}

--- the README demo, run verbatim on this fresh clone ---
POST /admin/tenants ->
{"id":"50cba457-0e0b-4b7a-842c-7f4d3694787b","name":"acme","active":true,
 "created_at":"2026-08-09T02:47:43.730039Z","updated_at":"2026-08-09T02:47:43.730039Z"}

POST /admin/keys -> (plaintext redacted after its prefix)
{"id":"71a44809-…","tenant_id":"50cba457-…","name":"laptop","key_prefix":"hk_rcGh-Zvy",
 "allowed_models":["mock-*"],"allowed_providers":[],"status":"active","revoked_at":null,
 "key":"hk_rcGh-Zvy..."}

POST /v1/messages with that key ->
{"id":"msg_mock_6ca20aa0926a68c6","type":"message","role":"assistant","model":"mock-model-1",
 "content":[{"type":"text","text":"mock reply from mock-model-1"}],"stop_reason":"end_turn",
 "usage":{"input_tokens":11,"output_tokens":7}}
-> 200
```

The first attempt at that last block **failed**, and the failure was worth having: the
demo's `POST /admin/tenants` came back `409 tenant_name_conflict`, because the Postgres
contract fixture truncated the control-plane tables on the way *in* and left the last
test's rows behind on the way *out*. So `make test` followed by the documented demo
collided on a tenant a test had invented. Fixed in-branch by truncating on both sides
(`tests/test_tenant_store.py`); the run above is the re-run. A repo whose own README stops
working after its own test suite is a repo a stranger gives up on.

**H-012 still holds with the new Postgres tests.** A fresh clone with no stack up must run
a smaller suite *loudly*, and a stated-but-unreachable endpoint must fail rather than skip:

```
$ docker compose stop db
$ uv run pytest -q                      # DATABASE_URL unset — inferred, unreachable
257 passed, 23 skipped, 2 deselected, 1 warning in 0.59s

$ DATABASE_URL=postgresql://…:5433/headroom uv run pytest -q tests/test_tenant_store.py
22 passed, 22 errors in 0.21s
ERROR tests/test_tenant_store.py::test_two_keys_cannot_share_a_hash - Failed: DATABASE_URL
was set to postgresql://…:5433/headroom and nothing is listening there
```

The 0.21s is deliberate. The first version of that fixture let the migration runner's ten
one-second connect retries run *per test*, so a wrong `DATABASE_URL` took ten minutes to
report itself; the fixture now probes reachability and fails immediately. A suite that
takes ten minutes to say "wrong address" is a suite people learn to interrupt.

**The sabotage runs.** Green on the first attempt is when a suite deserves the most
suspicion, so each of the phase's four claims was tested by breaking the thing it protects.
All four patches were reverted immediately; the suite is green above.

*Sabotage 1 — the proxy authenticates nobody* (`authenticate()` replaced with a fabricated
`Principal`):

```
31 failed, 249 passed, 2 deselected
FAILED tests/test_auth_matrix.py::test_a_request_with_no_key_is_401[anthropic]
FAILED tests/test_auth_matrix.py::test_a_revoked_key_is_401[anthropic]
FAILED tests/test_auth_matrix.py::test_a_model_outside_the_key_scope_is_403
FAILED tests/test_auth_matrix.py::test_an_anonymous_request_with_a_broken_body_is_401_not_400
FAILED tests/test_key_secrecy.py::test_the_request_log_line_carries_the_tenant_not_the_credential
FAILED tests/test_request_context.py::test_a_streamed_request_records_every_stage
…
```

*Sabotage 2 — `display_prefix` keeps the whole key* (the "store it for support" mistake, at
the one place that would put a plaintext into the database):

```
9 failed, 271 passed, 2 deselected
FAILED tests/test_admin_api.py::test_create_a_key_and_get_the_plaintext_once
FAILED tests/test_key_secrecy.py::test_no_read_endpoint_returns_the_key
FAILED tests/test_key_secrecy.py::test_the_store_holds_the_hash_and_a_short_prefix_and_nothing_else
FAILED tests/test_key_secrecy.py::test_the_auth_cache_is_keyed_by_hash_not_by_plaintext
FAILED tests/test_key_secrecy.py::test_an_error_message_about_scope_names_the_prefix_not_the_key
FAILED tests/test_tenant_store.py::test_the_plaintext_never_reaches_the_stored_row[memory]
FAILED tests/test_tenant_store.py::test_the_plaintext_never_reaches_the_stored_row[postgres]
FAILED tests/test_tenant_store.py::test_no_column_of_the_database_contains_a_plaintext_key
FAILED tests/test_virtual_keys.py::test_the_display_prefix_is_short_and_keeps_the_secret_out
```

That eighth line is the gate's *"a test greps the stored form"*, firing: it discovers the
text-ish columns of both tables from `information_schema` and searches every row, so a
future migration that adds a place to leak a key is covered the day it lands.

*Sabotage 3 — revocation never reaches the in-process cache* (`invalidate_key` made a
no-op). Note what does **not** fail: the TTL window is still correct, which is exactly the
distinction H-018 draws.

```
2 failed, 278 passed, 2 deselected
FAILED tests/test_auth_cache.py::test_revoking_through_the_admin_api_kills_the_key_on_the_next_request
FAILED tests/test_auth_cache.py::test_narrowing_a_keys_scope_takes_effect_on_the_next_request
```

*Sabotage 4 — the TTL is an hour instead of five seconds*:

```
1 failed, 279 passed, 2 deselected
FAILED tests/test_auth_cache.py::test_the_ttl_is_a_documented_number
```

Only one test fails, and that is honest rather than weak: the window tests are written
against `AUTH_CACHE_TTL_S` and follow it wherever it goes, so the *behaviour* around the
number is pinned by them and the *number itself* is pinned by exactly one assertion. A
future session that wants a different TTL has to change that assertion deliberately, which
is the point.

**End to end through the real container**, on a wiped volume, keyless, no network:

```
$ docker compose down -v && make up
 Container headroom-db-1 Healthy
 Container headroom-dynamodb-1 Healthy
 Container headroom-gateway-1 Healthy
docker compose exec -T gateway uv run --no-sync python -m headroom.db.migrate
applied 1 migration(s): 0001_tenants_and_virtual_keys

### 1. no key at all
{"type":"error","error":{"type":"authentication_error","message":"no virtual key on this
request; send it as `Authorization: Bearer hk_…` or `x-api-key: hk_…`"},"headroom":
{"reason":"missing_api_key","request_id":"hr_ddece398c52b49b4b7ee66d374186573"}}
-> 401

### 2. create a tenant
{"id":"1710e2b6-8e6e-49ef-a4f5-b4be8954bc36","name":"backline","active":true,
 "created_at":"2026-08-09T02:28:41.182544Z","updated_at":"2026-08-09T02:28:41.182544Z"}

### 3. mint a key scoped to mock-* models
{"id":"7708582e-b4df-4d36-9ec4-ecc9bab0e05b","tenant_id":"1710e2b6-…","name":"suite",
 "key_prefix":"hk_s1WmIE2d","allowed_models":["mock-*"],"allowed_providers":[],
 "status":"active","revoked_at":null,"key":"hk_s1WmIE2d…"}          <- the ONLY time

### 4. the key works
{"id":"msg_mock_6ca20aa0926a68c6","type":"message","role":"assistant","model":"mock-model-1",
 "content":[{"type":"text","text":"mock reply from mock-model-1"}],"stop_reason":"end_turn",
 "usage":{"input_tokens":11,"output_tokens":7}}
-> 200

### 5. read the key back through the admin API
{"id":"7708582e-…","tenant_id":"1710e2b6-…","name":"suite","key_prefix":"hk_s1WmIE2d",
 "allowed_models":["mock-*"],"allowed_providers":[],"status":"active","revoked_at":null}
                                                          ^ no `key` field exists here

### 6. a model outside the scope
{"type":"error","error":{"type":"permission_error","message":"key hk_s1WmIE2d… is not
scoped to model 'claude-haiku-4-5' (allowed: 'mock-*')"},"headroom":
{"reason":"model_out_of_scope","request_id":"hr_97fd8e340e05425997930daa1ed2b9da"}}
-> 403

### 7. revoke, then the very next request
DELETE /admin/keys -> 200
{"type":"error","error":{"type":"authentication_error","message":"this virtual key has been
revoked"},"headroom":{"reason":"revoked_api_key","request_id":"hr_9dfff9e4e75945fc924aacd9c17d7c3b"}}
-> 401

### 8. what the database actually holds
 name  | key_prefix  |        key_hash         | allowed_models | revoked
-------+-------------+-------------------------+----------------+---------
 suite | hk_s1WmIE2d | 51f255e1839a2011ea5b... | {mock-*}       | t
(1 row)

### grep every row of both tables for the plaintext key:
occurrences of the plaintext key: 0
occurrences of its 11-char display prefix: 1

### 9. the request log lines
{"request_id":"hr_97fd8e340e05425997930daa1ed2b9da","route":"/v1/messages","dialect":"anthropic",
 "tenant_id":"1710e2b6-8e6e-49ef-a4f5-b4be8954bc36","key_id":"7708582e-b4df-4d36-9ec4-ecc9bab0e05b",
 "model":"claude-haiku-4-5","provider":null,"stream":false,"outcome":"model_out_of_scope",
 "status":403,"error_source":"gateway","error_reason":"model_out_of_scope","ttft_ms":0.283,"total_ms":0.285}
{"request_id":"hr_9dfff9e4e75945fc924aacd9c17d7c3b","route":"/v1/messages","dialect":"anthropic",
 "tenant_id":null,"key_id":null,"model":null,"provider":null,"stream":false,
 "outcome":"revoked_api_key","status":401,"error_source":"gateway","error_reason":"revoked_api_key",
 "ttft_ms":0.929,"total_ms":0.931}
```

Step 7 is the gate's revocation clause, in the real container, with **no clock
manipulated anywhere**: `DELETE` returned, and the immediately following request was 401.
Step 9 is the ledger/context clause: `tenant_id` and `key_id` are in the log line for the
authenticated request — and, correctly, `null` on the 401, because a request that failed to
identify itself has no tenant and inventing one would put unattributable rows in Phase 3's
ledger. (The demo key above was minted into a volume that has since been wiped, was revoked
in step 7, and is truncated here regardless.)

**Assumed-facts register (§0.4)**

- **A1, A3, A4, A5, A6, A7** — not due at this gate, and none touched. A4 and A5 stay
  VERIFIED from Phase 1; the 137 tests that prove them now run through the authenticated
  path and are still green, which is a small piece of evidence that Phase 2 did not
  disturb the passthrough.
- **A2 — moved closer.** *Anthropic SDKs honour `base_url` override, so Backline can point
  its Anthropic provider at Headroom unchanged.* Still unverified end to end (that is the
  H2 pre-flight), but the credential half is now settled deliberately rather than by luck:
  the Anthropic SDK sends `x-api-key`, the OpenAI SDK sends `Authorization: Bearer`, and
  both spellings — plus Azure's `api-key` — are accepted on **both** routes and asserted in
  `tests/test_auth_matrix.py`. A gateway fussy about which header arrives on which route
  would have made a `base_url`-only integration a matter of luck.
- **New, and worth the register's discipline even though it is not a numbered assumption:**
  the gateway process starts and serves `/healthz` with **no database reachable** (the lazy
  pool), which is what keeps CI's `image` job honest and what Phase 9's container needs
  before its secrets arrive.

CI on PR-2 ([run 31291127046](https://github.com/sergioavilax/headroom/actions/runs/31291127046)),
all three jobs green, no annotations:

```
$ gh run view 31291127046 --json conclusion,jobs
success
lint + typecheck: success
pytest (postgres + dynamodb-local service containers): success
gateway image builds and serves: success

lint + typecheck | All checks passed!
lint + typecheck | Success: no issues found in 71 source files
pytest (…service containers) | ===== 280 passed, 2 deselected, 1 warning in 4.00s =====
pytest (…service containers) | tests/test_tenant_store.py::test_a_created_key_comes_back_whole[memory] PASSED
pytest (…service containers) | tests/test_tenant_store.py::test_a_created_key_comes_back_whole[postgres] PASSED
Migrations apply, twice is a no-op | migrations: up to date, nothing to apply
gateway image builds and serves | gateway healthy
```

**280 passed, 0 skipped in CI** — `grep -c SKIPPED` over the whole log returns `0`, so the
Postgres half of the contract suite executed against the service container rather than
skipping. Under H-012 that is enforced rather than hoped for: CI sets `DATABASE_URL`
explicitly, and an explicit endpoint that is unreachable now fails in 0.2 s.

The `image` job is the one that proves the lazy pool: it builds the container and smokes
`/healthz` with **no Postgres anywhere in the job**, which a gateway that connected at
startup could not do.

**Spend** — $0.00. No provider API was called in this phase; every test ran on the
MockProvider and the container demo talked to nothing.

---

## Phase 3 — Metering: the ledger that matches the invoice (2026-08-08)

**Shipped**

- **`config/models.yaml`, with dated price schedules** — the D-017 lesson as schema, not
  as a bugfix. A model has an *ordered history* of `(effective_from, usd_per_mtok_in,
  usd_per_mtok_out)` rows; a request resolves to the latest row on or before its own
  date; a price change is an append that cannot reach backwards. Rates are **quoted
  strings** and the loader refuses a YAML float by name — money in binary floating point
  is wrong before anything multiplies it. Matching is exact-first then longest-prefix
  (the H-013 rule). **H-023.**
- **A real dated boundary in the committed file.** Anthropic published Claude Sonnet 5
  at an introductory **$2/$10 per MTok through 2026-08-31**, $3/$15 after — so the
  shipped config contains a genuine vendor-published price change, and
  `tests/test_prices.py` asserts the identical request resolves to each rate on either
  side of that midnight. The D-017 property is exercised against reality, not only a
  fixture.
- **Mock models are FLAT-priced**, exactly one row from the epoch, deliberately
  asymmetric with the real models: every exact-cost assertion in the suite prices
  against them, and a dated tier would make the suite start failing on a calendar day.
  $0.25/$1.25 per MTok, chosen so the canonical 11-in/7-out fixture lands on
  **$0.0000115** — a terminating decimal, so no expected cost anywhere needs a
  tolerance. The flatness is asserted, not trusted. The operator's vLLM model is priced
  at an **honest zero** so a $0.00 row means "free" and not "no idea". **H-023.**
- **`migrations/0002_usage_ledger.sql`** — one row per completed request. It copies the
  **rates it billed at** into the row (`price_effective_from`, `usd_per_mtok_in`,
  `usd_per_mtok_out`), so editing the price file can never re-bill history. Money is
  `NUMERIC(24, 12)` — a millionth of a cent — never `DOUBLE PRECISION`. `NULL` and `0`
  are kept distinct and `cost_status` says which. `request_id` is UNIQUE (the writer's
  retry safety); the foreign keys are `ON DELETE RESTRICT` onto Phase 2's tables, whose
  never-delete design (H-022) this is what made load-bearing. `cache_disposition` and
  `failover_hops` ship empty for Phases 5 and 6, so the shape of a row stops changing
  here. **H-024.**
- **Usage extraction per dialect, from the usage block only.** Anthropic assembles
  `message_start` + `message_delta`; the OpenAI dialect reads the trailing usage chunk —
  which arrives *after* the frame carrying `finish_reason`, and is why the passthrough
  loop now feeds its observer past the terminal marker. Non-streamed forms for both.
  Reasoning tokens are recorded as the *breakdown* of `completion_tokens` they are,
  never added to it.
- **The placeholder trap, closed.** `message_start` reports `output_tokens: 0` before a
  single token exists. The observer ignores it, so a stream cut mid-answer honestly
  reports "output unknown" instead of billing a severed answer as a complete free one.
- **Exact cost in `Decimal`, end to end** (`headroom/metering/cost.py`). Rates parse from
  strings, arithmetic is decimal, storage is `NUMERIC`, serialization is a string —
  including in the log line, because JSON's only numeric type is a double. Five cost
  statuses, each meaning something different: `priced`, `partial`, `unpriced_model`,
  `usage_unknown`, `not_billable`.
- **Error accounting with decided semantics** (**H-025**): an upstream ≥400 and a request
  that never reached a provider cost **0** (a measurement); a **timeout** costs **NULL**,
  because it was sent and may well have been billed by someone we cannot ask; a
  mid-stream cut costs NULL unless its usage arrived first. Anonymous 401s get **no row
  at all** — they have no tenant, and an unattributable row in an attribution table is
  worse than none.
- **Prompt-cache tiers recorded, not priced** (**H-026**). `cache_read_tokens` /
  `cache_write_tokens` are columns; a non-zero value on a non-free model marks the row
  `partial` — an honest lower bound rather than a total that looks complete. Invariant
  6's instinct applied to money.
- **A fire-and-forget writer with a stated guarantee** (**H-027**):
  *at most once, in process, best effort.* `Meter.record` and `LedgerWriter.submit` are
  ordinary synchronous functions — there is no `await` on the metering path at all, so
  no slow database can suspend a response. A graceful stop drains the queue; a full
  queue drops and **counts**; a failing store does not kill the drain task; the insert
  is idempotent so a retry cannot double-bill. Phase 4's budget reservations are
  explicitly forbidden from reusing this path.
- **`LedgerStore`: one interface, two implementations, one contract suite** — the H-021
  shape applied to money. `PostgresLedgerStore` and `InMemoryLedgerStore`, with
  `tests/test_ledger_store.py` parametrised over both (42 tests), plus Postgres-only
  proofs that the RESTRICT keys and the `NUMERIC` scale are real.
- **`/admin/usage`** (`headroom/api/usage.py`): `GET /admin/usage` (filterable by tenant,
  key, model, provider, outcome, time window, with paging), `GET /admin/usage/totals`
  (per tenant, optionally split by model), `GET /admin/usage/{request_id}`. Read-only —
  every other verb is a 405 — behind the same root admin token. Money leaves as a
  string, and **every total publishes `unpriced_requests`** so a sum can never present
  itself as complete when it is not.
- **The request log line grew six fields** — `stop_reason`, the token counts, `usd_cost`,
  `cost_status` — written *before* the row is queued, which is what makes a lost row
  reconstructible (H-027's second leg).
- **Tests: 441 keyless** (280 → 441), 2 live-marked and deselected by default. New files
  — `test_ledger_store` (42), `test_metering` (32), `test_prices` (23),
  `test_usage_extraction` (22), `test_cost` (17), `test_admin_usage` (17),
  `test_ledger_writer` (8). Every Phase 0/1/2 test still green.
- **Docs**: H-023 … H-028 in `docs/DECISIONS.md`; README gains a *The cost ledger*
  section; `migrations/README.md` status table; `.env.example` documents
  `HEADROOM_MODELS_CONFIG`.

**Deferred**

- Nothing from the Phase 3 scope. Explicitly *not* built, per the plan: rate limits and
  budgets (P4), the cache disposition and avoided-cost fields (P5), failover hops (P6),
  the dashboard that reads all of this (P7). The seams exist and are columns already in
  migration 0002, so those phases add code and not a schema change.
- **Prompt-cache tier *pricing* is deferred and labelled** (H-026), not silently
  omitted: the tokens are recorded and any row affected by them is marked `partial`.

**Deviations**

1. **`config/models.yaml` gained a `match: prefix` mode** the plan's description does not
   mention. One `mock-` entry prices every mock model the suite invents, which is what
   keeps a new fixture from silently becoming unpriced. Exact ids still win; the rule is
   the routing table's (H-013), so there is no second convention to learn.
2. **Real models' price history starts on 2026-08-08 rather than at some earlier date.**
   A request dated before that is `unpriced_model` with a NULL cost. Guessing when a
   published rate began is the same class of error as guessing the rate, and this repo
   has never billed a request before that day. **H-023.**
3. **The Phase 1 passthrough loop changed shape**: it now feeds the SSE observer for the
   whole response instead of stopping at the dialect's terminal marker. Not tidying —
   in the OpenAI dialect the usage chunk arrives *after* the `finish_reason` frame, so
   the old loop metered nothing at all there. Sabotage E below is that old shape,
   restored briefly to prove the tests notice. **No forwarded byte changed**; the tap is
   still a tap.
4. **Cache-tier token columns and the `partial` status are additions** the plan's Phase 3
   text does not enumerate. Without them an Anthropic request using prompt caching would
   be silently under-billed on one dialect and over-billed on the other. **H-026.**
5. **Anonymous 401s are deliberately absent from the ledger**, so a tenant's row count is
   not their request count. The plan says failed requests are rows; this is the one class
   that cannot be, because it has no tenant. **H-025**, and the README says so.
6. **`Decimal` money is serialized with `format(value, "f")`, not `str()`.** Found in the
   container run: `str(Decimal("0.000000000000"))` is `"0E-12"` — correct, parseable, and
   a surprise in a JSON field a dashboard is about to render. One shared `format_usd`
   now serves the API and the log line, with a test at both.
7. **One Phase 1 test changed**, because the log-field set it pins grew:
   `test_request_context.py::test_the_log_shape_is_complete`. No Phase 1 *behaviour*
   changed.
8. **`HEADROOM_MODELS_CONFIG` joins `HEADROOM_ROUTING_CONFIG`** in `core/config.py` —
   environment-variable names live there (the Phase 2 precedent), while the loader lives
   in `metering/`, because routes are policy and prices are reference data (H-014's
   split, extended).

**Gate** — *a scripted mock conversation's metered cost matches a hand-computed figure to
the cent; a dated-price boundary test (same request, two dates, two prices); one live
smoke where Headroom's metered usage equals the provider's own reported usage exactly.*

Run on the operator's machine with the compose stack up and **nothing exported**:

```
$ make lint
uv run ruff check .
All checks passed!
uv run ruff format --check .
86 files already formatted

$ make typecheck
uv run mypy
Success: no issues found in 86 source files

$ make test
================= 441 passed, 2 deselected, 1 warning in 5.71s =================

$ uv run pytest -m live -q --collect-only
2/443 tests collected (441 deselected) in 0.07s
```

**441 passed, 0 skipped** — `grep -c SKIPPED` over the run returns `0`, so the Postgres
half of *both* contract suites executed against the compose containers rather than
skipping (H-012).

Per-file counts from the same run:

```
44 tests/test_tenant_store.py         14 tests/test_error_mapping.py
42 tests/test_ledger_store.py         11 tests/test_request_context.py
32 tests/test_metering.py             10 tests/test_reasoning_passthrough.py
29 tests/test_auth_matrix.py          10 tests/test_auth_cache.py
27 tests/test_virtual_keys.py          8 tests/test_ledger_writer.py
25 tests/test_admin_api.py             8 tests/test_key_secrecy.py
23 tests/test_prices.py                6 tests/test_non_streaming.py
22 tests/test_usage_extraction.py      6 tests/test_mid_stream_cut.py
19 tests/test_provider_clients.py      5 tests/test_tool_blocks.py
17 tests/test_sse.py                   5 tests/test_logging.py
17 tests/test_routing.py               4 tests/test_no_buffering.py
17 tests/test_cost.py                  3 tests/test_pytest_policy.py
17 tests/test_admin_usage.py           3 tests/test_migrations.py
14 tests/test_streaming_passthrough.py 2 tests/test_services.py
                                       1 tests/test_healthz.py
```

**The gate's three clauses, individually.**

*Exact price, hand-computed.* 11 prompt tokens at $0.25/MTok plus 7 generated at
$1.25/MTok is $0.00000275 + $0.00000875 = **$0.0000115**, and that is the figure the
ledger holds — in both dialects and both transports, because a price that depended on
the transport would be a bug:

```
$ uv run pytest tests/test_metering.py -q -k "exactly_priced or prices_identically"
....                                                                     [100%]
4 passed, 28 deselected in 0.03s
```

A second figure with no decimals to trust at all: 1,000,000 in and 1,000,000 out at the
same rates is **$1.50**, asserted in
`test_scripted_token_counts_produce_a_hand_checkable_figure`.

*Dated-price boundary.* One model, two rows ($1/$2 per MTok until 2026-06-01, $10/$20
from it), the same 4-in/10-out request on each side — $0.000024 and $0.00024:

```
$ uv run pytest tests/test_metering.py -k boundary -v
tests/test_metering.py::test_the_same_request_on_either_side_of_a_boundary_gets_each_price[when0-expected0] PASSED [ 50%]
tests/test_metering.py::test_the_same_request_on_either_side_of_a_boundary_gets_each_price[when1-expected1] PASSED [100%]
======================= 2 passed, 30 deselected in 0.01s =======================
```

And the same property against the **shipped** file, where the boundary is Anthropic's
own published one (`tests/test_prices.py::test_the_real_dated_boundary_in_the_shipped_file`):
Sonnet 5 resolves to $2/$10 on 2026-08-31 and $3/$15 on 2026-09-01.

*THE D-017 TEST.* Bill a request, replace the entire price book with rates a thousand
times higher, and read the row back — unmoved, down to the effective date
(`test_a_price_change_never_reprices_a_row_that_already_landed`). Its pair,
`test_the_new_price_does_apply_to_the_next_request`, asserts the other half: history is
immutable, the future is not.

*Reasoning tokens.* `test_a_reasoning_request_is_billed_on_completion_tokens_not_visible_text`
drives the Phase 1 reasoning fixture through the whole stack and asserts the ledger row
bills **63** output tokens — 57 of them chain of thought that never appears in
`delta.content` — while the same response's visible answer is **11 characters** long.
The row and the text are asserted in the same test, on purpose.

*Live smoke — deferred to the operator*, and it is the only clause not closed in this
session: the machine that built this cannot reach the operator's vLLM box, and the
Anthropic half is paid. Commands are in the PR description. Its keyless stand-in is
`tests/test_usage_extraction.py`, which asserts streamed and non-streamed usage agree in
both dialects against the mock's scripted blocks.

**The sabotage runs.** Green on the first attempt is when a suite deserves the most
suspicion, so each of the phase's five claims was tested by breaking the thing it
protects. All five patches were reverted immediately (`git status` clean of them; the
suite is 441 green above).

*Sabotage A — the meter bills only the visible part of the answer* (`completion_tokens`
minus `reasoning_tokens`, which is exactly the mistake a text-aware meter makes):

```
5 failed, 434 passed, 2 deselected
FAILED tests/test_metering.py::test_a_reasoning_request_is_billed_on_completion_tokens_not_visible_text
FAILED tests/test_metering.py::test_an_exhausted_reasoning_budget_is_a_complete_billable_request
FAILED tests/test_usage_extraction.py::test_reasoning_tokens_are_inside_the_output_count_not_beside_it
FAILED tests/test_usage_extraction.py::test_reasoning_usage_is_identical_in_the_non_streamed_form
FAILED tests/test_usage_extraction.py::test_an_exhausted_budget_still_reports_its_usage
```

*Sabotage B — the row references the price instead of copying it in* (the D-017 shape):

```
2 failed, 437 passed, 2 deselected
FAILED tests/test_admin_usage.py::test_a_row_reports_the_rates_it_was_billed_at
FAILED tests/test_metering.py::test_a_price_change_never_reprices_a_row_that_already_landed
```

*Sabotage C — an unknown cost is written as `0.00` instead of NULL.* This is the one
that would be invisible in production: every row has a number, every total sums, and the
figure is quietly too low.

```
6 failed, 433 passed, 2 deselected
FAILED tests/test_admin_usage.py::test_a_total_publishes_what_it_could_not_price
FAILED tests/test_cost.py::test_a_request_with_no_usage_block_is_unknown_not_free
FAILED tests/test_cost.py::test_half_a_usage_block_is_still_unknown
FAILED tests/test_metering.py::test_a_mid_stream_cut_is_a_row_whose_cost_is_unknown_not_zero
FAILED tests/test_metering.py::test_a_timeout_is_unknown_because_the_provider_may_have_billed_it
FAILED tests/test_metering.py::test_a_stream_without_include_usage_is_recorded_as_unknown
```

*Sabotage D — `message_start`'s `output_tokens: 0` placeholder is trusted*, which bills
a severed stream as a complete, free answer:

```
2 failed, 437 passed, 2 deselected
FAILED tests/test_metering.py::test_a_mid_stream_cut_is_a_row_whose_cost_is_unknown_not_zero
FAILED tests/test_usage_extraction.py::test_a_stream_cut_before_message_delta_leaves_the_output_count_unknown
```

*Sabotage E — the observer stops at the terminal marker* (the Phase 1 loop shape, before
deviation 3). Note which dialect notices: Anthropic's usage lands before `message_stop`
and is unaffected, while every OpenAI-dialect streamed row loses its counts entirely.

```
4 failed, 435 passed, 2 deselected
FAILED tests/test_admin_usage.py::test_totals_split_by_model_on_request
FAILED tests/test_metering.py::test_a_streamed_openai_request_prices_identically
FAILED tests/test_metering.py::test_a_reasoning_request_is_billed_on_completion_tokens_not_visible_text
FAILED tests/test_metering.py::test_an_exhausted_reasoning_budget_is_a_complete_billable_request
```

**End to end through the real container**, on a wiped volume, keyless, no network:

```
$ docker compose down -v && make up
 Container headroom-db-1 Healthy
 Container headroom-dynamodb-1 Healthy
 Container headroom-gateway-1 Healthy
docker compose exec -T gateway uv run --no-sync python -m headroom.db.migrate
applied 2 migration(s): 0001_tenants_and_virtual_keys, 0002_usage_ledger

### 2. a non-streamed request
{"id":"msg_mock_6ca20aa0926a68c6","type":"message","role":"assistant","model":"mock-model-1",
 "content":[{"type":"text","text":"mock reply from mock-model-1"}],"stop_reason":"end_turn",
 "usage":{"input_tokens":11,"output_tokens":7}}
x-headroom-request-id: hr_8836fd9616514df5a4c2b6d3ee972d3a

### 3. the same request, streamed   -> hr_450991253693464aab01922f5d918182
### 4. an unroutable model, same key -> 404
### 5. an anonymous request          -> 401

### 6. GET /admin/usage
{"model":"gpt-5","streamed":false,"outcome":"model_not_routed","status_code":404,
 "input_tokens":null,"output_tokens":null,"stop_reason":null,"usd_per_mtok_in":null,
 "usd_per_mtok_out":null,"usd_cost":"0.000000000000","cost_status":"not_billable",
 "price_effective_from":null}
{"model":"mock-model-1","streamed":true,"outcome":"ok","status_code":200,
 "input_tokens":11,"output_tokens":7,"stop_reason":"end_turn","usd_per_mtok_in":"0.2500000000",
 "usd_per_mtok_out":"1.2500000000","usd_cost":"0.000011500000","cost_status":"priced",
 "price_effective_from":"1970-01-01"}
{"model":"mock-model-1","streamed":false,"outcome":"ok","status_code":200,
 "input_tokens":11,"output_tokens":7,"stop_reason":"end_turn","usd_per_mtok_in":"0.2500000000",
 "usd_per_mtok_out":"1.2500000000","usd_cost":"0.000011500000","cost_status":"priced",
 "price_effective_from":"1970-01-01"}

### 7. GET /admin/usage/totals
[{"tenant_id":"d6b28c1f-c501-49a2-a1ce-e74db2852513","model":null,"requests":3,
  "input_tokens":22,"output_tokens":14,"reasoning_tokens":0,"usd_cost":"0.000023000000",
  "unpriced_requests":0,"errored_requests":1}]

### 8. what the database actually holds
    model     | str |     outcome      | in_t | out_t | usd_per_mtok_in |    usd_cost    | cost_status
--------------+-----+------------------+------+-------+-----------------+----------------+--------------
 mock-model-1 | f   | ok               |   11 |     7 |    0.2500000000 | 0.000011500000 | priced
 mock-model-1 | t   | ok               |   11 |     7 |    0.2500000000 | 0.000011500000 | priced
 gpt-5        | f   | model_not_routed |      |       |                 | 0.000000000000 | not_billable
(3 rows)

### 9. the column types money is stored in
   column_name    | data_type | prec | scale
------------------+-----------+------+-------
 usd_cost         | numeric   |   24 |    12
 usd_per_mtok_in  | numeric   |   20 |    10
 usd_per_mtok_out | numeric   |   20 |    10

### 10. one request log line
{"request_id":"hr_450991253693464aab01922f5d918182","route":"/v1/messages","dialect":"anthropic",
 "tenant_id":"d6b28c1f-…","key_id":"8415fe99-…","model":"mock-model-1","provider":"mock",
 "stream":true,"outcome":"ok","status":200,"upstream_status":200,"error_source":null,
 "error_reason":null,"stop_reason":"end_turn","input_tokens":11,"output_tokens":7,
 "reasoning_tokens":null,"cache_read_tokens":null,"cache_write_tokens":null,
 "usd_cost":"0.000011500000","cost_status":"priced","upstream_latency_ms":0.698,
 "ttft_ms":0.74,"passthrough_overhead_ms":0.042,"total_ms":0.833}
```

Three things in that output are the phase in miniature. The unroutable model has
`usd_cost` **`0.000000000000`** and `cost_status: not_billable` — a zero that is a
measurement — while a request whose cost were merely *unknown* would show `null` there.
The two `mock-model-1` rows are identical whether streamed or not, which is what "the
transport does not change the price" looks like. And the anonymous 401 has **no row at
all**: three requests reached the ledger out of four, exactly as H-025 says.

**Assumed-facts register (§0.4)**

- **A3 — VERIFIED (keyless half).** *Both dialects expose usage on streamed responses
  (Anthropic `message_delta.usage`; OpenAI-compat final chunk with
  `stream_options: {"include_usage": true}`).* `tests/test_usage_extraction.py` asserts
  the streamed and non-streamed forms of each dialect report the **same** counts against
  the mock's scripted blocks, and `tests/test_metering.py` asserts the resulting ledger
  rows are identical in cost. Two nuances the assumption did not anticipate, both now
  pinned: Anthropic's `message_start` carries an `output_tokens: 0` *placeholder* that
  must not be read as a measurement, and the OpenAI usage chunk arrives **after** the
  terminal `finish_reason` frame, which is why the passthrough loop had to change
  (deviation 3). The remaining half — *metered tokens from one live smoke match the
  provider's own reported usage* — is the operator's, below.
- **A4, A5 — still VERIFIED, and re-proved under a feature that had every reason to
  break them.** Metering is the first thing in the project that genuinely wants to
  rewrite bytes (inject `stream_options`, strip the usage chunk). It does not: H-028
  declines the trade, `test_the_gateway_does_not_add_stream_options_to_the_callers_body`
  asserts the provider received the caller's bytes verbatim, and
  `test_metering_a_stream_does_not_disturb_one_byte_of_it` asserts a fully metered
  reasoning stream is byte-identical to the mock's output.
- **A1, A2, A6, A7** — not due at this gate, none touched.

CI on PR-3 ([run 31299963043](https://github.com/sergioavilax/headroom/actions/runs/31299963043)),
all three jobs green, no annotations:

```
$ gh run view 31299963043 --json conclusion,jobs
success
pytest (postgres + dynamodb-local service containers): success
gateway image builds and serves: success
lint + typecheck: success

lint + typecheck | All checks passed!
lint + typecheck | Success: no issues found in 86 source files
pytest (…service containers) | ===== 441 passed, 2 deselected, 1 warning in 8.08s =====
Migrations apply, twice is a no-op | migrations: up to date, nothing to apply
gateway image builds and serves | gateway healthy
```

**441 passed, 0 skipped in CI** — `grep -c SKIPPED` over the whole log returns `0`, so
the Postgres halves of both the tenant-store and ledger-store contract suites executed
against the service container. The `image` job still proves the lazy pool: it builds the
container and smokes `/healthz` with no Postgres anywhere in the job, which a gateway
that connected — or loaded a price book from a database — at startup could not do.

**Live smoke — not run in this session, and it is the operator's.** The build machine
cannot reach the vLLM box, and the Anthropic half spends money. Both are cheap:

```
# free — the operator's GPU. Compare the ledger row against vLLM's own usage block.
$ VLLM_BASE_URL=http://<instance>:8000 uv run pytest -m live -k vllm -v

# ~$0.0001 against the $3 P1–P7 bucket.
$ ANTHROPIC_API_KEY=… uv run pytest -m live -k anthropic -v
```

Then, with the stack up, read the row back and check it against the provider's own
reported usage — that comparison is A3's remaining half:

```
$ curl -sS -H "Authorization: Bearer $HEADROOM_ADMIN_TOKEN" \
       localhost:8080/admin/usage/<request-id>
```

**Spend** — $0.00. No provider API was called in this phase; every test ran on the
MockProvider and the container demo talked to nothing.

### Addendum (2026-08-09) — the live smokes were broken from PR-2's merge until now

**The finding.** Both tests in `tests/test_live_smoke.py` failed with 401
`missing_api_key` on the operator's first live run after Phase 2 merged. They predate
tenancy and sent no virtual key; since Phase 2 every `/v1/*` request requires one. PR-2
moved the keyless suite onto the authenticated path (`GatewayHarness.post` presents a
key by default) and did not touch these two, so they have been red since 3c0cf63 — the
whole of Phase 3 — and the Phase 2 and Phase 3 gates both passed with them broken.

**Root cause, stated plainly.** The live smokes exercise the authentication surface and
are excluded from every automated run: `-m "not live"` deselects them in `make test` and
in all three CI jobs, and they only execute on the operator's machine, by hand, after a
phase is otherwise finished. So an auth change can break them and nothing reports it.
That is not a mistake in PR-2's diff; it is a structural gap in what CI can see, and it
will recur at every phase that touches the request path (Phase 4's budget gate is the
next one) unless something keyless stands in for them.

Two things about the gap are worth recording accurately, because the obvious mitigation
is aimed at the wrong failure:

- **Import-time bitrot was never uncovered.** `-m` deselects *after* collection, so every
  default run already imports `tests/test_live_smoke.py`; an `ImportError` or a bad
  decorator there fails `make test` today.
- **Behaviour was.** Nothing keyless could tell whether the request these tests build
  still authenticates — and that is exactly what rotted.

**The fix** (`tests/support/live.py`, new). Each live smoke now provisions its own
identity instead of assuming one: it creates — or reuses — a tenant named `live-smoke`
through the real `TenantStore`, mints an unrestricted key, sends the request with it, and
revokes the key on the way out. The operator's setup is unchanged: `make up` plus
`ANTHROPIC_API_KEY` / `VLLM_BASE_URL`, with migrations applied by the helper so a smoke
on a fresh volume cannot fail on a missing table after the money is spent. Argued in
**H-029**.

The stores are the real ones — Postgres, not the in-memory pair the keyless fixture uses
— because the ledger row is the point: each smoke now asserts a row landed, that it is
attributed to the smoke tenant and key, and prints the request id, tenant id, tokens,
cost, and the `curl` to read it back. A live run that spends money and then fails at the
ledger still hands over the id, because the report is emitted before the assertion.

**The mitigation** (`tests/test_live_smoke_wiring.py`, new — 5 keyless tests). The
smokes' own provisioning helper is driven through the real `Authenticator` on every CI
run: a provisioned credential authenticates and stamps the right tenant and key onto the
context; the sabotage — the headers these tests actually sent before today — raises
`MissingCredential`; provisioning twice reuses the tenant and mints a fresh key; and a
deactivated smoke tenant is reactivated rather than becoming an unexplained 401. It
cannot prove the live smokes pass. It proves the credential they present is one the
gateway accepts, which is the thing that broke.

**An operational caveat found while verifying this, previously undocumented.** The
Postgres half of the tenant-store contract suite runs `TRUNCATE virtual_keys, tenants
CASCADE`, and `usage_ledger` references both tables — so `make test` truncates the
ledger. Confirmed here: a row written by the dry run below was gone after the next
`make test`. Read a live smoke's row back *before* running the suite again; the smoke
prints that warning on the line after the request id.

**Verification.** The paid and GPU halves are still the operator's — the build machine
reaches neither. What was verified here is all of the new plumbing, for free, by driving
`live_gateway` at the routable `mock-` model against the running compose stack: real
gateway, real control plane, real ledger, no provider and no spend. The harness below
was a throwaway script (not committed — it is `live_gateway` plus a `mock-model-1` body
and six `print`s); it is deliberately *not* a keyless test, because the control plane has
no delete and a test that ran on every `make test` would leave a tenant and a ledger row
behind each time, which 2237d99 went to some trouble to stop.

```
$ uv run python dryrun_live_wiring.py     # scratch harness, not committed
status: 200
request_id: hr_aeec006559904a6599a3e34e60101fa9
tenant: live-smoke ab6a95b4-0724-45b4-b0e5-91495af0c33e
key: hk_Lu2kx0Vu 6533685f-1ea8-4eaf-a065-de8245964646
row: mock-model-1 mock ok 11 7 0.000011500000 priced
key revoked on exit: True

$ docker compose exec -T db psql -U headroom -d headroom \
    -c "SELECT request_id, tenant_id, model, outcome, usd_cost, cost_status FROM usage_ledger …"
             request_id              |              tenant_id               |    model     | outcome |    usd_cost    | cost_status
-------------------------------------+--------------------------------------+--------------+---------+----------------+-------------
 hr_aeec006559904a6599a3e34e60101fa9 | ab6a95b4-0724-45b4-b0e5-91495af0c33e | mock-model-1 | ok      | 0.000011500000 | priced
(1 row)

$ make test          # …and the caveat above, demonstrated
================= 446 passed, 2 deselected, 1 warning in 5.50s =================
$ docker compose exec -T db psql -U headroom -d headroom -c "SELECT count(*) FROM usage_ledger"
 ledger_rows
-------------
           0
```

**Gate.** `make lint typecheck test` on the branch:

```
$ make lint
uv run ruff check .
All checks passed!
uv run ruff format --check .
88 files already formatted

$ make typecheck
uv run mypy
Success: no issues found in 88 source files

$ make test
================= 446 passed, 2 deselected, 1 warning in 5.50s =================
```

441 → **446**: the five new keyless tests in `tests/test_live_smoke_wiring.py`. The two
deselected are the live smokes, still excluded, as invariant 4 requires.

**Spend** — $0.00. The dry run above talked to the MockProvider; no provider API was
called.

---

## Phase 4 — Limits and budgets: the D-019 lesson as a product (2026-08-09)

**Shipped**

- **The budget gate, reservation-based from the first line.** Admission reserves a
  request's worst case *before* the upstream is opened; completion settles the hold to
  the actual cost. Both halves are one atomic DynamoDB conditional write, and the
  sentence the whole phase rests on is: **every mutation of a budget is a single
  conditional write to a single item.** No transactions, no dual writes, no
  read-then-write anywhere on the path.
- **The condition, in full** (`headroom/db/budgets.py`):

  ```
  ConditionExpression: attribute_exists(scope_id)
                       AND window_expires_at > :now
                       AND remaining_picos  >= :estimate
                       AND attribute_not_exists(reservations.#rid)
  UpdateExpression:    SET remaining_picos = remaining_picos - :estimate,
                           reserved_picos  = reserved_picos  + :estimate,
                           reservations.#rid = :hold, updated_at = :updated
  ```

  `remaining` is a **stored** attribute rather than a derived one, because DynamoDB
  conditions compare an attribute to a value and do no arithmetic — there is no way to
  write `spent + reserved + estimate <= budget`. The item shape is downstream of what a
  condition can express, and that is what turns "may this proceed?" into one operation.
  **H-030.**
- **One item per scope**, carrying config, window stamp, counters, *and* the live
  reservations as a map. Keeping the holds inside the counter item is what avoids a
  transaction: a reservation in a second item would be a dual write, and a crash between
  the two either strands budget or invents it. The cost is DynamoDB's 400 KB item limit
  — a few thousand simultaneously in-flight requests for one tenant — documented and
  bounded by the sweep.
- **Money is integer picodollars** (1e-12 USD), which is *exactly*
  `metering.cost.USD_QUANTUM`, so `Decimal → int → Decimal` is lossless for every value
  the meter can produce and the gate and the ledger can be compared to the last digit.
  Cent-integers (the brief's suggestion) would round the canonical $0.0000115 fixture to
  zero; `Decimal` would put a rounding context in the middle of the one argument that has
  to be airtight. **H-030.**
- **Windows: `monthly` (calendar, UTC) and `total` (lifetime), with no reset job.**
  `window_expires_at > :now` is part of the admission condition, so the first request of
  a new month fails it and the failure path rolls the counters with a compare-and-set on
  the old `window_id`. Exactly one racer wins; the rest retry the ordinary path. The
  window is resolved from the request's own `started_at` — the H-023 rule, so a queued
  settlement cannot land in the wrong month. **H-033.**
- **The estimate, documented term by term** (`headroom/policy/budgets.py`):

  ```
  input_tokens  = min(ceil(len(request_body) / 3), context_window)
  output_tokens = max_tokens from the body, else 4096
  usd           = input_tokens * rate_in / 1e6 + output_tokens * rate_out / 1e6
  ```

  at the price in effect on the request's own date. Three bytes per token, not four,
  because a bound has to err upward. The prompt half is included even though the brief's
  letter says "from max_tokens": generated tokens dominate the *rate*, prompt tokens
  dominate the *count*, and an output-only estimate stops being an upper bound at
  `max_tokens: 8` — which sabotage I below demonstrates. **H-034.**
- **A refusal is 402**, in the caller's own dialect: Anthropic `billing_error`, OpenAI
  `insufficient_quota`, `headroom.reason: budget_exceeded`, and the tenant's own figures
  in the message. Not 429 — that means *slow down*, every SDK retries it, and a retry
  storm against the one item the gate serialises on is the failure this phase exists to
  prevent. No `retry-after`, for the same reason. **H-032.**
- **A refused request provably never reaches a provider.** The gate raises between
  `require_provider` and `provider.open`; `tests/test_budget_gate.py` asserts it against
  the MockProvider's own record of what it was handed rather than reasoning about it.
- **Settlement semantics, decided from the meter's `cost_status`** — and one deliberate
  disagreement with the ledger. `priced`/`partial` settle at the actual cost;
  `not_billable`/`unpriced_model` release to $0; **`usage_unknown` settles at the
  estimate**, because a timeout or a cut stream *did* reach a model and releasing the
  hold would be a cheerful guess that it cost nothing. The ledger still writes NULL there
  — it is an invoice and states facts; the budget is a guard rail and states bounds. Both
  figures sit on the same row. **H-031.**
- **Reservations cannot leak.** Every hold carries `expires_at = now + 900s`, and expired
  holds are **released, not charged** (an unknown *outcome* is not evidence of spend, and
  charging on suspicion would let one restart under load eat a tenant's month). Three
  sweep triggers, and the first is the one that matters: **on the refusal path, before
  refusing** — so a dead process's hold can never be the reason a live request is turned
  away. `ReturnValuesOnConditionCheckFailure` hands back the item the condition failed
  against, so that sweep costs **no extra read**. Also on an admin read, and via an
  explicit `sweep_expired`. No background task is load-bearing, so none is started.
  **H-032.**
- **`BudgetStore`: one interface, two implementations, one contract suite** — the H-021
  shape, with an honest caveat stated in the module docstring rather than discovered
  later: `InMemoryBudgetStore` reproduces the *semantics*, not the *concurrency*. Its
  operations never suspend, so they cannot interleave, so a stampede run against it would
  prove nothing. That proof belongs to DynamoDB Local alone.
- **boto3 calls are dispatched to a bounded thread pool** (`headroom/db/dynamo.py`).
  Not an optimisation: boto3 is synchronous, and calling it from a coroutine would block
  the event loop *and* serialise the budget gate — the stampede would pass against a
  design that had never actually been raced.
- **`/admin/budgets`**: `PUT`/`GET`/`DELETE /admin/budgets/{tenant_id}` and
  `GET /admin/budgets`, behind the same root token. Reports budget, spent, reserved,
  remaining, **committed** (spent + reserved — the figure BUILD_PLAN §0.2 rule 5 is
  about), live reservation count, and the sweep counters. Money leaves as a string; a
  JSON *number* for `usd` is refused by name, the H-023 rule applied to the admin API.
- **`migrations/0003_ledger_budget_columns.sql`** — `budget_status`,
  `budget_reserved_usd`, `budget_settled_usd` on `usage_ledger`, plus a partial index on
  refusals. A 402's row is the only surviving record that the request happened, since no
  provider was called. The request log line grew the same three fields.
- **Tests: 576 keyless** (446 → 576), 2 live-marked and deselected by default. New files
  — `test_budget_store` (62, the contract suite over both implementations),
  `test_admin_budgets` (20), `test_budget_gate` (18), `test_dynamo_client` (14),
  `test_budget_estimate` (12), `test_budget_stampede` (4). Every Phase 0/1/2/3 test still
  green, and **one** of them changed (deviation 4).
- **Docs**: H-030 … H-034 in `docs/DECISIONS.md`; README gains a *Budgets that hold under
  concurrency* section; `.env.example` documents `HEADROOM_BUDGETS_TABLE` and the
  emulator-credential trap; `migrations/README.md` status table.

**Deferred**

- **Token-bucket rate limits — the other half of BUILD_PLAN's Phase 4 — did not land.**
  The plan's Phase 4 text names *"token-bucket rate limits (requests/min and tokens/min
  per key and per tenant)"* alongside monthly budget caps; this session's brief scoped
  the phase to the budget gate alone and does not mention buckets, 429s, or
  `retry-after`. The budget half is complete and coherent on its own, so the remainder is
  marked here explicitly rather than half-built (`CLAUDE.md`: *finish a coherent
  sub-slice, mark the remainder, stop cleanly*). It is a natural **PR-4b**: a second
  DynamoDB table with its own access pattern and TTL, the same `DynamoClient`, the same
  conditional-write discipline, and a hammer test of its own. Nothing in this PR
  forecloses it — `BudgetScope` already carries a `kind`, and the client is shared.
- **Per-key budgets**, deliberately (**H-033**): enforcing two caps on one request means
  two holds, and two conditional writes are not one atomic operation. The compensating
  release when the second fails is a new home for exactly the bug this phase exists to
  remove. The scope abstraction is in place so a later phase adds a kind rather than
  reshaping the table.
- **Prompt-cache tier pricing** stays deferred from Phase 3 (H-026); a `partial` row's
  budget effect is a lower bound too, and is labelled as one.

**Deviations**

1. **BUILD_PLAN's Phase 4 is delivered in two PRs, not one.** See *Deferred* above. The
   plan's gate text for this phase mentions "clean 429 responses with retry-after", which
   belongs to the rate-limit half and is therefore not claimed here. The budget half's
   gate — the concurrency hammer and the sabotage — is met in full.
2. **`migrations/0003` adds columns to `usage_ledger`**, which contradicts H-024's closing
   remark that *"the shape of a ledger row stops changing after this migration"*. That
   sentence was about the Phase 5 and Phase 6 seams, which really are already present as
   columns; it did not anticipate a fourth phase needing a fact of its own on the row, and
   the brief requires it (*"the ledger row records the budget outcome"*). The alternative
   — a second table joined on `request_id` — would put the answer to "why was this
   refused" one join away from the row that refused it. Additive, nullable, and H-003's
   immutability rule is untouched: `0002` is not edited.
3. **`Meter.record` was split into `measure` + `commit`.** The settlement sits between
   them: what a hold settles at is decided from `cost_status`, which `measure` sets, and
   the settled figure is a column on the row, which `commit` writes. `record` remains, as
   `measure` + `commit`, for every caller with no budget to settle. Nothing about the
   metering path became asynchronous — both halves are the same synchronous calls (H-027).
4. **One Phase 1 test changed**, because the log-field set it pins grew:
   `test_request_context.py::test_the_log_shape_is_complete`. Same class of change as
   Phase 3's deviation 7. No Phase 1 *behaviour* changed.
5. **`Dialect` gained `max_output_tokens`.** The one field in a request body that bounds
   what a provider may generate, and the only way to estimate before the fact. Read-only,
   additive, and it validates nothing — a nonsense value reads as "not stated" and the
   provider's own rejection still stands (BUILD_PLAN L4).
6. **`GET /admin/budgets/{tenant}` has a side effect**: it releases expired holds before
   reporting. Taken deliberately (**H-032**) — releasing an already-expired hold changes
   nothing live and is idempotent, and the alternative is a `reserved` figure that is
   quietly wrong exactly when an operator is relying on it. The list route does not sweep.
7. **Two facts about `amazon/dynamodb-local:3.1.0` that cost real time**, recorded in
   H-030 and pinned by tests: **`scope` is a DynamoDB reserved word** (the partition key
   is `scope_id`), and **DynamoDB Local rejects an access key id containing a hyphen**
   with `UnrecognizedClientException: The Access Key ID or security token is invalid` —
   an error that reads exactly like a real AWS authentication failure. The first emulator
   credential was `headroom-local` and every DynamoDB-backed test failed with it.
8. **Additions the plan's Phase 4 text does not enumerate**, all additive:
   `tests/support/harness.py` gained a reusable `gateway_harness` context manager (so the
   stampede can build a gateway backed by DynamoDB Local while every other test keeps the
   in-memory default); `boto3`/`botocore` mypy overrides (neither ships type information,
   and `boto3-stubs` is a large generated package pinned to an SDK version, for a surface
   of five calls that cross one file); and `HEADROOM_BUDGETS_TABLE` in compose.

---

**Gate** — *the concurrency hammer: N parallel requests against a bucket sized for fewer,
asserting zero oversubscription — plus the sabotage test: a deliberately naive
implementation must FAIL the same hammer.*

### THE STAMPEDE — the headline artifact

64 concurrent requests through the whole gateway, against a budget sized for 5, on
DynamoDB Local. The MockProvider is scripted to report exactly the usage the estimate
assumed, so `actual == estimate == one unit` — the worst case a budget gate has to
survive, and the case that makes the closing arithmetic exact rather than approximate.

```
$ uv run pytest tests/test_budget_stampede.py -q -s

ATOMIC GATE (shipped)
  requests fired         64
  served (200)           5
  refused (402)          59
  budget                 $0.000237500000
  settled spend          $0.000237500000
  still reserved         $0.000000000000
  remaining              $0.000000000000
  overspend              $0.000000000000
  spend / budget         1.00x
```

Five served, fifty-nine refused, the cap consumed **exactly** — to the picodollar — and
no refused request left a hold behind. Every one of the 64 got a real HTTP answer.

### THE SABOTAGE — D-019, reconstructed

Same gateway, same budget, same 64 requests. The only difference is
`NaiveBudgetStore.reserve`, which **reads the balance, decides, and then writes**, as two
operations with an await between them. That is precisely what Backline's gate did.

```
SABOTAGED GATE (D-019: check, then write)
  requests fired         64
  served (200)           64
  refused (402)          0
  budget                 $0.000237500000
  settled spend          $0.003040000000
  still reserved         $-0.000950000000
  remaining              $0.000142500000
  overspend              $0.002802500000
  spend / budget         12.80x
```

**Every single request admitted. Nothing refused. 12.80× the budget spent.** Every racer
read the same untouched balance, every racer decided it fitted, and every racer was right
about a world that no longer existed by the time it wrote.

Look at `still reserved: -$0.000950000000`. It does not merely overspend — **the books
stop balancing**. Unconditional writes of a stale `reserved` clobber one another, so the
item ends up claiming a negative amount is held and a positive amount remains, while
twelve times the cap has already been settled. That is the shape of a lost update, and it
is why "check, then write" cannot be repaired by checking harder.

### THE SECOND SABOTAGE — atomic, and still wrong

BUILD_PLAN's own named sabotage is subtler and, for a reviewer, more useful: *"a
deliberately naive **landed-only** gate"*. This one fixes the obvious bug and keeps the
real one. The check and the deduction are a single atomic conditional write —
everything the first sabotage got wrong is right here — and the condition asks the wrong
question: `spent_picos <= :budget_minus_estimate`. **Landed** spend, not committed spend.

```
SABOTAGED GATE (atomic, but reads LANDED spend)
  requests fired         64
  served (200)           38
  refused (402)          26
  budget                 $0.000237500000
  settled spend          $0.001805000000
  still reserved         $0.000000000000
  remaining              $-0.001567500000
  overspend              $0.001567500000
  spend / budget         7.60x
```

7.60× the cap — and note `still reserved: $0.00` and an arithmetic identity that
**holds perfectly throughout**. That is the whole warning: everything about a landed-only
gate looks right except the number it compares, which is why it survives code review.

It is also **partly effective**, which is the most dangerous property a broken guard can
have: settlements land while the burst is still running, so `spent` does eventually climb
past the ceiling and the tail of the stampede really is refused. An operator watching 26
402s in the logs concludes the budget is working. It is working at roughly a seventh of
its stated cap.

This is BUILD_PLAN §0.2 rule 5 in its exact words — *"the budget gate reads **committed**
spend — reserved + landed — never landed alone"* — and it is why the shipped condition
tests `remaining`, a number that moves the instant a hold is taken. **Atomicity is
necessary and it is not sufficient.**

All three runs are in one test file and all three are asserted, so the two sabotages are
permanent: a future change that makes the stampede pass for the wrong reason fails the
two tests whose job is to fail.

### The keyless gate

```
$ make lint
uv run ruff check .
All checks passed!
uv run ruff format --check .
100 files already formatted

$ make typecheck
uv run mypy
Success: no issues found in 100 source files

$ make test
================= 576 passed, 2 deselected, 1 warning in 7.35s =================

$ uv run pytest -m live -q --collect-only
2/578 tests collected (576 deselected) in 0.08s
```

**576 passed, 0 skipped** — `grep -c SKIPPED` over the run returns `0`, so the DynamoDB
half of the budget-store contract suite and the stampede executed against the container
rather than skipping.

Per-file counts from the same run:

```
62 tests/test_budget_store.py         14 tests/test_error_mapping.py
44 tests/test_tenant_store.py         14 tests/test_dynamo_client.py
42 tests/test_ledger_store.py         12 tests/test_budget_estimate.py
32 tests/test_metering.py             11 tests/test_request_context.py
29 tests/test_auth_matrix.py          10 tests/test_reasoning_passthrough.py
27 tests/test_virtual_keys.py         10 tests/test_auth_cache.py
25 tests/test_admin_api.py             8 tests/test_ledger_writer.py
23 tests/test_prices.py                8 tests/test_key_secrecy.py
22 tests/test_usage_extraction.py      6 tests/test_non_streaming.py
20 tests/test_admin_budgets.py         6 tests/test_mid_stream_cut.py
19 tests/test_provider_clients.py      5 tests/test_tool_blocks.py
18 tests/test_budget_gate.py           5 tests/test_logging.py
17 tests/test_sse.py                   5 tests/test_live_smoke_wiring.py
17 tests/test_routing.py               4 tests/test_no_buffering.py
17 tests/test_cost.py                  4 tests/test_budget_stampede.py
17 tests/test_admin_usage.py           3 tests/test_pytest_policy.py
14 tests/test_streaming_passthrough.py 3 tests/test_migrations.py
                                       2 tests/test_services.py
                                       1 tests/test_healthz.py
```

**H-012 still holds with the new DynamoDB tests.** A fresh clone with no stack up must
run a smaller suite *loudly*, and a stated-but-unreachable endpoint must fail rather than
skip:

```
$ docker compose stop dynamodb
$ uv run pytest -q                          # DYNAMODB_ENDPOINT_URL unset — inferred
541 passed, 34 skipped, 2 deselected, 1 warning in 7.04s

$ DYNAMODB_ENDPOINT_URL=http://localhost:8001 uv run pytest -q tests/test_budget_store.py
36 passed, 26 errors in 0.28s
```

### The other sabotage runs

The stampede pair above is the phase's headline, but three more of its claims were tested
by breaking the thing they protect. All were applied with the Edit tool and reverted
immediately; `diff` against pre-sabotage copies of all three touched files reports them
identical, and the suite is 576 green above.

*Sabotage F — an unknown cost is released to zero* (the tidy-looking alternative to
H-031, and the one that undercounts every timeout a flaky provider produces):

```
3 failed, 573 passed, 2 deselected
FAILED tests/test_budget_gate.py::test_a_timeout_settles_at_the_estimate_because_the_provider_may_have_billed_it
FAILED tests/test_budget_gate.py::test_a_mid_stream_cut_settles_at_the_estimate
FAILED tests/test_budget_gate.py::test_a_stream_with_no_usage_block_settles_at_the_estimate
```

*Sabotage G — stranded holds are never released* (the sweep removed from the refusal path
in **both** implementations). Note that the *expiry* tests still pass: what breaks is the
end-to-end property, which is the one that matters operationally.

```
3 failed, 573 passed, 2 deselected
FAILED tests/test_budget_gate.py::test_a_stranded_hold_does_not_refuse_a_live_request
FAILED tests/test_budget_store.py::test_a_dead_process_hold_never_refuses_a_live_request[memory]
FAILED tests/test_budget_store.py::test_a_dead_process_hold_never_refuses_a_live_request[dynamodb]
```

*Sabotage H — the gate logs the refusal and continues*, which is what "fail open" looks
like when somebody is nervous about false 402s:

```
9 failed, 567 passed, 2 deselected
FAILED tests/test_budget_gate.py::test_an_exhausted_budget_is_402_in_the_callers_own_dialect[anthropic]
FAILED tests/test_budget_gate.py::test_an_exhausted_budget_is_402_in_the_callers_own_dialect[openai]
FAILED tests/test_budget_gate.py::test_a_refusal_never_reaches_a_provider
FAILED tests/test_budget_gate.py::test_a_refusal_writes_a_ledger_row
FAILED tests/test_budget_gate.py::test_a_budget_refusal_does_not_consume_the_budget
FAILED tests/test_budget_stampede.py::test_the_stampede - assert 64 == 5
FAILED tests/test_budget_stampede.py::test_every_refusal_is_a_ledger_row_and_no_provider_call
…
```

*Sabotage I — the estimate drops its prompt half*, i.e. the brief's literal wording. The
third failure is the interesting one: at `max_tokens: 8` an output-only estimate is
**$0.00001** against an actual cost of **$0.0000115**, so the reservation stops being an
upper bound at all — and with it, the stampede's headline claim stops being a property of
the design.

```
3 failed, 573 passed, 2 deselected
FAILED tests/test_budget_estimate.py::test_the_estimate_is_the_hand_computed_figure
FAILED tests/test_budget_estimate.py::test_the_prompt_estimate_is_capped_at_the_models_context_window
FAILED tests/test_budget_estimate.py::test_the_estimate_bounds_what_the_canonical_fixture_actually_costs[8]
```

### End to end through the real container, on a wiped volume

```
$ docker compose down -v && make up
 Container headroom-db-1 Healthy
 Container headroom-dynamodb-1 Healthy
 Container headroom-gateway-1 Healthy
docker compose exec -T gateway uv run --no-sync python -m headroom.db.migrate
applied 3 migration(s): 0001_tenants_and_virtual_keys, 0002_usage_ledger, 0003_ledger_budget_columns

### 3. no budget yet -> admitted, nothing held
status 200
{"error":{"type":"budget_not_found","message":"no budget for tenant 'bb069bc2-…';
 this tenant is uncapped"}}  <- 404

### 4. set a monthly cap of $0.0001
{"scope":"tenant#bb069bc2-…","window":"monthly","window_id":"2026-08",
 "usd":"0.000100000000","spent":"0.000000000000","reserved":"0.000000000000",
 "remaining":"0.000100000000","committed":"0.000000000000","reservations":0}

### 5. spend against it
  request 1 -> 200
  request 2 -> 200
{"usd":"0.000100000000","spent":"0.000023000000","reserved":"0.000000000000",
 "remaining":"0.000077000000","committed":"0.000023000000","reservations":0}

### 6. exhaust it
200 200 200 402 402 402 402 402 402 402 402 402 402 402 402 402 402 402 402 402

### 7. the 402, in the Anthropic dialect
HTTP/1.1 402 Payment Required
x-headroom-error-source: gateway
{
    "type": "error",
    "error": {
        "type": "billing_error",
        "message": "this request would exceed the tenant's monthly (2026-08) budget of
                    $0.000100000000: $0.000057500000 settled plus $0.000000000000
                    reserved leaves $0.000042500000, and this request reserves
                    $0.000047000000 (28 prompt + 32 generated tokens at the current rate)"
    },
    "headroom": {"reason": "budget_exceeded", "request_id": "hr_1bf7c5609392482085fd…"}
}

### 8. and in the OpenAI dialect
{
    "error": {
        "message": "this request would exceed the tenant's monthly (2026-08) budget …",
        "type": "insufficient_quota", "param": null, "code": "budget_exceeded"
    },
    "headroom": {"reason": "budget_exceeded", "request_id": "hr_4bea0c4a348c400da5c4…"}
}

### 10. what the ledger says (three refused rows)
{"model": "mock-model-1", "outcome": "budget_exceeded", "status_code": 402,
 "usd_cost": "0.000000000000", "cost_status": "not_billable",
 "budget_status": "exceeded", "budget_reserved_usd": "0.000047000000",
 "budget_settled_usd": null}

### 11. the raw DynamoDB item
{
 "budget_picos":     {"N": "100000000"},
 "budget_window":    {"S": "monthly"},
 "expired_released_picos": {"N": "0"},
 "expired_releases": {"N": "0"},
 "remaining_picos":  {"N": "42500000"},
 "reservations":     {"M": {}},
 "reserved_picos":   {"N": "0"},
 "scope_id":         {"S": "tenant#bb069bc2-f260-4ea4-bac3-835fa78ee19d"},
 "scope_kind":       {"S": "tenant"},
 "scope_ref":        {"S": "bb069bc2-f260-4ea4-bac3-835fa78ee19d"},
 "spent_picos":      {"N": "57500000"},
 "updated_at":       {"S": "2026-08-09T09:15:24.729004+00:00"},
 "window_expires_at":{"N": "1788220800"},
 "window_id":        {"S": "2026-08"}
}

### 12. request log lines (one served, one refused)
{"request_id":"hr_7eeecd8a0ad746c88a2f8354b2e7f537","model":"mock-model-1","outcome":"ok",
 "status":200,"usd_cost":"0.000011500000","cost_status":"priced",
 "budget_status":"reserved","budget_reserved_usd":"0.000047000000",
 "budget_settled_usd":"0.000011500000","ttft_ms":5.404,"total_ms":5.404}
{"request_id":"hr_4bea0c4a348c400da5c4939d29983f14","model":"mock-model-1",
 "outcome":"budget_exceeded","status":402,"upstream_status":null,
 "usd_cost":"0.000000000000","cost_status":"not_billable","budget_status":"exceeded",
 "budget_reserved_usd":"0.000047000000","budget_settled_usd":null,
 "upstream_latency_ms":null,"ttft_ms":4.671,"total_ms":4.673}
```

Four things in that output are the phase in miniature.

`remaining_picos: 42500000` is **$0.0000425**, and the gate refuses a request whose
*actual* cost would have been $0.0000115 — because its *estimate* is $0.000047. That is
the conservative bound doing exactly what it is for, and it is the honest cost of the
design: the gate stops slightly early rather than slightly late. Sabotage I above shows
what the other choice buys.

The refused row has `upstream_status: null` and `cost_status: not_billable` with a
`usd_cost` of `0.000000000000` — a zero that is a *measurement*, because no model ran —
while `budget_reserved_usd` records what the request wanted. Without that row a refused
request would leave no trace anywhere, since nothing upstream ever saw it.

`reservations: {}` and `reserved_picos: 0` after twenty-three requests: every hold that
was taken was settled. And `expired_releases: 0` — nothing leaked, which is the number an
operator should be alarming on in Phase 9.

**Assumed-facts register (§0.4)**

- **A1 — VERIFIED.** *`amazon/dynamodb-local` in compose behaves like DynamoDB for
  `ConditionExpression` conditional writes via boto3 `endpoint_url`.* Every primitive
  this phase depends on was probed against the pinned `3.1.0` image before a line of the
  store was written, and each is now pinned by a test: conditional `UpdateItem` including
  **nested document paths** (`reservations.#rid`) in both the condition and the update;
  `ReturnValuesOnConditionCheckFailure=ALL_OLD`, which returns the full old item on a
  refusal and returns *nothing* when the item does not exist — the two cases the gate
  must tell apart, in one call; `create_table` with `PAY_PER_REQUEST` and
  `ResourceInUseException` on a second create; 38-significant-digit numbers accepted and
  39 rejected; negative numbers; compare-and-set on a string stamp; `Scan`. And the
  property itself: **60 concurrent conditional writes against one item granted exactly
  the 10 the budget afforded**, with `remaining` landing on zero.

  Two caveats the assumption did not anticipate, both now documented (H-030) and pinned:
  `scope` is a **reserved word** in expressions, and DynamoDB Local **rejects an access
  key id containing a hyphen**. Neither is a difference from real DynamoDB in behaviour
  that matters — the first is true of DynamoDB proper, the second is an emulator quirk in
  a credential real AWS would reject for different reasons — but both cost time.

  The second half of A1 (*"then identically against real DynamoDB in P9"*) is Phase 9's,
  and the code is arranged so it is the same code: no branch anywhere asks whether it is
  talking to a container or to AWS, the endpoint override is the only difference, and the
  table definition is one function.
- **A2, A3, A4, A5, A6, A7** — not due at this gate, none touched. A4 and A5 stay
  VERIFIED: the budget gate reads the request body but never rebuilds it, and the 576
  tests that prove passthrough fidelity all still run through the gate.

### CI

CI on PR-4 ([run 31305643286](https://github.com/sergioavilax/headroom/actions/runs/31305643286)),
all three jobs green on the first run, no annotations:

```
$ gh run view 31305643286 --json conclusion,jobs
success
lint + typecheck: success
pytest (postgres + dynamodb-local service containers): success
gateway image builds and serves: success

lint + typecheck | All checks passed!
lint + typecheck | Success: no issues found in 100 source files
pytest (…service containers) | ====== 576 passed, 2 deselected, 1 warning in 15.23s ======
pytest (…service containers) | tests/test_budget_stampede.py::test_the_stampede PASSED
pytest (…service containers) | tests/test_budget_stampede.py::test_the_sabotage_blows_the_budget PASSED
pytest (…service containers) | tests/test_budget_stampede.py::test_a_landed_only_gate_blows_the_budget_even_though_it_is_atomic PASSED
Migrations apply, twice is a no-op | migrations: up to date, nothing to apply
gateway image builds and serves | gateway healthy
```

**576 passed, 0 skipped in CI** — `grep -c SKIPPED` over the whole log returns `0`, so the
DynamoDB half of the budget-store contract suite **and the stampede** executed against the
service container rather than skipping. That is the half of this gate a green pytest alone
would not prove: under H-012 an explicitly-configured but unreachable endpoint fails
rather than skipping, and CI sets `DYNAMODB_ENDPOINT_URL` explicitly. **The D-019 stampede
and both of its sabotages now run on every pull request.**

No AWS credential is set anywhere in the workflow. The gateway supplies its own dummy
when — and only when — an emulator endpoint is configured, which is why the job is still
fully keyless (invariant 4).

The `image` job continues to prove the lazy clients: it builds the container and smokes
`/healthz` with **no Postgres and no DynamoDB anywhere in the job**, which a gateway that
connected to either at startup could not do.

**Spend** — $0.00. No provider API was called in this phase; every test ran on the
MockProvider, and the container demo talked to nothing outside the compose network.

---

## Phase 4b — Token-bucket rate limits: the other half of Phase 4 (2026-08-09)

The remainder BUILD_PLAN's Phase 4 names and PR-4 deferred, in its own PR
(`claude/p4b-rate-limits`, GitHub #6). The plan's words are the spec: *"Token-bucket rate
limits (**requests/min and tokens/min per key and per tenant**) … enforced on DynamoDB
conditional writes (A1), against dynamodb-local in compose"*, with *"clean 429 responses
with retry-after"* in the gate.

**Shipped**

- **Token buckets on the same conditional-write discipline as the budget gate**, and the
  sentence this half rests on is the budget half's, one noun over: **every consumption
  from a token bucket is a single conditional write to a single item.** No read to be
  stale, no refill computed in application code, no window in which two racers can both
  decide they fit.
- **The bucket is stored as a *time*, not as a count of tokens** — the decision the whole
  phase turns on (**H-035**). The obvious item, `tokens` plus `refilled_at`, needs
  `min(capacity, tokens + elapsed*rate) - cost >= 0` evaluated at write time, and a
  DynamoDB `ConditionExpression` compares an attribute to a value and does **no
  arithmetic**. So a stored count can only be checked by reading it first, which is D-019
  in a different noun. The GCRA formulation makes admission a bare comparison:

  ```
  T          emission interval = ceil(60s / limit)      nanoseconds per unit
  D          capacity          = T * limit              nanoseconds
  available  at now            = clamp((now + D - tat) / T, 0, limit)   <- derived
  admit c    iff  tat <= now + D - c*T
  on admit   tat := max(tat, now) + c*T
  ```

  `tat` — the moment the bucket will next be full — **is** the bucket. Everything else on
  the item is for a human or for the garbage collector.
- **`max(tat, now)` is recovered with a second conditional write, never a read**
  (`headroom/db/buckets.py`). It is the one term the expression language cannot express,
  and dropping it is not cosmetic: an idle bucket would accumulate unbounded credit.
  Two mutually exclusive branches, each atomic on its own:

  ```
  hot    cond: tat > :now AND tat <= :ceiling      upd: SET tat = tat + :charge
  cold   cond: attribute_not_exists(tat) OR tat <= :now
                                                  upd: SET tat = :now_plus_charge
  ```

  Hot is attempted first, so under load — the only time a limiter's cost matters — **one
  write is all that happens**. When it fails, `ReturnValuesOnConditionCheckFailure=ALL_OLD`
  hands back the item the condition was evaluated against, so "cold" and "genuinely
  refused" are told apart with **no second read**, and the refusal's `retry-after` is
  computed from the very `tat` the condition saw. That is H-032's refusal-path trick
  applied to a different question.
- **Both scopes and both dimensions, from the first line** — BUILD_PLAN's *"per key and
  per tenant"*, *"requests/min and tokens/min"* — as four independent items,
  `key#…#requests`, `key#…#tokens`, `tenant#…#requests`, `tenant#…#tokens`, consumed
  key-first and requests-first. **The asymmetry with H-033 (which deferred per-key
  *budgets*) is real rather than inconsistent**: a budget admission takes a *hold* that
  must be settled, so two of them need a compensating release; a bucket consumption holds
  nothing and settles never. **H-036.**
- **Nothing is refunded when a later bucket refuses**, and the argument is the phase's
  best sentence: **a budget is a stock and a rate limit is a flow.** An over-charge
  against a budget persists for a month and must be corrected; an over-charge against a
  bucket is erased by the bucket's own refill within one emission interval — the refill
  *is* the compensating transaction, it runs continuously, and it costs nothing. Writing a
  refund would add the one thing this design does not have: an operation whose absence
  breaks an invariant. Cost, stated and tested: the limiter is fractionally **stricter**
  than configured, by at most one unit per bucket, for at most one interval. **H-036.**
- **The limits live in Postgres, on the rows authentication already reads**
  (`migrations/0004_rate_limits.sql`: `requests_per_min`, `tokens_per_min` on `tenants`
  and `virtual_keys`, NULL meaning unlimited). BUILD_PLAN L2 settles it — *Postgres for
  config, DynamoDB for token buckets and budget reservations only* — and the placement
  pays for itself: `find_by_hash` already joins both rows, so both scopes' limits arrive
  on the `Principal` with **no second query and no second cache**, inheriting H-018's
  documented 5-second bound rather than inventing another. A limit change bites on the
  next request in the process that made it, and within 5 s everywhere else. **H-037.**
- **A refusal is 429 with `retry-after`** — the mirror image of H-032's 402, and for the
  mirror reason: this one *does* heal with time and the amount of time is known exactly.
  Anthropic and OpenAI both spell it `rate_limit_error`; the precision travels in
  `headroom.reason`. One refusal deliberately carries **no** `retry-after`: a request
  larger than the bucket's whole capacity, where every value would be a lie
  (`rate_limit_exceeds_capacity`, reachable only on the tokens dimension). **H-038.**
- **Headroom's 429 is distinguishable from a provider's, three independent ways**, which
  is what P6's failover logic will read: `x-headroom-error-source: gateway`,
  `x-headroom-ratelimit-scope: tenant:requests` (beside `-limit`, `-remaining`, `-reset`),
  and `headroom.reason: rate_limited`. The headers are load-bearing and are only
  trustworthy because of the one change that makes this a rule rather than a convention:
  **`forward_response_headers` now strips the whole `x-headroom-*` namespace from every
  upstream response**, success path included. It was already stripped from requests
  (H-010); without the response half, "no provider sends such a header" would be a
  property of today's providers rather than of this proxy. **H-038.**
- **Order of gates, decided and pinned by tests** (**H-039**): authenticate (401) → read
  the body → model scope (403) → route → provider scope (403) → **rate limit (429)** →
  budget reservation (402) → open the upstream. Rate limit *before* budget for three
  reasons: a rate-refused request must not take a hold it hands straight back (a
  compensating action on the hot path is the shape this phase refuses to add); a burst is
  exactly the traffic the budget gate is worst at, since every request in it serialises on
  one item, so shedding it earlier is what keeps the cap's latency bounded; and a bucket
  write contends on a per-scope item where a budget write contends on the tenant's single
  one. Consequence, stated so it cannot look accidental: a request over *both* answers 429
  and answers 402 on the retry.
- **One estimate, two gates.** The limiter needs the request's size in tokens and the
  budget needs it in dollars; they are the same bound from the same formula (H-034), so
  `headroom/api/proxy.py` computes it once and hands it to both. `BudgetGate.admit` gained
  an optional `estimate` parameter and recomputes when it is absent, so every existing
  caller is unchanged. A pleasant side effect: the tokens limit **works on models the
  budget cannot see** — an unpriced model estimates $0 and is invisible to the cap
  (H-034's named trap), but its token counts are real, and the limiter needs no price book
  at all.
- **A second DynamoDB table, `headroom_buckets`, with its own TTL attribute.** Not more
  items in the budgets table: the two have opposite retention rules. A budget item must
  never be reaped; a bucket item is **safe** to reap, because an absent bucket and a full
  bucket are the same state to the cold branch — which is also why DynamoDB TTL is right
  here and was wrong for reservations (H-032 rejects it there). `expires_at` is written on
  every consumption; *enabling* TTL is Terraform's job in Phase 9 and nothing depends on
  the reaper running. `DynamoClient.ensure_table` now takes the partition key by name
  rather than assuming the budget table's.
- **`/admin/limits`**: `GET`/`PUT`/`DELETE /admin/limits/{scope_kind}/{scope_id}` and
  `GET /admin/limits`, behind the same root token. The GET **joins two datastores** — the
  Postgres row that says what the limit is, the DynamoDB item that says what is left of it
  — because "why is this tenant getting 429s" is answered by neither alone. `PUT`
  *replaces*: an omitted dimension is unlimited, not unchanged, which is the only reading
  under which a limit can be removed at all. `DELETE` clears the limits **and** empties the
  buckets: the incident-response route.
- **A refusal is a ledger row**, exactly as a budget refusal is, and with **no new
  migration**: `outcome`, `status_code`, and `error_reason` already carry it
  (`rate_limited` / `rate_limit_exceeds_capacity`, 429, no upstream status, `not_billable`
  with a `usd_cost` of zero that is a *measurement*). The log line grew two fields —
  `rate_limit_status` and `rate_limit_scope` — because four buckets can refuse one request
  and an HTTP status names none of them. Two fields and not five: a bucket never settles,
  so there is no "what it ended up costing" to record.
- **`RateLimitStore`: one interface, two implementations, one contract suite** — the H-021
  shape, with the same honest caveat the budget store carries and for the same reason:
  `InMemoryRateLimitStore` reproduces the *semantics*, not the *concurrency*.
- **Tests: 670 keyless** (576 → 670), 2 live-marked and deselected by default. New files —
  `test_rate_limit_store` (30, the contract suite over both implementations),
  `test_rate_limit_gate` (18), `test_admin_limits` (18), `test_rate_limit_hammer` (9),
  `test_429_distinguishability` (6); `test_tenant_store` grew 12 (44 → 56) for the
  configuration half, and `test_live_smoke_wiring` one. Every Phase 0/1/2/3/4 test still
  green, and **one** of them changed (deviation 2).
- **Docs**: H-035 … H-039 in `docs/DECISIONS.md`; README gains a *Rate limits that cannot
  be raced either* section; `.env.example` documents `HEADROOM_BUCKETS_TABLE` and the
  admin call; `migrations/README.md` status table; the CI comment names the new
  service-backed tests.

**Deferred**

- **A `daily` or per-route dimension.** BUILD_PLAN names requests/min and tokens/min and
  nothing else. A third dimension is a third bucket per scope on every request.
- **Concurrency limits** (max in-flight requests per tenant) — a genuinely different
  primitive: it needs a *decrement on completion*, which is a settlement, which is the
  budget's shape rather than this one's. Not named by the plan; not built.
- **A separate `burst` knob.** Capacity is one window's worth of units, deliberately: a
  second number to explain, a second column to migrate, and a second thing to get wrong,
  for a case nobody has asked for. A tenant who wants a smaller burst wants a smaller
  limit.
- **Enabling DynamoDB TTL on `headroom_buckets`.** The attribute is written; turning the
  feature on belongs to Phase 9's Terraform, where the table is really created.

**Deviations**

1. **`forward_response_headers` now strips `x-headroom-*` from upstream responses.** The
   only behaviour change to existing code in this PR, and it is what makes the P6
   distinction a property of the proxy rather than of today's providers (**H-038**).
   Additive in effect — no provider sends such a header — and asserted from the hostile
   direction by `test_429_distinguishability.py`, which has an upstream forge both markers
   on the success path and the error path.
2. **One Phase 1 test changed**, because the log-field set it pins grew:
   `test_request_context.py::test_the_log_shape_is_complete`. The same class of change as
   Phase 3's deviation 7 and Phase 4's deviation 4. No Phase 1 *behaviour* changed.
3. **`DynamoClient.ensure_table` gained a required `partition_key` keyword**, because
   there are now two tables with two different keys and a default would silently create
   the wrong schema for whichever store forgot to pass it. Two call sites in
   `tests/test_dynamo_client.py` updated; no behaviour changed.
4. **`BudgetGate.admit` gained an optional `estimate` parameter.** Additive: passing
   nothing recomputes exactly as before, which is what every existing caller and every
   Phase 4 test does. It exists so one request is not measured twice, not so two callers
   can measure it differently.
5. **`Tenant` and `VirtualKey` gained a `limits` field**, and `TenantStore` two methods
   (`set_tenant_limits`, `set_key_limits`). Assignment semantics, not `COALESCE`: on this
   surface `None` means *clear*, and mixing that into the existing patchers would give one
   spelling two meanings. `tests/test_tenant_store.py` asserts that renaming a key,
   revoking it, and deactivating a tenant all preserve limits — uncapping something by
   accident is invisible until the traffic arrives.
6. **No new ledger columns**, unlike the budget half's `0003`. A refusal's outcome, status,
   and reason already say everything the row needs; *which bucket* refused goes to the log
   line and the response headers. `migrations/0004` touches only `tenants` and
   `virtual_keys`, and is additive and nullable.
7. **The plan's Phase 4 gate text is now met in full across the two PRs.** PR-4 met the
   concurrency hammer and the sabotage for budgets and explicitly did not claim *"clean
   429 responses with retry-after"*; this PR claims it, with the rate-limit analogue of
   the stampede and three permanent sabotages.

---

**Gate** — *the hammer: a concurrent burst against a small bucket on dynamodb-local,
asserting exact admit/refuse counts and bucket arithmetic, plus a sabotaged non-atomic
variant that over-admits, permanent.*

### THE HAMMER — the headline artifact

64 concurrent requests through the whole gateway, against a bucket holding five, on
DynamoDB Local. A limit of five per minute is an emission interval of exactly **twelve
seconds**, and the burst completes in milliseconds — so no unit can refill during it and
the count is exact rather than approximate. Twelve seconds of margin against a burst
measured in hundredths is why this test does not flake.

```
$ uv run pytest tests/test_rate_limit_hammer.py -q -s

ATOMIC BUCKET (shipped)
  requests fired         64
  served (200)           5
  refused (429)          59
  bucket capacity        5 requests / 60s
  emission interval      12s per request
  available after        0
  full again in          60s
  served / capacity      1.00x
```

Five served, fifty-nine refused, the bucket empty and exactly one window from full — five
admissions at one instant is precisely one window of credit at twelve seconds each. Every
one of the 64 got a real HTTP answer, every refusal carried a `retry-after` between 1 and
60, no refused request reached the provider, and all 64 have a ledger row.

### SABOTAGE A — D-019, in the noun every rate limiter uses

The same gateway, the same limit, the same 64 requests. The only difference is a bucket
that stores a **count**: read the item, add the tokens accrued since `refilled_at`, check,
write it back. It is what a token bucket looks like in every tutorial, and it passes every
single-threaded test in this repo.

```
SABOTAGED (read, refill, decide, write)
  requests fired         64
  served (200)           31
  refused (429)          33
  bucket capacity        5 requests / 60s
  emission interval      12s per request
  served / capacity      6.20x
```

Six times the bucket's capacity, and every one of those extra admissions really did reach
a provider — the test asserts that too. (The exact figure varies run to run, which is what
a race looks like; an earlier run of the identical code served 41. The assertion is
`served > capacity`, not a number.)

### SABOTAGE B — atomic, and still wrong: the fixed window

The more instructive one, and the reason the hammer alone is not the whole gate. A counter
per clock-minute, reset when the minute changes: the check and the increment are a
**single conditional write**, evaluated against committed state, with nothing read.
Everything sabotage A got wrong is right here. It is the most common "rate limiter" in
production anywhere. **It passes the hammer:**

```
SABOTAGED (atomic, but a fixed window)
  requests fired         64
  served (200)           5
  refused (429)          59
  served / capacity      1.00x
```

And then, on a controlled clock, two seconds straddling a minute boundary:

```
BOUNDARY BURST (limit 5/min, two seconds spanning a minute)
  shipped token bucket   5
  fixed-window counter   10
```

**Twice the configured rate, in two seconds, at every minute boundary, forever.** Nothing
about the implementation is un-atomic — the counter is perfect. It is counting the wrong
thing. A token bucket has no boundary to straddle because it has no window, only a rate
and a capacity.

### SABOTAGE C — atomic, one write, and still wrong: no clamp

The tempting simplification of the shipped store, and the reason it has two branches
instead of one. Drop `max(tat, now)`: the condition and the update become a *single*
atomic operation, strictly simpler than what ships and one round trip cheaper on an idle
bucket. **It also passes the hammer** — a fresh bucket has no idle credit to accumulate,
so on the hammer's own terms it is indistinguishable:

```
SABOTAGED (atomic, one write, no clamp)
  requests fired         64
  served (200)           5
  refused (429)          59
  served / capacity      1.00x
```

Then an hour of quiet, on a controlled clock:

```
BURST AFTER AN HOUR IDLE (limit 5/min)
  shipped token bucket   5
  unclamped GCRA         300
```

**Sixty times the configured rate**, because `tat` spent that hour falling further behind
the clock with nothing to stop it. The burst an unclamped bucket admits is proportional to
how long it was *idle*, not to its capacity. One missing `max()` is what the shipped
store's second conditional write buys.

All four runs are in one test file and all four are asserted, so the three sabotages are
permanent: a future change that makes the hammer pass for the wrong reason still fails the
tests whose job is to fail. **Atomicity is necessary and it is not sufficient** — the
lesson the budget half's landed-only gate taught, twice over.

### The keyless gate

```
$ make lint
uv run ruff check .
All checks passed!
uv run ruff format --check .
110 files already formatted

$ make typecheck
uv run mypy
Success: no issues found in 110 source files

$ make test
================ 670 passed, 2 deselected, 1 warning in 12.15s =================

$ uv run pytest -m live -q --collect-only
2/672 tests collected (670 deselected) in 0.12s
```

**670 passed, 0 skipped** — `grep -c SKIPPED` over the run returns `0`, so the DynamoDB
half of the rate-limit contract suite and the hammer executed against the container rather
than skipping.

Per-file counts for the new and changed files, from the same run:

```
30 tests/test_rate_limit_store.py       9 tests/test_rate_limit_hammer.py
18 tests/test_rate_limit_gate.py        6 tests/test_429_distinguishability.py
18 tests/test_admin_limits.py           6 tests/test_live_smoke_wiring.py  (was 5)
56 tests/test_tenant_store.py (was 44) 11 tests/test_request_context.py
```

**H-012 still holds with the new DynamoDB tests.** A fresh clone with no stack up runs a
smaller suite *loudly*, and a stated-but-unreachable endpoint fails rather than skips:

```
$ docker compose stop dynamodb
$ uv run pytest -q                          # DYNAMODB_ENDPOINT_URL unset — inferred
614 passed, 56 skipped, 2 deselected, 1 warning in 8.75s

$ DYNAMODB_ENDPOINT_URL=http://localhost:8001 uv run pytest -q \
      tests/test_rate_limit_store.py tests/test_rate_limit_hammer.py
4 failed, 18 passed, 17 errors in 0.21s
```

### The other sabotage runs

Two more of this PR's claims were tested by breaking the thing they protect. Both were
applied with the Edit tool and reverted immediately; `diff` against pre-sabotage copies of
both files reports them identical, and the suite is 670 green above.

*Sabotage D — the gate order is swapped*, so the budget reservation runs before the rate
limiter. This is the tidy-looking alternative, and it puts a compensating release on the
hot path:

```
2 failed, 668 passed, 2 deselected
FAILED tests/test_rate_limit_gate.py::test_a_rate_limited_request_never_reserves_budget
FAILED tests/test_rate_limit_gate.py::test_a_request_over_both_limits_answers_429_and_402_on_the_retry
```

*Sabotage E — the response deny-list keeps its Phase 1 shape*, i.e. `x-headroom-*` is no
longer stripped from upstream responses. Everything still works, and an upstream can now
claim to be the gateway — which is exactly the fact Phase 6 would be about to trust:

```
2 failed, 668 passed, 2 deselected
FAILED tests/test_429_distinguishability.py::test_an_upstream_cannot_write_in_headrooms_header_namespace[429]
FAILED tests/test_429_distinguishability.py::test_an_upstream_cannot_write_in_headrooms_header_namespace[200]
```

### End to end through the real container, on a wiped volume

```
$ docker compose down -v && make up
 Container headroom-db-1 Healthy
 Container headroom-dynamodb-1 Healthy
 Container headroom-gateway-1 Healthy
applied 4 migration(s): 0001_tenants_and_virtual_keys, 0002_usage_ledger,
                        0003_ledger_budget_columns, 0004_rate_limits

### 3. no limit yet -> unlimited, and no bucket exists
{"scope":"tenant#be95eb6a-…","name":"limits-demo","requests_per_min":null,
 "tokens_per_min":null,"buckets":[]}

### 4. three requests a minute
{"requests_per_min":3,"tokens_per_min":null,
 "buckets":[{"dimension":"requests","limit_per_min":3,"available":3,"reset_after_s":0}]}

### 5. six requests
200 200 200 429 429 429

### 6. the 429, in the Anthropic dialect
HTTP/1.1 429 Too Many Requests
x-headroom-ratelimit-scope: tenant:requests
x-headroom-ratelimit-limit: 3
x-headroom-ratelimit-remaining: 0
retry-after: 20
x-headroom-ratelimit-reset: 20
x-headroom-error-source: gateway
{
    "type": "error",
    "error": {
        "type": "rate_limit_error",
        "message": "this request would exceed the tenant's rate limit of 3 requests per
                    minute: it needs 1 and 0 are available; retry in 20s"
    },
    "headroom": {"reason": "rate_limited", "request_id": "hr_01e9438c438346529473700…"}
}

### 7. and in the OpenAI dialect
{
    "error": {
        "message": "this request would exceed the tenant's rate limit of 3 requests …",
        "type": "rate_limit_error", "param": null, "code": "rate_limited"
    },
    "headroom": {"reason": "rate_limited", "request_id": "hr_9f82b5ad21314388b8b5008…"}
}

### 8. the live bucket, beside the limit
{"requests_per_min":3,"tokens_per_min":null,
 "buckets":[{"dimension":"requests","limit_per_min":3,"available":0,
             "reset_after_s":60,"reset_at":"2026-08-09T19:49:15.796967Z"}]}

### 9. a tokens/min limit smaller than one request
HTTP/1.1 429 Too Many Requests
x-headroom-ratelimit-scope: tenant:tokens
x-headroom-ratelimit-limit: 10
x-headroom-ratelimit-remaining: 10
x-headroom-error-source: gateway            <- and NO retry-after
{
    "type": "error",
    "error": {
        "type": "rate_limit_error",
        "message": "this request needs 60 tokens, which is more than the whole tenant
                    tokens-per-minute allowance of 10; waiting will not help — raise the
                    limit or lower max_tokens"
    },
    "headroom": {"reason": "rate_limit_exceeds_capacity", "request_id": "hr_ebfd8839…"}
}

### 10. a per-key limit (1/min), alongside a roomy tenant one (100/min)
200 429
[{"scope":"tenant#be95eb6a-…","requests_per_min":100,
  "buckets":[{"dimension":"requests","limit_per_min":100,"available":99,"reset_after_s":1}]},
 {"scope":"key#71a68701-…","requests_per_min":1,
  "buckets":[{"dimension":"requests","limit_per_min":1,"available":0,"reset_after_s":60}]}]

### 11. the raw DynamoDB items
{"bucket_id": {"S": "key#71a68701-…#requests"}, "dimension": {"S": "requests"},
 "expires_at": {"N": "1786305016"}, "scope_kind": {"S": "key"},
 "scope_ref": {"S": "71a68701-…"}, "tat": {"N": "1786304956151432000"},
 "updated_at": {"S": "2026-08-09T19:48:16.151432+00:00"}}
{"bucket_id": {"S": "tenant#be95eb6a-…#requests"}, "dimension": {"S": "requests"},
 "expires_at": {"N": "1786305016"}, "scope_kind": {"S": "tenant"},
 "scope_ref": {"S": "be95eb6a-…"}, "tat": {"N": "1786304896751432000"},
 "updated_at": {"S": "2026-08-09T19:48:16.151432+00:00"}}

### 12. what the ledger says
ok|200|200|priced|4
rate_limited|429||not_billable|7
rate_limit_exceeds_capacity|429||not_billable|2

### 13. request log lines (one refused, one served under a limit)
{"request_id":"hr_96ed4090de2d4d1d8dbd5fa13256786d","model":"mock-model-1",
 "outcome":"rate_limited","status":429,"upstream_status":null,"error_source":"gateway",
 "error_reason":"rate_limited","usd_cost":"0.000000000000","cost_status":"not_billable",
 "budget_status":null,"rate_limit_status":"limited","rate_limit_scope":"key:requests",
 "upstream_latency_ms":null,"ttft_ms":4.113,"total_ms":4.114}
{"request_id":"hr_c0c1476bcada4b5d9abc11f0092dfa5f","model":"mock-model-1","outcome":"ok",
 "status":200,"upstream_status":200,"usd_cost":"0.000011500000","cost_status":"priced",
 "budget_status":"no_budget","rate_limit_status":"ok","rate_limit_scope":null,
 "ttft_ms":19.245,"total_ms":19.246}

### 14. DELETE clears the limits and empties the buckets
{"scope":"key#71a68701-…","requests_per_min":null,"tokens_per_min":null,"buckets":[]}
next request -> 200
```

Five things in that output are the phase in miniature.

`"buckets":[]` on an unlimited scope, and `clear` returning nothing afterwards: an
unconfigured scope is not "a limit of infinity" that gets consumed and always passes — it
is skipped entirely, so a deployment that caps nobody does no DynamoDB work at all on this
path. That is what keeps this phase additive for every tenant nobody has limited.

The two `tat` values in step 11 are the whole design visible on the wire:
`1786304956151432000` and `1786304896751432000` are nanosecond timestamps sixty seconds
apart, one per bucket, and there is **no token count anywhere in the item**. Available
capacity is derived from them, never stored, which is precisely what lets admission be a
single comparison.

Step 9's refusal has `x-headroom-ratelimit-remaining: 10` against a limit of 10 and **no
`retry-after`**: the bucket is completely full and the request still cannot fit, so waiting
is not the answer and the honest header is the absent one.

Step 10 is the two scopes doing their separate jobs: the tenant's roomy bucket admits and
drops to 99, the key's bucket admits once and refuses the second — and `rate_limit_scope`
on the refused log line says `key:requests`, which is the field an operator needs when four
buckets could each have been the one.

And the ledger has a row for every refusal, with `upstream_status` NULL and a `usd_cost` of
zero that is a *measurement* rather than a default — because no model ran. Without those
rows a rate-limited request would leave no trace anywhere, since nothing upstream ever saw
it.

**Assumed-facts register (§0.4)**

- **A1 — VERIFIED again, on a second access pattern.** *`amazon/dynamodb-local` in compose
  behaves like DynamoDB for `ConditionExpression` conditional writes via boto3
  `endpoint_url`.* Phase 4 verified it for the budget item; this phase exercised primitives
  it did not: 19-digit nanosecond integers in both a condition and an arithmetic update
  (`SET tat = tat + :charge`); `attribute_not_exists(x) OR x <= :v` as a disjunctive
  condition; `if_not_exists` in an arithmetic update (in sabotage C); and
  `ReturnValuesOnConditionCheckFailure=ALL_OLD` on a *second* table with a different
  partition key. Each was probed against the pinned `3.1.0` image before the store was
  written and each is now pinned by a test. No new emulator quirk surfaced — in
  particular, `dimension`, `tat`, and `expires_at` are **not** reserved words, which was
  checked rather than assumed after Phase 4 lost time to `scope`.
- **A2, A3, A4, A5, A6, A7** — not due at this gate, none touched. A4 and A5 stay VERIFIED:
  the rate limiter reads the estimate the budget gate already computed and never touches
  the request body, and the 670 tests that prove passthrough fidelity all still run through
  the new gate.

**Spend** — $0.00. No provider API was called in this phase; every test ran on the
MockProvider, and the container demo talked to nothing outside the compose network.

### CI

CI on PR-4b ([run 31332854738](https://github.com/sergioavilax/headroom/actions/runs/31332854738)),
all three jobs green on the first run, no annotations:

```
$ gh run view 31332854738 --json conclusion,jobs
success
lint + typecheck: success
pytest (postgres + dynamodb-local service containers): success
gateway image builds and serves: success

lint + typecheck | All checks passed!
lint + typecheck | Success: no issues found in 110 source files
pytest (…service containers) | ====== 670 passed, 2 deselected, 1 warning in 20.82s ======
pytest (…service containers) | tests/test_rate_limit_hammer.py::test_the_hammer PASSED
pytest (…service containers) | tests/test_rate_limit_hammer.py::test_the_sabotage_over_admits PASSED
pytest (…service containers) | tests/test_rate_limit_hammer.py::test_a_fixed_window_passes_the_hammer PASSED
pytest (…service containers) | tests/test_rate_limit_hammer.py::test_the_fixed_window_admits_double_the_limit_across_the_boundary PASSED
pytest (…service containers) | tests/test_rate_limit_hammer.py::test_an_unclamped_bucket_passes_the_hammer PASSED
pytest (…service containers) | tests/test_rate_limit_hammer.py::test_an_unclamped_bucket_accumulates_an_hours_worth_of_credit PASSED
Migrations apply, twice is a no-op | migrations: up to date, nothing to apply
gateway image builds and serves | gateway healthy
```

**670 passed, 0 skipped in CI** — `grep -c SKIPPED` over the whole log returns `0`, so the
DynamoDB half of the rate-limit contract suite (12 parametrised tests) **and all nine
hammer tests** executed against the service container rather than skipping. Under H-012 an
explicitly-configured but unreachable endpoint fails rather than skips, and CI sets
`DYNAMODB_ENDPOINT_URL` explicitly — so **the hammer and its three sabotages now run on
every pull request**, beside the D-019 stampede.

The six `test_429_distinguishability.py` tests run there too, which matters more than their
number suggests: they are the contract Phase 6 will build its failover rule on, pinned
before the code that reads it exists.

No AWS credential is set anywhere in the workflow (`grep -c AWS_ACCESS` over the log
returns `0`). The gateway supplies its own dummy when — and only when — an emulator
endpoint is configured, which is why the job is still fully keyless (invariant 4).

---

## Phase 5 — The cache: exact, semantic, and never poisoned (2026-08-09)

**Shipped**

- **`migrations/0005_response_cache.sql`** — `CREATE EXTENSION vector` (the image has
  offered it since Phase 0 and nothing needed it until now, H-001), the `response_cache`
  table with a `vector(384)` column and an HNSW cosine index, the per-tenant cache policy
  as three columns on `tenants`, and three columns on `usage_ledger` for the *avoided*
  cost and the provenance of a hit. `cache_mode` is `NOT NULL DEFAULT 'disabled'`, which
  is the feature rather than a detail: no existing tenant and no future one caches
  anything until somebody says so.
- **Two layers behind one interface.** **Exact**: SHA-256 over the canonicalised request,
  salted with the namespace. **Semantic**: `bge-small-en-v1.5` on CPU (BUILD_PLAN L6),
  pgvector cosine over the tenant's own namespace, hit above a per-tenant threshold.
  Semantic is a strict superset — the exact hash is tried first, because it is cheaper
  *and* safer than the search that would otherwise follow it, and an exact hit never pays
  for an embedding.
- **Isolation is a value, not a habit** (**H-040**). `CacheNamespace` is the only address
  the cache has; it salts the exact key *and* leads every index and predicate, and both
  are downstream of one function called from one place. `tests/test_cache_isolation.py`
  removes it — one patch, both mechanisms — and asserts the leak really happens, so the
  tests protecting the property are known to be capable of failing. That sabotage lives
  **permanently in the suite** rather than as a one-off run recorded here.
- **Eligibility as the safety case** (**H-041**). Single-turn text only; `temperature ≤
  0.2`; one completion; and **no tools in any form** — a request that merely *declares*
  `tools` is refused even though nothing has been called, because the same words with
  tools available may legitimately produce a tool call. The tool scan is a structural walk
  of the whole body looking at keys and typed markers, never at free text, so it cannot be
  fooled by a block type nobody anticipated and cannot be tripped by a user asking a
  question *about* tool use.
- **Invariant 6, enforced at the one place a truncated answer could become permanent.**
  Only a response that ended `ok`, with an upstream status under 400 and a **complete**
  stop reason (`end_turn` / `stop_sequence` / `stop`), is ever written. `max_tokens` and
  `length` are explicitly not that — they are a complete *stream* of a truncated *answer*,
  the distinction H-008 drew, and the case that looks perfectly healthy from outside.
- **The transport is part of the key; an entry is replayed, never converted** (**H-043**).
  A streaming caller is served by an entry a streaming request populated, byte for byte —
  which is a *stricter* claim than A4's content equality, and it is available only because
  no code here assembles a message from frames or synthesises frames from a message. The
  demo moment survives: stream a question twice and the second one's first token is
  already there.
- **A reasoning response is cacheable exactly and never semantically** (**H-044**). Replay
  hands the caller the original chain of thought, which is right for an identical question
  and is a category of wrongness beyond "the answer is wrong" for a near-miss. Two
  booleans, `store` and `embed`, rather than one.
- **A hit is not an upstream call wearing a hat** (**H-045**). NULL `upstream_status`,
  NULL `provider`, NULL token counts, NULL `passthrough_overhead_ms` (there was no
  upstream byte to measure from), `usd_cost = 0` with `not_billable` beside it — H-025's
  existing rule for a request that never reached a provider, not a sixth status — and the
  saving in `cache_avoided_usd`, a column of its own, taken from the entry's own recorded
  cost so it is a fact rather than a re-priced hypothetical. `ttft_ms` and `total_ms` are
  real, because they happened.
- **Its position in the pipeline, decided** (**H-046**, the question H-039 left open):
  after the rate limiter — a hit costs this process a connection, a search, and on the
  semantic path a CPU embedding, so a tenant that could serve unlimited traffic by
  repeating itself would have a denial of service for the asking — and **before** the
  budget gate, taking no reservation at all. The consequence is stated rather than
  discovered: **a tenant over its cap still gets its cached answers**, because a budget
  bounds spend and a hit does not spend.
- **`/admin/cache`** (`headroom/api/cache.py`): `GET`/`PUT`/`DELETE /admin/cache/{tenant}`
  and a listing, behind the same root token. `PUT` replaces, and an absent field means
  *the documented default* rather than *unchanged*. Enabling `semantic` **loads the model
  in the request that asked for it**, so a missing extra is a 503 naming the fix rather
  than a silent bypass on somebody's traffic an hour later. `DELETE` disables **and**
  purges, in that order.
- **Two embedders, chosen by name, never by what happens to be importable**
  (`headroom/cache/embedding.py`). `BGEEmbedder` is the real one; `HashingEmbedder` is
  deterministic, dependency-free, and a genuine lexical similarity function rather than a
  stub. There is deliberately **no fallback**: a gateway that silently degraded would keep
  answering, keep hitting, and quietly stop being the system anybody measured. The model
  id is part of the semantic namespace, so a swap can never cross-serve.
- **A committed, content-hashed semantic corpus** (`tests/fixtures/semantic_corpus.json`,
  generated by `tests/support/build_semantic_corpus.py`): 12 canonical questions from 4
  templates crossed with 3 artists, 24 paraphrases, each probe naming its source question,
  every vector produced by the **real** `bge-small-en-v1.5`. §P8.H1's shape in miniature —
  the dangerous collision class ships with the corpus, so no "hard negative" had to be
  invented or tuned until a test passed. CI replays real similarity arithmetic with no
  torch, no model, and no network.
- **The default threshold is 0.90, measured rather than picked.** On that corpus a
  paraphrase scores at worst **0.9237** against its own question and at best **0.8511**
  against any other. 0.90 sits in the gap asymmetrically on purpose — 0.049 above the
  wrong band, 0.024 below the right one — because a false hit is a wrong answer served
  with confidence and a false miss is an upstream call somebody was going to make anyway.
  `tests/test_cache_semantic.py` asserts both bands and asserts the constant against them,
  so the number cannot drift away from its own justification.
- **The offline sweep §P8.H1 needs, already working.** `search(threshold=0.0, limit=k)` is
  the neighbour-list primitive; `cache_similarity` and `cache_source_request_id` are
  ledger columns; and a keyless test already replays the admission decision across
  0.70 → 0.99 from one similarity matrix. At the shipped default: 24 hits, **0 wrong**.
- **Tests: 924 keyless** (670 → 924), 2 live-marked and deselected by default. New files —
  `test_cache_eligibility` (53), `test_cache_store` (46, the contract suite over both
  implementations), `test_cache_keys` (33), `test_embedding` (23), `test_admin_cache` (23),
  `test_cache_gate` (21), `test_cache_poison` (18), `test_cache_replay` (16),
  `test_cache_semantic` (15), `test_cache_isolation` (6). Every Phase 0/1/2/3/4/4b test
  still green, and **one** of them changed (deviation 3).
- **Docs**: H-040 … H-047 in `docs/DECISIONS.md`; the **BUILD_PLAN §P8.H2 amendment**
  (below); README gains *The cache that is allowed to say no*; `.env.example` documents
  `HEADROOM_EMBEDDER` and why there is no fallback; `docker-compose.yml` passes it
  through; `migrations/README.md` status table.

**The BUILD_PLAN amendment (deliverable, not a note)**

BUILD_PLAN §P8.H2 now carries, verbatim: *"H2 runs against a tenant with caching disabled
entirely; overhead is measured on pure passthrough."* Logged as **H-047**. A cached hit
answers in microseconds without touching a provider, so a suite run against a
cache-enabled tenant would report an "overhead" figure that is really a hit-rate figure —
flattering the gateway by exactly the amount Backline's 133 questions happen to repeat
themselves. Amended **before any data exists**, per invariant 8, and made checkable three
ways: it is the shipped default, the pre-flight asserts it via `GET /admin/cache/{tenant}`,
and every ledger row carries `cache_disposition` so the report can state that the count of
non-`cache_disabled` rows is zero.

**Deferred**

- **The compose image does not carry the `embed` extra**, deliberately. A ~200 MB torch
  install in a container whose tenants all have caching disabled by default buys nothing,
  and baking the weights is Phase 9's job (L6). Out of the box the compose gateway does
  **exact** caching and answers 503 to a request for `semantic`, naming the fix — which is
  demonstrated in the container run below rather than described. `HEADROOM_EMBEDDER=hashing`
  gives a working semantic path locally with no torch; `uv sync --extra embed` and running
  the gateway on the host gives the real one, which is how the semantic evidence below was
  produced.
- **Nothing else from the Phase 5 scope.** Explicitly *not* built, per the plan: failover
  and the `failover_hops` column (P6), the savings counter and the cache panel (P7), the
  H1 corpus at full scale (P8). The seams exist and are columns already: `cache_similarity`
  and `cache_source_request_id` are what P8's sweep reads, and `/admin/cache` is the config
  surface it sweeps.
- **Cache entries have a TTL and no size cap.** `delete_expired` exists and is called by
  nothing on a schedule — every read already excludes expired rows, so a sweep that never
  runs costs disk and never costs a wrong answer. Phase 9 gets to schedule it beside the
  rollup Lambda.

**Deviations**

1. **`cache_disposition` has five values, not the three migration 0002's comment
   anticipated.** That comment said `exact | semantic | miss`; the shipped values are
   `cache_hit_exact`, `cache_hit_semantic`, `cache_miss`, `cache_bypass`, `cache_disabled`
   — the first two spelled as BUILD_PLAN §P5's own gate text spells them. The two extra
   values exist because an operator needs to tell "I turned it off" from "it is on and
   never applies to my traffic": those have completely different fixes, and collapsing them
   would hide the more common one. The comment is a comment; `0002` is not edited (H-003).
2. **`migrations/0005` adds three columns to `usage_ledger`**, which H-024's closing remark
   said would stop happening after `0002`. The same answer Phase 4's deviation 2 gave: that
   sentence was about the Phase 5 and 6 *seams*, which really are already present as
   columns, and the avoided cost is a fact about the row that has nowhere else to live. A
   second table joined on `request_id` would put "what did this hit save" one join away from
   the row that saved it. Additive, nullable, and `0002` is untouched.
3. **One Phase 1 test changed**, because the log-field set it pins grew:
   `test_request_context.py::test_the_log_shape_is_complete`. The same class of change as
   Phase 3's deviation 7 and Phase 4's deviation 4. No Phase 1 *behaviour* changed.
4. **`Dialect` gained `cache_probe`**, a sixth question a dialect answers: *is this one
   plain question, and where is it?* It is a dialect method rather than shared code because
   Anthropic keeps `system` as a top-level field while the OpenAI dialect keeps it in
   `messages` — a single implementation that blanked "the messages" would drop the OpenAI
   system prompt out of `context_hash` and let two different system prompts share an entry.
   Read-only and additive; it validates nothing.
5. **Canonicalising a request parses and re-serialises it**, which is the thing H-028 and
   H-007 refuse to do. The distinction is enforced by where the output goes: the canonical
   bytes are hashed and thrown away, and nothing produced in `headroom/cache/keys.py` is
   ever sent, stored, or returned. Argued in **H-042**, because a reader who has internalised
   the earlier rule should not have to work that out.
6. **The streaming path keeps a copy of the response** — the only new work on the hot loop.
   It happens **only when there is a live cache plan** (so a disabled tenant, an ineligible
   request, and a hit all copy nothing), it sits beside the observer feed on the same side
   of the `yield` where a parse already happens, and it is abandoned mid-stream if the
   response outgrows 1 MiB. The write itself is after the last byte, so it delays the client
   by nothing at all on that path.
7. **Additions the plan's Phase 5 text does not enumerate**, all additive: `cache_bypass` /
   `cache_disabled` and `cache_reason` (an operator needs to know *why* a cache is doing
   nothing); the `x-headroom-cache*` response headers; `HEADROOM_EMBEDDER` and the two-
   embedder resolution with no fallback; `/admin/cache`'s embedder probe; and the committed
   semantic corpus with its generator.

---

**Gate** — *exact hit/miss matrix; semantic hit on a committed paraphrase fixture and
miss on a committed hard-negative fixture (same template, different entity); streamed
replay fixture; the poison-attempt test; ledger rows correctly marked `cache_hit_exact` /
`cache_hit_semantic` with cost $0 and the avoided cost recorded.* Plus the brief's
additions: isolation sabotage, D-021 poison attempts, threshold config round-trip,
disabled-tenant zero-compute proof, disposition ledger tests, replay fidelity.

Run on the operator's machine with the compose stack up and **nothing exported**:

```
$ make lint
uv run ruff check .
All checks passed!
uv run ruff format --check .
130 files already formatted

$ make typecheck
uv run mypy
Success: no issues found in 130 source files

$ make test
================ 924 passed, 2 deselected, 1 warning in 17.20s =================

$ uv run pytest -m live -q --collect-only
2/926 tests collected (924 deselected) in 0.15s
```

**924 passed, 0 skipped** — `grep -c SKIPPED` over the run returns `0`, so the Postgres
half of *all three* contract suites, including the new pgvector one, executed against the
compose containers rather than skipping (H-012).

**And the same 924 pass with no torch installed at all**, which is the claim the whole
committed-corpus design rests on. CI proved that first, by failing — see below — and it is
now checked locally by reproducing CI's environment rather than trusting it:

```
$ uv sync                       # drop the `embed` extra: this is what CI has
$ uv run mypy
Success: no issues found in 130 source files
$ uv run pytest -q
================ 924 passed, 2 deselected, 1 warning in 15.84s =================
$ uv sync --extra embed         # put it back
```

Per-file counts for the new files, from the same run:

```
53 tests/test_cache_eligibility.py     18 tests/test_cache_poison.py
46 tests/test_cache_store.py           16 tests/test_cache_replay.py
33 tests/test_cache_keys.py            15 tests/test_cache_semantic.py
23 tests/test_embedding.py              6 tests/test_cache_isolation.py
23 tests/test_admin_cache.py
21 tests/test_cache_gate.py
```

### THE ISOLATION SABOTAGE — permanent, not a one-off

The brief asks for a sabotage that *"removes the tenant scoping and proves cross-tenant
leakage, then restores"*. It is in the suite rather than in this log, because a sabotage
that runs once and is written down decays into a claim about a machine nobody has.
`tests/test_cache_isolation.py` patches `namespace_for` so the namespace no longer carries
the tenant — the realistic bug, which is not "somebody deleted a WHERE clause" but
"somebody decided the namespace did not need the tenant in it" — and that one patch takes
out **both** mechanisms, the hash salt and the SQL predicate, because both are downstream
of it.

```
$ uv run pytest tests/test_cache_isolation.py -v
tests/test_cache_isolation.py::test_an_exact_entry_never_crosses_a_tenant_boundary PASSED
tests/test_cache_isolation.py::test_a_semantic_entry_never_crosses_a_tenant_boundary PASSED
tests/test_cache_isolation.py::test_the_namespace_is_what_separates_them PASSED
tests/test_cache_isolation.py::test_sabotage_removing_the_tenant_scoping_leaks_an_exact_entry PASSED
tests/test_cache_isolation.py::test_sabotage_removing_the_tenant_scoping_leaks_a_semantic_entry PASSED
tests/test_cache_isolation.py::test_the_store_itself_still_refuses_when_asked_correctly PASSED
6 passed in 0.04s
```

The two `sabotage` tests **assert the leak happens**: under the patch, tenant B receives
tenant A's answer, on the exact layer and — worse — on a *paraphrase* through the semantic
layer, where no request B sent was ever byte-identical to anything A sent. The two tests
above them assert the opposite on the same fixtures. A leak test that could only defeat one
of two defences would prove nothing about the other.

### THE SABOTAGE RUNS — seven, each reverted immediately

Green on the first attempt is when a suite deserves the most suspicion, so each claim was
tested by breaking the thing it protects. All seven patches were reverted; `git status` is
clean of them and the 924 above is the re-run.

*Sabotage A — the truncation guard is removed.* **D-021, exactly**: a complete stream of a
truncated answer becomes a permanent entry.

```
9 failed, 911 passed, 2 deselected
FAILED tests/test_cache_eligibility.py::test_a_response_that_did_not_finish_is_never_stored[anthropic_truncation]
FAILED tests/test_cache_eligibility.py::test_a_response_that_did_not_finish_is_never_stored[openai_truncation]
FAILED tests/test_cache_eligibility.py::test_a_response_that_did_not_finish_is_never_stored[a_tool_call]
FAILED tests/test_cache_eligibility.py::test_a_response_that_did_not_finish_is_never_stored[filtered]
FAILED tests/test_cache_eligibility.py::test_a_response_that_did_not_finish_is_never_stored[refused]
FAILED tests/test_cache_eligibility.py::test_a_response_that_did_not_finish_is_never_stored[never_reported]
FAILED tests/test_cache_poison.py::test_an_answer_truncated_by_max_tokens_is_never_cached
FAILED tests/test_cache_poison.py::test_a_streamed_answer_truncated_by_max_tokens_is_never_cached
FAILED tests/test_cache_poison.py::test_the_openai_length_finish_reason_is_never_cached
```

The last three are the ones that matter: they drive the real gateway, not a predicate.

*Sabotage B — the outcome check is removed*, so a cut stream and a client disconnect are
storable:

```
5 failed, 915 passed, 2 deselected
FAILED tests/test_cache_eligibility.py::test_a_request_that_did_not_end_ok_is_never_stored[upstream_stream_cut]
FAILED tests/test_cache_eligibility.py::test_a_request_that_did_not_end_ok_is_never_stored[upstream_stream_incomplete]
FAILED tests/test_cache_eligibility.py::test_a_request_that_did_not_end_ok_is_never_stored[client_disconnect]
FAILED tests/test_cache_eligibility.py::test_a_request_that_did_not_end_ok_is_never_stored[upstream_error]
FAILED tests/test_cache_poison.py::test_a_stream_that_simply_stops_is_never_cached
```

*Sabotage C — the transport leaves the cache key:*

```
2 failed, 918 passed, 2 deselected
FAILED tests/test_cache_keys.py::test_dialect_model_and_transport_are_all_in_the_namespace
FAILED tests/test_cache_keys.py::test_a_streaming_request_and_a_body_request_are_different_entries
```

Only the key-level tests notice, and that is **honest rather than a gap**: `stream` is also
inside the request body, so the exact hash separates the two transports even without the
namespace's help. The namespace's `transport` is defence in depth, and its value is that a
dialect signalling streaming out of band would stay separated by construction. Recorded in
H-043 rather than papered over.

*Sabotage D — the semantic search ignores `context_hash`*, so a different system prompt
matches. *Sabotage E — the embedding model leaves the filter*, so two vector spaces are
compared. Both fail in both store implementations, which is the contract suite paying for
itself:

```
2 failed, 918 passed   FAILED tests/test_cache_store.py::test_a_different_context_never_matches[memory]
                       FAILED tests/test_cache_store.py::test_a_different_context_never_matches[postgres]

2 failed, 918 passed   FAILED tests/test_cache_store.py::test_a_different_embedding_model_never_matches[memory]
                       FAILED tests/test_cache_store.py::test_a_different_embedding_model_never_matches[postgres]
```

*Sabotage F — the cache is consulted before the rate limiter* (H-046 run backwards):

```
2 failed, 918 passed, 2 deselected
FAILED tests/test_cache_gate.py::test_a_hit_still_consumes_its_rate_limit
FAILED tests/test_cache_gate.py::test_a_rate_limited_request_never_reaches_the_cache
```

*Sabotage G — the `disabled` check is removed*, so every tenant caches whether they asked
or not. This one is worth reading for what it says about the **rest** of the suite:

```
12 failed, 908 passed, 2 deselected
FAILED tests/test_admin_cache.py::test_put_switches_caching_on_and_it_bites_immediately
FAILED tests/test_admin_cache.py::test_delete_disables_and_purges
FAILED tests/test_admin_usage.py::test_totals_aggregate_a_tenants_spend
FAILED tests/test_budget_stampede.py::test_every_refusal_is_a_ledger_row_and_no_provider_call
FAILED tests/test_cache_gate.py::test_a_disabled_tenant_does_no_cache_work_at_all
FAILED tests/test_cache_gate.py::test_a_disabled_tenants_row_says_so
FAILED tests/test_metering.py::test_the_new_price_does_apply_to_the_next_request
FAILED tests/test_rate_limit_gate.py::test_a_requests_per_minute_limit_admits_exactly_its_capacity
FAILED tests/test_rate_limit_hammer.py::test_the_hammer
FAILED tests/test_rate_limit_hammer.py::test_the_sabotage_over_admits
FAILED tests/test_request_context.py::test_the_log_shape_is_complete
FAILED tests/test_tool_blocks.py::test_escaped_unicode_survives_exactly_as_written
```

Seven of those twelve belong to **earlier phases** — metering, budgets, rate limits, tool
fidelity — and they fail because a cache that switches itself on changes what every one of
those tests observes. That is the clearest available evidence that "off by default" is what
makes this phase genuinely additive (invariant 7): the 924 green above are green *because*
no pre-existing test's behaviour moved.

### The gate's clauses, individually

*Exact hit/miss matrix.* `tests/test_cache_gate.py` — a second identical request hits with
one upstream call for two requests; a different question misses; the two dialects have
separate namespaces; a streaming caller does not hit a body entry; an `exact` tenant never
embeds; and an exact hit in `semantic` mode still never embeds.

*Semantic hit on a paraphrase, miss on a hard negative.* Against the committed corpus and
**real** `bge-small` vectors:

```
$ uv run pytest tests/test_cache_semantic.py -q
15 passed in 0.10s
```

The load-bearing one is `test_every_paraphrase_in_the_corpus_resolves_to_its_own_question`:
12 seeds and 24 probes through the whole gateway, every hit checked against
`x-headroom-cache-source` for **which question it actually answers**. At the shipped
default: 24 hits, 0 resolved to a different source question. The near-miss tests are the
other half — same template, different artist, provably different answers in the key.

*Streamed replay.* `tests/test_cache_replay.py`, 16 tests: byte-identical bodies and
streams on both dialects, identical event sequences, `[DONE]` preserved, streams recorded
from 1-byte chunking replaying intact, the H-016 reasoning fixtures (literal 2-, 3-, and
4-byte UTF-8) surviving, upstream headers deliberately *not* replayed, and a replayed
stream writing an ordered context and its own ledger row.

*The poison attempts.* `tests/test_cache_poison.py`, 18 tests, every one driving the real
gateway with caching on and the request eligible: a cut stream, a stream that simply stops,
a `max_tokens` truncation (streamed and not), the OpenAI `length` case from Phase 1's live
smoke, upstream 400/429/500/529, a timeout, a connect failure, a response carrying a tool
call, a request declaring tools, a tool_result conversation, a budget refusal, and a
response over the size bound. Each asserts the cache is still empty — and the file ends
with `test_the_happy_path_really_does_store`, the control without which "the cache is
empty" would be satisfied by a cache that never stores anything.

*Ledger rows.* `test_a_hits_row_is_distinguishable_from_an_upstream_call` checks every
column that could imply an upstream call for not implying one, and checks the avoided cost
equals the source row's actual cost.

*Threshold config round-trip.* `test_the_threshold_round_trips_exactly` (0.7, 0.85, 0.9123,
0.99 through `NUMERIC(5,4)` and back) and — the half a GET cannot show —
`test_the_threshold_that_round_trips_is_the_one_the_gate_uses`, which puts a threshold
either side of a known 0.82 pair and asserts the hit becomes a miss.

*Disabled-tenant zero compute.* `test_a_disabled_tenant_does_no_cache_work_at_all` drives
three requests and asserts `embedder.calls == 0` **and** `store.reads == 0` — read off the
objects' own counters, not asserted about.

### What CI caught that the operator's machine could not

The first PR-5 run failed `lint + typecheck` on two lines:

```
headroom/cache/embedding.py:192: error: Cannot find implementation or library stub
                                 for module named "sentence_transformers"  [import-not-found]
tests/support/build_semantic_corpus.py:153: error: Cannot find implementation or library
                                 stub for module named "sentence_transformers"  [import-not-found]
```

Both files import `sentence_transformers` lazily, inside a function, and both were green
locally — because the machine that built this phase had run `uv sync --extra embed` to
generate the corpus. H-004 deliberately does **not** install that extra in CI, so
`make typecheck` quietly meant two different things in the two places, and the *keyless*
one was the one nobody was running.

Fixed with a `[[tool.mypy.overrides]]` for `sentence_transformers.*`, the same shape and
the same reason as the existing `boto3` one: the package ships no `py.typed` marker, its
`encode` result is converted to `list[list[float]]` at the boundary, and nothing is lost
by ignoring its missing stubs. Verified by *reproducing* CI's environment locally
(`uv sync`, typecheck, test, `uv sync --extra embed`) rather than by pushing and hoping —
which is also how the "924 pass with no torch" line above got measured.

Worth stating plainly: this is CI doing the job H-004 built it for. A keyless job that
only ever runs the same environment the operator has is a keyless job in name.

### The bug the container run found

The end-to-end run caught something the whole keyless suite had missed: **the embedder
probe did not probe.** `PUT /admin/cache {"mode": "semantic"}` called
`LazyEmbedder.resolve()`, which *built* a `BGEEmbedder` — and constructing one touches no
weight file. On an image with no `sentence-transformers` installed it returned **200**, and
an operator would have believed semantic caching was on while every request bypassed
silently. A probe that does not probe is worse than no probe, because it reads as a
guarantee.

Fixed by giving `Embedder` a `load()` method that `BGEEmbedder` implements by pulling the
weights, with `_inner` assigned only after a successful load so a retry really retries. Two
tests now cover it —
`test_embedding.py::test_resolving_a_missing_model_raises_rather_than_returning_an_object`
and `test_admin_cache.py::test_enabling_semantic_without_an_embedder_is_a_503_naming_the_extra`
— and the corrected behaviour is step 3 of the container run below.

### End to end through the real container, on a wiped volume, keyless

```
$ docker compose down -v && make up
 Container headroom-db-1 Healthy
 Container headroom-dynamodb-1 Healthy
 Container headroom-gateway-1 Healthy
docker compose exec -T gateway uv run --no-sync python -m headroom.db.migrate
applied 5 migration(s): 0001_tenants_and_virtual_keys, 0002_usage_ledger,
                        0003_ledger_budget_columns, 0004_rate_limits, 0005_response_cache

### 2. caching is OFF for a brand-new tenant — the shipped default
{"mode":"disabled","ttl_s":null,"similarity_threshold":null,"effective_ttl_s":86400,
 "effective_similarity_threshold":0.9,"embedding_model":"BAAI/bge-small-en-v1.5",
 "entries":0,"semantic_entries":0,"body_bytes":0}

### 3. ask for SEMANTIC on an image with no embedder — 503, naming the fix
status=503
{"error":{"type":"embedder_unavailable","message":"embedder 'BAAI/bge-small-en-v1.5' needs
 sentence-transformers, which is not installed; run `uv sync --extra embed`, or set
 HEADROOM_EMBEDDER=hashing"},"headroom":{"reason":"embedder_unavailable", …}}

### 4. switch on EXACT caching (needs no embedder at all)     -> 200, mode: exact

### 5. first request -> miss, and it populates
x-headroom-request-id: hr_cc298dd31937460da608429e2082d0d9

### 6. the identical request -> HIT, and no provider was called
x-headroom-cache: cache_hit_exact
x-headroom-cache-source: hr_cc298dd31937460da608429e2082d0d9
x-headroom-cache-age: 0
{"id":"msg_mock_6ca20aa0926a68c6","type":"message","role":"assistant","model":"mock-model-1",
 "content":[{"type":"text","text":"mock reply from mock-model-1"}],"stop_reason":"end_turn",
 "usage":{"input_tokens":11,"output_tokens":7}}

### 7. the SAME question with tools declared -> BYPASS, nothing read, nothing written
"cache_disposition":"cache_bypass","cache_reason":"tools_present"

### 8. the same question STREAMED -> a miss: transport is part of the key
(no cache header -> not a hit)

### 9. streamed again -> a byte-identical replay, first token already there
x-headroom-cache: cache_hit_exact
event: message_start
data: {"type":"message_start","message":{"id":"msg_mock_6ca20aa0926a68c6", …}}

### 10. what the database actually holds
  req_hash  | transport | stop_reason | cost_status |    usd_cost    | exact_only | bytes
------------+-----------+-------------+-------------+----------------+------------+-------
 a75e23a37c | body      | end_turn    | priced      | 0.000011500000 | t          |   251
 0792ace48e | stream    | end_turn    | priced      | 0.000011500000 | t          |  1157

### 11. the ledger — a hit is not an upstream call wearing a hat
 cache_disposition | provider | up  | in_t | out_t |    usd_cost    | cache_avoided_usd | cost_status  | overhead_ms | ttft_ms
-------------------+----------+-----+------+-------+----------------+-------------------+--------------+-------------+---------
 cache_miss        | mock     | 200 |   11 |     7 | 0.000011500000 |                   | priced       |       0.009 | 334.216
 cache_hit_exact   |          |     |      |       | 0.000000000000 |    0.000011500000 | not_billable |             |   0.893
 cache_bypass      | mock     | 200 |   11 |     7 | 0.000011500000 |                   | priced       |       0.008 |   6.664
 cache_miss        | mock     | 200 |   11 |     7 | 0.000011500000 |                   | priced       |       0.046 |   9.074
 cache_hit_exact   |          |     |      |       | 0.000000000000 |    0.000011500000 | not_billable |             |   1.724
 cache_hit_exact   |          |     |      |       | 0.000000000000 |    0.000011500000 | not_billable |             |   1.334

### 12. the totals still count only tokens a model produced
[{"requests":6,"input_tokens":33,"output_tokens":21,"reasoning_tokens":0,
  "usd_cost":"0.000034500000","unpriced_requests":0,"errored_requests":0}]

### 14. DELETE disables and purges
{"mode":"disabled","entries":0, …}
entries left in response_cache: 0
```

Three things in that output are the phase. **`334.216` → `0.893` ms** is the demo moment,
measured rather than asserted. **33 in / 21 out over 6 requests** is exactly the three
upstream calls at 11/7 each — the three hits contributed no tokens, so the totals mean what
they meant before this phase. And `overhead_ms` is blank on every hit, because
`passthrough_overhead_ms` is measured *from* a first upstream byte and there was not one.

### The semantic layer against the real model

The container carries no embedder by design, so this is the `uv sync --extra embed` path:
the gateway on the host, the compose Postgres behind it, and **`BAAI/bge-small-en-v1.5`
loaded on CPU by the `PUT` that asked for semantic caching**.

```
### 1. semantic caching on, at the shipped default threshold (0.90)

### 2. seed the cache
    Q: What was Radiohead's total streaming revenue in 2019?     -> miss, one upstream call

### 3. a PARAPHRASE — different words, same question
    Q: In 2019, how much streaming revenue did Radiohead bring in overall?
x-headroom-cache: cache_hit_semantic
x-headroom-cache-source: hr_a2f7dac0691941f0967fb14050beda11
x-headroom-cache-similarity: 0.97643

### 4. a NEAR-MISS — same template, different artist. The dangerous class.
    Q: What was Coldplay's total streaming revenue in 2019?
    (no cache header -> MISS: it went upstream, as it must)

### 5. empty the cache, drop the threshold to 0.70, and seed ONLY Radiohead
1 entry, probe: What was Radiohead's total streaming revenue in 2019?
    now ask the near-miss:  What was Coldplay's total streaming revenue in 2019?
x-headroom-cache: cache_hit_semantic
x-headroom-cache-similarity: 0.83732

### 6. WHICH question did that answer come from? Checked, not narrated.
 cache_disposition  | cache_similarity |                 answers_the_question
--------------------+------------------+-------------------------------------------------------
 cache_hit_semantic |          0.83732 | What was Radiohead's total streaming revenue in 2019?

### 7. the ledger rows, with the similarity of every hit
 cache_disposition  | cache_similarity |   served_by    | provider |    usd_cost    | cache_avoided_usd
--------------------+------------------+----------------+----------+----------------+-------------------
 cache_miss         |                  |                | mock     | 0.000011500000 |
 cache_hit_semantic |          0.97643 | hr_a2f7dac0691 |          | 0.000000000000 |    0.000011500000
 cache_miss         |                  |                | mock     | 0.000011500000 |
 cache_miss         |                  |                | mock     | 0.000011500000 |
 cache_hit_semantic |          0.83732 | hr_5dc9a94feb2 |          | 0.000000000000 |    0.000011500000
```

Step 6 is the whole project in one table: a Coldplay question answered from a Radiohead
entry, at a threshold somebody chose. **That is §P8.H1's silent wrong answer**, reproduced
on purpose on real hardware, and the reason the threshold is per-tenant configuration
rather than a constant.

The first version of that demo got step 5 **wrong** and it is worth recording. It lowered
the threshold and re-asked the near-miss *without purging* — but step 4's miss had already
populated a Coldplay entry of its own, so the 0.98 hit that followed was Coldplay's own
answer to Coldplay's question. Correct behaviour, narrated as poison. The purge is now the
control, and the claim is checked by joining the ledger row to the entry's `probe` rather
than asserted in prose.

**Assumed-facts register (§0.4)** — nothing was due at this gate. One adjacent fact was
verified in passing: `pgvector/pgvector:pg16` ships pgvector **0.8.6**, so `hnsw` with
`vector_cosine_ops` is available and the index in `0005` is created rather than skipped
(H-001's promise, cashed four phases later).

**Spend** — $0.00. Every test above ran on the MockProvider; the semantic evidence ran on
`bge-small-en-v1.5` on the operator's own CPU. No provider API was called in this phase.

**Live smokes** — untouched and unaffected. They provision their own tenant (H-029), which
now means a tenant with caching **disabled**, so both smokes exercise pure passthrough
exactly as they did before this phase — which is also, and not by coincidence, the H2
configuration H-047 pre-registers.

**Operator verification** — the exact commands are in the PR description.

CI on PR-5 ([run 31337135353](https://github.com/sergioavilax/headroom/actions/runs/31337135353)),
all three jobs green, no annotations:

```
$ gh run view 31337135353 --json conclusion,jobs
success
lint + typecheck: success
pytest (postgres + dynamodb-local service containers): success
gateway image builds and serves: success

lint + typecheck | All checks passed!
lint + typecheck | Success: no issues found in 130 source files
pytest (…service containers) | ===== 924 passed, 2 deselected, 1 warning in 25.61s =====
gateway image builds and serves | gateway healthy
```

**924 passed, 0 skipped in CI**, on a runner with **no `embed` extra installed** — no
torch, no model, no network to HuggingFace. Every similarity assertion in this phase still
ran, against real `bge-small-en-v1.5` numbers, out of the committed corpus. That is the
whole point of the fixture, and CI is where it is proved rather than claimed.

The `image` job is worth one line too: it builds the container **without** the extra and
smokes `/healthz`, which is why `LazyEmbedder` had to be lazy in the first place — a
gateway that imported torch at construction could not have booted there.

---

## Phase 6 — Failover and resilience: kill a GPU on camera (2026-08-09)

Branch `claude/p6-failover`, GitHub #8. BUILD_PLAN §P6's words are the spec: *"provider
health tracking (rolling error/latency windows), retry with jittered exponential backoff on
429/5xx, and same-dialect failover chains from the routing table (L4) … a circuit breaker
trips a provider out of rotation after a threshold and probes it back in."*

**Shipped**

- **The failover executor** (`headroom/policy/failover.py`) — the only object in the
  codebase allowed to call a provider twice for one request. It replaces **one line** of
  `headroom/api/proxy.py`, the one that used to read `await provider.open(...)`, and
  everything above and below it is unchanged. That is the phase in a sentence: failover is
  a widening of *how* an upstream is obtained, not of what a request is.
- **The splice guard, which is the decision the phase turns on** (**H-048**). A request may
  be retried for exactly as long as nothing about its response has been committed to the
  client — the predicate is `ctx.first_token_out_at is None`. In the shipped proxy that
  holds *structurally*: the executor returns before any code that can yield. It is checked
  anyway, on every retry, because "structurally unreachable" is a property of today's call
  sites and call sites change. After the line, H-008's discipline is unchanged: a terminal
  error event in the caller's own dialect, never a second provider's prose.
- **The commit point is later than it looks, and that is worth having.** A *non-streamed*
  body is read in full before anything is sent, so a connection that dies mid-read has
  still committed nothing and can still fail over. The executor therefore reads
  non-streamed bodies **inside** the retry loop, which is why `BufferedUpstreamResponse`
  exists — and operationally it is the difference between killing a container mid-request
  and losing that request, or not.
- **A closed, small trigger set** (**H-049**): transport faults, a body that dies mid-read,
  upstream **429**, upstream **5xx**. Not an upstream 4xx (the next provider says the same
  thing one round trip later), not a `ConfigurationError` (routing around one's own
  misconfiguration is how it stays undiscovered for a month), and **never the gateway's
  own 402 or 429** — those are raised before the executor exists in the call path and
  neither is a `ProviderError`, the only class this file catches. Failing over on your own
  rate limit moves a burst instead of shedding it; failing over on your own budget refusal
  spends the money somewhere else.
- **Chains are per-route config, and failover is opt-in.** `fallbacks:` plus an optional
  `max_attempts: 1..5`, both defaulted so that **a rule mentioning neither behaves exactly
  as it did in Phase 5** — one call, no retry, no backoff, no breaker on its path. The
  attempt sequence wraps above the chain length (`a, b, a`), which is how a
  single-provider route asks to be retried rather than abandoned.
- **BUILD_PLAN L4 stopped being structural and became checked.** `register_kind` now
  requires each kind to declare which dialects it speaks, and `build_gateway` refuses to
  start on a route whose primary *or* fallback cannot speak the route's dialect. Routing
  being per dialect made a chain same-dialect by construction; nothing structural stopped
  `fallbacks: [anthropic]` under an `openai:` route, which would hand a chat-completions
  body to the Messages API on exactly the day the primary went down.
- **A scope narrows a chain and can never widen it.** A key scoped to `vllm_a` and not
  `vllm_b` is not served by `vllm_b` when `vllm_a` fails. The primary still answers 403
  through `require_provider`; the rest is filtered, so an outage cannot widen a permission.
- **Backoff is paid to a provider that already failed, not to a fresh one** (**H-050**).
  Nothing about `vllm_a` being down suggests `vllm_b` needs a moment, so moving down a
  chain costs no latency at all — which is the opposite of what a retry library written for
  one endpoint does. Coming back to one that already failed *this request* pays full jitter
  over 50 ms, doubling, capped at 2 s; `BackoffPolicy.worst_case_s` publishes the bound
  (150 ms across three attempts). `sleep` and `jitter` are injected, so CI asserts the exact
  schedule in microseconds of wall clock.
- **Provider health and a circuit breaker** (`headroom/policy/health.py`, **H-052**): a
  rolling window of 20, a floor of 5 samples, a 0.5 failure ratio, a 10-second cooldown,
  one probe at a time, and the window cleared when a probe succeeds — without which the
  failures that tripped the breaker are still in it and the next blip re-trips, turning a
  ten-second outage into a ten-minute one. **In memory, per process, never shared**: a
  breaker is not a fact about the world, it is a record of what *this* task can reach.
- **The breaker never skips the last candidate.** Refusing the only remaining upstream
  would convert a provider's outage into the gateway's own — strictly worse than trying and
  failing — so the final slot is always attempted, which also makes it the natural probe.
- **A provider is scored when its response finishes, not when it starts.** An upstream that
  answers headers and then dies mid-answer is not healthy, and that is exactly what
  `docker kill` on a live vLLM produces. The executor scores what it can judge alone; the
  streaming path scores the stream when it ends. One attempt, one observation. A client
  disconnect is scored by nobody, and an upstream 4xx counts as a *success* — a 400 is a
  healthy provider correctly refusing a bad request, and counting it would let one tenant's
  malformed payloads trip a breaker for everybody else.
- **`migrations/0006_ledger_failover.sql`** — `failover_from` and `failover_error`, plus a
  partial index on `failover_hops > 0`. `failover_hops` itself has been a column since
  `0002`, reserved for this phase. The three together answer three different questions:
  `provider` says who served, `failover_hops` how many candidates were passed over,
  `failover_from`/`failover_error` which one first and why — the operational question
  (*we are serving from `vllm_b`; what happened to `vllm_a`?*) that `error_reason` and
  `upstream_status`, which describe the **last** thing that happened, cannot answer.
  **H-051.**
- **A breaker-skipped candidate counts as a hop**, so `failover_hops > 0` keeps meaning
  "the primary did not serve this" once an outage becomes persistent enough to trip the
  breaker — which is exactly when somebody is reading it. `failover_error: breaker_open`
  keeps the two cases distinguishable.
- **The upstream timing mark is rewound between attempts**
  (`RequestContext.restart_upstream_timing`, the only place in the project where a mark
  moves backwards). A failed attempt's error body stamps `first_upstream_byte_at`, and
  marks are idempotent — so without the rewind `passthrough_overhead_ms`, the column
  §P8.H2 publishes against a pre-registered p50 < 50 ms, would silently measure the whole
  failover sequence instead of the gateway's own cost.
- **One request, one row, one reservation, however many providers it took** (**H-053**).
  Admission happens above the executor and settlement below it, so hops are invisible to
  money by construction — no compensating release, no second hold, nothing to keep in sync.
  A hop consumes no extra rate-limit unit either: the limiter meters client requests, not
  upstream attempts, and charging rate for the gateway's own retry decision would make a
  provider outage look like the tenant misbehaving.
- **`/admin/providers`** (`headroom/api/providers.py`): `GET` (listing and one), and
  `DELETE /{name}/health` — the incident-response route, spelled with `/health` because a
  `DELETE` on the provider itself would read as *remove this provider*, which a running
  gateway can never do. The view joins health with **the chains each provider sits in**,
  because "why is my traffic coming from the fallback" is answered by neither alone. This
  is what Phase 7's provider-health tiles read.
- **Response headers in Headroom's own closed namespace**: `x-headroom-failover-hops` and
  `-from`, present only when there was a hop — so the overwhelmingly common response gains
  no bytes. Trustworthy because H-038 already strips every `x-headroom-*` header from every
  upstream response.
- **The MockProvider became fault-injectable from *outside* a test process.** Scripts may
  be specialised per instance (`"name@mock_b"`), which is how one request makes A fail and
  B serve; and a small closed vocabulary of built-ins — `fault-529` (any status),
  `fault-timeout`, `fault-connect`, `fault-cut`, each optionally aimed with `@name` — needs
  no script book at all, so `make up` plus two curls demonstrates a failover with no key,
  no network, no GPU, and no spend. A mistyped name still raises.
- **The shipped `config/routing.yaml` carries two real chains**: `vllm_a → vllm_b` for the
  OpenAI dialect (the operator's two 4090s, ports 8010 and 8011 per `docs/vllm.md`) and
  `mock → mock_fallback` so the keyless demo has one too. The `claude-` route deliberately
  has none.
- **Tests: 1056 keyless** (924 → 1056), 2 live-marked and deselected by default. New files —
  `test_failover_matrix` (32), `test_failover_backoff` (20), `test_provider_health` (17),
  `test_failover_config` (17), `test_failover_chaos` (16), `test_failover_ledger` (11),
  `test_failover_boundary` (8), `test_admin_providers` (8); `test_routing` grew 3 (17 → 20).
  Every Phase 0…5 test still green, and **one** of them changed (deviation 3).
- **Docs**: H-048 … H-053 in `docs/DECISIONS.md`; **`docs/vllm.md` updated** (below);
  `docs/evidence/` created with the P6 capture list; README gains *Failover that refuses to
  serve a Frankenstein answer*; `.env.example` documents `VLLM_B_BASE_URL`;
  `migrations/README.md` status table.

**The `docs/vllm.md` update (a deliverable, not a note)**

The GPU-pinning workaround that shipped in Phase 2 as **UNTESTED** — and that Phase 2's
*Deferred* section named as Phase 6's pre-flight — has been run and works:

- **`--gpus all` + `-e CUDA_VISIBLE_DEVICES=<UUID>` is now VERIFIED** (2026-08-09): the
  model landed on the named card first try, confirmed by the uuid-keyed `nvidia-smi` query.
- **`--gpus device=N` and `--gpus device=UUID` remain VERIFIED-broken** and the file now
  says *do not use* rather than *unreliable*.
- **The standing two-instance topology is documented**: instance A on host port 8010
  (`vllm_a`, the chain's primary, `VLLM_BASE_URL`), instance B on 8011 (`vllm_b`, the
  fallback, `VLLM_B_BASE_URL`), one per card, identical launch flags, both named so the
  demo's `docker kill vllm-a` is unambiguous.
- **One new negative fact**: `nvidia-smi --query-compute-apps` cannot map a container to a
  card under WSL2 — it reports a single virtual PID against both GPUs with `[N/A]` memory.
  Per-GPU *memory* is the only reliable signal, which is why `--query-gpu=uuid,memory.used`
  stays the documented check.

Corroborated during this session by inspecting both running containers rather than by
trusting the flags: the instance launched with `--gpus all` + `CUDA_VISIBLE_DEVICES` is
pinned to the UUID it names, while the instance still launched with `--gpus device=<the
same UUID>` is demonstrably not on that card — both cards read ~23 GB used, so the two are
on different GPUs and only one of them is where it asked to be. The broken form and the
working form, running side by side, landing in different places.

```
$ nvidia-smi --query-gpu=index,uuid,name,memory.used,memory.free --format=csv
index, uuid, name, memory.used [MiB], memory.free [MiB]
0, GPU-61bdb28e-…, NVIDIA GeForce RTX 4090, 23736 MiB, 407 MiB
1, GPU-ad348511-…, NVIDIA GeForce RTX 4090, 23332 MiB, 811 MiB

$ for p in 8010 8011; do curl -sS "http://localhost:$p/v1/models" | head -c 60; echo; done
{"object":"list","data":[{"id":"cyankiwi/Qwen3.6-27B-AWQ-INT4"
{"object":"list","data":[{"id":"cyankiwi/Qwen3.6-27B-AWQ-INT4"
```

**Deferred**

- **The two-GPU kill demo itself.** The chain, the config, the ledger columns, the admin
  surface, and the pre-flight are all in place and verified; the `docker kill` is the
  operator's, because it takes down a container that spends minutes reloading a 27B
  checkpoint and this session is not the one that should decide when that happens. The
  exact commands, in order, with what correct output looks like at each step, are in *The
  live demo* below; `docs/evidence/p6-failover/` is waiting with a capture list.
- **The dashboard half of §P6's demo.** BUILD_PLAN's Phase 6 text says *"watch the
  dashboard show traffic shift"* — there is no dashboard until Phase 7, and §P7's own gate
  re-runs this demo watching it ("that's the hero GIF"). What this phase produces instead is
  the ledger-and-log version of the same evidence, which is what §P8.H3 actually reports
  from.
- **Latency-based tripping.** The rolling window records latency and `/admin/providers`
  reports p50/p95, and nothing trips on it: a useful rule needs a baseline per model and
  per prompt size, which is a research project rather than a threshold (H-052).
- **Health shared across processes.** Deliberately never (H-052), not deferred.
- **Concurrency limits, per-key budgets, prompt-cache tier pricing** — still deferred from
  Phases 4, 4b, and 3 respectively; untouched by this phase.

**Deviations**

1. **The shipped `config/routing.yaml` renamed `vllm` to `vllm_a` and gained three
   providers** (`vllm_b`, `mock_fallback`). BUILD_PLAN L5 has always named *two* vLLM
   instances as launch providers and §P6 makes the pair the demo; the single `vllm` entry
   was the placeholder Phase 1 shipped, with a comment saying Phase 6 would add the second.
   `VLLM_BASE_URL` still addresses the primary, so the live smoke and every doc that names
   it are unchanged. `mock_fallback` is the same argument one layer down: a keyless demo
   that needs a config edit before it works is a demo nobody runs.
2. **`register_kind` gained a required `dialects` keyword.** A tightening of a registration
   contract rather than an addition to it, taken deliberately: a kind that has not said
   which wire format it speaks cannot be wired safely, and a permissive default is how L4
   ends up unenforced. Three in-repo call sites, no external ones.
3. **One Phase 1 test changed**, because the log-field set it pins grew:
   `test_request_context.py::test_the_log_shape_is_complete`. The same class of change as
   Phase 3's deviation 7, Phase 4's deviation 4, and Phase 5's deviation 3. No Phase 1
   *behaviour* changed.
4. **`migrations/0006` adds two more columns to `usage_ledger`**, which H-024's closing
   remark said would stop after `0002`. The same answer Phases 4 and 5 gave: that sentence
   was about the Phase 5 and 6 *seams*, which really are present as columns —
   `failover_hops` is one of them — and it did not anticipate a phase needing to record
   *why* a request left where it was routed. A second table joined on `request_id` would
   put that one join away from the row that answers everything else. Additive, nullable,
   `0002` untouched (H-003).
5. **The live smokes gained one assertion**, and it is a Phase 6 consequence rather than a
   tidy-up: the shipped OpenAI route is now a chain, so a smoke pointed at a *broken*
   primary would be quietly served by the fallback and pass. Both smokes now assert
   `failover_hops == 0` with a message naming the provider that actually served.
6. **The MockProvider gained built-in fault scripts** (`fault-*`) and per-instance script
   specialisation (`"name@provider"`). The first is what makes a *running container* demo
   of this phase possible at all — a script book only exists inside a test process — and
   the second is what lets one request make A fail and B serve. Both are additive: a
   mistyped script name still raises, and every script written before this phase resolves
   exactly as it did.
7. **Additions the plan's Phase 6 text does not enumerate**, all additive: `/admin/providers`
   (the plan puts health *tiles* in Phase 7 and this is what they read); the
   `x-headroom-failover-*` response headers; `max_attempts` as per-route config with a
   published ceiling; `Route`/`RouteRule.attempts()` on the routing table; and
   `docs/evidence/` with its README, which invariant 9 has required since Phase 0 and which
   no phase had yet needed.

---

**Gate** — *chaos suite green in CI; the two-GPU kill demo captured to `docs/evidence/`
with the ledger rows showing the hop counts.* Plus the session brief's additions: the
failover matrix, the streaming-boundary pair with a splice sabotage, no double billing, an
exhausted-chain honesty test, and backoff on controlled clocks.

### THE SPLICE — the headline artifact

The naive implementation, executed. `tests/test_failover_boundary.py` mounts a
`_passthrough` that opens the fallback when a stream dies and keeps yielding — fifteen
lines, each locally reasonable — over the real one, and drives the real gateway through it.
`mock_a` starts answering about France and is cut after five deltas; `mock_b` answers about
Germany.

```
$ uv run pytest tests/test_failover_boundary.py -q
........                                                                 [100%]
8 passed in 0.06s
```

What the two implementations hand the caller, for the identical fault:

```
SHIPPED
  text     "The capital of France is "
  events   message_start, content_block_start, ping, 5 × content_block_delta, error
  no message_stop, no second message_start
  outcome  upstream_stream_cut     failover_hops 0     mock_b was never called

SPLICED (the sabotage)
  text     "The capital of France is The capital of Germany is Berlin."
  events   message_start, …, message_start, …, message_stop
  HTTP 200, stop_reason "end_turn", and NO error event anywhere in the stream
```

The frightening assertion in that test is not `assert "Berlin" in text`. It is
**`assert "error" not in events`** — the spliced stream is well formed, terminates cleanly,
and every SDK on the far end returns it as one complete message. Two models wrote it and
nothing in the transcript says so. That is what H-048 exists to make impossible, and it is
why the boundary is a checked predicate rather than a comment.

### THE FAILOVER MATRIX

Every trigger, every non-trigger, driven through the whole stack — routes, auth, limiter,
cache, budget gate, meter — rather than against the executor.

```
$ uv run pytest tests/test_failover_matrix.py -q
................................                                         [100%]
32 passed in 0.18s
```

| Fault on the primary | Result |
|---|---|
| upstream 429, 500, 502, 503, 529 | fallback serves, `failover_hops: 1` |
| timeout, connect error | fallback serves, `failover_error: upstream_timeout` / `upstream_unavailable` |
| 529 on a *streamed* request | fallback serves the whole stream, one `message_start`, no error frame |
| **upstream 400, 401, 403, 404, 422** | **forwarded verbatim; the fallback is never called** |
| **gateway 429 (rate limit)** | **no provider in the chain is called at all** |
| **gateway 402 (budget)** | **no provider in the chain is called at all** |
| **403 (provider out of scope)** | **no provider in the chain is called at all** |
| **mid-stream cut** | terminal error event; fallback never called |
| **cache hit** | served from the entry; both providers scripted to fail and neither is asked |

The two gateway rows are asserted in the strong form — *not* "the header says gateway",
which is H-038's own test, but **`chain.providers["mock_b"].received == []`**. The limiter
and the budget gate raise before the executor exists in the call path, so failing over on
our own refusal is not a bug that was avoided, it is a bug with nowhere to live.

### THE EXHAUSTED CHAIN

Fail closed, carrying the **last** failure, with the error class preserved so Phase 1's
invented statuses and stable `headroom.reason` values still mean what they meant.

```
A: 529, B: 503   ->  HTTP 503, B's own body forwarded verbatim, error-source: upstream
                     x-headroom-failover-hops: 1   x-headroom-failover-from: mock_a
A: connect, B: timeout
                 ->  HTTP 504, headroom.reason "upstream_timeout"
                     message: "…; failover chain exhausted:
                               mock_a:upstream_unavailable -> mock_b:upstream_timeout"
```

And a route with **no** chain keeps its Phase 1 error exactly: no trail, no decoration,
`failover_hops: 0`.

### NO DOUBLE BILLING

```
$ uv run pytest tests/test_failover_ledger.py -q
...........                                                              [100%]
11 passed in 0.06s
```

The arithmetic, on the tenant's own counters rather than on the code's shape:

| | requests | upstream calls | ledger rows | settled spend |
|---|---|---|---|---|
| primary serves | 1 | 1 | 1 | $0.0000115 |
| primary 529s, fallback serves | 1 | 2 | **1** | **$0.0000115** |
| both 529 | 1 | 2 | 1 | $0 (hold released — no model ran) |
| both time out | 1 | 2 | 1 | the estimate (H-031: a model may well have been billed) |

Two identical requests, one clean and one after a hop, move the counter by exactly
`2 × $0.0000115`. A hop also consumes no second rate-limit unit — a bucket at 10/min reads
`available: 9` after a request that touched two providers.

### BACKOFF, ON A CLOCK THAT NEVER MOVES

```
$ uv run pytest tests/test_failover_backoff.py -q
....................                                                     [100%]
20 passed in 0.05s
```

Nothing in this suite sleeps. The executor takes its `sleep` as a parameter and CI passes a
recorder; the assertions are on the exact schedule.

```
chain a -> b, one attempt each        delays []            no wait for a fresh candidate
chain a, max_attempts 3               delays [0.05, 0.1]   worst case 0.15s, published
chain a -> b, max_attempts 3          delays [0.05]        a, b, a — one repeat, one sleep
no chain at all                       delays []            Phase 5's path, bit for bit
```

### THE CHAOS SUITE — three intensities, deterministic

```
$ uv run pytest tests/test_failover_chaos.py -q
................                                                         [100%]
16 passed in 0.21s
```

Twenty requests per intensity, with a fixed fault schedule cycling through 529 / 429 / 500 /
timeout / connect error — 25%, 50%, and 100% of requests hitting a broken primary, with the
breaker's clock frozen so that once it trips the fallback carries everything.

```
light  (25%)   20 requests, 20 × HTTP 200, zero caller-visible 5xx
heavy  (50%)   20 requests, 20 × HTTP 200, zero caller-visible 5xx
brutal (100%)  20 requests, 20 × HTTP 200, zero caller-visible 5xx
```

One ledger row per request at every intensity, hop counts matching what the contexts
recorded, and every row `outcome: ok`. Mid-stream cuts are deliberately *outside* that
promise — they cannot be inside it, since the status line is spent — and are asserted
separately at four cut points including one *after* the last text delta, where the fragment
reads as a finished answer and only the missing `message_stop` gives it away: terminal
error event, 100%, no hop, fallback never called. That is §P8.H3's falsification condition
run forwards.

### THE SABOTAGE RUNS — eight, each reverted immediately

Green on the first attempt is when a suite deserves the most suspicion, so each claim was
tested by breaking the thing it protects. All eight were applied by script and reverted;
`diff` against pre-sabotage copies of all four touched files reports them identical, and the
1056 below is the re-run.

*Sabotage A — the splice guard is removed.* Only the direct executor test notices, and that
is **honest rather than weak**: in the shipped proxy the guard is structurally unreachable,
which is exactly why it needs a test that reaches past the proxy to it.

```
1 failed, 1048 passed, 2 deselected
FAILED tests/test_failover_boundary.py::test_the_executor_itself_refuses_to_retry_once_a_byte_is_out
```

*Sabotage B — every 4xx becomes retryable* (the permissive reading of "retry on errors"):

```
6 failed, 1043 passed, 2 deselected
FAILED tests/test_failover_matrix.py::test_a_client_error_from_upstream_is_forwarded_not_retried[400]
FAILED tests/test_failover_matrix.py::test_a_client_error_from_upstream_is_forwarded_not_retried[401]
FAILED tests/test_failover_matrix.py::test_a_client_error_from_upstream_is_forwarded_not_retried[403]
FAILED tests/test_failover_matrix.py::test_a_client_error_from_upstream_is_forwarded_not_retried[404]
FAILED tests/test_failover_matrix.py::test_a_client_error_from_upstream_is_forwarded_not_retried[422]
FAILED tests/test_provider_health.py::test_an_upstream_client_error_does_not_count_as_ill_health
```

*Sabotage C — backoff before every attempt, fresh providers included.* This is what every
retry library written for one endpoint does, and it costs latency on precisely the case
failover exists to make fast:

```
6 failed, 1043 passed, 2 deselected
FAILED tests/test_failover_backoff.py::test_moving_to_a_fresh_provider_costs_no_delay
FAILED tests/test_failover_backoff.py::test_a_wrapped_chain_pays_only_on_the_repeat
FAILED tests/test_failover_chaos.py::test_the_backoff_stays_inside_its_published_bound[light]
FAILED tests/test_failover_chaos.py::test_the_backoff_stays_inside_its_published_bound[heavy]
FAILED tests/test_failover_chaos.py::test_the_backoff_stays_inside_its_published_bound[brutal]
FAILED tests/test_failover_chaos.py::test_a_tripped_breaker_stops_a_retry_budget_being_spent_on_a_dead_provider
```

*Sabotage D — hops count only real attempts, so a breaker-skipped candidate vanishes.* The
hop count would drop back to zero the moment an outage became persistent — which is exactly
when somebody is reading it:

```
3 failed, 1046 passed, 2 deselected
FAILED tests/test_failover_chaos.py::test_every_request_is_metered_exactly_once_under_chaos[heavy]
FAILED tests/test_failover_chaos.py::test_every_request_is_metered_exactly_once_under_chaos[brutal]
FAILED tests/test_provider_health.py::test_a_tripped_primary_is_skipped_and_the_hop_is_still_counted
```

*Sabotage E — the breaker may skip the last candidate*, converting an upstream's outage into
the gateway's own:

```
2 failed, 1047 passed, 2 deselected
FAILED tests/test_failover_chaos.py::test_a_tripped_breaker_stops_a_retry_budget_being_spent_on_a_dead_provider
FAILED tests/test_provider_health.py::test_the_breaker_never_skips_the_last_candidate
```

*Sabotage F — the upstream timing mark is not rewound between attempts.* The one that would
be invisible in production: every failed-over row's `passthrough_overhead_ms` would quietly
include the whole failover sequence, and §P8.H2's headline number would be measuring the
wrong thing.

```
1 failed, 1048 passed, 2 deselected
FAILED tests/test_failover_ledger.py::test_the_upstream_mark_is_rewound_between_attempts
```

*Sabotage G — a recovered provider's window is not cleared*, so the failures that tripped
the breaker are still in it and the next blip re-trips:

```
1 failed, 1048 passed, 2 deselected
FAILED tests/test_provider_health.py::test_a_successful_probe_closes_the_breaker_and_clears_the_window
```

*Sabotage H — half-open admits everybody*, stampeding a provider at the moment it recovers:

```
1 failed, 1048 passed, 2 deselected
FAILED tests/test_provider_health.py::test_the_cooldown_admits_exactly_one_probe
```

### The keyless gate

```
$ make lint
uv run ruff check .
All checks passed!
uv run ruff format --check .
141 files already formatted

$ make typecheck
uv run mypy
Success: no issues found in 141 source files

$ make test
================ 1056 passed, 2 deselected, 1 warning in 15.57s ================

$ uv run pytest -m live -q --collect-only
2/1058 tests collected (1056 deselected) in 0.16s
```

**1056 passed, 0 skipped** — the Postgres and DynamoDB halves of all four contract suites
executed against the compose containers rather than skipping (H-012).

Per-file counts for the new and changed files:

```
32 tests/test_failover_matrix.py     16 tests/test_failover_chaos.py
20 tests/test_failover_backoff.py    11 tests/test_failover_ledger.py
17 tests/test_provider_health.py      8 tests/test_failover_boundary.py
17 tests/test_failover_config.py      8 tests/test_admin_providers.py
20 tests/test_routing.py (was 17)
```

**And the same 1056 pass with no torch installed at all** — CI's environment, reproduced
locally rather than trusted (the Phase 5 lesson):

```
$ uv sync                       # drop the `embed` extra: this is what CI has
$ uv run mypy
Success: no issues found in 141 source files
$ uv run pytest -q
================ 1056 passed, 2 deselected, 1 warning in 15.83s ================
$ uv sync --extra embed         # put it back
```

**H-012 still holds with the new tests.** A fresh clone with no stack up runs a smaller
suite *loudly*, and a stated-but-unreachable endpoint fails rather than skips:

```
$ docker compose stop db dynamodb
$ uv run pytest -q                          # endpoints unset — inferred
925 passed, 131 skipped, 2 deselected, 1 warning in 4.63s

$ DATABASE_URL=postgresql://…:5433/headroom uv run pytest -q tests/test_ledger_store.py
19 passed, 23 errors in 0.22s
```

### End to end through the real container, on a wiped volume, keyless

```
$ docker compose down -v && make up
 Container headroom-db-1 Healthy
 Container headroom-dynamodb-1 Healthy
 Container headroom-gateway-1 Healthy
docker compose exec -T gateway uv run --no-sync python -m headroom.db.migrate
applied 6 migration(s): 0001_tenants_and_virtual_keys, 0002_usage_ledger,
                        0003_ledger_budget_columns, 0004_rate_limits,
                        0005_response_cache, 0006_ledger_failover

### 2. every configured provider, its breaker, and the chains it sits in
anthropic      anthropic      closed  samples=0   anthropic:'claude-'->anthropic
mock           mock           closed  samples=0   anthropic:'mock-'->mock+mock_fallback openai:'mock-'->mock+mock_fallback
mock_fallback  mock           closed  samples=0   anthropic:'mock-'->mock+mock_fallback openai:'mock-'->mock+mock_fallback
vllm_a         openai_compat  closed  samples=0   openai:''->vllm_a+vllm_b
vllm_b         openai_compat  closed  samples=0   openai:''->vllm_a+vllm_b

### 3. the primary serves — and the response says nothing about failover
HTTP 200   x-headroom-failover-*: absent

### 4. the primary 529s -> the fallback answers, and the response says so
HTTP 200
  x-headroom-failover-from: mock
  x-headroom-failover-hops: 1
  body: {"id":"msg_mock_6ca20aa0926a68c6","type":"message","role":"assistant", …

### 5. a client error is NOT a hop: forwarded verbatim, fallback untouched
HTTP 400   x-headroom: {'x-headroom-error-source': 'upstream'}

### 6. a mid-stream cut: a terminal error event, and NO second provider
HTTP 200   x-headroom: absent
  last frames: event: error | data: {"type":"error","error":{"type":"api_error",
    "message":"mock upstream closed the connection after 5 chunk(s)"},
    "headroom":{"reason":"upstream_stream_cut","request_id":"hr_db15336f2c23…"}}

### 7. five failures -> the breaker trips
state=open samples=5 failures=3 ratio=0.6 consecutive=2
last_error=upstream_timeout reopen_in_s=9.977

### 8. tripped: the primary is skipped entirely, the hop is still counted
HTTP 200   x-headroom: {'x-headroom-failover-hops': '1', 'x-headroom-failover-from': 'mock'}

### 9. what the ledger says
   provider    | hops | failover_from |   failover_error    |       outcome       | st  |    usd_cost    |  cost_status
---------------+------+---------------+---------------------+---------------------+-----+----------------+---------------
 mock          |    0 |               |                     | ok                  | 200 | 0.000011500000 | priced
 mock_fallback |    1 | mock          | upstream_status_529 | ok                  | 200 | 0.000011500000 | priced
 mock          |    0 |               |                     | upstream_error      | 400 | 0.000000000000 | not_billable
 mock          |    0 |               |                     | upstream_stream_cut | 200 |                | usage_unknown
 mock_fallback |    1 | mock          | upstream_timeout    | ok                  | 200 | 0.000011500000 | priced
 mock_fallback |    1 | mock          | breaker_open        | ok                  | 200 | 0.000011500000 | priced
 mock_fallback |    1 | mock          | breaker_open        | ok                  | 200 | 0.000011500000 | priced
 mock_fallback |    1 | mock          | breaker_open        | ok                  | 200 | 0.000011500000 | priced
 mock_fallback |    1 | mock          | breaker_open        | ok                  | 200 | 0.000011500000 | priced
 mock_fallback |    1 | mock          | breaker_open        | ok                  | 200 | 0.000011500000 | priced
(10 rows)

### 10. two request log lines, each naming both providers
{"request_id":"hr_c25a95e89c02432db1f056321858215e","model":"mock-model-1",
 "provider":"mock_fallback","outcome":"ok","status":200,"upstream_status":200,
 "failover_hops":1,"failover_from":"mock","failover_error":"upstream_status_529",
 "failover_attempts":["mock:upstream_status_529","mock_fallback:ok"],
 "usd_cost":"0.000011500000","cost_status":"priced",
 "upstream_latency_ms":13.737,"ttft_ms":13.755,"passthrough_overhead_ms":0.019,"total_ms":13.756}
{"request_id":"hr_6bfafb4d2f07490fbf72e2d72e8e3287","model":"mock-model-1",
 "provider":"mock_fallback","outcome":"ok","status":200,"upstream_status":200,
 "failover_hops":1,"failover_from":"mock","failover_error":"breaker_open",
 "failover_attempts":["mock:breaker_open","mock_fallback:ok"],
 "usd_cost":"0.000011500000","cost_status":"priced",
 "upstream_latency_ms":4.438,"ttft_ms":4.457,"passthrough_overhead_ms":0.02,"total_ms":4.458}

### 11. DELETE the health record: back in rotation immediately
state=closed samples=0 total_failures=3 (the record survives, the verdict does not)
next request -> HTTP 200  x-headroom: absent
```

Five things in that output are the phase in miniature.

**Step 3 against step 4.** A request the primary served carries no failover headers at all —
not `hops: 0`, *absent* — so the overwhelmingly common response gained no bytes from this
phase. That matters because §P8.H2 measures exactly that path.

**Step 5.** A 400 is forwarded verbatim with `error-source: upstream` and no hop. The
fallback was never asked, because it would have said the same thing one round trip later.

**Step 6.** The cut produces a terminal error event and `mock_fallback` is never called —
the boundary, in the real container, with a healthy fallback one line away.

**Step 7's arithmetic is worth reading slowly.** `samples=5 failures=3 ratio=0.6`, and the
breaker trips on a window that is only 60% failures: the 400 in step 5 was recorded as a
**success** (a healthy provider correctly refusing a bad request) and the cut in step 6 as a
failure (an upstream that answered and then died). One request, one observation, and the
ratio rule catches the half-failing provider a consecutive-failures rule never would.

**And `passthrough_overhead_ms: 0.019` on a request whose `upstream_latency_ms` was 13.7.**
That is the timing rewind doing its job: the gateway's own cost is 19 microseconds, not the
thirteen milliseconds the failed attempt took. Without it, §P8.H2's column would silently
measure failover duration.

### The live demo — the operator's, with the exact commands

**Pre-flight (assumption A6).** Both instances must answer with the same checkpoint, and
each must be on its own card:

```bash
nvidia-smi --query-gpu=index,uuid,name,memory.used,memory.free --format=csv
for p in 8010 8011; do curl -sS "http://localhost:$p/v1/models" | head -c 80; echo; done
```

*Correct output:* two 4090s each holding ~23 GB, and two identical
`cyankiwi/Qwen3.6-27B-AWQ-INT4` listings. If both models are on one card, stop — see
`docs/vllm.md`, and relaunch with `--gpus all -e CUDA_VISIBLE_DEVICES=<uuid>`.

**Which instance is the primary.** `config/routing.yaml` routes every OpenAI-dialect model
to **`vllm_a`** with **`vllm_b`** as its fallback. `vllm_a` is `VLLM_BASE_URL`, defaulting
to `http://localhost:8010`; `vllm_b` is `VLLM_B_BASE_URL`, defaulting to
`http://localhost:8011`. **The container to kill is the one publishing 8010.**

```bash
docker ps --format '{{.Names}}\t{{.Ports}}' | grep 8010     # confirm its name first
```

*(At the time of writing that container is unnamed — `busy_cartwright`. Relaunching it with
`--name vllm-a` per `docs/vllm.md` makes step 3 unambiguous, which matters on camera.)*

**Setup — the gateway on the host**, so `localhost:8010/8011` mean the instances:

```bash
cd ~/code/headroom
make up                                    # postgres + dynamodb + migrations
export HEADROOM_ADMIN_TOKEN=…              # leading space, per invariant 3
uv run uvicorn headroom.api.main:app --port 8090 &

GW=http://localhost:8090
TENANT=$(curl -sS -X POST $GW/admin/tenants -H "Authorization: Bearer $HEADROOM_ADMIN_TOKEN" \
  -H 'content-type: application/json' -d '{"name":"gpu-failover-demo"}' | jq -r .id)
KEY=$(curl -sS -X POST $GW/admin/keys -H "Authorization: Bearer $HEADROOM_ADMIN_TOKEN" \
  -H 'content-type: application/json' -d "{\"tenant_id\":\"$TENANT\",\"name\":\"demo\"}" | jq -r .key)
MODEL=cyankiwi/Qwen3.6-27B-AWQ-INT4
```

**(a) A request served normally by the primary.**

```bash
curl -sS -D- -o /tmp/a.json -X POST $GW/v1/chat/completions \
  -H "Authorization: Bearer $KEY" -H 'content-type: application/json' \
  -d "{\"model\":\"$MODEL\",\"max_tokens\":64,\"messages\":[{\"role\":\"user\",\"content\":\"Say: headroom ok\"}]}" \
  | grep -Ei '^(HTTP|x-headroom)'
```

*Correct output:* `HTTP/1.1 200 OK`, an `x-headroom-request-id`, and **no
`x-headroom-failover-*` header at all**. A request the primary served has no story to tell.

```bash
curl -sS $GW/admin/providers -H "Authorization: Bearer $HEADROOM_ADMIN_TOKEN" \
  | jq -r '.[] | select(.name|startswith("vllm")) | "\(.name) \(.state) ok=\(.total_successes) fail=\(.total_failures) p50=\(.p50_latency_ms)"'
```

*Correct output:* `vllm_a closed ok=1 fail=0 p50=<some hundreds of ms>` and
`vllm_b closed ok=0 fail=0 p50=null`.

**(b) Kill the primary, mid-conversation-flow.**

```bash
docker kill vllm-a          # or: docker kill $(docker ps -q --filter publish=8010)
```

*Correct output:* the container name, and `curl http://localhost:8010/v1/models` now fails
with connection refused. Any request that was in flight when the kill landed gets one of
two honest answers depending on where it was: a **streamed** one that had already sent
bytes ends in an `event: error` with `upstream_stream_cut` — no splice, ever — while a
**non-streamed** one whose body was still being read fails over silently and returns 200.

**(c) The next request fails over.**

```bash
curl -sS -D- -o /tmp/b.json -X POST $GW/v1/chat/completions \
  -H "Authorization: Bearer $KEY" -H 'content-type: application/json' \
  -d "{\"model\":\"$MODEL\",\"max_tokens\":64,\"messages\":[{\"role\":\"user\",\"content\":\"Say: headroom ok\"}]}" \
  | grep -Ei '^(HTTP|x-headroom)'
```

*Correct output:*

```
HTTP/1.1 200 OK
x-headroom-failover-hops: 1
x-headroom-failover-from: vllm_a
```

The ledger row, which is the evidence the gate asks for:

```bash
docker compose exec -T db psql -U headroom -d headroom -c \
  "SELECT provider, failover_hops, failover_from, failover_error, outcome, status_code
     FROM usage_ledger ORDER BY started_at DESC LIMIT 3"
```

*Correct output:* the newest row reads
`vllm_b | 1 | vllm_a | upstream_unavailable | ok | 200`.

And the log line naming **both** providers — the one to screenshot:

```bash
# the gateway is in the foreground shell; or, if backgrounded, tail its output
grep failover_attempts /tmp/gateway.log | tail -1 | jq .
```

*Correct output, the fields that matter:*

```json
{"provider": "vllm_b", "failover_hops": 1, "failover_from": "vllm_a",
 "failover_error": "upstream_unavailable",
 "failover_attempts": ["vllm_a:upstream_unavailable", "vllm_b:ok"],
 "outcome": "ok", "status": 200}
```

**(d) Watch the breaker trip, then bring the instance back.**

```bash
for i in $(seq 1 5); do
  curl -sS -o /dev/null -w '%{http_code} ' -X POST $GW/v1/chat/completions \
    -H "Authorization: Bearer $KEY" -H 'content-type: application/json' \
    -d "{\"model\":\"$MODEL\",\"max_tokens\":16,\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}]}"
done; echo
curl -sS $GW/admin/providers/vllm_a -H "Authorization: Bearer $HEADROOM_ADMIN_TOKEN" | jq \
  '{state, samples, failures, failure_ratio, consecutive_failures, last_error, reopen_in_s}'
```

*Correct output:* five `200`s — **zero failed requests, which is the claim** — and then
`"state": "open"`, `"last_error": "upstream_unavailable"`, `"reopen_in_s"` counting down
from 10. From here on `vllm_a` is skipped rather than tried, and the rows still read
`failover_hops: 1` with `failover_error: breaker_open`.

```bash
# restart instance A exactly as docs/vllm.md documents it, then wait ~10s and ask again
curl -sS -o /dev/null -w '%{http_code}\n' -X POST $GW/v1/chat/completions … # as above
curl -sS $GW/admin/providers/vllm_a -H "Authorization: Bearer $HEADROOM_ADMIN_TOKEN" | jq .state
```

*Correct output:* `200`, then `"closed"` — one probe went through, succeeded, and closed the
breaker. The next ledger row reads `vllm_a | 0 | | | ok | 200`. If the operator would rather
not wait out the cooldown:

```bash
curl -sS -X DELETE $GW/admin/providers/vllm_a/health -H "Authorization: Bearer $HEADROOM_ADMIN_TOKEN" | jq .state
```

**Capture** everything above into `docs/evidence/p6-failover/` per that directory's README,
**before** the next `make test` — the Postgres contract suite truncates the control plane
and `usage_ledger` references it, so the rows go with it (H-029's caveat, unchanged).

**Cost: $0.00.** Both models are the operator's own, on the operator's own cards.

**Assumed-facts register (§0.4)**

- **A6 — VERIFIED.** *The two local vLLM instances still serve with `--tool-call-parser
  qwen3_xml --reasoning-parser qwen3` per the operator's known-good config.* Both answered
  `/v1/models` with `cyankiwi/Qwen3.6-27B-AWQ-INT4` on 2026-08-09, launched with those exact
  flags (read back from `docker inspect`, not assumed), on ports 8010 and 8011, one per
  4090. The GPU-placement half — which `docs/vllm.md` had carried as **UNTESTED** since
  Phase 2 and which Phase 2 explicitly deferred to this gate — is now VERIFIED: `--gpus
  all` + `CUDA_VISIBLE_DEVICES=<UUID>` pins to the named card, and Docker's own
  `--gpus device=` still does not.
- **A4, A5 — still VERIFIED, and re-proved under the feature most likely to break them.**
  Failover is the first thing in the project that can hand a caller a *different provider's*
  bytes, and the fidelity guarantee survives because nothing about the passthrough changed:
  a chain member's response is forwarded by the same code, and the one implementation that
  would have violated it (splicing) is the sabotage rather than the design. All 1056 tests
  that prove passthrough fidelity run through the executor.
- **A1, A2, A3, A7** — not due at this gate, none touched.

**Spend** — $0.00. Every test ran on the MockProvider, the container demo talked to nothing
outside the compose network, and the vLLM pre-flight ran on the operator's own GPUs.

### CI

CI on PR-6 ([run 31340486751](https://github.com/sergioavilax/headroom/actions/runs/31340486751)),
all three jobs green on the first run, no annotations:

```
$ gh run view 31340486751 --json conclusion,jobs
success
lint + typecheck: success
pytest (postgres + dynamodb-local service containers): success
gateway image builds and serves: success

lint + typecheck | All checks passed!
lint + typecheck | Success: no issues found in 141 source files
pytest (…service containers) | ===== 1056 passed, 2 deselected, 1 warning in 27.93s =====
pytest (…service containers) | tests/test_failover_boundary.py::test_the_sabotage_serves_a_frankenstein_answer PASSED
pytest (…service containers) | tests/test_failover_boundary.py::test_the_shipped_gateway_refuses_the_same_splice PASSED
pytest (…service containers) | tests/test_failover_boundary.py::test_the_executor_itself_refuses_to_retry_once_a_byte_is_out PASSED
pytest (…service containers) | tests/test_failover_chaos.py::test_no_caller_sees_a_5xx_at_any_intensity[light] PASSED
pytest (…service containers) | tests/test_failover_chaos.py::test_no_caller_sees_a_5xx_at_any_intensity[heavy] PASSED
pytest (…service containers) | tests/test_failover_chaos.py::test_no_caller_sees_a_5xx_at_any_intensity[brutal] PASSED
pytest (…service containers) | tests/test_failover_chaos.py::test_every_request_is_metered_exactly_once_under_chaos[brutal] PASSED
Migrations apply, twice is a no-op | migrations: up to date, nothing to apply
gateway image builds and serves | gateway healthy
```

**1056 passed, 0 skipped in CI** — `grep -c SKIPPED` over the whole log returns `0`, so the
Postgres and DynamoDB halves of all four contract suites executed against the service
containers rather than skipping (H-012).

**BUILD_PLAN §P6's "chaos suite green in CI" is now a property of every pull request.** The
whole of this phase is keyless: three fault intensities against a mock chain, the splice
sabotage, the boundary pair, and the backoff schedule all run on a runner with no GPU, no
provider key, and no network to anything — which is what makes §P8.H3's mock-chain half
reproducible by a stranger with a clone, and the two-GPU half the only part that needs the
operator's desk.

The `image` job is worth one line again: it builds the container and smokes `/healthz` with
no Postgres, no DynamoDB, and no `embed` extra anywhere in the job. Phase 6 added a fifth
provider to the shipped routing config and a startup dialect check on top of it, and the
gateway still boots with nothing reachable — which is what Phase 9's container needs before
its secrets arrive.

---

## Phase 7 — The dashboard (2026-08-09)

Branch `claude/p7-dashboard`, GitHub #9. BUILD_PLAN §P7's words are the spec: *"Next.js,
the operator's design language — true black `#000000`, cool zinc surfaces, mono for
numbers … Surfaces: **Overview** … **Requests** … **Tenants & Keys** … **Limits** …"*

**Shipped**

- **The console** (`ui/`) — Next.js 16 App Router, TypeScript, dark only, true black on
  cool zinc with mono numerals. Seven views, and every admin surface built in P2–P6 has
  one: **Overview**, **Live traffic**, **Requests**, **Tenants & keys**, **Limits &
  budgets**, **Cache**, **Providers**.
- **It is a client of `/admin/*` and nothing else** (**H-054**). No `DATABASE_URL`, no
  DynamoDB endpoint, no client for either; the `ui` service's whole compose environment is
  one URL. A view that needed a number the API did not publish caused the API to publish
  it — three times, all reads of columns that already existed — rather than causing a
  query. `usage_ledger` has thirty-nine columns and five `cost_status` values whose
  distinctions are the point of Phase 3, and a second reader would re-decide all of them
  silently.
- **The admin API additions, tested on both store implementations:**
  - **`GET /admin/usage/series`** — the ledger in `minute`/`hour`/`day` buckets, oldest
    first, for the charts. `date_trunc` is applied *in UTC* on both sides (`started_at AT
    TIME ZONE 'UTC'`, then back) so the grain does not depend on a Postgres session
    setting nothing in this repo controls. `limit` keeps the **newest** buckets — a chart
    that dropped its newest point would be worse than one missing its oldest — and empty
    buckets are absent rather than zero, because gap-filling belongs to whoever knows the
    x-domain being drawn.
  - **Eight counters on `/admin/usage/totals`** — the five cache dispositions, the avoided
    cost, **the hits it could not price**, and the failover count. On the existing
    aggregate rather than in a new endpoint: the Overview asks one question and four round
    trips would let the four answers disagree under live traffic.
  - **Three cache fields on a ledger row** — `cache_avoided_usd`, `cache_similarity`,
    `cache_source_request_id`. The last turns "this was a semantic hit" into "…and here is
    the request whose answer you were served", which is the only way a human audits a
    similarity score after the fact — the same provenance §P8.H1 measures silent wrong
    answers with.
- **`cache_avoided_unknown`, and why it exists.** Skipping a NULL and adding it as zero
  produce the *identical* sum, always — zero is the additive identity — so the savings sum
  alone can never say a saving was left out. Only a count can. Without it the console's
  "avoided" tile is a confident understatement the moment one unpriced model enters a
  tenant's traffic. This is H-025's rule (*a total says how much of the picture it is
  missing*) applied to Phase 5's column, and it exists because a sabotage run **failed to
  fire** and the reason turned out to be that the property as first written was not one.
- **The admin token is typed in, never deployed** (**H-055**). The operator enters the root
  token in a sign-in screen; the console's server probes it against `GET /admin/tenants`,
  distinguishes 200 / 401 / 503-because-the-gateway's-own-token-is-unset, and exchanges it
  for an `httpOnly`, `SameSite=Strict` session cookie. `lib/session.ts` and `lib/admin.ts`
  import `server-only`, so touching them from client code is a **build error** — invariant
  3 enforced by the compiler rather than by a reviewer. The `ui` service therefore holds no
  secret at all, not even by reference: nothing to rotate, nothing to leak from a `docker
  inspect`, nothing to scrub from a bundle.
- **It polls; it does not stream** (**H-056**). 2 s on the live view and provider health,
  5 s on the overview and the meters, 15 s on the control-plane tables. A hidden tab does
  not poll at all. Refetch never flashes a skeleton — including across a filter change.
  And `fetchedAt` is the clock: every window a view draws ends when the data was *read*,
  never at `Date.now()` during render, which keeps rendering pure and stops a chart drawing
  a four-second gap that looks like an outage.
- **The charts are hand-rolled inline SVG** (**H-057**) — five forms in one file, to the
  mark spec: bars capped at 24 px with a 4 px rounded *data* end and a square baseline, a
  2 px surface **gap** between touching fills rather than a stroke, solid hairline
  gridlines one step off the surface, no number on every point, text in ink tokens rather
  than the series colour. The palette is **computed, not chosen**: five validated dark
  categorical steps checked against this console's own surface (`#131417`) — worst-pair
  CVD ΔE 8.4, worst-pair normal-vision ΔE 19.3, all ≥ 3:1 — and a colour follows the
  **entity**, so a provider going silent mid-kill never repaints the one that took over.
- **The channel strip**, the one flourish §P7 licenses, and the metaphor is load-bearing:
  settled spend and live reservations stack from the bottom like signal, the hairline
  across the top is the cap, and the gap between them is headroom. That is BUILD_PLAN §0.2
  rule 5 drawn — the gate compares **committed** spend, landed *plus* reserved, and a
  dashboard rendering only the landed bar would be D-019 with a nicer font.
- **`make seed`** (`scripts/seed_demo.py`) — four tenants with genuinely different
  profiles and ~74 requests, so every view has something real to show. **It writes no
  SQL**: it configures through `/admin/*` and generates traffic through `/v1/*` against the
  MockProvider, so every figure on screen is a figure the gateway really computed. Costs
  $0.00. Re-runnable: it resets each tenant's cache, buckets, and budget first, through the
  incident-response routes the admin API already ships, so the second run looks like the
  first.
- **The `ui` service joins `docker-compose.yml`** — the service §0.5 has said "later the
  ui" about since Phase 0 — on port **3001** (Backline holds 3000; H-006's argument one
  service along), gated on the gateway being healthy, with a liveness-only healthcheck of
  its own that deliberately does *not* probe its upstream.
- **Tests: 1092 keyless Python** (1056 → 1092) plus **28 console unit tests and 7 browser
  tests**. The console's unit tests run on **`node --test` with native TypeScript
  stripping** — zero test dependencies, no transform, no config — and the browser smoke
  runs against a Node stub of `/admin/*` and against the **standalone server the image
  actually ships**, not `next start` (**H-058**).
- **CI gains two jobs and a second image smoke**, all keyless: `ui` (eslint, `tsc
  --noEmit`, unit tests, `next build`) and `ui-e2e` (Chromium against the stub). `next
  build` reaches no gateway, no database, and no network, which is what lets those jobs
  need no services and no secrets.
- **Docs**: H-054 … H-058 in `docs/DECISIONS.md`; `docs/evidence/p7-dashboard/` with the
  capture list for the watched kill demo; `ui/README.md`; a *The console* section in the
  README; `.env.example` documents `UI_PORT` and `HEADROOM_GATEWAY_URL` **and states that
  there is deliberately no admin-token variable for the console**.

**Deferred**

- **The watched kill demo itself** — the hero GIF. Everything it needs is in place and
  verified: the console ships in compose, `make seed` fills it, the P6 chain is untouched.
  The `docker kill` is the operator's, because it takes down a container that spends
  minutes reloading a 27B checkpoint. The commands, in order, with what correct output
  looks like at each step, are in *The watched kill demo* below;
  `docs/evidence/p7-dashboard/` is waiting with a capture list.
- **Screenshots of the seven views against the seeded stack.** The console was verified
  rendering by the Playwright harness against the stub (which carries a failover, a
  semantic hit, a budget at 83%, and an open breaker), and its figures were cross-checked
  against `psql` on the seeded stack below. What has not happened is a human looking at the
  seeded stack in a browser — the session that built this deliberately did not sign in as
  the operator, and the screenshots belong with the demo.
- **A live/streaming transport**, deliberately never (H-056), not deferred.
- **Visual-regression snapshots.** Genuinely useful for a design this specific, and a
  stream of false failures from font rendering across machines. The operator's screenshots
  are the visual record instead.
- **Concurrency limits, per-key budgets, prompt-cache tier pricing, latency-based
  breaker tripping** — still deferred from Phases 4, 4b, 3, and 6; untouched here.

**Deviations**

1. **Two new top-level directories: `ui/` and `scripts/`.** `ui/` is in §0.5's repo map and
   is simply arriving; **`scripts/` is not**, and is an amendment. `make seed` had to live
   somewhere, and the honest shape for a thing that drives the *public HTTP API* is a
   client script rather than a module inside the gateway package — putting it in
   `headroom/` would have implied it was part of the service. §0.5's map is amended in this
   PR to list both.
2. **`migrations/` is untouched.** Every figure this phase publishes is a read of a column
   that already existed. That is worth stating because the four previous phases each added
   columns and each had to explain why (H-024's closing remark, argued with four times);
   this one had nothing to add.
3. **The `LedgerStore` interface gained an abstract method** (`series`). Additive to the
   interface, and therefore a required change in three places — both implementations and
   the `BrokenStore` double in `tests/test_ledger_writer.py` — which is H-021's intended
   friction working as designed.
4. **`TotalsView` and `LedgerRowView` grew fields.** Additive to a response model: existing
   clients see everything they saw before. No existing test changed.
5. **The plan's "SSE-fed live tiles where it's cheap" is not built** (H-056). The console
   polls. Argued in full in the entry rather than skipped: what a push channel would buy
   here is under two seconds on a figure a human is watching, and what it would cost is a
   second transport, a fan-out story for Phase 9's several tasks, and a reconnect path only
   exercised when something is already wrong.
6. **The `frontend-design` skill `CLAUDE.md` mandates is not present in this session's
   skill set.** Stated rather than quietly skipped. What was used instead: the operator's
   design language as BUILD_PLAN §P7 and `CLAUDE.md` state it (true black, cool zinc, mono
   numerals, restrained accent, no gradients-for-decoration), and the `dataviz` skill for
   the chart layer — which is where its palette validator, mark specs, and anti-pattern
   catalogue come from. The palette was **run through the validator** rather than eyeballed;
   the numbers are in H-057.
7. **Node 24 and npm are new build-time dependencies** — for the console only. Neither the
   gateway image nor `make test` acquires them: `make ui-check` and `make ui-e2e` run in
   containers built from `ui/`, so a contributor with no host Node can still run every
   check in this repo.
8. **Additions the plan's Phase 7 text does not enumerate**, all additive: the **Live
   traffic** view (the brief's addition, and what makes the kill demo legible on screen);
   the **Cache** and **Providers** views (the brief's, reading the P5 and P6 admin
   surfaces); `make seed`, `make ui-check`, `make ui-e2e`; and `cache_avoided_unknown`,
   which a sabotage run demanded.

---

**Gate** — *dashboard renders a seeded compose environment truthfully (numbers
cross-checked against psql); Playwright smoke; the P6 kill demo re-run once watching the
dashboard.* Plus the session brief's additions: reads through the real admin API only,
admin-token handling decided and logged, the ui in compose, keyless CI, and a seed target.

### THE CROSS-CHECK — "truthfully", checked rather than asserted

`make up && make seed`, then the figures the Overview renders beside the same aggregates
computed by the database. The console's numbers come from `GET /admin/usage/totals`; the
comparison numbers come from `psql`, which has never heard of the API.

```
$ curl -sS localhost:8080/admin/usage/totals -H "Authorization: Bearer $HEADROOM_ADMIN_TOKEN"
tenant      reqs          usd_cost  hit_e  hit_s           avoided  f/o  err
90495565      25    0.000276000000      0      0                 0    2    1
acd3dea2      27    0.000184000000      9      0    0.000103500000    0    2
976a8303      13    0.000092000000      0      0                 0    4    5
cb9b4be4       9    0.000034500000      0      0                 0    0    6

$ docker compose exec -T db psql -U headroom -d headroom -f - < crosscheck.sql
      name      | requests |    usd_cost    | hits_exact | hits_semantic |    avoided     | failed_over | errored
----------------+----------+----------------+------------+---------------+----------------+-------------+---------
 backline       |       25 | 0.000276000000 |          0 |             0 |              0 |           2 |       0
 atlas-research |       27 | 0.000184000000 |          9 |             0 | 0.000103500000 |           0 |       2
 probe          |       13 | 0.000092000000 |          0 |             0 |              0 |           4 |       5
 nightshift     |        9 | 0.000034500000 |          0 |             0 |              0 |           0 |       6
(4 rows)
```

Every column agrees, to the last picodollar. The series endpoint agrees with `date_trunc`
too — same buckets, same counts, same sums:

```
$ curl -sS "localhost:8080/admin/usage/series?bucket=minute&limit=10" …
2026-08-10T04:36:00Z    74 requests    0.000517500000    7 hits    5 f/o
series total: 74 requests

$ psql -c "SELECT date_trunc('minute', started_at AT TIME ZONE 'UTC'), count(*), sum(usd_cost) …"
       bucket        | count |      sum
---------------------+-------+----------------
 2026-08-10 04:36:00 |    74 | 0.000517500000
(1 row)
```

And the seed leaves an outcome mix worth looking at — which is the other half of
"truthfully", because a dashboard that only ever renders `ok` has not been tested:

```
$ psql -c "SELECT outcome, count(*) FROM usage_ledger GROUP BY 1 ORDER BY 2 DESC"
       outcome       | count
---------------------+-------
 ok                  |    60
 rate_limited        |     6      <- nightshift, 3/min, hammering
 upstream_error      |     3
 budget_exceeded     |     2      <- atlas-research, at its cap
 upstream_stream_cut |     2
 upstream_timeout    |     1
(6 rows)
```

### THE BROWSER SMOKE

Seven tests, Chromium, against a Node stub of `/admin/*` and the standalone server the
image ships. No compose stack, no Postgres, no DynamoDB, no provider, no key.

```
$ make ui-e2e
docker build --target e2e -t headroom-ui-e2e ./ui
docker run --rm headroom-ui-e2e npm run e2e

Running 7 tests using 7 workers
  ✓ an unauthenticated visitor is asked for the token and shown nothing else
  ✓ a wrong token is refused and does not sign anybody in
  ✓ the session cookie is httpOnly, so the page cannot read the token back
  ✓ the overview renders the numbers the admin API reported
  ✓ the live view names the upstream that served each request, and the hop
  ✓ a request's detail shows what it cost, what the budget held, and what failed over
  ✓ signing out clears the session
  7 passed (1.6s)
```

The first one is the one that matters. A console whose views rendered before a session
existed would be an unauthenticated tenant-and-key CRUD on any deployment that published
its port — H-019's failure mode, one layer up.

### THE SABOTAGE RUNS — five, and two of them found real gaps

Green on the first attempt is when a suite deserves the most suspicion. Each claim was
tested by breaking the thing it protects. **Two sabotages did not fire, and both were
tests that could not have caught their bug** — the fixes are in *Shipped* above.

*Sabotage A — the series keeps the OLDEST buckets when it has to choose.* A chart that
silently drops its newest point, which is the wrong end to lose:

```
2 failed, 64 passed
FAILED tests/test_ledger_store.py::test_a_series_keeps_the_newest_buckets_when_it_has_to_choose[memory]
FAILED tests/test_ledger_store.py::test_a_series_keeps_the_newest_buckets_when_it_has_to_choose[postgres]
```

*Sabotage B — an unknown avoided cost is summed as zero rather than skipped.* **It did not
fire: 95 passed.** And it cannot, ever — zero is the additive identity, so the two
behaviours produce the identical sum by arithmetic. The test as first written asserted a
property that was not one. The fix is `cache_avoided_unknown`: a *count* beside the sum,
because a count is the only thing that can say a saving was left out. Re-run against the
counter (**B2 — the total stops reporting the savings it could not add up**):

```
2 failed, 64 passed
FAILED tests/test_ledger_store.py::test_a_total_counts_the_savings_it_could_not_add_up[memory]
FAILED tests/test_ledger_store.py::test_a_total_counts_the_savings_it_could_not_add_up[postgres]
```

*Sabotage C — the console parses money with `parseFloat` instead of picodollars.* D-017 in
the last mile, and **it did not fire either: 27 passed.** The reason is worth keeping:
`Math.round(parseFloat(x) * 1e12)` lands on the right integer for every value under about
$9,007 — 2^53 picodollars — so a suite full of $0.0000115 assertions cannot see it. Above
that the double has fewer bits than the number needs. One assertion at budget scale closes
it, and re-running the same sabotage:

```
28 tests, 1 fail
✖ a budget-sized amount stays exact past the point a double stops being one
```

*Sabotage D — the admin proxy forwards any `/admin` path*, turning the console from a
client of seven surfaces into a general-purpose authenticated relay:

```
28 tests, 1 fail
✖ a prefix nobody allow-listed is refused
```

*Sabotage E — a series colour follows its rank instead of its entity.* Recolour-on-filter:
the reader who learned "vllm_a is blue" is now misled, at the exact moment they are
watching a provider die:

```
28 tests, 1 fail
✖ a sixth series does not invent a hue
```

All five were applied by script and restored **from file copies, not from `git`** — the
first attempt used `git checkout --`, which reverted an hour of uncommitted work in `ui/`
along with the sabotage, because `ui/` was not tracked yet. Recorded because it is a
mistake worth not repeating. Every file was diffed against its pre-sabotage copy
afterwards:

```
=== every file identical to its pre-sabotage copy? ===
  identical  headroom/db/memory.py
  identical  headroom/db/ledger.py
  identical  ui/lib/format.ts
  identical  ui/lib/proxy.ts
  identical  ui/lib/series.ts
```

### The keyless gate

```
$ make lint
uv run ruff check .
All checks passed!
uv run ruff format --check .
142 files already formatted

$ make typecheck
uv run mypy
Success: no issues found in 141 source files

$ make test
================ 1092 passed, 2 deselected, 1 warning in 18.05s ================

$ uv run pytest -m live -q --collect-only
2/1094 tests collected (1092 deselected) in 0.17s
```

**1056 → 1092.** The 36 are `tests/test_ledger_store.py` (42 → 66: the series contract and
the new counters, over both implementations) and `tests/test_admin_usage.py` (17 → 29: the
series route, the cache fields on a row, the disposition counters). `test_ledger_writer.py`
is unchanged in count; its `BrokenStore` grew the new abstract method.

The console's own checks, in a container so no host Node is needed:

```
$ make ui-check
docker build --target check -t headroom-ui-check ./ui
docker run --rm headroom-ui-check npm run check

> headroom-ui@0.1.0 lint
> eslint .

> headroom-ui@0.1.0 typecheck
> tsc --noEmit

> headroom-ui@0.1.0 test
> node --test "tests/unit/*.test.ts"
ℹ tests 28
ℹ pass 28
ℹ fail 0
ℹ duration_ms 109.671566
```

**H-012 still holds with the new tests.** A fresh clone with no stack up runs a smaller
suite *loudly*, and a stated-but-unreachable endpoint fails rather than skips:

```
$ docker compose stop db dynamodb
$ uv run pytest -q                          # endpoints unset — inferred
949 passed, 143 skipped, 2 deselected, 1 warning in 3.27s

$ DATABASE_URL=postgresql://…:5433/headroom uv run pytest -q tests/test_ledger_store.py
31 passed, 35 errors in 0.88s
```

### End to end through the real containers

```
$ make up
 Container headroom-db-1 Healthy
 Container headroom-dynamodb-1 Healthy
 Container headroom-gateway-1 Healthy
 Container headroom-ui-1 Healthy
docker compose exec -T gateway uv run --no-sync python -m headroom.db.migrate
migrations: up to date, nothing to apply

NAME                  IMAGE              SERVICE    STATUS                  PORTS
headroom-gateway-1    headroom-gateway   gateway    Up 11 seconds (healthy) 0.0.0.0:8080->8000/tcp
headroom-ui-1         headroom-ui        ui         Up 5 seconds (healthy)  0.0.0.0:3001->3000/tcp

### the console is alive, and its health is its own
$ curl -sS localhost:3001/api/healthz
{"status":"ok"}

### and it will not proxy anything without a session
$ curl -sS localhost:3001/api/admin/usage/totals
{"error":{"type":"no_session","message":"sign in with the root admin token"},
 "headroom":{"reason":"no_session","source":"console"}}

$ make seed
seeding http://localhost:8080 — 4 tenants, ~74 requests

  backline         the sibling project's agents — tool-heavy, so nothing caches
  atlas-research   a read-heavy analytics workload — repeats itself, so the cache pays
  nightshift       a batch job that does not know when to stop — rate limited
  probe            synthetic monitoring — small, uncapped, and unlucky

74 requests in 0.5s
```

Two things in that output are the phase in miniature. The console's healthcheck answers
without touching the gateway — compose already waits for the gateway to be healthy, and a
console that reported its upstream's health as its own would take a working container down
during a restart. And an unauthenticated call to its admin proxy is refused *by the
console*, before any credential exists to attach: the session is the gate, and it is
checked in the root server layout so a route added by a later phase is behind it by
construction.

### The watched kill demo — the operator's, with the exact commands

This is the third clause of the gate and the one that needs two 4090s. It is the P6 demo
(PHASE_LOG → Phase 6 → *The live demo*) with the console on screen; the capture list is
`docs/evidence/p7-dashboard/README.md`.

**Pre-flight (A6), unchanged from P6.** Both instances answering, one per card:

```bash
nvidia-smi --query-gpu=index,uuid,name,memory.used --format=csv
for p in 8010 8011; do curl -sS "http://localhost:$p/v1/models" | head -c 80; echo; done
```

**Setup — the gateway on the host, the console pointed at it.** The gateway must run on
the host so `localhost:8010/8011` mean the two instances; the console then needs the
host's address rather than the compose service name:

```bash
cd ~/code/headroom
make up                                    # postgres + dynamodb + migrations
export HEADROOM_ADMIN_TOKEN=…              # leading space, per invariant 3
uv run uvicorn headroom.api.main:app --port 8090 > /tmp/gateway.log 2>&1 &

# point the console at the host gateway and restart just that service
HEADROOM_GATEWAY_URL=http://host.docker.internal:8090 docker compose up -d ui

# a tenant, a key, and enough traffic that the views are not empty
HEADROOM_GATEWAY_URL=http://localhost:8090 make seed
```

Open **http://localhost:3001**, sign in with `$HEADROOM_ADMIN_TOKEN`, and go to **Live
traffic**.

**(a) Steady state — the shot before the kill.** Drive the two GPUs with a slow loop, in a
second terminal:

```bash
GW=http://localhost:8090
KEY=…                                       # the seed prints one, or mint one
MODEL=cyankiwi/Qwen3.6-27B-AWQ-INT4
while true; do
  curl -sS -o /dev/null -X POST $GW/v1/chat/completions \
    -H "Authorization: Bearer $KEY" -H 'content-type: application/json' \
    -d "{\"model\":\"$MODEL\",\"max_tokens\":32,\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}]}"
  sleep 2
done
```

**Watch here:** the *Requests by upstream* chart fills with bars in one colour —
`vllm_a`'s. Both provider tiles read `closed`. `Failed over` reads **0**.
→ `01-preflight.png`

**(b) Kill the primary.**

```bash
date -u +%FT%TZ | tee -a /tmp/kill.txt      # bracket the screenshots
docker kill vllm-a                          # or: docker kill $(docker ps -q --filter publish=8010)
```

**Watch here — this is the hero shot.** Within one poll (2 s) the stack changes colour:
new bars arrive in `vllm_b`'s. `Failed over` starts climbing. **`Caller-visible 5xx` stays
at 0** — that is the claim, on screen, while a GPU is dead. Rows in *Recent requests* gain
a badge reading `← vllm_a · upstream_unavailable`.
→ `03-shift.png`, and the GIF should span from here back through (a).

**(c) The breaker trips.** After four or five failures, **Providers** shows `vllm_a` as
`open` with `probes in 9.8s` counting down, its last error `upstream_unavailable`, and the
chain `openai:* → vllm_a → vllm_b` beside it. From here the rows read
`failover_error: breaker_open` rather than `upstream_unavailable` — the two stay
distinguishable, which is why a breaker-skipped candidate still counts as a hop.
→ `04-breaker-open.png`, `05-failover-rows.png`

**(d) One request's whole story.** In **Requests**, click a failed-over row. The drawer
shows what it cost *and the rates it was billed at*, what the budget held and what the hold
settled at, what the cache did, and the hop — six phases on one screen.
→ `06-request-detail.png`

**(e) Bring it back.** Restart instance A exactly as `docs/vllm.md` documents it, wait out
the cooldown (or `DELETE /admin/providers/vllm_a/health` from the **Providers** view's
*Clear health* button), and watch the tile go `closed` and the stack return to `vllm_a`'s
colour.
→ `07-recovered.png`

**(f) The cross-check.** With the demo still on screen, run the psql comparison above and
capture it beside the Overview. → `08-psql-crosscheck.txt`

**Capture everything before the next `make test`** — the Postgres contract suite truncates
the control plane and `usage_ledger` references it, so the rows behind every screenshot go
with them (H-029's caveat, unchanged).

**Cost: $0.00.** Both models are the operator's own, on the operator's own cards.

**Assumed-facts register (§0.4)** — nothing was due at this gate, and none of A1–A7 was
touched. One adjacent fact was established in passing and is worth the register's
discipline: **`next build` needs no gateway, no database, and no network**, which is what
lets CI build and smoke the console image with no services and no secrets — the same
property H-021's lazy pool gives the gateway, one language over.

**Spend** — $0.00. Every request in this phase went to the MockProvider; the console talks
to nothing but the gateway, and the browser smoke talks to nothing but a Node stub.

### CI

CI on PR-7 ([run 31356382482](https://github.com/sergioavilax/headroom/actions/runs/31356382482)),
all **five** jobs green on the first run, no annotations:

```
$ gh run view 31356382482 --json conclusion,jobs
success
lint + typecheck: success
pytest (postgres + dynamodb-local service containers): success
ui lint + typecheck + unit tests + build: success
ui browser smoke (chromium, stub gateway): success
gateway and ui images build and serve: success

lint + typecheck | All checks passed!
lint + typecheck | 142 files already formatted
lint + typecheck | Success: no issues found in 141 source files
pytest (…service containers) | ===== 1092 passed, 2 deselected, 1 warning in 30.23s =====
Migrations apply, twice is a no-op | migrations: up to date, nothing to apply
ui lint + typecheck + unit tests + build | ℹ tests 28
ui lint + typecheck + unit tests + build | ℹ pass 28
ui lint + typecheck + unit tests + build | ℹ fail 0
ui lint + typecheck + unit tests + build | ✓ Compiled successfully in 5.7s
ui browser smoke (chromium, stub gateway) | ✓ Compiled successfully in 5.8s
ui browser smoke (chromium, stub gateway) |   7 passed (4.4s)
gateway and ui images build and serve | gateway healthy
gateway and ui images build and serve | console healthy
```

**1092 passed, 0 skipped in CI** — `grep -c SKIPPED` over the whole log returns `0`, so the
Postgres and DynamoDB halves of all four contract suites executed against the service
containers rather than skipping (H-012). The `series` contract and the new disposition
counters therefore ran against **real SQL** as well as against the dict, which is the whole
point of testing them there: a `count(*) FILTER` and a Python `sum(1 for …)` are two
sentences about one rule.

**Two of the five jobs are new and neither reads a secret.** `ui` runs eslint, `tsc
--noEmit`, the 28 unit tests, and `next build`; `ui-e2e` runs the browser smoke against a
Node stub. That `next build` succeeds at all in a job with **no gateway, no database, and
no network** is the property that makes them cheap — a console that had quietly grown a
build-time dependency on a running backend would fail here rather than on somebody else's
machine.

The `image` job now builds and smokes **both** runtime images. `console healthy` is the
console answering `/api/healthz` with no gateway anywhere in the job — which is what makes
it safe for compose to gate on, and the same liveness-only rule the gateway's own
`/healthz` has followed since H-000.

---

## Phase 8 — The experiments (2026-08-10)

Branch `claude/p8-experiments`. BUILD_PLAN §P8's words are the spec: *"Three pre-registered
experiments. Hypotheses, metrics, and falsification below are the pre-registration; they
were written before any data existed and get adjudicated in `experiments/results/REPORT.md`
with the same verdict discipline as Backline's BENCHMARK_NOTES."*

**No gateway feature was added, changed, or removed in this phase.** `headroom/` is
untouched. That is the phase in one line: the machinery finished at Phase 7, and this PR
points it at a question.

**Shipped**

- **The pre-registration, committed before any data existed** —
  `experiments/PRE_REGISTRATION.md` at `aa134f4`, with **H-059 … H-067**, ahead of any
  corpus, sweep, run, or analysis in the tree. It fixes every metric definition, bound, and
  analysis choice §P8 left open, and where document and code could differ the document wins.
  One disclosure sits in §0 rather than hidden: H3's live rows already existed, so every H3
  bound is derived from H-052's published constants instead of from the data.
- **Amendment A1, also before any data**: H1's corpus is the **130 answer-keyed questions**,
  not all 133. Three of Backline's questions (`hand-adversarial-01…03`) are prompt-injection
  canaries with `expected: null` and `tiers: ["t2"]` — they carry no T1 answer key, so they
  cannot be seeded from ground truth (H-059) and have no defined equivalence (H-061). The
  rule is mechanical and stated two ways that must agree; `load_suite` raises if they ever
  disagree.
- **`experiments/`** — eleven modules, all under `mypy --strict` and ruff, with exactly one
  able to spend money:

  ```
  artifacts.py   stable JSON, content hashing over *meaning*, provenance blocks
  h1/suite.py    Backline's answer-keyed questions, split question | answer-protocol
  h1/rubric.py   the preserve-every-entity rubric, hashed into the artifact
  h1/checks.py   mechanical entity / period / figure survival checks
  h1/generate.py PAID, operator-run, hard $1.00 stop reading committed spend
  h1/build.py    equivalence + embeddings -> the golden artifact
  h1/corpus.py   reading it back — keyless, torch-free, Backline-free
  h1/sweep.py    the admission decision replayed across the whole grid
  h1/figure.py   the curve, in the console's own validated palette
  h2/preflight.py refuse an $8 run against a tenant that would invalidate it
  h2/bench.py    the gateway's admission cost, on the MockProvider
  h2/analyze.py  the run adjudicated against the pre-registration and nothing else
  h3/chaos.py    the mock chain at three intensities, promoted to a figure
  h3/livekill.py the two-GPU kill, adjudicated from the ledger it left
  ```

- **H1's headline finding, for $0.00.** Family B — the leave-one-out novel-question family
  (H-062) — needs no paraphrases, so the hard-negative half of the curve was produced before
  a dollar was spent. At the shipped 0.90 default, **98 of 130 never-before-seen questions
  are answered from cache and 92 of those answers are provably wrong**. At 0.99, 18 still hit
  and all 18 are wrong. **τ₀ does not exist** — and cannot, since adding Family A can only
  add probes. BUILD_PLAN §P8.H1's second branch, decided.
- **The mechanism, named rather than mysterious.** The closest wrong answer scores
  **0.999539**: two reconciliation questions differing in one period token (`2026-02` against
  `2026-04`) with completely different answers. Genuinely unrelated questions sit at 0.578,
  so the embedding is working exactly as designed — entity-substituted templated questions
  are maximally similar in form and maximally different in answer.
- **H-060's sensitivity check paid for itself immediately.** Stripping the shared
  answer-format tail drops the 0.90 hit rate from **75% to 24%**. Reporting only the
  tail-stripped curve would have understated the danger threefold; naming which curve was
  primary *before* either existed is why that is a finding here rather than an argument.
- **The gateway's admission cost, measured** (H-065's secondary): 2,000 requests through the
  full pipeline against the MockProvider, **p50 0.207 ms** in memory and **1.441 ms** with
  DynamoDB Local behind the limiter and the budget gate. The two atomic conditional writes
  Phases 4 and 4b exist for cost **1.23 ms** — priced for the first time, and 0.01% of
  Backline's 12,678 ms per-question p50.
- **H3, adjudicated with no new GPU session** (H-067). The mock chain at three intensities:
  60 requests, **0 caller-visible 5xx**, four cut points, **4/4 terminal error events, 0
  silent truncations, 0 splices**. The live kill: **270 requests over 77 minutes across two
  kill-and-restore cycles, every one HTTP 200**, 141 failovers, and the breaker's 10-second
  cooldown visible in the raw attempt spacing (4.7 s before it trips, 14.1 s after).
- **The H2 harness, complete and tested, with the run left to the operator.** A pre-flight
  that refuses on any of six conditions that would invalidate the measurement, a paid tool
  smoke for risk register item 2, and an analyser whose every verdict — including
  `INVALIDATES THE RUN` — is exercised on fixture rows before a dollar is spent.
- **`experiments/RUNBOOK.md`** — every money-spending command in dependency order with its
  cost stated before it, and three stops at three values (H-066): $1.00 inside the generator,
  $12.00 on Backline's own committed-spend gate, $15.00 as Headroom's independent backstop.
- **`experiments/results/REPORT.md`** — the adjudication, with the verdicts above and what
  each measures *and does not*.
- **Evidence**: `docs/evidence/p8-experiments/` with the 492 exported ledger rows and a
  README explaining why they are committed — the ledger is a test fixture and those rows were
  one `make test` from gone (H-029).
- **Tests: 1148 keyless** (1092 → 1148), 2 live-marked and deselected. New files —
  `test_experiments_h1` (31), `test_experiments_h2` (17), `test_experiments_h3` (13). Every
  Phase 0…7 test still green, and **none changed**.

**Deferred**

- **H1's Family A — the paraphrase probes.** The ~$0.30 paid step, operator-run, with the
  runbook's §1. It measures the *savings* side: how many legitimate hits a cache would give
  up. It **cannot change the τ₀ verdict**, which is why the finding is publishable now.
- **H2's run itself**, ~$8.20, operator-run. Everything it needs is in place and tested.
- **The paired same-day control for H2** — deliberately, and named as the single
  highest-value follow-up in the repo (H-064). §A5.5 defines parity as a paired comparison;
  a control run costs another ~$8 against §0.6's $10, so H2 supports a statement about a
  *bound* rather than an effect size, and the report says so before the result.
- **A `provider_open_at` timing mark**, which would separate admission cost from provider
  time directly and is the clean fix for H-065's problem. Rejected *for this phase*:
  modifying the gateway in order to measure it is the shape of mistake the plan's invariants
  exist to avoid. It is the right first change of Phase 9 or 11.
- **An entity-and-period filter on semantic hits** — what H1's finding actually argues for.
  Named in REPORT.md as the first thing the cache should grow; not built, because Phase 8
  adds no gateway features.

**Deviations**

1. **`experiments/` joins `mypy --strict`.** `pyproject.toml`'s `files` gains it. It is not
   in the wheel — it is a sibling tree like `scripts/` — but it computes the project's
   headline finding, and code that produces a published number deserves the gateway's
   treatment.
2. **`experiments/h3/chaos.py` imports from `tests/`.** The one module in `experiments/`
   that does. It reads `INTENSITIES` and `PRE_TOKEN_FAULTS` out of
   `tests/test_failover_chaos.py` so the artifact describes the scenarios **CI actually
   runs**; a copy would be a second definition free to drift, and the number in the report
   would slowly stop meaning what the tick means. `tests/support/` has been an explicit
   harness layer since Phase 1 and the MockProvider it drives is production code. The
   dependency points at the harness, never the reverse.
3. **H1's corpus is 130 questions, not 133** — amendment A1, above. Reported in the artifact,
   in the report, and asserted by a test rather than left to a document.
4. **The paraphrase generator talks to Anthropic directly by default**, not through Headroom.
   `--base-url` points it at the gateway for anyone who wants the dogfood, and the runbook
   says so. Direct is the default because the generator builds the *instrument*, and an
   instrument built through the system under measurement is a confound if anything goes
   wrong — on the one step that costs money to repeat.
5. **`experiments/h1/generate.py` prices with the gateway's own dated price book.** Its
   budget stop resolves `claude-haiku-4-5`'s rate through `headroom.metering.prices` rather
   than a constant, so the experiment's money guard is the product's own D-017-proof
   arithmetic. Read-only; no metering behaviour changed.
6. **§0.6's H2 line and the `--budget` flag are different numbers, deliberately** (H-066).
   Backline's own computed projection is **$11.27**, and its runner refuses to start above
   `--budget` without `--yes` — so `--budget 10.00` buys a refusal or a disabled guard. The
   stop is $12, the expectation is $8.09, the gap is §0.6's contingency bucket, and this log
   records what is actually spent.
7. **Additions the plan's Phase 8 text does not enumerate**, all additive: the leave-one-out
   probe family (H-062, and the reason a result exists at $0.00); the two-embedding
   sensitivity curve (H-060); the answer-equivalence matrix and the benign-collision column
   (H-061); the MockProvider admission-cost bench (H-065); the per-probe breaker-mechanism
   check (§H3.2's claim without its aggregate); and `docs/evidence/p8-experiments/`.

---

**Gate** — *all three adjudicated in REPORT.md with verdicts; the H1 curve as both committed
JSON and a chart in the repo's visual language; the corpus hashed and drift-pinned;
PHASE_LOG records spend vs the §0.6 caps.* Plus the session brief's additions: all experiment
code keyless-tested in CI, all prior tests green, ruff + mypy clean.

### THE HEADLINE — H1, and it cost nothing

```
$ uv run python -m experiments.h1.sweep
[prompt] 130 probes · counts {'silent_wrong_answer': 123, 'benign_collision': 7}
         · max SWA similarity 0.999539 · min correct similarity None · τ₀ None
[body]   130 probes · counts {'silent_wrong_answer': 122, 'benign_collision': 8}
         · max SWA similarity 0.998554 · min correct similarity None · τ₀ None
```

The curve, at the thresholds a reader will look for (prompt space — what the gateway really
embeds):

```
threshold   served   silently wrong   benign   miss   modelled saving
   0.70       130          123           7        0       $7.71
   0.80       127          121           6        3       $7.53
   0.85       117          111           6       13       $6.94
   0.90 <--    98           92           6       32       $5.81      the shipped default
   0.95        32           32           0       98       $1.90
   0.99        18           18           0      112       $1.07
```

**τ₀ — the recommended threshold, by a rule fixed before the curve (H-063) — does not
exist.** No grid point in 0.70–0.99 reaches zero wrong answers.

And the pair that makes it concrete, printed from the artifact rather than narrated:

```
cos 0.999539   reconciliation-008  <-  reconciliation-010
  ASKED : Scan every statement for period 2026-02 for reporting anomalies — …
  SERVED: Scan every statement for period 2026-04 for reporting anomalies — …
  the caller would receive: 'Scan complete: 2 out-of-tolerance finding(s). …'
  the true answer is:       'Scan complete: 5 out-of-tolerance finding(s). …'

sanity: cos(catalog_lookup-001, reconciliation-001) = 0.578098
```

The embedding is not broken. Two questions differing in seven characters are, to a cosine,
the same question.

### THE BUILD CAUGHT TWO THINGS THAT WOULD HAVE BEEN SILENT

*The diagonal check fired on the first run:*

```
$ python -m experiments.h1.build --no-paraphrases
equivalence: 130² pairs through Backline's own scorer…
contract_terms-004: its own rendered answer does not score 1.0 through Backline's scorer.
Ground truth must satisfy the scorer or 'provably wrong' means nothing (H-061).
Rendered: 'ANSWER: 2E+1%'
```

Three of Backline's percent answer keys are stored as `2E+1` / `3E+1`, and its own `_MONEY`
regex reads `2E+1` as **2**. So a naive render of ground truth scored 2% against an expected
20% and failed for its own question — which would have made every "wrong" classification
downstream meaningless. Fixed with `format(value, "f")`: **Phase 3's `0E-12` scar, one repo
over**, and found by the check that exists for exactly this.

*The exclusion rule found the second:* three questions carry no T1 answer key at all. They
cannot be seeded from ground truth and have no defined equivalence, so amendment A1 removes
them — mechanically, stated twice, asserted by a test, and reported in the artifact.

### H3 — THE THREE CLAUSES

*The mock chain, three deterministic intensities:*

```
$ uv run python -m experiments.h3.chaos
  light   20 requests, 5 faults (25%)  -> {200: 20}, caller-visible 5xx: 0, hops 5,  breaker closed
  heavy   20 requests, 10 faults (50%) -> {200: 20}, caller-visible 5xx: 0, hops 18, breaker open
  brutal  20 requests, 20 faults (100%)-> {200: 20}, caller-visible 5xx: 0, hops 20, breaker open
  mid-stream cuts: 4/4 terminal error events, 0 silent truncations, 0 splices
```

*The two-GPU kill, from the 492 rows the operator's run left behind:*

```
$ uv run python -m experiments.h3.livekill --rows docs/evidence/p8-experiments/h3-livekill-ledger-rows.json
270 requests on the vllm chain over 77.2 min, one every 4.65s
  failover: {0: 129, 1: 141}  reasons: {'upstream_unavailable': 58, 'breaker_open': 82, 'upstream_timeout': 1}
  clause 1 (no caller-visible 5xx): HOLDS (0 found)
  clause 2 (recovery within bound):  EXCEEDED (max probe gap 14.74s vs bound 14.65s)
  clause 3 (terminal error events):  NO CUTS IN THIS WINDOW
  outages: 2
```

**Clause 2 is the one worth reading, because the pre-registered bound is exceeded and was not
moved.** Two of 39 probe gaps sit over it, by 0.09 s and 0.02 s. The bound was
`COOLDOWN_S + T` with `T` the median load interval — 14.65 s — and its premise, *"one request
every T seconds"*, idealises a loop whose intervals actually spanned **4.01 s to 19.99 s**.

So the claim underneath the bound was checked without any aggregate: **every one of the 37
probes was the first request to arrive at or after the previous attempt plus the cooldown.
37/37.** Both are published; the flattering one is not published alone.

The cooldown is legible in the raw attempt spacing, which is the nicest thing in this data:

```
before the breaker trips   4.7  4.8  4.7  4.6  4.8  4.8  4.6  4.7    the load's own interval
after it trips            14.1 14.1 14.1 14.2 14.3 14.3 14.2 14.1    cooldown + one interval
```

Two outages, 339 s and 380 s, each recovering **one load interval** after the last hop — one
probe went through, succeeded, closed the breaker, and the next request was on `vllm_a`.

### H2 — THE HALF THAT IS FREE

```
$ uv run python -m experiments.h2.bench
  in-memory        n=2000   p50 0.2074 ms · p95 0.3288 ms · p99 0.4765 ms
  dynamodb-local   n=2000   p50 1.4408 ms · p95 2.2875 ms · p99 4.6334 ms
  two DynamoDB conditional writes cost p50 1.2334 ms, p95 1.9587 ms
```

Against Backline's measured 12,678 ms per-question p50, the gateway's whole admission path —
authentication, routing, the token buckets, the cache lookup, the budget reservation — is
**0.011% of a request**. The conditional writes that make the budget gate and the rate
limiter unraceable cost **1.2 ms**, priced here for the first time.

### The keyless gate

```
$ make lint
uv run ruff check .
All checks passed!
uv run ruff format --check .
163 files already formatted

$ make typecheck
uv run mypy
Success: no issues found in 162 source files

$ make test
================ 1148 passed, 2 deselected, 1 warning in 18.15s ================
```

**1092 → 1148.** The 56 are `test_experiments_h1.py` (31), `test_experiments_h2.py` (17) and
`test_experiments_h3.py` (13). **No existing test changed** — the first phase since Phase 2
where that is true, and it follows from the phase adding no gateway code.

**And the same 1148 pass with no torch installed at all**, which is the claim the whole
committed-artifact design rests on — CI's environment, reproduced locally rather than trusted
(the Phase 5 lesson):

```
$ uv sync                       # drop the `embed` extra: this is what CI has
$ .venv/bin/python -c "import torch"
ModuleNotFoundError: No module named 'torch'
$ uv run mypy
Success: no issues found in 162 source files
$ uv run pytest -q
1148 passed, 2 deselected, 1 warning in 18.14s
$ uv run pytest -q | grep -c SKIPPED
0
$ uv sync --extra embed         # put it back
```

Every similarity number in H1 was produced by the real `bge-small-en-v1.5` and is asserted in
CI **with no model, no torch, no network, and no Backline** — out of a committed artifact
whose hash covers its texts and its equivalence matrix. That is the Phase 5 corpus pattern at
133-question scale, and it is what makes this finding reproducible by a stranger with a clone.

### The gate's clauses, individually

*All three adjudicated in REPORT.md with verdicts.* H1: no safe threshold exists, with the
threshold-by-threshold table. H2: not run, with the free half measured and the paid half's
criteria fixed. H3: clauses 1 and 3 hold; clause 2's aggregate bound is exceeded by 0.09 s
and its mechanism holds 37/37 — reported as both.

*The H1 curve as both committed JSON and a chart in the repo's visual language.*
`experiments/results/h1_curve.json` (every grid point, both families, both embeddings) and
`h1_curve.svg` — two panels, the console's own tokens, the palette validated in Phase 7
against this exact surface (H-057) rather than re-picked. Rendered and **looked at**, which
caught a header colliding with the panel titles and direct labels running off the right edge;
the legend that replaced them is in the file's docstring with that reason attached.

*The corpus hashed and drift-pinned.* `corpus_hash` covers the texts, the provenance mapping
and the equivalence matrix — never the vectors, never the timestamps, so a rounding change is
not a corpus change (the Phase 5 rule). `test_the_corpus_hash_still_describes_the_corpus`
fails on a hand-edit; the vectors file names the corpus hash it was built beside and
`load_vectors` refuses a mismatch; and
`test_the_committed_curve_is_the_one_this_corpus_produces` recomputes the published curve from
the published corpus on every run, so a stale result file cannot survive.

*All experiment code keyless-tested in CI.* 61 of the 56 new tests run against committed
artifacts and fixtures; the rest are pure arithmetic. Two are worth naming:

- `test_the_offline_sweep_makes_the_same_decision_as_the_shipped_store` — the whole experiment
  is an *offline replay* of the gateway's admission decision, and "offline replay" is an
  equivalence claim. So 130 entries go into a real `ResponseCacheStore` with the corpus's own
  vectors and its `search(..., limit=1)` is asked for the same top-1 the sweep computed. They
  agree on which entry and on the similarity to 1e-6.
- `test_one_5xx_would_falsify_clause_1` — the sabotage shape, applied to an *analysis*: a
  committed row is edited to carry a 503 and the verdict must flip to FALSIFIED. A verdict
  that has never been seen to fail is a verdict nobody should believe.

**Assumed-facts register (§0.4)**

- **A2 — VERIFIED (the mechanism; the end-to-end run is the operator's).** *Anthropic SDKs
  honor `base_url` override, so Backline can point its Anthropic provider at Headroom
  unchanged.* Read out of the installed SDK rather than assumed: `anthropic` 0.120.2 resolves
  `base_url` from `ANTHROPIC_BASE_URL` when the constructor is passed `None`, and Backline's
  `AnthropicProvider` passes `None` by default. So the integration is two environment
  variables and **zero changes to Backline**, its scoring included. The $0.02 smoke that
  proves it end to end with a tool block is `RUNBOOK.md` §2c, and remains the operator's.
- **A5 — still VERIFIED, and about to be tested where it has never been tested.** Tool
  round-trips are proved keylessly (`tests/test_tool_blocks.py`, byte equality both
  directions) and nothing in this phase touched the proxy. What has *not* happened is a tool
  block through the real Anthropic API through this gateway; that is exactly what the
  pre-flight smoke buys for two cents before the $8 run, per risk register item 2.
- **A1, A3, A4, A6, A7** — not due at this gate, none touched.

**Spend — $0.00.** Against §0.6's Phase 8 caps of **$1** (H1 generation), **$10** (the H2
run) and **$6** (contingency). Every number in this entry came from the operator's own CPU,
the MockProvider, or ledger rows a GPU demo left behind. The two paid steps are specified,
capped, and handed over: ~$0.30 for H1's paraphrase batch and ~$8.20 for H2, worst case
$13 against the $20 project total.

**Operator verification** — `experiments/RUNBOOK.md`, in dependency order, with each
command's expected cost stated before it.

### CI

CI on PR-8 ([run 31430133857](https://github.com/sergioavilax/headroom/actions/runs/31430133857)),
all **five** jobs green on the first run, no annotations:

```
$ gh run view 31430133857 --json conclusion,jobs
success
lint + typecheck: success
pytest (postgres + dynamodb-local service containers): success
ui lint + typecheck + unit tests + build: success
ui browser smoke (chromium, stub gateway): success
gateway and ui images build and serve: success

lint + typecheck | All checks passed!
lint + typecheck | Success: no issues found in 162 source files
pytest (…service containers) | ===== 1148 passed, 2 deselected, 1 warning in 31.66s =====
ui lint + typecheck + unit tests + build | ℹ tests 28  ℹ pass 28
ui browser smoke (chromium, stub gateway) |   7 passed (4.8s)
gateway and ui images build and serve | gateway healthy
gateway and ui images build and serve | console healthy
```

**1148 passed, 0 skipped in CI** — `grep -c SKIPPED` over the whole log returns `0`, so the
Postgres and DynamoDB halves of all four contract suites executed against the service
containers rather than skipping (H-012).

**All 56 experiment tests ran on a runner with no `embed` extra** — no torch, no model, no
network to HuggingFace, and no Backline checkout anywhere:

```
$ gh run view 31430133857 --log | grep -c "test_experiments_.*PASSED"
56
```

That is the sentence the whole phase rests on. Every similarity number in H1 came out of the
real `bge-small-en-v1.5`, once, on the operator's CPU, and is asserted in CI out of a
committed artifact whose hash covers its texts and its equivalence matrix. Including
`test_the_committed_curve_is_the_one_this_corpus_produces`, which recomputes the published
curve from the published corpus — so **the numbers in `REPORT.md` are regenerated on every
pull request rather than trusted**. Phase 11's doc-pinning discipline, arriving three phases
early because a published finding is exactly the thing that must not drift from its input.

---

### Phase 8 addendum — the entity checker's false positives (pre-measurement, H-068)

**Not a new phase; a correction on the open PR-8 branch, before any measurement exists.**

The operator ran the paid paraphrase batch. It landed **114/130 for $0.19** and left **16
UNRESOLVED**, and the failure log showed the cause was the checker, not the model: the
mechanical entity extractor was reading sentence-initial capitalised common words as entity
names that a paraphrase must preserve. Verbatim from the run — `Suppose` (×6), `Across`
(×4), `Summing` (×2), `Counting`, `Please`, `Exactly`, `Audit`, plus the code `ONLY`. The
failures were identical across all 3 candidates over all 3 attempts, so re-drawing could
never have cleared them: the rule admitted no correct answer.

**Shipped**

- `experiments/h1/checks.py` — three narrow amendments, argued per case in **H-068**:
  sentence position is not entityhood (exempt only when the word both opens a sentence *and*
  is an ordinary English word); ALL-CAPS emphasis at length ≥ 3 is not a code; and a gloss
  the body writes itself (`United States (US)`) becomes one `AliasGroup` satisfied by either
  spelling, with `U.S.` normalising to `US`. The exemptions apply to the **body** only —
  `salient_tokens` still reads a candidate literally, so a paraphrase that opens with
  `Voltage has …` is not failed for losing the name it opens with.
- `tests/test_experiments_h1.py` — **27 new tests, 1148 → 1175**. All 16 failing questions
  have their extraction pinned per question, asserting both halves: the spurious token
  absent *and* the real entities still required. Plus five faithful paraphrases of stuck
  questions that now pass, and the guards that keep the amendment narrow — `EP` still
  required exactly, an alias group still failing when both spellings go, a common word still
  required mid-clause, a real name still required at a sentence start.
- `experiments/RUNBOOK.md` — the exact `--only` command for the 16, with the harness's own
  projection (`worst case $0.043393`, ~$0.13 if every question needs all three rounds).
- `docs/DECISIONS.md` — H-068, including the per-case adjudication the correction turns on.

**Adjudicated per case, not by one rule**

`US ≡ U.S. ≡ United States` (and `GB`, `DE`, `JP`) — **canonical-variant equivalence
accepted**, because the question's own text writes the gloss, and the run produced correct
paraphrases resolving in *both* directions. `EP` — **exact survival still required**: it
fixes the scope of `hand-catalog_lookup-01`, has no in-text gloss, and two of three
candidates kept it unprompted, so the requirement costs nothing. `ONLY` — **dropped**, as
emphasis capitalisation of a word already droppable in title case. A paraphrase that drops
or changes a real entity, period token or figure still fails, which is the poison H1
measures.

**Verified rather than assumed**

- The 114 committed paraphrases were re-validated against the corrected checker: **342
  paraphrases, 0 new failures.** Nothing moved to unresolved.
- The exemption's blast radius was measured over all 130 bodies: it changes the status of
  the eight tokens listed above and **nothing else** — no artist, track, label, ISRC,
  statement id, figure or period.

**Deferred to the operator (invariant: Claude Code never runs spend)**

The 16 still need regenerating — the generator never persisted the rejected drafts, so the
candidates that would now pass are gone. The `--only` command is in the runbook, costs about
$0.05, and leaves the 114 untouched. `build.py` still refuses to assemble a corpus while
`unresolved` is non-empty, so nothing downstream can quietly proceed without it.

**Gate**

```
uv run ruff check .          All checks passed!
uv run ruff format --check . 163 files already formatted
uv run mypy                  Success: no issues found in 162 source files
uv run pytest                1175 passed, 2 deselected, 1 warning in 17.23s
```

---

## Phase 8 — two pre-measurement corrections: the compound ask, and `--only`

**Date**: 2026-08-10 · **Branch**: `claude/p8-experiments` · **Decision**: H-069

Both fixes land under risk register item 3 — the corpus is corrected *before* a measurement
reads it. No sweep has consumed the current corpus, no H1 number changed, and neither fix
touches the sweep's arithmetic.

**Shipped**

- **The compound-ask check** (`experiments/h1/checks.py`). A body whose ask has two parts —
  an interrogative *plus* a separate sentence opening with a request verb — now requires both
  parts to survive in a candidate, on the same footing as an entity. The shape is extracted
  from the body, not listed: `compound_ask()` finds **25 questions across three categories**,
  and the words `clause` and `rate` appear nowhere in the module. A candidate passes when the
  two demands land in two different asks, where `ask_segments()` splits on the three things
  English uses to join demands — a coordinator, a sentence break, a participial adjunct.
- **`--only` is a redo** (`experiments/h1/generate.py`). `--help` had documented "ids to
  (re)do" while the implementation skipped anything already complete; forcing the operator's
  redraw had cost hand-deleting a JSON entry. `select()` is now one function shared by the
  projection and the run, an unknown id is an error rather than a silent no-op, and the entry
  being replaced is dropped only once its first call is paid for.
- **16 tests**, including the operator's rejected draw and both redraw failures verbatim, the
  four faithful forms that must keep passing, and the invariant that every compound body
  satisfies its own rule.

**Findings**

- **The exposure was wider than one id.** Auditing the accepted batch found **25 collapsed
  candidates across 17 of the 25 compound questions** — a 1-in-3 rate. The probe the operator
  caught had a collapsed neighbour in its own batch that the seeded sample of 20 never showed
  him.
- **The rubric was deliberately not strengthened**, argued in H-069: bumping `RUBRIC_VERSION`
  forces a redraw of all 130 and a fresh spot-check of all 390 probes, and 50 of 75
  compound-ask candidates already pass — this is a retry, not H-068's rule that admitted no
  correct answer. The lever is recorded for the operator with its cost, should redraws thrash.

**Deferred to the operator (invariant: Claude Code never runs spend)**

The 17 need redrawing. The command is in `experiments/RUNBOOK.md` §1b; `--dry-run` projects
`worst case $0.045754` for one round each, ceiling ~$0.14 at three rounds, against the
unchanged `$1.00` stop. `build.py` refuses the corpus until they are clean. Only two probes in
the seeded spot-check sample belong to those 17 — `contract_terms-002#p1` and
`contract_terms-004#p3` — so the re-check after the rebuild is two sentences, not twenty.

**Pre-existing red, not introduced here, and not papered over**

`test_the_committed_curve_is_the_one_this_corpus_produces` fails, and it has failed since
`d57aa33`. `experiments/results/h1_curve.json` carries `stage: family_b_only` and the corpus
hash of the pre-paraphrase artifact; the corpus was rebuilt to `stage: complete` at `d57aa33`
and again at `71afa0e` without the sweep being re-run. The test is doing its job — REPORT.md's
H1 numbers no longer follow from the committed corpus.

It was **left red on purpose**. Clearing it means running the sweep, and the only corpus
available to sweep is the one this session has just proven carries 25 collapsed probes;
publishing Family A numbers off it is exactly the "plausible corpus, unexplainable curve"
failure the checks exist to prevent. The order is: redraw the 17 → rebuild → spot-check →
sweep → figure. The red test is the marker for that outstanding work, and it clears when the
sweep runs on a corpus that deserves it.

**Gate**

```
uv run ruff check .          All checks passed!
uv run ruff format --check . 163 files already formatted
uv run mypy                  Success: no issues found in 162 source files
uv run pytest                1 failed, 1190 passed, 2 deselected, 1 warning in 18.77s
                             (the failure is the pre-existing stale-curve pin above;
                              1174 passed / 1 failed on this branch before this session)
```

---

## Phase 8 — the redraws thrashed: `RUBRIC_VERSION` 2, and versions stop mixing by hand

**Date**: 2026-08-10 · **Branch**: `claude/p8-experiments` · **Decision**: H-070

H-069 shipped the compound-ask check and deliberately left the prompt alone, recording the
lever to pull *if redraws thrash*. They thrashed; the operator invoked it. Still
pre-measurement under risk register item 3 — no sweep has read the corpus, and no H1 number
exists to be flattered by any of this.

**The evidence that triggered it** (the operator's three `--only` rounds)

- **17 → 8 → 5 → 5**, ~**$0.11**. `contract_terms-008`, `-012`, `-013`, `-014` and
  `hand-contract_terms-04` each failed three to four independent rounds — nine to twelve
  draws apiece at `MAX_ROUNDS = 3` — every failure the same compound-ask collapse on the
  rate-plus-cite body. `hand-contract_terms-04` also loses the name `Japanese`
  intermittently. The failure lines are committed verbatim in the artifact's `unresolved`.
- Reading the collapse rate back out of the attrition (9 of 17, then 3 of 8, then 0 of 5)
  puts it near **one in two** on these bodies rather than H-069's batch-wide one in three,
  and all three candidates must pass in the same round — so a clean round is about **one in
  eight**. Round four buys the same distribution, not more information.

**Shipped**

- **`RUBRIC_VERSION = 2`** with one new rule (`experiments/h1/rubric.py`): a two-part ask
  stays two parts, stated positively, one faithful form shown, the rest of the rubric
  byte-for-byte unchanged. The worked example is invented rather than lifted from the suite.
- **The mechanical check is untouched** — belt and braces, as asked. The rubric asks, the
  checker verifies, and `test_the_rubric_teaches_a_form_the_checker_accepts` runs the
  prompt's own example through `compound_ask()` and `check_paraphrase()`, so a rubric that
  taught a form the harness rejects turns the suite red instead of burning a regeneration.
- **"Batches never mix versions" is now code, not a sentence in a docstring.** It had to be:
  without it, the honest full-regeneration command would have read the complete v1 file,
  found every question complete and printed `nothing to do`. `load_batch()` returns a batch
  from another version holding **no questions** (so a bare run selects all 130), `--only` on
  a superseded batch is **refused with nothing sent**, and `build.py` refuses to assemble a
  corpus from a batch stamped at another version.
- **7 tests**, including the refusal, the "dry run writes nothing" property, and both
  directions of the resume/regenerate split.
- **Housekeeping**: `a01e239` had appended a truncated duplicate of H-069's header to the
  end of `docs/DECISIONS.md`. Removed.

**Deferred to the operator (invariant: Claude Code never runs spend)**

The regeneration itself. `experiments/RUNBOOK.md` §1b carries the command and the dry run's
verbatim output, produced here (free, nothing sent): `130 to generate`,
`worst case $0.376095`, cap `$1.00` unchanged. Expected actual **~$0.30** — the first full
run drew 114 questions in 163 calls, retries included, for $0.19. Then rebuild, then a
**fresh spot-check of all 20** sampled probes: every one of the 390 is new text, which is the
price H-069 costed and the operator has now accepted.

**Spend against §0.6, since the artifact under-reports it**

The `spend` block records only the *last* run, so the file says `$0.015973`. Cumulative
landed H1 paraphrase spend is **~$0.32**: $0.19 (first full run) + $0.02 (the H-068 redraw)
+ $0.001 (the forced redraw) + ~$0.11 (the three compound rounds). §0.6's line is **$1
whole-project** while the harness's stop is **$1 per invocation** — not the same number.
After the ~$0.30 regeneration, roughly two-thirds of the line is spent and a third full
regeneration would need a budget amendment. Making the stop read cumulative landed spend out
of the artifact is the obvious follow-up and is deliberately not done mid-lever (H-070).

**Still red, still on purpose**

`test_the_committed_curve_is_the_one_this_corpus_produces` — unchanged from the previous
entry, and now one step further from clearing: the corpus it pins will be rebuilt from the
v2 batch. Order is unchanged: regenerate → rebuild → spot-check → sweep → figure.

**Gate**

```
uv run ruff check .          All checks passed!
uv run ruff format --check . 163 files already formatted
uv run mypy                  Success: no issues found in 162 source files
uv run pytest                1 failed, 1197 passed, 2 deselected, 1 warning in 18.94s
                             (the failure is the same pre-existing stale-curve pin;
                              1190 passed / 1 failed before this session)
```

---

## Phase 8 — the spot-check's second catch: a prohibition must stay a prohibition (2026-08-10)

**Branch** `claude/p8-experiments` · **Decisions** H-071 · No spend, no sweep, no build.

**What the operator found**

Their spot-check failed `reconciliation-006#p1`. The body ends `Do not submit a batch.`; the
paraphrase ended *"submitting them individually rather than as a batch"* — a bare prohibition
turned into an order to do the alternative. The forced redraw returned a **clean `#p1` and the
identical inversion in `#p3`**, so a re-read of the same sampled ids would have approved it.
This is the third pre-measurement checker amendment, and the **second distinct systematic
drift** the human clause of the QA chain has caught that the mechanical clause could not see:
scope compression (H-069), now negation inversion.

**Shipped**

- **`prohibitions()` and `Prohibition`** in `experiments/h1/checks.py`. The shape is read off
  the body — negator opening an ask, then a verb — and the forbidden verb and its object are
  extracted, not listed: hand it `Do not overwrite the staging table.` and it reads
  `overwrite`/`table`. It finds **15 bodies, the whole reconciliation family**. Two clauses: the
  prohibition must still be negated somewhere, and the forbidden verb must not appear as an
  order. Both are load-bearing — the first alone passes every inversion in the batch, the
  second alone misses all three of `hand-reconciliation-01`'s dropped candidates.
- **Six faithful constructions pinned as passing** (verbatim, `without submitting a batch`,
  `not a batch submission`, `not batched`, `avoiding batch submission`, `do not batch-submit`),
  and **all 15 prohibiting bodies satisfy their own rule** — H-068's diagnostic, third outing.
- **A regex bug fixed, and it was total for this check.** `_ASK_BREAK` carried `re.IGNORECASE`
  across the whole pattern, which turned its `(?<![A-Z]\.)` initials guard into "any letter
  before a full stop" — so **`ask_segments()` has never split a sentence ending in a letter**.
  Prohibitions end in `batch.` and `anything.`, so the new check found *zero* of fifteen until
  the flag was scoped to the word alternatives with `(?i:…)`. Measured effect on H-069: **0
  verdict changes across all 390 committed probes**, and all four of its pinned faithful forms
  pass either way. Effect on H-070's thrash: unknowable, because rejected drafts are never
  persisted — recorded as a possibility, not a cause.
- **The build refusal names every failing id at once**, with the `--only` command. It stopped
  at the first id before, which would have made this seven-id audit cost six surprised rebuilds.
- **18 tests**, including the three observed inversions, the drop-vs-invert split, the scope
  boundary (`hand-reconciliation-01` carries both an in-scope prohibition and an out-of-scope
  adjunct in one body), and the regex fix in both directions.

**The audit, and `AWAITING_REDRAW`**

13 of 390 probes fail across **7 questions** — `hand-reconciliation-01` (3 of 3),
`reconciliation-007` (3 of 3), `-002` and `-014` (2 of 3), `-001`, `-006`, `-010` (1 of 3).
They are in `AWAITING_REDRAW` as an upper bound, never as a tolerated exception; `build.py`
refuses the corpus while they are there. Two fail by *dropping* rather than inverting —
`Do not group submissions.`, `Do not process multiple statements.` — the prohibition's shape
kept and its content swapped.

**H-069's 17 emptied exactly as H-070 said they would.** The same audit reports **zero**
compound-ask failures across the v2 batch, down from 25 across 17 questions. That is the
rubric bump's receipt, and it is now a committed fact rather than an expectation.

**The rubric was deliberately not bumped**

32 of 45 candidates on prohibiting bodies already pass, in six constructions; eight of the
thirteen failures are one template. H-070's lever exists and its condition — the same ids
`unresolved` across repeated `--only` rounds — has not been tested once here. Expected retry
burden is stated in H-071 and the runbook so it can be held to account: ≈74% of questions
resolve in three rounds on the batch-wide rate, ≈16% on the pessimistic selected rate, so
**expect one to three of the seven to need a second round**.

**A correction to H-070's budget arithmetic**

H-070 projected the v2 regeneration at ~$0.30 and concluded a third would need a budget
amendment. It landed at **$0.190212 in 147 calls** (`0f1556d`). Recoverable cumulative landed
H1 paraphrase spend is ≈ **$0.51** — a *lower bound*, since the `spend` block holds only the
last run and the `--only` rounds between `0f1556d` and `7d75d41` are gone. Roughly half of
§0.6's `$1` whole-project line remains, so a `RUBRIC_VERSION = 3` would fit inside it. The
argument for trying the checker alone rests on evidence, not on affordability.

**Deferred to the operator (invariant: Claude Code never runs spend)**

The seven-id redraw. `experiments/RUNBOOK.md` §1b carries the command; the projection was
computed here from `Rates.worst_case()` without invoking the generator — **`worst case
$0.020541`** for one round each, ceiling **~$0.062** at three rounds, against the unchanged
`$1.00` stop. Confirm with `--dry-run` (free, sends nothing) before dropping the flag. Only
**2 of the 20** sampled probes belong to a redrawn id (`reconciliation-006#p1`,
`reconciliation-010#p1`), so the re-check after the rebuild is two sentences, not twenty. Then:
redraw → rebuild → spot-check → sweep → figure.

**Pre-existing red, not introduced here**

`test_the_committed_curve_is_the_one_this_corpus_produces`, unchanged and for the same reason:
`experiments/results/h1_curve.json` pins a corpus hash the current artifact no longer has, and
clearing it means sweeping a corpus that this session has just proven carries 13 bad probes.
It clears when the sweep runs on a corpus that deserves it.

**Gate**

```
uv run ruff check .          All checks passed!
uv run ruff format --check . 163 files already formatted
uv run mypy                  Success: no issues found in 162 source files
uv run pytest                1 failed, 1215 passed, 2 deselected, 1 warning in 17.51s
                             (the failure is the same pre-existing stale-curve pin;
                              1197 passed / 1 failed before this session)
```

---

## Phase 8 — closed: three experiments adjudicated, and the gate that failed on both runs

**Date**: 2026-08-10 · **Branch**: `claude/p8-experiments` · **Decisions**: H-072 ·
**Spend this session: $0.00** (Claude Code ran nothing that costs money; the operator's H1
and H2 runs are accounted below)

The phase gate, from BUILD_PLAN §P8: *all three adjudicated in REPORT.md with verdicts; the
H1 curve as both committed JSON and a chart in the repo's visual language; the corpus hashed
and drift-pinned; PHASE_LOG records spend vs the §0.6 caps.* All four met.

**Shipped**

- **`experiments/results/REPORT.md`, finished.** H1 with both families and the two-number
  overlap that is the actual reason τ₀ does not exist; H2 with parity, three overhead
  numbers, the cache-disabled proof, the two-meter cross-check and the gate adjudication;
  H3 unchanged from its earlier adjudication; and an honest cumulative money table that
  publishes the *evidenced* H1 figure separately from the reconstructed one.
- **Two harness gaps the operator hit, fixed and pinned (H-072).**
  - RUNBOOK §2f's export SQL selected none of the five token columns `analyze.py` reads, so
    the operator's first export died on `KeyError: input_tokens`. The SQL now selects them,
    the analyzer checks `REQUIRED_COLUMNS` **once, up front**, and names every missing column
    plus the runbook step that produces them.
    `test_the_runbook_export_selects_every_column_the_analyzer_reads` parses the SELECT out
    of the markdown and holds the two to each other, so they cannot drift again. The stronger
    half of that fix: three columns were read through `.get` and would have degraded
    *silently* — a missing `cache_disposition` reports every row as not-cache-disabled and
    invalidates a perfectly good run.
  - `analyze.py` printed `parity: NO DATA` against a real `summary.json` because it read a
    key Backline has never written. Backline computes `overall` at render time as the
    n-weighted mean of category scores (`evals/report.py`); `overall_score()` is now that
    arithmetic. **The parity verdict was formalised against the pre-registered bound with the
    numbers already public and committed** — 93.7 gateway, 93.3 reference, both landed at
    `0914cc7` — so this is adjudication, not tuning.
- **`experiments/h2/adjudicate.py`** and `experiments/results/h2_gate_adjudication.json` —
  the gate's FAIL decided from committed evidence rather than from a scratch script, with the
  per-question half folded in when Backline's `results.jsonl` is supplied.
- **Backline's evidence committed here**, per invariant 9: both summaries and the gate
  baseline in `docs/evidence/p8-experiments/`. The parity claim previously depended on a
  directory in another repo that one `make test` there would truncate.
- **Tests: 44 for H2 (from 15), 5 new H1 pins.** Two are the artifact-to-input pins the H1
  curve already had — `test_the_committed_analysis_is_the_one_the_committed_inputs_produce`
  and its adjudication sibling — plus the residual-attribution check that ties the $0.000855
  meter difference to the pre-flight smoke's own `request_id`.

**Findings — H2, beyond the pre-registered clauses**

- **The two meters agree exactly, not merely within $0.01.** Headroom metered $7.541253 and
  Backline $7.540398. The entire $0.000855 residual is one identified row:
  `hr_e171f6024fc64772a66840fda6aab05a`, the §2c pre-flight tool-block smoke, recorded in
  `h2_preflight.json`, issued on the same tenant five minutes before the suite and never seen
  by Backline. Set it aside and 461 requests reconcile to twelve decimal places.
- **One pre-registered clause could not be evaluated, and that is a defect in the
  pre-registration.** §H2.4 asks that token totals agree exactly; Backline publishes cost and
  **no token totals**, in neither `summary.json` nor `results.jsonl`. Reported as
  `NOT EVALUABLE` rather than dropped. The related premise *is* checkable and holds:
  `cache_read_tokens` and `cache_write_tokens` are 0 across all 462 rows.

**The gate adjudication — the honest version**

Backline's strict regression gate failed on the H2 run. The verbatim output is below. What
the adjudication turns on is that **the direct-local reference run — the very run whose 93.3
is H2's pre-registered comparator — fails the same gate against the same baseline, on
disjoint categories.** So does the committed sweep row, and so did the AWS run. No full-suite
`claude-sonnet-5` run in Backline's recorded history has ever passed this gate.

- **The pre-registered parity instrument is the Δ bound, not the gate.** §H2.2 makes the
  overall-score comparison primary and names `evals gate` the secondary, "on the record with
  its known failure modes", including the advance notice that a legitimate fresh run can fail
  it on variance alone. With the fixed reader: **93.7, Δ +0.4, bound 3.0, WITHIN NOISE.**
  Nothing was swapped, widened or re-run to get there.
- **It scatters both ways, which is what §A5.5 pre-declared variance looks like.** Over all
  133 questions the gateway scored higher on 13, lower on 17, identical on 103; it *improved*
  `abstention` by 10.0 and `multi_step` by 6.1. **T1 — the deterministic answer-key tier —
  is identical on 132 of 133**, and the one that moved moved in the gateway's favour.
- **`contract_terms` 76.7:** the dip partly pre-exists (the reference is itself 2.33 under the
  baseline), the category's same-model spread across five full runs is **8.33 points — larger
  than the gate's whole 3.0 tolerance** — the AWS run with no Headroom in it scored 77.0, and
  the committed baseline is a self-described *composite* no single run has reproduced.
- **56% of the 6.00-point drop is one question**, `contract_terms-016`, zeroed because a
  question scores `min(tier scores)` and its T2 failed. Two of the gateway's three T2
  violations are the identical `sql_clean`/`information_schema` mode as both of the
  reference's, on different ids — textbook flicker, and both runs sit at the bottom of the
  historical 2–8 range.
- **The third violation is unique to the gateway run and is named in REPORT.md in full**, with
  its mechanism, both answers, and the raw recorded check detail in the artifact.
  `cites_clause` requires the contract code and the section marker to be **adjacent**. Both
  runs cite the same clause and both get T1 right (`8%`); the gateway's answer interposed a
  parenthetical, so the regex extracted nothing. It is adjudicated as §A5.5's *own* named
  precedent — a checker false positive — and it is written down rather than folded into
  "variance", so a reader can disagree using the same evidence.
- **Nothing was re-run, re-rolled or healed** (§H2.5). `errors.n` was 0; there was no heal
  pass to use.

**Deviations from the plan — the three amendments, straight**

All three were **pre-measurement** corrections under risk-register item 3 (a bad batch is
regenerated *before* the sweep, never after). No H1 number existed to be flattered by any of
them, and each is a DECISIONS entry with its alternatives and its cost.

1. **H-069 — the compound ask.** The operator's spot-check failed `contract_terms-004#p3`:
   the body asked for a rate *and* a citation, the paraphrase asked only for the citation.
   The forced redraw reproduced the collapse in 2 of 3 fresh candidates. Made mechanical; the
   audit then found **25 collapsed candidates across 17 questions** — a 1-in-3 rate the
   sampled twenty could never have shown. The rubric was deliberately *not* bumped, and the
   lever was recorded with its cost in case the redraws thrashed.
2. **H-070 — `RUBRIC_VERSION` 2, after they thrashed.** They did: **17 → 8 → 5 → 5**, ~$0.11,
   with five ids failing three to four independent rounds on the identical shape. The lever
   was pulled. Rule 4 now asks for what the checker checks, "batches never mix versions"
   became code rather than a docstring sentence, and the full regeneration landed
   **$0.190212 in 147 calls** — under its own ~$0.30 projection.
3. **H-071 — a prohibition must survive as a prohibition.** The operator's spot-check failed
   `reconciliation-006#p1`: `Do not submit a batch.` had become an instruction to submit
   individually. The forced redraw came back with a clean `#p1` and **the identical inversion
   in `#p3`** — so re-reading the same sampled ids would have passed it. Made mechanical; the
   audit found **13 bad probes across 7 questions**, the whole reconciliation family. It also
   surfaced a regex bug that was total for the check: `_ASK_BREAK` carried `re.IGNORECASE`
   across its initials guard, so `ask_segments()` had **never** split a sentence ending in a
   letter — and prohibitions end in `batch.` and `anything.`. Measured effect on H-069's
   verdicts across all 390 committed probes: **zero**.

**What that sequence actually demonstrates, since it is the part worth keeping:** the
operator's human spot-check caught **two distinct systematic generation failures across three
review rounds that the mechanical checks could not see** — scope compression, then negation
inversion — and *neither* was reachable by re-reading the sampled twenty. Each catch was one
probe; each audit turned that one probe into 25 and 13. The mechanical clause found the rest;
the human clause found the *kind*. Every round was pre-measurement, and the sweep did not run
until the approval was recorded in the corpus artifact's provenance block.

**Deferred — nothing, and two things named for later**

No part of Phase 8 is outstanding. Recorded as follow-up work rather than deferred scope:

- **The entity-and-period filter H1 argues for.** REPORT.md names it as the first thing
  Headroom's cache should grow — a filter, not a higher bar. Not built here; building the fix
  a measurement recommends inside the measurement's own PR is how a finding becomes a pitch.
- **`generate.py`'s stop reads per-invocation landed spend, not cumulative.** §0.6's $1 is
  whole-project; the harness's $1 is per run. It never bound (the phase used ~$0.53–0.57), and
  the obvious fix — read cumulative landed spend out of the artifact — was deliberately not
  done mid-lever in H-070 and is not done here either, for the same reason: the artifact's
  `spend` block is what a fix would have to read, and this session is the one publishing that
  it under-reports.

**Spend against §0.6 — and the H1 receipt is partly reconstructed, which is said out loud**

| bucket | cap | spent | note |
|---|---:|---:|---|
| H1 paraphrase generation | $1.00 | **≈ $0.53–0.57** | **$0.443670 evidenced** across 8 committed `spend` blocks / 357 calls; two compound-ask redraw rounds were overwritten before commit and are reconstructed at $0.09–0.13 |
| H2 suite through the gateway | $10.00 | **$7.541253** | $7.540398 suite + $0.000855 pre-flight smoke (budgeted ~$0.02) |
| P8 contingency / heal passes | $6.00 | **$0.00** | no heal pass; no stop fired |
| H3 | — | **$0.00** | mock chain and the operator's own GPUs |
| **Phase 8** | **$17.00** | **≈ $8.07–8.11** | |
| **Project, all phases** | **$20.00** | **≈ $8.08–8.12** | P0–P7 smokes < $0.01 |

No budget amendment was needed and no cap was raised. The $12.00 Backline stop was never
approached and the $15.00 Headroom backstop never fired — which is the point of setting a
backstop above the operative stop (H-066).

**Backline's gate, verbatim — both runs**

The gateway run `21369386-a040-4589-90a1-0e75409711ec`:

```
gate: FAIL
  ✗ contract_terms: 76.7 vs baseline 85.0 (-8.3 pts > 3)
  ✗ 3 T2 violation(s) — process assertions failed
  · adversarial: improved 93.3 → 100.0
  · reconciliation: improved 96.7 → 98.3
```

The direct-local reference `a309dc57-b68e-4fbd-8591-38c2e7c63263`, whose 93.3 is H2's
pre-registered comparator, against the same committed baseline:

```
gate: FAIL
  ✗ abstention: 90.0 vs baseline 100.0 (-10.0 pts > 3)
  ✗ multi_step: 65.0 vs baseline 72.8 (-7.8 pts > 3)
  ✗ 2 T2 violation(s) — process assertions failed
  · adversarial: improved 93.3 → 100.0
  · reconciliation: improved 96.7 → 98.3
```

Both exit non-zero. Both are published.

**The analyzer, verbatim**

```
462 rows
  cache disabled (H-047): HOLDS (0 rows not cache_disabled)
  passthrough overhead: p50 0.0612 ms, p95 0.1176 ms, p99 0.1644 ms -> HOLDS
  outcomes: {'ok': 462}
  failover hops: {0: 462} (must be {0: n})
  parity: WITHIN NOISE — overall 93.7 vs direct_local 93.3, delta +0.4 against a bound of 3.0
  two-meter cross-check: AGREE (Headroom $7.541253000000 vs Backline $7.540398)
```

**The adjudication, verbatim**

```
treatment gate: FAIL
  ✗ contract_terms: 76.7 vs baseline 85.0 (-8.3 pts > 3)
  ✗ 3 T2 violation(s) — process assertions failed
reference gate: FAIL
  ✗ abstention: 90.0 vs baseline 100.0 (-10.0 pts > 3)
  ✗ multi_step: 65.0 vs baseline 72.8 (-7.8 pts > 3)
  ✗ 2 T2 violation(s) — process assertions failed

both fail: True · disjoint reasons: True
reading: variance per Backline §A5.5's pre-declared failure modes
gateway-only T2 violations (named, not folded in):
  · contract_terms-016 (contract_terms) ['cites_clause']
```

**Gate**

```
uv run ruff check .          All checks passed!
uv run ruff format --check . 164 files already formatted
uv run mypy                  Success: no issues found in 163 source files
uv run pytest                1250 passed, 2 deselected, 1 warning in 19.71s
```

**The stale-curve pin is green.** `test_the_committed_curve_is_the_one_this_corpus_produces`
has been red since `d57aa33` — through three PHASE_LOG entries, deliberately, because clearing
it meant sweeping a corpus that each of those sessions had just proven carried bad probes. The
sweep ran at `c94657c` on the corpus the operator approved, and the pin has been green since.
It was doing its job the whole time: REPORT.md's H1 numbers now follow from the committed
corpus, and they did not before.

Run with the compose stack up (`make up`), so nothing skips — 143 of these tests skip loudly
on a missing store endpoint rather than inventing a fallback, and a gate reported with them
skipped is not this repo's gate.

---

## Phase 9 — AWS: the playbook, second run (2026-08-11)

Branch `claude/p9-aws`, GitHub #11. BUILD_PLAN §P9's words are the spec: *"ECS Fargate
(gateway + ui) behind a two-listener ALB locked to the home /32, RDS Postgres 16 +
pgvector, **real DynamoDB** (on-demand, pennies — the A1 code path unchanged), **one
Lambda**: the nightly cost-rollup … plus CloudWatch alarms that would actually page.
Terraform file-per-concern, all the destroy flags from day one, secrets out of state,
**cost-allocation tags activated in Billing before the first apply**, evidence in the repo
not in a bucket."*

**Dated in UTC**, which is what every artifact in this entry stamps; the session ran on the
operator's local evening of 2026-08-10.

**Nothing in this PR has been applied to an AWS account.** Invariant 2 gives every
`terraform apply`, every `destroy`, and every AWS mutation to the human. What Claude Code
ran is `terraform fmt`, `init -backend=false`, and `validate`; everything else in the
verification below is the compose stack, a container on the operator's own machine, and the
Lambda's own packaged artifact.

**Shipped**

- **`deploy/aws/`, two Terraform roots split by lifetime** (**H-074**) — `data` (VPC, RDS,
  both DynamoDB tables, both ECR repositories, three secret containers) and `compute` (ALB,
  both Fargate services, the rollup Lambda, four alarms, the log groups, one interface
  endpoint). File-per-concern, twenty-three `.tf` files, both `validate` clean.
  `compute` reads `data` through `terraform_remote_state`; `data` names nothing in
  `compute`, so `destroy compute` is a plan somebody reads rather than a `-target` flag
  somebody types. §P10 reuses the data layer, and the runbook's step 10 checks that by
  running `terraform -chdir=../data plan` and expecting `No changes.`
- **All the destroy flags, from day one**: `skip_final_snapshot`, `deletion_protection = false`,
  `backup_retention_period = 0` and `delete_automated_backups` on RDS; `force_delete` on both
  ECR repositories; `deletion_protection_enabled = false` on both tables;
  `enable_deletion_protection = false` on the load balancer; and
  **`recovery_window_in_days = 0`** on all three secrets — the one that is not obvious, because
  the 30-day default keeps a deleted secret's *name* reserved and turns this phase's own
  teardown-and-rebuild into a `ValidationException` four weeks later.
- **No secret in the repo, in a tfvars file, or in Terraform state** (**H-077**). Terraform
  creates three empty `aws_secretsmanager_secret` resources and **no version**; the values go
  in by hand with a leading space (runbook §4). RDS generates its own master password
  (`manage_master_user_password = true`), so even that never passes through a plan — the
  alternatives, a `password` argument and `random_password`, both write it to state. What
  state *does* hold is identifiers and ARNs, listed in H-077 rather than left to be
  discovered, and an ARN names a secret without being one.
- **No NAT gateway** (**H-075**), which is $1.08/day — forty percent of the bill — removed by
  running the Fargate tasks in public subnets with no inbound rule except the load balancer's,
  and DynamoDB through a *free* gateway endpoint. RDS and the Lambda stay in private subnets
  with no default route. The one endpoint that is not free (~$0.48/day, Secrets Manager, for
  the Lambda) is in the **compute** root, so it is not charged while only the data layer
  stands — and the runbook says plainly that a scheduled ECS task would have cost nothing and
  that it is a Lambda because Lambda is one of the two gaps this project exists to close.
- **The console reaches the gateway by service discovery**, not through the load balancer, so
  the ALB's security group stays at exactly one source address — §P9's "locked to the
  operator's home /32", kept literally true rather than widened to "…and also anything in
  this VPC".
- **`migrations/0007_daily_rollups.sql`** — the first **derived** table in this schema, and
  `LedgerStore.write_daily_rollup` / `list_rollups` on both implementations, asserted by the
  same contract suite as `totals` and `series` (**H-073**). A day is **replaced**, not
  accumulated into: `DELETE` then `INSERT … SELECT` in one transaction, which is what makes
  the schedule safe to retry and safe to fire by hand at a gate.
- **`headroom/rollup/`** — `resolve_days`, `run_rollup`, the Lambda handler, and
  `python -m headroom.rollup`. The handler is a wrapper: it resolves which days, calls the
  store method, closes the pool in a `finally`, and prints one line of JSON in the same shape
  every request the gateway serves is logged in. The scheduled run covers **today and
  yesterday** (**H-078**), because the ledger writer is fire-and-forget and a request that
  arrived at 23:59:59 can land its row after midnight.
- **`GET /admin/usage/rollups`**, read-only, declared above `/{request_id}` — and there is
  deliberately **no route that fires the rollup**. `ui/lib/proxy.ts` named "a Phase 9 rollup
  trigger" as its example of what the console must never relay; the route it must not relay
  does not exist.
- **The console's eighth view: History.** Days rather than minutes, read from the rollups —
  a ninety-day chart is one indexed query rather than a scan of every request ever served.
  Still a client of `/admin/*` and nothing else (H-054). Its *Last rollup* tile is the
  operational one: an absent day is ambiguous ("no traffic" or "nobody rolled it up") and
  `computed_at` is the only thing on screen that tells them apart.
- **Four CloudWatch alarms** (**H-079**), three of them metric filters over the structured
  request log the gateway has emitted since Phase 1 — **no gateway code was added, changed,
  or removed to be observed.** A 5xx *rate* (≥5% over five minutes, not evaluated below
  twenty requests, because one failure out of one request is a 100% error rate and not an
  incident); provider-down (≥3 in five minutes, a number that comes from H-052's breaker
  constants); a budget-gate refusal (≥1, deliberately the most sensitive); and a lost ledger
  row — H-027's own request from Phase 3, honoured six phases later.
- **The deploy image bakes `bge-small-en-v1.5`'s weights** (BUILD_PLAN L6) behind
  `--build-arg WITH_EMBED=1` on the *same* Dockerfile (**H-076**). Built and verified on the
  operator's machine: 3.81 GB unpacked, **810 MiB compressed**, serves `/healthz`, and
  `LazyEmbedder().resolve()` returns the model at 384 dimensions **with `HF_HUB_OFFLINE=1`**
  — the only check that distinguishes a baked image from one that would have downloaded on
  first use.
- **`scripts/chaos_smoke.py`** (**H-081**) — the gate's *"chaos test's keyless subset against
  the deployed stack"* as a command rather than a paragraph. Nine checks over HTTP, every
  fault injected into the MockProvider, $0.00, and **run against the compose stack before it
  was handed over**.
- **`deploy/aws/README.md`** — the runbook: twelve steps, every command in dependency order,
  the expected output under each, and the cost stated before every paid one. Plus the
  chicken-and-egg §P9's own instruction does not mention (**H-080**): AWS will not let a tag
  key be activated until it has seen it on a resource, so step 1 creates the two *free* ECR
  repositories, activates, and only then applies anything charged by the hour.
- **`docs/evidence/p9-aws/README.md`** — an eighteen-item capture list with provenance
  discipline, per P6/P7/P8.
- **Tests: 1324 → 1329 keyless** (1250 at the start of the phase), 2 live-marked and
  deselected. `test_ledger_store` 66 → 90 (the rollup contract over both stores),
  `test_admin_usage` 29 → 34, and two new files: `test_rollup` (16) and `test_deploy_aws`
  (34). **No existing test changed** except `test_ledger_writer`'s `BrokenStore`, which grew
  the two new abstract methods — H-021's intended friction.
- **CI gains a sixth job**, keyless: `terraform fmt`/`validate` over both roots and
  `make lambda-build`. Neither touches an account.

**Deferred**

- **The apply itself, and everything downstream of it** — invariant 2. The runbook is
  written, the plan validates, the images build, and the Lambda package assembles; what has
  not happened is `terraform apply`, the live streamed request, the chaos smoke against the
  ALB, the manual rollup invocation, the screenshots, the destroy, and the empty checks.
  Those are the operator's, in order, in `deploy/aws/README.md`.
- **An alarm on `expired_releases`** — H-032 named it for this phase and it is the one
  signal that is *not* in the gateway's log. It is an in-process counter reported by `GET
  /admin/budgets/{tenant}` and nothing else, so alarming on it needs the gateway to start
  emitting a line. That is a gateway change in a deploy phase; recorded here as follow-up
  work rather than smuggled in, and the honest split is stated in H-079: **alarm on what the
  process already says out loud; name what it does not.**
- **HTTPS on the load balancer.** There is no domain, so there is no ACM certificate, and the
  security control is the `/32` allow-list — which is what §P9 asks for. The cost is real and
  is in the runbook rather than in a footnote: the root admin token crosses the operator's
  own connection in the clear.
- **A `provider_open_at` timing mark** — still deferred from Phase 8 (H-065), which named it
  as "the right first change of Phase 9 or 11". It is not Phase 9's: this phase deploys the
  gateway it measured, and adding a mark here would mean the AWS run and the H2 run measured
  different code.
- **Per-key budgets, concurrency limits, prompt-cache tier pricing, latency-based breaker
  tripping** — still deferred from Phases 4, 4b, 3, and 6; untouched.

**Deviations**

1. **`WITH_EMBED` is a build argument on the root `Dockerfile`, not a second file in
   `docker/`.** H-000 anticipated Phase 9 introducing `docker/` "when there is a second thing
   to name"; it turned out not to be a second thing. The deploy image differs by an extra and
   a download, and a second Dockerfile would duplicate the base image, the layer order, the
   `COPY` set, and the entrypoint — four things that must not drift. **H-076.**
2. **CI does not build the deploy image.** `WITH_EMBED=1` is a multi-gigabyte download per
   run for a variant only `docker push` uses. The verification is a hand-run recorded in this
   entry, and `.github/workflows/ci.yml` says so beside the build it does run.
3. **`migrations/0007` adds a table rather than columns**, which every phase since 0002 has
   had to explain. This one does not extend `usage_ledger`: it is *derived* data with a
   different lifetime and a different writer, and putting a rollup's columns on a per-request
   row would have been the actual mistake. `0002` is untouched (H-003).
4. **`headroom/` gained a package (`rollup/`), a store method pair, and one admin route** —
   the first gateway code since Phase 7. All three are required by the gate's own wording:
   *"one Lambda rollup fired manually and verified visible in the dashboard's history view"*
   needs a table, a writer, a reader, and a view. Nothing else in `headroom/` changed;
   `git diff --stat` on the pre-existing modules is `core/ledger.py`, `db/ledger.py`,
   `db/memory.py`, and `api/usage.py`, all additive.
5. **`scripts/` gained a second file** (`chaos_smoke.py`), under the same rule as the first
   (H-054's): it drives the public HTTP API and writes no SQL.
6. **The stub gateway was missing `cache_avoided_unknown`** on its `/admin/usage/totals`
   fixture — a field Phase 7 added to `TotalsView` and the fixture never grew. Found while
   adding the rollup fixture beside it, and fixed: H-058 states that keeping the stub in step
   with the API's shapes is a real maintenance cost, and this is the first instalment.
7. **Additions the plan's Phase 9 text does not enumerate**, all additive: the History view
   and `GET /admin/usage/rollups`; `scripts/chaos_smoke.py`; `make lambda-build`,
   `make tf-check`, `make rollup`, `make chaos-smoke`; the fourth alarm (H-027's); Cloud Map
   service discovery (H-075); and `deploy/aws/lambda/build.py`, which exists because
   Terraform's `archive_file` zips a directory and this repo has no `zip` binary in its
   toolchain.

---

**Gate** — *human applies; smoke = a live streamed request through the ALB + the chaos
test's keyless subset against the deployed stack + one Lambda rollup fired manually and
verified in the dashboard; screenshots; **destroy the same day**; per-service empty checks.*

**That gate is the operator's and is not claimed here.** What follows is everything that
could be verified without an AWS account — which is the whole of the mechanism the gate
exercises, with the account swapped for the compose stack.

### The keyless gate

```
$ make lint
uv run ruff check .
All checks passed!
uv run ruff format --check .
171 files already formatted

$ make typecheck
uv run mypy
Success: no issues found in 168 source files

$ make test
================ 1329 passed, 2 deselected, 1 warning in 21.22s ================

$ uv run pytest -m live -q --collect-only
2/1331 tests collected (1329 deselected) in 0.21s

$ uv run pytest -q -v | grep -c SKIPPED
0
```

Per-file counts for the new and changed files:

```
90 tests/test_ledger_store.py (was 66)     16 tests/test_rollup.py
34 tests/test_deploy_aws.py                 8 tests/test_ledger_writer.py
34 tests/test_admin_usage.py (was 29)
```

```
$ make tf-check
terraform fmt -check -recursive deploy/
terraform -chdir=deploy/aws/data init -backend=false -input=false -no-color >/dev/null
terraform -chdir=deploy/aws/data validate -no-color
Success! The configuration is valid.

terraform -chdir=deploy/aws/compute init -backend=false -input=false -no-color >/dev/null
terraform -chdir=deploy/aws/compute validate -no-color
Success! The configuration is valid.
```

The console's own checks, unchanged in shape and larger by four:

```
$ make ui-check
ℹ tests 31        (was 28)
ℹ pass 31
ℹ fail 0

$ make ui-e2e
Running 8 tests using 8 workers
  ✓ the history view renders the days the rollup Lambda wrote
  8 passed (1.6s)
```

### THE LAMBDA, END TO END — from its own packaged artifact

Not the repo's `headroom` on `sys.path`: the directory Terraform's `archive_file` zips,
imported from outside the repo, connected to the compose Postgres.

```
$ make lambda-build
built deploy/aws/lambda/build: asyncpg, headroom
13.0 MiB unzipped · handler headroom.rollup.handler.handler

$ cd /tmp && PYTHONPATH=…/deploy/aws/lambda/build …/.venv/bin/python -c \
    "import headroom.rollup.handler as h, asyncpg; print(h.__file__); print(asyncpg.__file__)"
…/deploy/aws/lambda/build/headroom/rollup/handler.py
…/deploy/aws/lambda/build/asyncpg/__init__.py
```

Then the rollup itself, against five real ledger rows the chaos smoke had just left behind:

```
### ledger rows the chaos smoke left behind
ok|4|0.000046000000
upstream_stream_cut|1|0

### the rollup, run exactly as the Lambda runs it (packaged artifact, not the repo)
{
    "event": "daily_rollup",
    "days": [
        {"day": "2026-08-10", "tenants": 0, "requests": 0, "usd_cost": "0"},
        {"day": "2026-08-11", "tenants": 1, "requests": 5, "usd_cost": "0.000046000000"}
    ],
    "requests": 5,
    "duration_ms": 18.725
}

### what the table holds
    day     | requests |    usd_cost    | errored_requests | failover_requests |          computed_at
------------+----------+----------------+------------------+-------------------+-------------------------------
 2026-08-11 |        5 | 0.000046000000 |                1 |                 3 | 2026-08-11 01:32:06.654608+00

### and the same numbers through the admin API the console reads
[
    {
        "day": "2026-08-11", "tenant_id": "2f0e41a8-…", "requests": 5,
        "input_tokens": 55, "output_tokens": 28,
        "usd_cost": "0.000046000000", "unpriced_requests": 1, "errored_requests": 1,
        "cache_disabled": 5, "cache_avoided_usd": "0.000000000000",
        "cache_avoided_unknown": 0, "failover_requests": 3,
        "computed_at": "2026-08-11T01:32:06.654608Z"
    }
]

### idempotence: firing it twice is a no-op, not a doubling
1|5
```

Three things in that output are the phase in miniature. **`unpriced_requests: 1`** — the
mid-stream cut's cost is NULL, so the sum excludes it and a *count* says so; a rollup that
folded NULL into zero would understate a day nobody can re-check once the window has passed
(H-073). **`failover_requests: 3`** — the three pre-first-token faults, aggregated. And
`1|5` after a second run: one row, five requests, because a day is replaced rather than
added to.

### THE CHAOS SUBSET — the gate step, run before it was handed over

`scripts/chaos_smoke.py` against the compose gateway over HTTP. The identical command runs
against the ALB in runbook §8c.

```
$ uv run python scripts/chaos_smoke.py --base-url http://localhost:8080 --key hk_…
chaos smoke against http://localhost:8080
ok    no fault: 200, and no failover headers at all (a request the primary served has no story to tell)
ok    fault-529@mock: 200 hops=1 from=mock (want 200 hops=1 from=mock)
ok    fault-timeout@mock: 200 hops=1 from=mock (want 200 hops=1 from=mock)
ok    fault-connect@mock: 200 hops=1 from=mock (want 200 hops=1 from=mock)
ok    fault-cut: the stream ends in a terminal error event
ok    fault-cut: the reason is upstream_stream_cut, not a generic api_error
ok    fault-cut: no message_stop — a cut answer never claims to have finished
ok    fault-cut: exactly one message_start (saw 1)
ok    fault-cut: HTTP 200 — the status line was spent before the fault
{"checks": 9, "failed": 0}
```

`exactly one message_start` is H-048's splice test from the outside: two would mean two
providers wrote one answer, and it is the property worth asserting on a *deployed* stack
because a load balancer, a proxy, or a retry policy in between could break it.

### THE DEPLOY IMAGE — L6's baked weights, measured

```
$ docker build --build-arg WITH_EMBED=1 -t headroom:aws-test .
… DONE 55.0s      EXIT=0

$ docker images | grep headroom:aws-test
headroom:aws-test 3.81GB

$ docker save headroom:aws-test | gzip -6 | wc -c
849752197                                        # 810 MiB — the actual ECR push

$ docker run --rm -e HF_HUB_OFFLINE=1 headroom:aws-test /app/.venv/bin/python -c \
    "from sentence_transformers import SentenceTransformer; \
     print('embedded:', SentenceTransformer('BAAI/bge-small-en-v1.5').encode(['headroom']).shape)"
Loading weights: 100%|██████████| 199/199 [00:00<00:00, 3759.56it/s]
embedded: (1, 384)

$ (running container) healthz: {"status":"ok"}
$ (running container) LazyEmbedder().resolve()  ->  BAAI/bge-small-en-v1.5 384 dims
$ (running container) discover_migrations()     ->  0001 … 0007, all seven
```

`HF_HUB_OFFLINE=1` is the whole of that check. Without it the model would load from
HuggingFace and prove nothing about the layer.

### THE SABOTAGE RUNS — six, each reverted from a file copy

Green on the first attempt is when a suite deserves the most suspicion, and this phase's
new tests protect against failures that are *silent by construction*: renaming a log field
does not break an alarm, it stops it firing. So each was tested by breaking the thing it
protects. All six were restored from pre-sabotage copies — never with `git checkout --`,
which ate an hour of uncommitted work in Phase 7 — and every file was diffed afterwards.

*Sabotage A — a log field is renamed, and the alarm quietly stops matching* (`budget_status`
→ `budget_state` in `RequestContext.as_log_fields`):

```
FAILED tests/test_deploy_aws.py::test_every_field_an_alarm_filters_on_is_a_field_the_gateway_logs
1 failed, 33 passed
```

*Sabotage B — the budgets table is renamed under the task definition* (`DEFAULT_BUDGETS_TABLE`),
which in production means the gateway creates its own empty table beside Terraform's and the
budget gate starts from zero:

```
FAILED tests/test_deploy_aws.py::test_the_dynamodb_tables_are_the_names_the_gateway_defaults_to
1 failed, 33 passed
```

*Sabotage C — a destroy flag goes missing* (`skip_final_snapshot = false`):

```
FAILED tests/test_deploy_aws.py::test_the_database_carries_every_destroy_flag[skip_final_snapshot     = true-…]
1 failed, 33 passed
```

*Sabotage D — the Lambda reads a variable Terraform does not set* (`DATABASE_URL_SECRET_ARN_ENV`
renamed), which falls through to a compose-shaped default in a VPC where nothing is
listening on it, once a night:

```
FAILED tests/test_deploy_aws.py::test_the_rollup_lambda_is_given_the_secret_arn_the_handler_reads
1 failed, 33 passed
```

*Sabotage E — an alarm matches a value the gateway never emits* (`breaker_open` →
`circuit_open`). Well-formed, applies cleanly, matches nothing forever:

```
FAILED tests/test_deploy_aws.py::test_every_value_an_alarm_matches_is_a_value_the_gateway_can_produce
1 failed, 33 passed
```

*Sabotage F — `/admin/usage/rollups` is declared **below** `/{request_id}`.* FastAPI matches
in declaration order, so the literal route never runs and `rollups` is read as a request id
nobody has — a 404 from the ledger, which is exactly the wrong place to go looking:

```
FAILED tests/test_admin_usage.py::test_the_rollups_route_is_not_swallowed_by_the_request_id_route
FAILED tests/test_admin_usage.py::test_a_rollup_row_is_a_days_total_with_the_stamp_that_says_how_fresh_it_is
FAILED tests/test_admin_usage.py::test_a_rollup_window_bounded_beyond_a_year_is_refused
3 failed, 31 passed
```

```
=== every file identical to its pre-sabotage copy? ===
  identical  headroom/core/context.py
  identical  headroom/db/dynamo.py
  identical  deploy/aws/data/rds.tf
  identical  headroom/rollup/handler.py
  identical  deploy/aws/compute/alarms.tf
  identical  headroom/api/usage.py
```

### What the operator still has to run

`deploy/aws/README.md`, steps 1–11, in order. The clauses of §P9's gate map to it as:

| Gate clause | Runbook |
|---|---|
| human applies (data, then compute) | §1–§2, §6 |
| a live streamed request through the ALB | §8b — the only paid step, ~$0.001 |
| the chaos test's keyless subset against the deployed stack | §8c — `make chaos-smoke`, $0.00 |
| one Lambda rollup fired manually and verified in the dashboard | §8d — invoke, read back through the API, then the **History** view |
| screenshots | §9, against `docs/evidence/p9-aws/README.md`'s capture list |
| destroy the same day | §10, and `terraform -chdir=../data plan` → `No changes.` |
| per-service empty checks (not the tag scan) | §11, twelve queries, one per service |

**Assumed-facts register (§0.4)**

- **A1 — the second half is the operator's, and the code is arranged so it is the same
  code.** *"…then identically against real DynamoDB in P9."* Nothing in `headroom/db/{dynamo,
  budgets,buckets}.py` changed for this phase, and nothing in the Terraform is shaped to
  make it easier: the key names, the billing mode, and the TTL attribute are the ones the
  code already writes. The only difference between the two environments is one line the task
  definition **does not have** — `DYNAMODB_ENDPOINT_URL` — which is what makes
  `headroom/db/dynamo.py` resolve the regional endpoint and sign with the task role instead
  of the emulator's dummy credential. `test_the_deployed_gateway_is_not_told_about_an_emulator`
  asserts that absence, and `test_the_dynamodb_tables_are_the_names_the_gateway_defaults_to`
  asserts the tables are the ones the code would otherwise create for itself. The
  verification is the first conditional write against real DynamoDB, at runbook §8.
- **A7 — moved, not verified.** *"EKS + Helm on 2 small nodes for 3 days lands ≈ $20–25 …
  and yes, cost-allocation tags get **activated in Billing at P9 day one** this time."* The
  activation is now a runbook step with the chicken-and-egg solved (H-080) rather than an
  instruction that cannot be followed literally, and the data layer carries `Layer=data` so
  Cost Explorer can answer "what did the data layer cost during P10's window" — which is the
  number A7's estimate-versus-actual table needs. The estimate itself is Phase 10's.
- **H-001's promise comes due at runbook §7.** *"The Phase 9 RDS instance must be Postgres 16
  with the `vector` extension enabled from the RDS-supported list, which it is."*
  `migrations/0005` runs `CREATE EXTENSION vector`; locally that extension comes from the
  `pgvector/pgvector:pg16` image, and RDS is the first place it has to come from AWS's own
  list. It is the one step in the runbook that says "if it fails, stop and paste the error".
- **A2, A3, A4, A5, A6** — not due at this gate, none touched. A4 and A5 stay VERIFIED: this
  phase changed no byte of the passthrough, and all 1329 tests that prove them are green.

**Spend — $0.00 in this session.** No AWS resource was created, no provider API was called,
and the deploy image was built from a local cache and a public index. Against §0.6's **$5–8**
for P9 infrastructure: the runbook projects **$3–4** from list price for one day of the full
stack, with the data layer then running at **~$0.53/day** until Phase 10 destroys it. The
actual figure goes in this entry after the operator's run, whichever way it lands.

### CI

CI on PR-9 ([run 31450971052](https://github.com/sergioavilax/headroom/actions/runs/31450971052)),
all **six** jobs green on the first run:

```
$ gh run view 31450971052 --json conclusion,jobs
success
lint + typecheck: success
pytest (postgres + dynamodb-local service containers): success
ui lint + typecheck + unit tests + build: success
ui browser smoke (chromium, stub gateway): success
terraform validates, and the Lambda package builds: success
gateway and ui images build and serve: success
```

**The sixth job is new and is the whole of what CI can honestly say about a deployment it
will never perform.** `terraform fmt -check`, `init -backend=false`, and `validate` over
both roots, plus `make lambda-build` — no AWS credential is read anywhere in the workflow
(`grep -c AWS_ACCESS` over the log returns `0`), the providers come from the registry, and
`validate` reads no data source. Everything *else* about the deployment is asserted by
`tests/test_deploy_aws.py` in the `test` job, which holds the `.tf` files to the constants
the gateway actually uses. Invariant 2 gives the apply to the human; this is the part a
machine can check.

Building the Lambda package here is not ceremony either: a handler that grew an import of
`fastapi`, or an `asyncpg` with no wheel for the runtime, now fails on the pull request
rather than at the apply.

**One annotation, fixed in-branch.** The first run carried a deprecation notice —
`hashicorp/setup-terraform@v3` targets Node 20 and the runner forces it onto 24. Bumped to
`v4.0.1`, which is the same class of fix Phase 0 applied to `checkout` and `setup-uv`, and
for the same reason: an annotation nobody acts on is an annotation nobody reads. Confirmed
rather than assumed on the branch head
([run 31451242728](https://github.com/sergioavilax/headroom/actions/runs/31451242728), six
jobs green): the only remaining `deprecat` line in the whole log is the
`StarletteDeprecationWarning` from `fastapi.testclient` that Phase 0 deliberately left
visible.

**And one real defect the first run did not catch, fixed in the same push.**
`aws_security_group.service` carried an inline `egress` block *and* three standalone
`aws_vpc_security_group_ingress_rule` resources. Both `validate` and `plan` accept that;
the AWS provider is explicit that it does not survive contact with a second apply, because
an inline rule set is authoritative and revokes what it does not know about. The group now
has **no** inline rules at all — the self-referencing ingress (the console reaching the
gateway) is what forced standalone rules in the first place, and mixing the two styles on
one group is a rule that vanishes silently on the apply after the one that worked. Every
other group in both roots is pure-inline; this one is pure-standalone, and the file says
why.

The `image` job continues to build the **default** gateway image, not the deploy variant:
`WITH_EMBED=1` is a multi-gigabyte download per run for a build only `docker push` uses,
and its verification is the hand-run recorded above.

---

## Phase 9 — closed: the operator ran it on AWS, and five things fell out (2026-08-11)

The entry above ends with *"Nothing in this PR has been applied to an AWS account."* That
is no longer true. The operator ran `deploy/aws/README.md` end to end on the evening of
2026-08-10 (UTC stamps throughout, so the artifacts read 08-10/08-11): data apply, both
images pushed, three secrets placed by hand, compute apply, all seven migrations on RDS
including `0005`'s `CREATE EXTENSION vector`, the whole of §8's smoke, the evidence
captured, compute destroyed the same day, and the data layer's plan afterwards reading
`No changes.`

**Invariant 2 held throughout.** Every `apply`, every `destroy`, every AWS mutation was the
human's; this session read what came back, cross-checked it, and wrote the four documents
and one test below. Nothing in this entry was executed by Claude Code against an account.

### The evidence, cross-checked rather than filed

Sixteen artifacts in `docs/evidence/p9-aws/`. Every number in them was re-derived here
before this entry was written, because a capture list nobody recomputes is a filing cabinet:

- **The live row's arithmetic.** 15 input tokens at $1.00/MTok and 7 output at $5.00/MTok
  is `0.000015 + 0.000035 = 0.000050`, and `06-live-ledger-row.json` carries
  `"usd_cost": "0.000050000000"` beside the two rates it was billed at. That is H-024's
  guarantee — *the row says what it was priced at, not what a price table says today* — on
  AWS, with an Anthropic request id (`req_011CdvDQhev8amkduqtVeUKr`) behind it.
  `05-live-request-headers.txt` has the 200, the `x-headroom-request-id`, and **no**
  `x-headroom-failover-*` header of any kind.
- **The whole day reconciles to the dollar.** The rollup's `2026-08-11` is 6 requests and
  `$0.000096`: one live request at `$0.000050` plus the chaos smoke's five at `$0.000046`,
  and `$0.000046` is the *same* figure the compose-stack rehearsal produced in the entry
  above, because it is the same five deterministic mock requests. `unpriced_requests: 1`
  and `errored_requests: 1` are the mid-stream cut, whose cost is NULL and stays NULL.
  `failover_requests: 3` are `fault-529`, `fault-timeout`, and `fault-connect`.
  `cache_disabled: 6` is all of them.
- **And the console's larger numbers are the same numbers.** `11-console-overview.png`
  reads 11 requests / `$0.000142` / 6 failed over / 2 unpriced against the rollup's 6 /
  `$0.000096` / 3 / 1 — because the operator ran the chaos smoke a second time after firing
  the rollup. `0.000096 + 0.000046 = 0.000142` exactly, and 3 + 3 = 6. The two artifacts are
  ten minutes and one deliberate re-run apart, not in conflict.
- **`07-chaos-smoke.txt`: nine `ok` lines, `{"checks": 9, "failed": 0}`**, against
  `http://headroom-1115834826.us-east-1.elb.amazonaws.com:8080` — the ALB, not localhost.
  Including `exactly one message_start (saw 1)`, which is H-048's splice test asserted from
  outside a load balancer for the first time.
- **The rollup is idempotent where it counts.** `08-rollup-invoke.json` (the function's own
  return) and `09-rollup-api.json` (the same day back through `GET /admin/usage/rollups`,
  after the second invoke) agree exactly: 6 requests, `0.000096000000`. A day is replaced,
  not accumulated into (H-073). `10-console-history.png`'s *Last rollup* tile stamps
  `computed_at 2026-08-11T03:04:12Z`, which is `09`'s value to the second.
- **`15-destroy.txt`: `Destroy complete! Resources: 43 destroyed.`** and the file contains
  exactly 43 `Destruction complete` lines — the count is not a header the tail of the log
  contradicts.
- **`16-data-plan-after-destroy.txt`: `No changes. Your infrastructure matches the
  configuration.`** with all 27 data-layer resources refreshed above it. That line is the
  two-root split (H-074) paying for itself, and it is what Phase 10 starts from.
- **`17-empty-checks.txt`: every per-service query empty, and the survivors are exactly the
  right five** — `headroom-db`, `headroom_buckets`, `headroom_budgets`, `headroom/gateway`,
  `headroom/ui`. No clusters, services, load balancers, target groups, functions, rules, log
  groups, alarms, or topics. Two cosmetic blemishes in the capture (a missing closing brace
  on the ECS block, an interface-endpoint query that printed nothing rather than an empty
  table) are noted in the evidence README rather than tidied away.
- **`12-alarms.json`**: four alarms, each with `arn:aws:sns:us-east-1:…:headroom-alarms` as
  both its `alarm_actions` and its `ok_actions`.

**Shipped this session** — the four documents and one test the run's findings earned:

- **`test_every_single_line_description_is_a_string_aws_would_accept`** and
  **`test_no_resource_description_is_a_heredoc`** (**H-082**) — the charset that killed two
  applies, as a keyless test over every description string in both roots, plus the test
  that keeps the heredoc exemption honest. Both sabotage-checked (below).
- **The runbook's Prerequisites now pin `AWS_DEFAULT_REGION`** (**H-083**), quote the
  `ResourceNotFoundException` that means it is unpinned, and say that a new terminal starts
  without it. `test_the_runbook_names_the_region_the_secrets_commands_need` holds both
  halves in place.
- **H-080, amended after contact** — activation is apply-then-**retry-until-discovered**,
  possibly next-day, and never a gate. Runbook §1 rewritten to match, with the
  `ValidationException: tag key missing` quoted and a check that the keys are at least *on*
  the resources, which is the half that cannot be retrofitted.
- **The evidence README records the substitution** at item 13 and argues why the
  unstaged alarm is the stronger capture (**H-084**), and marks `02` and `18` as open with
  the reason.
- **The mangled prose is repaired as English** — `"from the home CIDR only"`, `"the gateway
  container port"`, and the one that was not cosmetic: `"More than 5 percent of requests"`,
  which the strip had reduced to `"More than 5 of requests"` by eating the `%`.

**Deferred**

- **`02-cost-allocation-tags.png` and `18-billing.png`.** Both blocked on Billing's tag-key
  discovery, which had not offered `Layer`, `Phase`, or `ManagedBy` for activation by the
  end of the session. `18` lands when Cost Explorer does; `02` lands with it, because a
  screenshot of three keys that are not there yet is a picture of an empty screen. The
  retry belongs at the start of Phase 10's first session, before the cluster exists.
- **Three description strings, frozen as applied** — `aws_security_group.workload`'s and the
  two secret descriptions in the data root. A security group's description is immutable, so
  re-wording it *replaces* a group RDS's own group references and that `No changes` depends
  on; the secrets would re-introduce the drift wrinkle below for prose. They go when the
  data layer does, at the end of Phase 10, and each says so in a comment (H-082).
- Everything the entry above defers — the `expired_releases` alarm, HTTPS on the ALB, the
  `provider_open_at` mark, per-key budgets, concurrency limits, prompt-cache tier pricing —
  is untouched.

**Deviations**

1. **Two applies died on a charset no local check enforces.** The first data apply stopped
   with a VPC and subnets already created, on an **apostrophe** in a security-group
   description; five hours later the compute plan stopped on an **em dash** in an alarm
   description — a character the apostrophe fix had no reason to look at. `fmt`, `validate`,
   `plan`, `make tf-check`, and CI's sixth job all pass on a configuration that cannot be
   applied. Fixed by the operator with a strip, and closed as a class by the CI test above.
   **H-082.**
2. **§4 lost an evening to a region.** The operator's CLI profile still defaulted to
   `us-west-2` from a previous project, so `terraform apply` went to `us-east-1` (it reads
   `var.region`) and every `aws secretsmanager put-secret-value` went elsewhere, answering
   `ResourceNotFoundException` for a secret that existed. `export
   AWS_DEFAULT_REGION=us-east-1` fixed all three at once. **H-083.**
3. **Tag activation lagged past the end of the session, so H-080's timing was wrong.** With
   both ECR repositories applied and carrying all four keys, exactly one key activated —
   `Project`, and only because another project had already made it known to Billing.
   `Layer`, `Phase`, and `ManagedBy` each answered `ValidationException: tag key missing`
   and had not surfaced in the console hours later. Amended in H-080 rather than papered
   over: the resources are tagged from their first second, which is the half A7's lesson is
   about; activation is grouping, and it can wait.
4. **The alarm screenshot is not the alarm the list asked for.** Item 13 registered
   `headroom-budget-refusals` in ALARM after a staged 402; what was captured is
   **`headroom-provider-down` in ALARM, fired organically** by §8c's three faults inside one
   five-minute window. Recorded as a substitution and argued as stronger evidence — a staged
   402 tests the plumbing an operator built to drive it, while this is a fault injected three
   steps earlier for another purpose, noticed by an alarm nobody was pointing at it, off a
   log line no code was added to emit. **H-084.**
5. **The charset fix left the data layer briefly dirty, and this is the honest version.**
   The strip changed two *secret* descriptions after the secrets existed, so the first
   post-destroy `terraform -chdir=deploy/aws/data plan` did **not** read `No changes.` — it
   showed **two in-place updates**, both description-only. The operator reconciled with an
   apply and re-ran the plan; `16-data-plan-after-destroy.txt` is that second, clean run.
   The committed evidence is therefore true and is not the first thing the operator saw,
   which is worth saying plainly: a description edited after an apply is drift like any
   other, and the two-root split's promise is about *compute's destroy* not touching data —
   not about the data root being immune to its own edits.
6. **One existing test was wrong in a way only a real run could show.**
   `test_the_only_tfvars_git_keeps_is_the_example` asserted
   `not list(DEPLOY.rglob("terraform.tfvars"))` — true only on a machine where nobody has
   ever run the runbook. Step 6 writes that file, so the suite went red on the one machine
   that had done the thing the suite is about: green CI, red laptop, which is the wrong way
   round. It now asks *git* (`git ls-files`) instead of the filesystem, which is what the
   test's own name always claimed it was doing.
7. **The charset test exempts heredoc descriptions, and the brief said every description
   string.** Held literally, the rule would strip apostrophes, em dashes, and backticks out
   of thirteen multi-paragraph `variable` descriptions that Terraform prints to a human and
   never sends to an API — degrading the files to satisfy a rule about a field they are not.
   So single-line descriptions are held to the charset everywhere, heredocs are exempt, and
   `test_no_resource_description_is_a_heredoc` makes the exemption sound by forbidding a
   `resource` from using one. Both applies that failed are caught. Recorded here because it
   is a deliberate narrowing of an instruction, not an oversight.

---

**Gate** — *human applies; smoke = a live streamed request through the ALB + the chaos
test's keyless subset against the deployed stack + one Lambda rollup fired manually and
verified in the dashboard; screenshots; **destroy the same day**; per-service empty checks.*

**Met.** Verbatim, from `docs/evidence/p9-aws/`:

### The live streamed request, through the ALB (§8b)

```
HTTP/1.1 200 OK
Content-Type: text/event-stream; charset=utf-8
request-id: req_011CdvDQhev8amkduqtVeUKr
x-headroom-request-id: hr_7277f657da9244659dee12225c08dcf5
```

no `x-headroom-failover-*` header of any kind, and the row that request wrote:

```json
{
  "request_id": "hr_7277f657da9244659dee12225c08dcf5",
  "model": "claude-haiku-4-5", "provider": "anthropic", "streamed": true,
  "outcome": "ok", "status_code": 200, "stop_reason": "end_turn",
  "input_tokens": 15, "output_tokens": 7,
  "price_effective_from": "2026-08-08",
  "usd_per_mtok_in": "1.0000000000", "usd_per_mtok_out": "5.0000000000",
  "usd_cost": "0.000050000000", "cost_status": "priced",
  "cache_disposition": "cache_disabled",
  "ttft_ms": 1259.0451359999406, "passthrough_overhead_ms": 0.024925999923652853,
  "failover_hops": 0, "started_at": "2026-08-11T03:00:42.585043Z"
}
```

`15 / 1e6 × $1.00 + 7 / 1e6 × $5.00 = $0.000050`, and `passthrough_overhead_ms` is
**25 microseconds** — through a load balancer, on Fargate.

### The chaos subset, against the ALB (§8c)

```
chaos smoke against http://headroom-1115834826.us-east-1.elb.amazonaws.com:8080
ok    no fault: 200, and no failover headers at all (a request the primary served has no story to tell)
ok    fault-529@mock: 200 hops=1 from=mock (want 200 hops=1 from=mock)
ok    fault-timeout@mock: 200 hops=1 from=mock (want 200 hops=1 from=mock)
ok    fault-connect@mock: 200 hops=1 from=mock (want 200 hops=1 from=mock)
ok    fault-cut: the stream ends in a terminal error event
ok    fault-cut: the reason is upstream_stream_cut, not a generic api_error
ok    fault-cut: no message_stop — a cut answer never claims to have finished
ok    fault-cut: exactly one message_start (saw 1)
ok    fault-cut: HTTP 200 — the status line was spent before the fault
{"checks": 9, "failed": 0}
```

### The Lambda, fired by hand, and read back three ways (§8d)

```json
{"event": "daily_rollup",
 "days": [{"day": "2026-08-10", "tenants": 0, "requests": 0, "usd_cost": "0"},
          {"day": "2026-08-11", "tenants": 1, "requests": 6, "usd_cost": "0.000096000000"}],
 "requests": 6, "duration_ms": 165.167}
```

```json
[{"day": "2026-08-11", "requests": 6, "input_tokens": 70, "output_tokens": 35,
  "usd_cost": "0.000096000000", "unpriced_requests": 1, "errored_requests": 1,
  "cache_disabled": 6, "failover_requests": 3,
  "computed_at": "2026-08-11T03:04:12.761377Z"}]
```

and the console's **History** view rendering the same day: `SPEND $0.000096`,
`REQUESTS 6`, `1 request could not be priced · 1 did not end ok · 3 failed over`,
`LAST ROLLUP 12m ago · covering 2026-08-11`. Fired twice; the numbers did not move.

### Destroyed the same day (§10)

```
aws_service_discovery_private_dns_namespace.main: Destruction complete after 44s

Destroy complete! Resources: 43 destroyed.
```

```
No changes. Your infrastructure matches the configuration.

Terraform has compared your real infrastructure against your configuration
and found no differences, so no changes are needed.
```

### The per-service empty checks (§11)

```
== ECS ==            {"clusterArns": []}   {"serviceArns": []}
== ALB / target groups ==      []   []
== Lambda / EventBridge ==     []   []
== Log groups ==               []
== Alarms / SNS ==             []   []
== What SHOULD remain ==
["headroom-db"]
{"TableNames": ["headroom_buckets", "headroom_budgets"]}
["headroom/ui", "headroom/gateway"]
```

### The keyless gate, after this session's changes

```
$ make lint
uv run ruff check .
All checks passed!
uv run ruff format --check .
171 files already formatted

$ make typecheck
uv run mypy
Success: no issues found in 168 source files

$ make test
================ 1334 passed, 2 deselected, 1 warning in 21.55s ================

$ uv run pytest -m live -q --collect-only
2/1336 tests collected (1334 deselected) in 0.20s

$ uv run pytest -q -v | grep -c SKIPPED
0

$ make tf-check
terraform fmt -check -recursive deploy/
terraform -chdir=deploy/aws/data validate -no-color
Success! The configuration is valid.
terraform -chdir=deploy/aws/compute validate -no-color
Success! The configuration is valid.
```

**1329 → 1334**, all five in `tests/test_deploy_aws.py` (34 → 39): the charset check and the
heredoc check over both roots, and the runbook's region prerequisite. No other test file
changed; `test_the_only_tfvars_git_keeps_is_the_example` was repaired rather than added to.

### The sabotage runs — two more, both from file copies

The charset test protects against a failure that is invisible until an apply is half done,
so it was checked by re-introducing exactly the two characters that stopped the two applies.

*Sabotage G — an apostrophe goes back into a security-group description* (`"from the home
CIDR only"` → `"from the operator's network only"`, the string that stopped the first data
apply):

```
FAILED tests/test_deploy_aws.py::test_every_single_line_description_is_a_string_aws_would_accept[compute]
AssertionError: security.tf:21 has ["'"] in a description; AWS rejects the whole apply
with an InvalidParameterValue naming the string: "Gateway listener, from the operator's network only"
```

*Sabotage H — an em dash goes back into a secret description* (the second failure's
character, in the other root, to prove the test is not one root's):

```
FAILED tests/test_deploy_aws.py::test_every_single_line_description_is_a_string_aws_would_accept[data]
2 failed, 37 passed
```

Both restored from pre-sabotage copies — never `git checkout --`, which ate an hour of
uncommitted work in Phase 7 — and both diffed afterwards:

```
=== every file identical to its pre-sabotage copy? ===
  identical  deploy/aws/compute/security.tf
  identical  deploy/aws/data/secrets.tf
```

**Assumed-facts register (§0.4)**

- **A1 — VERIFIED.** *"…then identically against real DynamoDB in P9."* The deployed
  gateway ran the whole of §8 against real DynamoDB with no `DYNAMODB_ENDPOINT_URL` in its
  task definition: it resolved the regional endpoint, signed with the task role, and served
  six requests including three failovers and a budget-gated path, with
  `headroom/db/{dynamo,budgets,buckets}.py` unchanged from the emulator run. The code path
  is the same code path, which is what the assumption claimed.
- **A7 — half verified, half moved.** The tags are on every resource from the first apply
  (`default_tags`, both roots), which is the half Backline's cost chase lacked. Activation
  is the half that lagged, and it is now an amended H-080 with a retry that belongs at the
  start of P10. The estimate-versus-actual table is Phase 10's and needs `Layer` active by
  then.
- **H-001 — cashed, at last.** *"The Phase 9 RDS instance must be Postgres 16 with the
  `vector` extension enabled from the RDS-supported list, which it is."* `04-migrations.txt`
  is `applied 7 migration(s): … 0005_response_cache …` from the one-off ECS task's own log:
  `CREATE EXTENSION vector` against RDS, from AWS's list rather than from the
  `pgvector/pgvector:pg16` image, first try.
- **A2–A6** — not due at this gate, none touched.

**Spend.** Projected **$3–4** for this phase from list price (`deploy/aws/README.md`'s
table: ≈$2.77/day with compute up, ≈$0.53/day for the data layer alone), against §0.6's
**$5–8** for P9 infrastructure. The compute layer stood for roughly six hours of one day
and was destroyed the same day as the gate requires, so the projection should land at or
under its low end; the data layer keeps accruing ≈$0.53/day until Phase 10 destroys it,
which comes out of §0.6's P10 line. The live request itself cost **$0.000050** — the
runbook budgeted ~$0.001 for it. **The actual figure is not in this entry yet**: Cost
Explorer needs its tag keys activated and then up to 24 hours, and three of the four keys
had not been offered for activation when the session ended. It lands with
`docs/evidence/p9-aws/18-billing.png`, and this line gets the number then, whichever way it
falls.

### CI, on the closing head

Checked on the branch head rather than assumed, the way the entry above's annotation fix
was: [run 31456533329](https://github.com/sergioavilax/headroom/actions/runs/31456533329)
on `8ef4d1a`, all **six** jobs green.

```
$ gh run view 31456533329 --json status,conclusion,jobs
status=completed conclusion=success sha=8ef4d1a
  success  lint + typecheck
  success  pytest (postgres + dynamodb-local service containers)
  success  terraform validates, and the Lambda package builds
  success  ui lint + typecheck + unit tests + build
  success  ui browser smoke (chromium, stub gateway)
  success  gateway and ui images build and serve
```

```
1334 passed, 2 deselected, 1 warning in 35.34s
8 passed (6.0s)
```

Three things worth checking rather than inferring, and all three hold. The **new charset
tests really ran on a runner** — both roots' parametrisations and the runbook's region
check appear by name in the `pytest` job's log, which matters for a test whose whole
purpose is to run somewhere the operator is not. **Zero annotations** across all six jobs
(`check-runs/{id}/annotations` is empty for every one), so nothing about this push
re-opened the deprecation the entry above closed. And the only `deprecat` line in 3,890
lines of log is still the `StarletteDeprecationWarning` from `fastapi.testclient` that
Phase 0 deliberately left visible.

The sixth job is the one this phase's two failed applies argue about, and it is worth being
precise about what it now does and does not catch. `terraform fmt -check`,
`init -backend=false`, and `validate` over both roots still say nothing about whether AWS
will accept the configuration — that is the gap H-082 exists for. What closes it is in the
`test` job instead, keylessly, as an assertion about strings: an apostrophe or an em dash
in a description now fails on the pull request, in milliseconds, rather than halfway
through creating a VPC.

---

## Phase 10 — Kubernetes: the three-day EKS window (2026-08-11)

Branch `claude/p10-eks`, GitHub #12. BUILD_PLAN §P10's words are the spec: *"A **Helm
chart** for the gateway + ui (values for image, secrets refs, resource requests, HPA
optional-off), documented against the compose parity. EKS via `eksctl` (2 small managed
nodes), RDS/DynamoDB reused from P9's Terraform (don't rebuild the data layer as k8s pods
— using managed services from k8s IS the realistic architecture and the writeup says so).
The human runs every `eksctl`/`helm` mutation; CC writes charts and runs `helm
lint`/`template`. Evidence window: three days."*

**Dated in UTC**; the session ran on the operator's local evening of 2026-08-10.

**Nothing in this PR has been applied to an AWS account or to a cluster.** Invariant 2
gives every `eksctl`, every `helm install`, every `kubectl` mutation and every AWS mutation
to the human. What Claude Code ran is `helm lint`, `helm template`, `kubeconform`,
`terraform fmt`/`validate`, `terraform output` (local state, no AWS call), the test suite,
and the new load loop against the **compose** stack.

**Shipped**

- **`deploy/k8s/headroom/` — the Helm chart.** Ten templates, a `values.yaml` whose
  defaults name no cloud at all, and a `values.schema.json` with
  `additionalProperties: false`. `helm lint` clean, `helm template` clean, and the rendered
  manifests schema-validated by `kubeconform -strict` in two shapes: the local default (6
  resources) and the full AWS shape with the load balancer, the tailscale egress proxy and
  the HPA all on (9 resources).
- **Compose parity as a test, not a claim** (**H-088**). `tests/test_deploy_k8s.py` parses
  `local.gateway_environment` and the `secrets` block out of `deploy/aws/compute/ecs.tf`
  and asserts every name appears in the chart — so the three descriptions of one gateway
  (`docker-compose.yml`, the ECS task definition, this chart) cannot drift silently. Ports,
  probe paths, and the DynamoDB table names are pinned the same way. And
  `DYNAMODB_ENDPOINT_URL` is asserted to appear **nowhere** under `deploy/k8s/`: its
  absence is assumption A1's second half, now on a third runtime.
- **One Network Load Balancer with instance targets, locked to the operator's `/32`**
  (**H-085**), and `externalTrafficPolicy: Cluster` — which is what makes the zero-drop
  claim reachable at all: the load balancer's targets are the *nodes*, so replacing a pod
  changes an `Endpoints` object and nothing about the load balancer. The console is a
  ClusterIP service reached by `kubectl port-forward`: no second load balancer, and RBAC
  rather than an IP allow-list guarding the one component that holds the admin token.
- **The chart declares no `kind: Secret` and has nowhere to put one** (**H-086**). The
  three Phase 9 credentials arrive in a Secret the operator creates by hand from Secrets
  Manager, referenced by `valueFrom.secretKeyRef`. External Secrets Operator is argued down
  rather than skipped: a second Helm release, CRDs, an IRSA role and a `ClusterSecretStore`
  to replace one command that runs once, in a three-day window, for one operator.
- **Tailscale reaches home as one egress pod** (**H-087**) — the tailscale container in
  `TS_TAILNET_TARGET_IP` mode with a two-port ClusterIP Service in front of it, so
  `VLLM_BASE_URL` points at a cluster DNS name exactly where compose puts
  `host.docker.internal`. **Nothing in `headroom/` changes, `config/routing.yaml` is
  untouched, no CRD is installed and no route is advertised into the cluster.**
- **`deploy/k8s/eksctl/cluster.yaml`** — two `t3.medium` managed nodes in the data layer's
  own VPC and public subnets (H-075 inherited: there is no NAT gateway), the OIDC provider,
  and one IRSA service account whose policy is asserted to grant **exactly** what
  `aws_iam_role.gateway_task` granted on ECS. Committed *and* generated: `make k8s-config`
  rewrites it from `deploy/aws/data`'s outputs, and the runbook's Day 1 expects `git diff`
  to be empty — which is how "is the data layer still the one this was configured against"
  becomes a step rather than a hope.
- **`deploy/k8s/render_config.py`** — the *"exact command that generates it"* §P10 asks
  for. Reads `terraform output -json` from local state and makes no AWS call; its own
  `REQUIRED_OUTPUTS` list is held to `deploy/aws/data/outputs.tf` by a test, which is the
  keyless half of the check Terraform performs for `deploy/aws/compute` (H-074).
- **`scripts/load_loop.py`** (**H-090**) — the instrument behind "zero dropped requests",
  and the part worth reading is `classify()` rather than the loop. Three outcomes: a 402 or
  429 carrying `x-headroom-error-source: gateway` is **shed** (the product working), a 2xx
  is **ok** — and under `--stream` only if the stream reached its terminal marker — and
  *everything else*, including a connection with no status line at all, is **dropped**.
  `max_gap_ms` sits beside the counts because a rollout that dropped nothing and was
  unreachable for nine seconds has an error count of zero and is still an outage.
- **Two `t3.medium` nodes, and the arithmetic that says the pods fit** (**H-089**).
  `test_every_pod_this_chart_schedules_fits_on_the_node_group_it_targets` reads the instance
  type out of the eksctl config and the requests out of `values.yaml`, models EKS's
  allocatable pessimistically, and checks both the cluster-wide sum *with the surge pod* and
  the per-node case the `preferred` anti-affinity leaves open. A Deployment whose pods do not
  fit does not fail — it sits in `Pending` with nothing red anywhere, which is H-082's
  problem in Kubernetes clothes.
- **The HPA ships and ships off**, with the two prerequisites for turning it on written
  beside it (no `metrics-server`; a node group that cannot grow). The Deployment omits
  `replicas` entirely when the HPA is enabled, so Helm and the HPA never fight over one
  field.
- **`migrations/` runs as a `pre-install,pre-upgrade` hook Job** in the gateway's own image
  — §P9's "same runner everywhere" one runtime along. Against the schema Phase 9 already
  applied it reads `up to date`, which is the confirmation that catches a chart pointed at
  the wrong database before any traffic reaches it.
- **The data layer's one change**: two Kubernetes discovery tags on the public subnets and
  one new output (`public_subnet_azs`). In place, two resources changed, nothing replaced —
  and without `kubernetes.io/role/elb` a `Service` of type LoadBalancer is created, finds no
  eligible subnet, and stays `<pending>` with the reason only in a `describe` event.
- **`deploy/k8s/README.md`** — the runbook: three days, seventeen sections, every command in
  dependency order with its expected output, and the cost stated before every paid step. Day
  1 opens with the two things Phase 9 left open (the cost-allocation tag retry, H-080 as
  amended, and `02-cost-allocation-tags.png`), because H-080 puts the retry *"at the start of
  P10's first session, before the cluster exists"*.
- **`docs/evidence/p10-eks/README.md`** — a twenty-three-item capture list with provenance
  discipline, per P6–P9, plus the two rows it inherits from Phase 9.
- **Tests: 1334 → 1390 keyless**, 2 live-marked and deselected, 0 skipped. Two new files:
  `test_deploy_k8s` (36) and `test_load_loop` (20). **No existing test changed.**
- **CI's sixth job grows a third thing it can honestly check**: `helm lint`, `helm template |
  kubeconform -strict`, and the two `fail` guards asserted **as refusals** — a guard that
  stopped guarding is otherwise invisible.

**Deferred**

- **The window itself, and everything downstream of it** — invariant 2. The chart lints and
  renders, the cluster config is generated from the live data layer, the load loop is
  verified against compose; what has not happened is `eksctl create cluster`, the install,
  the rolling upgrade, the failover demo, the screenshots, the teardown, the empty checks,
  and the billing table. Those are the operator's, in order, in `deploy/k8s/README.md`.
- **A7's estimate-versus-actual table** — the runbook projects **$17–19** for a three-day
  window against A7's **$20–25**, and the actual goes in the spend line below after the run.
  **`docs/evidence/p9-aws/18-billing.png` is still open too**, and for the same reason it has
  been since Phase 9: three of the four cost-allocation tag keys had not been offered for
  activation when that session ended. Runbook §1 retries; §17 captures both.
- **An Ingress controller, cert-manager, metrics-server, Prometheus, a service mesh, a
  GitOps controller, a Cluster Autoscaler.** Each is defensible and each would be another
  Helm release in a window whose job is to show one application deployed correctly. Named in
  the runbook's closing table rather than omitted quietly.
- **HTTPS.** There is no domain, so there is no certificate, and the security control is the
  `/32` — which is what §P9 asked for and what this inherits. The cost is the same one: the
  root admin token crosses the operator's own connection in the clear.
- **The `expired_releases` alarm, a `provider_open_at` timing mark, per-key budgets,
  concurrency limits, prompt-cache tier pricing, latency-based breaker tripping** — still
  deferred from Phases 3, 4, 4b, 6, 8 and 9; untouched.

**Deviations**

1. **One load balancer, not two.** §P9 published the gateway *and* the console on one ALB;
   this phase publishes only the gateway and reaches the console with `kubectl
   port-forward`. It is cheaper ($0.54/day) and the door is stronger (IAM and RBAC rather
   than an IP allow-list in front of a cleartext listener), and the cost is a screenshot
   whose address bar says `localhost`. Said out loud in the evidence README rather than
   left to be noticed. **H-085.**
2. **`t3.medium`, not the smallest thing available.** §P10 says "2 small managed nodes". A
   `t3.small` cannot hold two gateway pods plus the surge pod of a rolling upgrade, and the
   failure mode is a `Pending` pod rather than an error. The arithmetic is in the suite.
   **H-089.**
3. **The data layer is modified, by two subnet tags and one output.** Phase 9's two-root
   split promised that *compute's destroy* would not touch data; it never promised the data
   root would not be edited (H-082's own deviation 5 made the same point). This is an
   in-place update the runbook plans, plans, and checks. **H-088.**
4. **`scripts/` gains a third file** (`load_loop.py`), under the same rule as the first two
   (H-054's): it drives the public HTTP API and writes no SQL.
5. **`--model` and `--dialect` on the load loop.** The brief asks for a load loop for the
   rolling upgrade; the same instrument is pointed at the vLLM chain for the Day 3 kill
   demo, which turns the P6/P7 demo from a thing you watch into a thing with a number. Two
   flags, one classifier, and "zero dropped" therefore means the same thing in both
   captures.
6. **The chart's probes are `httpGet`, where compose and ECS use a shell command.** Same
   endpoint, same port, same meaning; different idiom per runtime. The endpoint is pinned to
   `headroom/api/main.py` and `ui/app/api/healthz/route.ts` by a test, so the shape being
   different cannot become the path being wrong.
7. **Additions the plan's Phase 10 text does not enumerate**, all additive: the migration
   hook Job, the PodDisruptionBudget, the tailscale egress Deployment, `values.schema.json`,
   `deploy/k8s/render_config.py`, `make helm-check` / `k8s-config` / `load-loop`, and three
   rows added to `docs/evidence/README.md`'s index — which had been missing `p8-experiments`
   and `p9-aws` since those phases closed.

---

**Gate** — *the chart in-repo with lint clean; the evidence set committed; the cluster
provably gone; `deploy/k8s/README.md` runbook complete enough that a stranger could repeat
it.*

**Two of the four are met here; the other two are the operator's.** The cluster cannot be
provably gone before it exists, and the evidence set is a capture list until there is a
window to capture. What follows is everything that could be verified without an AWS account
or a cluster.

### The keyless gate

```
$ make lint
uv run ruff check .
All checks passed!
uv run ruff format --check .
175 files already formatted

$ make typecheck
uv run mypy
Success: no issues found in 170 source files

$ make test
================ 1390 passed, 2 deselected, 1 warning in 22.28s ================

$ uv run pytest -m live -q --collect-only
2/1392 tests collected (1390 deselected) in 0.21s

$ uv run pytest -q -v | grep -c SKIPPED
0
```

Per-file counts for the two new files:

```
36 tests/test_deploy_k8s.py     20 tests/test_load_loop.py
```

```
$ make tf-check
terraform fmt -check -recursive deploy/
terraform -chdir=deploy/aws/data validate -no-color
Success! The configuration is valid.
terraform -chdir=deploy/aws/compute validate -no-color
Success! The configuration is valid.

$ make helm-check
==> Linting deploy/k8s/headroom
[INFO] Chart.yaml: icon is recommended
1 chart(s) linted, 0 chart(s) failed

helm template headroom deploy/k8s/headroom | kubeconform -strict -summary -kubernetes-version 1.31.0
Summary: 6 resources found parsing stdin - Valid: 6, Invalid: 0, Errors: 0, Skipped: 0

helm template headroom deploy/k8s/headroom --set gateway.service.type=LoadBalancer \
  --set gateway.service.loadBalancerSourceRanges={203.0.113.7/32} \
  --set vllm.enabled=true --set vllm.targetIP=100.64.0.1 --set autoscaling.enabled=true \
  | kubeconform -strict -summary -kubernetes-version 1.31.0
Summary: 9 resources found parsing stdin - Valid: 9, Invalid: 0, Errors: 0, Skipped: 0
```

The console's own checks, unchanged in shape and unchanged in count — nothing in `ui/`
moved this phase:

```
$ make ui-check
ℹ tests 31        ℹ pass 31        ℹ fail 0

$ make ui-e2e
  8 passed (2.0s)
```

### THE CHART'S THREE REFUSALS, EXECUTED

A guard that stopped guarding is invisible: the chart renders, installs, and publishes an
admin API to nobody or to everybody. So all three are run rather than described.

```
$ helm template hr deploy/k8s/headroom --set gateway.service.type=LoadBalancer
Error: execution error at (headroom/templates/gateway-service.yaml:2:4):
gateway.service.type is LoadBalancer with no loadBalancerSourceRanges: refusing to publish
the gateway to 0.0.0.0/0. Set gateway.service.loadBalancerSourceRanges
(deploy/k8s/README.md section 4)

$ helm template hr deploy/k8s/headroom --set vllm.enabled=true
Error: execution error at (headroom/templates/vllm-egress.yaml:4:4):
vllm.enabled is true but vllm.targetIP is empty: there is nowhere to forward to.
`tailscale status` prints the address of the machine running the two vLLM containers
(deploy/k8s/README.md section 5).

$ helm template hr deploy/k8s/headroom --set gateway.service.loadbalancerSourceRanges={1.2.3.4/32}
Error: values don't meet the specifications of the schema(s) in the following chart(s):
headroom:
- at '/gateway/service': additional properties 'loadbalancerSourceRanges' not allowed
```

The third is the one that would otherwise be silent. A mistyped key merges in as a new
value, leaves the real one empty, and the template's own guard never sees it — which is
`config/routing.yaml`'s `extra="forbid"` argument (H-014) arriving in a values file.

### THE LOAD LOOP — verified before it was handed over

H-081's rule: *a gate step that has never been executed is a gate step that fails at the
gate.* Against the compose stack over HTTP, with a tenant and a key provisioned through
`/admin/*`:

```
### non-streamed, 15 s, 4 in flight
{"label": "smoke-plain",  "requests": 561, "ok": 561, "shed": 0, "dropped": 0,
 "latency_ms": {"p50": 5.85, "p95": 8.95, "p99": 10.91}, "max_gap_ms": 109.9, "incidents": []}
exit=0

### streamed — the harder test: an in-flight stream is what a terminating pod can break
{"label": "smoke-stream", "requests": 566, "ok": 566, "shed": 0, "dropped": 0,
 "latency_ms": {"p50": 5.35, "p95": 8.11, "p99": 12.91}, "max_gap_ms": 108.0, "incidents": []}
exit=0
```

**And the two runs that prove the instrument can say something other than zero.** A zero
from a loop that cannot count is not a measurement.

```
### a tenant given a cap of $0.000001, then made to hit it
HTTP/1.1 402 Payment Required
x-headroom-error-source: gateway

{"label": "smoke-shed", "requests": 50, "ok": 0, "shed": 50, "dropped": 0,
 "by_status": {"402": 50}}
exit=0

### the same loop against a port with nothing listening on it
{"label": "smoke-nothing-there", "requests": 40, "ok": 0, "dropped": 40,
 "max_gap_ms": 4024.1,
 "first_incident": {"t_s": 0.027, "kind": "dropped", "status": null,
                    "detail": "ConnectError: All connection attempts failed"}}
exit=1
```

Fifty deliberate refusals score `shed` and exit 0; forty connections that went nowhere score
`dropped`, take `max_gap_ms` to the whole window, and exit 1. That is the discrimination the
whole claim rests on, checked against the real gateway rather than against a fixture.

### THE CLUSTER CONFIG — generated from the live data layer

`deploy/k8s/eksctl/cluster.yaml` is committed and was produced by the generator's own
template from `terraform output -json` against the operator's standing Phase 9 state — so
the runbook's Day 1 `git diff --stat deploy/k8s/eksctl/cluster.yaml` is a real check rather
than a ceremony. Verified to regenerate byte-identically after `ruff format` touched the
generator.

One honest wrinkle, recorded because the runbook depends on it: `public_subnet_azs` is a
**new** output and outputs only reach state on an `apply`, so the generator will refuse
until the operator runs runbook §2 — which is exactly the order §2 puts them in, and the
refusal names the step. The committed file's two zones were read out of the existing state
file rather than out of an output that does not exist yet.

### THE SABOTAGE RUNS — seven, each reverted from a file copy

Green on the first attempt is when a suite deserves the most suspicion, and this phase's
tests protect against failures that are *silent by construction*: a chart that renders
perfectly and configures the wrong thing. All seven were restored from pre-sabotage copies
— never with `git checkout --`, which ate an hour of uncommitted work in Phase 7 — and every
file was diffed afterwards.

```
A  the chart stops setting a variable the ECS task definition sets   RED
   test_the_chart_sets_every_variable_the_ecs_task_definition_sets
B  DYNAMODB_ENDPOINT_URL is set on the cluster "for parity"          RED
   test_the_deployed_gateway_is_not_told_about_an_emulator
C  the gateway asks for the 2048 MiB its Fargate task had            RED
   test_every_pod_this_chart_schedules_fits_on_the_node_group_it_targets
D  the load balancer's source-range guard is deleted                 RED (see below)
   test_a_load_balancer_cannot_be_rendered_without_source_ranges
E  the subnet tag names a cluster nobody creates                     RED
   test_the_subnet_tag_names_the_cluster_the_eksctl_config_creates
F  the header the load loop classifies on is renamed                 RED
   test_the_marker_the_loop_reads_is_the_one_the_gateway_writes
G  the pods' IAM role gains an action the ECS task role never had    RED
   test_the_pods_iam_role_grants_what_the_ecs_task_role_granted_and_nothing_more
```

```
=== every file identical to its pre-sabotage copy? ===
  identical  deploy/aws/data/variables.tf
  identical  deploy/k8s/eksctl/cluster.yaml
  identical  deploy/k8s/headroom/templates/_helpers.tpl
  identical  deploy/k8s/headroom/templates/gateway-service.yaml
  identical  deploy/k8s/headroom/values.yaml
  identical  scripts/load_loop.py
```

**Sabotage D passed on the first run, and that is the most useful thing in this section.**
The test read the raw template and asserted the string `headroom.requireSourceRanges`
appeared in it. Deleting the guard left the string in place — three lines below, in the
comment that explains the guard (*"the template refuses to render a LoadBalancer without
them at all (see `headroom.requireSourceRanges`)"*). So the test was finding the prose
*about* the rule and reporting it as the rule: **H-072's lesson, in a file whose own
docstring quotes H-072**, and invisible to anything except breaking the thing. Fixed by
reading through `code_only()` — which strips `#` lines *and* Helm's `{{/* … */}}` blocks,
because a block comment never reaches a manifest — and the same treatment was then applied
to every other raw-text assertion in the file rather than only to the one that was caught.

### One footgun, found by the first `helm lint`

`.helmignore` looks like a `.gitignore` and is not one. Helm's matcher **inverts** the
meaning of a leading `!`: where git reads `!foo` as "and keep foo after all", helm reads it
as "ignore everything that does **not** match foo". A single `!values.*.yaml.example` line
therefore excluded `Chart.yaml` and the whole `templates/` directory, and `helm lint`
answered:

```
[ERROR] templates/: Chart.yaml file is missing
[ERROR] : unable to load chart
```

— which names neither the file that caused it nor the rule that did. Recorded in
`.helmignore` itself, where the next person to reach for a negation will read it.

### What the operator still has to run

`deploy/k8s/README.md`, §1–§17, across three days. §P10's gate clauses map to it as:

| Gate clause | Runbook |
|---|---|
| the chart in-repo with lint clean | met here — `make helm-check`, and CI's sixth job |
| `kubectl get pods/svc/events` | §4, §6, §12 |
| a rolling `helm upgrade` with zero dropped requests | §8 — the load loop, `--stream`, ten minutes |
| the dashboard served from the cluster | §9 — port-forward, and `04` names the node |
| the two-vLLM failover pointed at the cluster gateway | §11 — tailscale reaches home, measured not watched |
| `helm uninstall`, `eksctl delete cluster` | §13 then §14, **in that order** |
| per-service empty checks (tombstones lie) | §15, and §16 after the data layer |
| billing estimate-vs-actual, closing A7 | §17, with Phase 9's `18-billing.png` beside it |

**Assumed-facts register (§0.4)**

- **A7 — the estimate is written down and the actual is the operator's.** *"EKS + Helm on 2
  small nodes for 3 days lands ≈ $20–25."* The runbook's table projects **$17–19** from list
  price: $2.40/day control plane, $2.00/day for two `t3.medium`, $0.54/day for the load
  balancer, $0.11/day of EBS, and the data layer's $0.53/day carried from Phase 9. Both
  outcomes are publishable and the table in §17 gets filled in either way. The other half of
  A7 — *"cost-allocation tags activated in Billing"* — is runbook §1, retried at the start of
  each day, and it is what `Layer` needs to be active for if the table is to separate the
  cluster from the data layer it is borrowing.
- **A1 — the property holds on a third runtime, and is asserted the same way.** No
  `DYNAMODB_ENDPOINT_URL` anywhere under `deploy/k8s/`, so `headroom/db/dynamo.py` resolves
  the regional endpoint and signs — now with an IRSA role rather than a task role. Nothing in
  `headroom/db/{dynamo,budgets,buckets}.py` changed. The verification is the first
  conditional write from a pod, at runbook §7.
- **A6 — due at runbook §3, before the cluster exists rather than on the day of the demo.**
  Both vLLM instances serving with the known-good parser flags, *and* the same two ports
  reachable on the machine's tailnet address — which is the half `docs/vllm.md` has never had
  to check, and the half that usually fails (a host firewall, not tailscale).
- **A2, A3, A4, A5** — not due at this gate, none touched. A4 and A5 stay VERIFIED: this
  phase changed no byte of the passthrough, and all 1390 tests that prove them are green.

**Spend — $0.00 in this session.** No AWS resource was created, no cluster exists, and no
provider API was called; the load loop's 1,217 requests all went to the MockProvider on a
container on the operator's own machine. Against §0.6's **$20–25** for P10: the runbook
projects **$17–19** for a three-day window, of which ~$1.60 is the data layer that has been
running since Phase 9. The actual figure goes in this entry after the operator's run,
whichever way it lands — and so does `docs/evidence/p9-aws/18-billing.png`, which has been
waiting on the same tag keys since 2026-08-11.

---

### CI

CI on PR-12, all **six** jobs green on the first run
([run 31460210153](https://github.com/sergioavilax/headroom/actions/runs/31460210153)),
and green again on the branch head after the one annotation it carried was cleared
([run 31460377415](https://github.com/sergioavilax/headroom/actions/runs/31460377415), on
`6ad2322`):

```
$ gh run view 31460377415 --json status,conclusion,jobs
status=completed conclusion=success sha=6ad2322
  success  lint + typecheck
  success  pytest (postgres + dynamodb-local service containers)
  success  terraform validates, the Lambda packages, and the chart renders
  success  ui lint + typecheck + unit tests + build
  success  ui browser smoke (chromium, stub gateway)
  success  gateway and ui images build and serve

1390 passed, 2 deselected, 1 warning in 38.32s
```

**The sixth job grew a third thing it can honestly check**, and the parts worth naming
because they are the parts that could have been ceremony:

```
1 chart(s) linted, 0 chart(s) failed
Summary: 6 resources found parsing stdin - Valid: 6, Invalid: 0, Errors: 0, Skipped: 0
Summary: 9 resources found parsing stdin - Valid: 9, Invalid: 0, Errors: 0, Skipped: 0
both guards fired
```

`helm lint` catches a chart that is not a chart. `helm template` catches a template that
does not render. **`kubeconform -strict` catches the one neither of them looks at**: a
`maxUnavaialble` typo renders perfectly, applies cleanly, and is silently ignored by a real
cluster. Both shapes are rendered — the local default and the full AWS one with the load
balancer, the tailscale proxy and the HPA all on — because the LoadBalancer branch is the
one a default render never reaches and it is the one with a guard in it. And `both guards
fired` is the two `fail` refusals asserted **as refusals**: a guard that stopped guarding
would otherwise install cleanly and publish an admin API to `0.0.0.0/0` with nothing red
anywhere.

No AWS credential is read anywhere in the workflow, no cluster is contacted, and
`kubeconform` fetches schemas from a public index rather than from an API server. Invariant
2 gives the apply, the `eksctl` and the `helm install` to the human; this is the part a
machine can check.

**One annotation, fixed in-branch — the third time this repo has had this exact one.**
`azure/setup-helm@v4.3.1` targets Node 20 and the runner forces it onto 24. Bumped to
`v5.0.1`, which is the same class of fix Phase 9 applied to `hashicorp/setup-terraform`
(v3 → v4.0.1) and Phase 0 to `checkout` and `setup-uv`, for the same reason. Confirmed on
the branch head rather than assumed: `check-runs/{id}/annotations` is **empty for every one
of the six jobs**, and the only remaining `deprecat` line in the whole log is the
`StarletteDeprecationWarning` from `fastapi.testclient` that Phase 0 deliberately left
visible.

`kubeconform` is installed by `curl` and a pinned release tag rather than by an action, so
the job's two tool installs are symmetric and neither adds a third-party Node runtime to
the workflow. `KUBE_VERSION` is pinned to `1.31.0` in both the workflow and the Makefile and
is **not** `latest`: a schema set from a newer Kubernetes accepts fields this chart's own
`kubeVersion` floor does not promise are there, so the check would drift more permissive
without anyone deciding to.

---

## Phase 10 — the §8 drop, found and fixed mid-window (2026-08-11)

A pre-teardown session on `claude/p10-eks` while the cluster was still up. The operator ran
§8 twice, both runs read non-zero, and the evidence went into the branch before anything was
diagnosed — which is what made this session a find-fix-verify rather than a guess. The verify
is still the operator's: run 3, against a live cluster, with the commands in §8.

### Shipped

**The finding.** A rolling `helm upgrade` dropped exactly one in-flight streamed request per
replaced pod. Run 1 at `preStopSleepSeconds: 5` — 8331 requests, 1 dropped at t=87s. Run 2
with the sleep tripled to 15 — 8326 requests, 2 dropped, one per termination. Every drop read
`RemoteProtocolError: Server disconnected without sending a response.`, and `max_gap_ms`
stayed at 258-402 ms across both, so there was no outage window at any point: purely
connection-level, and **the sleep length was irrelevant**.

**The red herring, killed first.** The terminating pods exit in `Error`, not `Completed`,
which pointed hard at the entrypoint — `uv run --no-sync uvicorn …`, with `uv` at PID 1 and
possibly eating SIGTERM. Reproduced against compose, it is innocent on every count: `uv`
catches SIGTERM (bit 15 in `/proc/1/status`'s `SigCgt`), forwards it, and propagates the
child's status; uvicorn logs its full graceful shutdown; `Application shutdown complete`
means the lifespan ran and the H-027 ledger drain fired. The 143 is uvicorn's
`Server.capture_signals()` deliberately re-raising the signal it shut down for, and
Kubernetes renders any non-zero exit as `Error`. Cosmetic. The runbook now says so where an
operator will read it, because it is the kind of clue that costs an afternoon.

**The actual cause.** A pod is sent SIGTERM and removed from Endpoints at the same instant.
The preStop sleep covers the *new connection* race while kube-proxy catches up and has no
reach whatsoever over a connection that already exists — conntrack pins an established flow
to the pod it was given to. A client holding keep-alive connections spends the entire sleep
talking to the pod that is about to stop, and loses whatever it had written when uvicorn
closes them.

**The fix — a lame-duck drain (H-091).** `preStop` touches a sentinel file *before* it
sleeps; a pod that has seen the sentinel answers every response with `Connection: close`;
clients retire those connections themselves during the sleep and open the next one against a
pod Endpoints has already moved them to. `headroom/api/drain.py` is the whole of it — a
latching `DrainSwitch` and a `DrainMiddleware` added *inside* `RequestContextMiddleware` so
the outermost middleware stays the one whose docstring requires it. One values key,
`gateway.lifecycle.drainFilePath`, feeds both the hook and `HEADROOM_DRAIN_FILE`, so the two
halves cannot disagree about where the sentinel lives.

**Reproduced on a laptop, at the cost of one insight** — `scripts/endpoints_proxy.py` and
`scripts/rollout_repro.sh`. Two gateway containers sharing one database, a sixty-line
kube-proxy that pins established connections and points new ones at the replacement, the
real load loop across the switch. The first version measured **zero drops on the broken
build**, and that null result is the finding: `httpcore` checks whether a pooled socket has
become readable before it reuses one, so on loopback the server's FIN always wins and the
client quietly opens a new connection. The race window is one RTT. With 2 ms of emulated
latency on the close path:

```
baseline   12150 requests   2 dropped   t=17.0s   RemoteProtocolError: Server disconnected…
baseline   12142 requests   1 dropped   t=17.0s   ReadError
baseline   12372 requests   1 dropped   t=17.0s   RemoteProtocolError: Server disconnected…
drain      12087 requests   0 dropped
drain      12108 requests   0 dropped
drain      12359 requests   0 dropped
```

Three for three, against three for three, and the last line of each arm is from the committed
scripts rather than from a scratch copy.

**The instrument lesson (H-092).** §11's first run inherited `--timeout 15.0`, the
mock-tuned default, against 27B inference that takes 12-16 s to first token — and scored
**fourteen legitimate completions as `dropped`**. Run 2 with `--timeout 60`, GPU killed
mid-run: 92/92, `dropped: 0`. Both files stay committed. The runbook now passes `--timeout`
explicitly in both §8 and §11, and the flag's `--help` carries the story so the next person
to point the loop somewhere new reads it while they are choosing.

**Two smaller §11 fixes.** The Terminal C watch used `jq '.rows[]'` against `/admin/usage`,
which answers a bare array — it printed nothing, silently. Now `.[]`.

### Deferred

**Run 3 is the operator's, and it is not optional.** Everything above is a mechanism proved
keylessly and a race reproduced at 2 ms on loopback. The claim §P10 makes is about a rolling
upgrade in us-east-1, and it is unproven until §8 reads `dropped: 0` there. The exact
commands are in `deploy/k8s/README.md` §8 under "Run 3", including the part that is easy to
get wrong: the upgrade that *rolls the fix out* is measured by the old build, so the run that
counts is the upgrade after it, with draining pods on both sides.

**The residual is real and named.** A connection idle in a client's pool for the whole drain
window and first reused in the milliseconds after SIGTERM is still broken, because nothing
ever handed it a `Connection: close` to act on. No server-side change closes that. If run 3
catches one, that is the residual and it goes in the log in those words rather than being
re-run until it flatters.

### Deviations

**A `docker-compose.yml` change in a Kubernetes fix.** `HEADROOM_DRAIN_FILE` is now set for
the compose gateway too. It changes nothing locally — nothing writes the sentinel — and it is
what makes the cluster's race reproducible on a laptop, which is the difference between a fix
and a plausible fix. Called out because it touches a file no other part of this work needed.

**Two new files in `scripts/`.** The repro rig, under the same rule as the other three: it
drives the public HTTP API, writes no SQL, and spends nothing. It is a diagnostic rather than
a test, so it is not in the pytest gate; what *is* in the gate is the mechanism it exercises
(`tests/test_drain.py`, nine tests) and the chart-to-code agreement it depends on
(`tests/test_deploy_k8s.py`, three more).

### The keyless gate

```
uv run ruff check .          All checks passed!
uv run ruff format --check . 178 files already formatted
uv run mypy                  Success: no issues found in 172 source files
uv run pytest                1402 passed, 2 deselected, 1 warning in 22.12s
```

---

## Phase 10 — closed: the operator ran it on EKS, and the window was fourteen hours (2026-08-11)

Branch `claude/p10-eks`, GitHub #12. The runbook was executed end to end on a real cluster in
`us-east-1`: created ≈23:00 on 2026-08-10, deleted ≈13:00 on 2026-08-11, data layer destroyed
behind it. **Nothing of Headroom remains on AWS.** This entry closes the phase against the gate
and records what the window found, including the three things it found about itself.

**All four gate conditions are now met.** The two that were the operator's — *the cluster
provably gone* and *the evidence set committed* — are `20`/`21`/`22` and the twenty-two files in
`docs/evidence/p10-eks/`.

### Shipped in the window

- **The cluster served, live, through a Network Load Balancer.** One streamed request to
  `claude-haiku-4-5`, `x-headroom-request-id: hr_a12bb3905a424a1e8ec6c06cf73bc7aa`, **no
  `x-headroom-failover-*` headers**, and the ledger row to match: 15 in / 7 out at
  `usd_per_mtok_in 1.00 / out 5.00` from `price_effective_from: 2026-08-08`, `usd_cost:
  0.000050000000` — exact, not rounded — `cost_status: priced`, `cache_disposition:
  cache_disabled`, and **`passthrough_overhead_ms: 0.0175`** against `upstream_latency_ms:
  1006.18`. The third runtime to produce that row shape, after compose and ECS.
- **Chaos 9/9 against the cluster.** `{"checks": 9, "failed": 0}` — including the three that are
  about a cut stream saying so (`upstream_stream_cut`, no `message_stop`, exactly one
  `message_start`), through an NLB rather than an ALB.
- **The unattended overnight run.** Every pod `AGE 10h`, **`RESTARTS 0`** — gateway ×2, console,
  vLLM egress, and the completed migration Job.
- **Zero dropped requests on a rolling upgrade, in three runs that tell the whole story.**
  Run 1 (`preStopSleepSeconds: 5`): 8331 requests, **1 dropped**. Run 2 (sleep tripled to 15):
  8326 requests, **2 dropped** — one per replaced pod again, *the sleep is irrelevant*, which is
  the diagnosis. Run 3, against the lame-duck drain: **8342 requests, 8342 ok, `dropped: 0`,
  `incidents: []`**, `max_gap_ms: 535`, on a rollout where the pods leaving and the pods arriving
  were both draining pods. The find-fix-verify is complete and all three files are committed.
- **The tailnet path, proved from inside the cluster.** The egress pod's `Startup complete`, then
  a curl from a pod in `us-east-1` answered by a 4090 on the operator's desk listing
  `cyankiwi/Qwen3.6-27B-AWQ-INT4`. Nothing advertised into the cluster, nothing changed in
  `headroom/`.
- **A GPU killed from orbit, measured: 92 requests, 92 ok, `dropped: 0`** across a `docker kill
  vllm-a` mid-run. The ledger says the same thing from inside — 40 rows, all `200`: 22 on
  `vllm_a` at `failover_hops: 0`, 18 at `hops: 1, from: vllm_a`, of which **10 `breaker_open` and
  8 `upstream_unavailable`**. `/admin/providers` caught `vllm_a` with 25 total failures and
  `last_error: upstream_unavailable`, re-admitted after the restart.
- **The console, served from a pod in the cluster, during the kill.** `24-live-flip.png`: 104
  requests, **66 served by a fallback, `CALLER-VISIBLE 5XX: 0`**, every flipped row naming the
  hop reason. `25-breaker-open.png`: `vllm_a` **`open`**, `4/5` breakers closed, `failures 10/20`,
  `probes in 0s`.
- **A clean teardown, in the order that matters.** `helm uninstall` before `eksctl delete
  cluster`, then the per-service checks: no cluster, no stacks, no instances, no auto-scaling
  group, **no `available` EBS volume**, no load balancers of either kind, no target groups, no
  `eksctl-headroom` role, no OIDC provider — with the VPC, both data-layer security groups and
  RDS still standing, which is what says the cluster left without taking the data layer with it.
  Then `No changes.` on the data layer, `Destroy complete! Resources: 26 destroyed.`, and six
  final checks all empty. **$0 in orphans.**

### Shipped in this session (the close)

- **H-093 and H-094 formalised** — the two first-contact bugs the operator fixed and committed on
  day 1, which had a fix in the chart and no decision record. Both are the same shape and it is
  the shape worth having a number for: *the thing that broke is the thing the previous runtime
  was doing for free.*
- **The region fix pushed down into the code** (H-094). `headroom/db/dynamo.py` now resolves the
  region itself from either `AWS_REGION` or `AWS_DEFAULT_REGION` and passes it explicitly, so no
  fourth runtime has to know that botocore reads only the second name. Bounded: with no region
  stated anywhere, nothing is invented and `NoRegionError` still fires — a default there would
  mean a misconfigured pod quietly writing budget reservations to the wrong region and looking
  healthy. Six tests, sabotage-checked.
- **The load loop refuses a key that is not a key** (H-095), exiting 2 before the first request.
- **Two scar tests for the day-1 fixes**: the egress pod's two capabilities beside its
  `drop: ["ALL"]`, and the gateway's region under both names.
- **The evidence README reconciled against every file actually present** — including the five
  rows that read **not captured** in those words, and the two dashboard captures renumbered out
  of the range the runbook had reserved for `18-events.txt` and `19-uninstall.txt`.

### Deviations

1. **The three-day evidence window was run in about fourteen hours** (**H-096**). §P10 says
   *"Evidence window: three days"*; the cluster was created ≈23:00 on 2026-08-10 and deleted
   ≈13:00 on 2026-08-11. The reasoning, in full: **three days was a cost-and-scope ceiling, not
   an evidence requirement.** Nothing on §P10's list is a function of elapsed time — the rolling
   upgrade is a ten-minute measurement, the failover demo seven, the empty checks a teardown —
   and every capture the list asks for that could be taken was taken inside the window. The one
   thing duration genuinely buys was deliberately preserved: the unattended overnight run
   (`day2-pods-overnight.txt`, `AGE 10h`, `RESTARTS 0`). What it costs is stated rather than
   waved past: A7 is answered against a shorter denominator, the slow failures (a rotating
   certificate, an expiring token, a filling disk) are untested, and §10's day-2 billing check
   had almost no history to check.
2. **`24`/`25` are not the console captures the list asked for.** `12-console-overview.png` and
   `13-console-requests.png` — Overview and Requests at rest — were not taken. What the window
   produced instead is **Live traffic during the kill**, which proves the same "served from the
   cluster" claim on a harder view and proves the failover claim at the same time. The trade is
   stated in the evidence README rather than hidden: **this set has no capture of the Overview
   view on EKS at all.** Same shape as H-084 in Phase 9, and the same rule — the substitution is
   argued in the open, not renumbered into the slot it did not fill.
3. **Four install-time captures were lost** — `01-cluster-created`, `02-nodes`, `03-helm-install`,
   `05-migrate-job`: console output from a shell whose scrollback was not redirected at the time,
   in a window that then compressed. Each is partly recoverable from what did land (the two node
   names from `day1-pods.txt`, the four revisions from `11-helm-history.txt`, the migration Job's
   `Completed` line from both), and none is recoverable in full, because the cluster is gone.
   **The install is the thinnest-evidenced step of this phase**; everything from §7 onward has a
   file. Recorded because a capture list that quietly closed its own gaps would be worthless.
4. **`19-uninstall.txt` was not captured, and the property it existed to prove survived.** The
   file was to show the load-balancer list going empty *before* the cluster was deleted;
   `20-empty-checks.txt` shows both `elbv2` and `elb` lists empty with the VPC still standing,
   which is the outcome that ordering exists to produce.
5. **`18`/`19` were renumbered to `24`/`25`.** The two dashboard captures were committed under
   numbers the runbook had already reserved for the event stream (§12) and the uninstall check
   (§13). Renumbered out of that range so a reader following the runbook to a file does not find
   a screenshot of something else; `23` stays reserved for the billing capture.
6. **`docs/evidence/p9-aws/02-cost-allocation-tags.png` is still not in the repo.** Runbook §1
   retried it, per H-080 as amended. No capture landed. The evidence README's own instruction was
   that if the keys had not surfaced by §17 *"that is itself the finding and it goes in the phase
   log in those words"* — so: **it is the finding, and this is it.** Whether the three keys are
   now Active is unknown to this repo, and the §17 read below will settle it either way.
7. **A code change in a closing session** — `headroom/db/dynamo.py`. Additive, behaviour-
   preserving on both existing runtimes (compose sets no region and still gets the emulator
   default; ECS sets both names and gets the same one it got before), and it removes the need for
   a fourth runtime to rediscover H-094. Called out because a phase close is not normally where
   `headroom/` changes.

### A7 — the spend line, estimate versus actual

**Estimates from `deploy/k8s/README.md`'s table; the actuals are pending on Cost Explorer, which
lags up to 24 hours behind the final day of usage.** The window was fourteen hours, so the
comparison A7 pre-registered — three days at $20–25 — is answered against a shorter denominator,
and the **rate** is the honest number to read.

| | Estimate | Actual |
|---|---:|---:|
| EKS control plane | $2.40/day | *pending* |
| Nodes (2 × `t3.medium`) + EBS | $2.11/day | *pending* |
| Network Load Balancer | $0.54/day | *pending* |
| Data layer (`Layer=data`) | $0.53/day | *pending* |
| **Rate** | **$5.58/day** | *pending* |
| **Window total** (≈14 h, ≈0.58 day) | **≈$3.25** | *pending* |
| **The three-day figure the table projected** | **$17–19** | not run |
| **A7's pre-registered estimate** | **$20–25** | not run |

The only Cost Explorer read taken during the window is Phase 9's day-of check: **$0.04** for the
Phase 9 deploy day. **`23-billing.png` and `docs/evidence/p9-aws/18-billing.png` land tomorrow as
a docs-only commit to `main` after this PR merges** — both need the same 24-hour lag, and neither
is worth a branch.

**A7 is therefore not yet answered, and the reason is arithmetic rather than reluctance.** A
fourteen-hour window cannot confirm or falsify a three-day estimate directly; what it can do is
give the rate the estimate was built from, which is what the table above will carry.

### Deferred

- **`23-billing.png`, `../p9-aws/18-billing.png`, and the actuals column** — tomorrow, on `main`.
- **`02-cost-allocation-tags.png`** — deviation 6. If the keys are Active when §17 is read, the
  capture goes with the billing commit; if they are not, that is the closing state of H-080 and
  the Phase 9 spend line stays unattributable, which is a fact about AWS Billing rather than
  about this project.
- **What a longer window would have tested** — deviation 1's second cost. Named, not fixed.
- **The residual H-091 names** — a connection idle for the whole drain window and first reused in
  the milliseconds after SIGTERM. Not observed in run 3, and **not thereby ruled out**: one
  600-second run at four in flight is not a proof of absence, and the decision record still says
  so in those words.
- **An Ingress controller, cert-manager, metrics-server, Prometheus, a service mesh, a GitOps
  controller, a Cluster Autoscaler, HTTPS** — unchanged from the opening entry.
- **The `expired_releases` alarm, a `provider_open_at` timing mark, per-key budgets, concurrency
  limits, prompt-cache tier pricing, latency-based breaker tripping** — still deferred from
  Phases 3, 4, 4b, 6, 8 and 9; untouched.

### The keyless gate

```
$ make lint
uv run ruff check .
All checks passed!
uv run ruff format --check .
178 files already formatted

$ make typecheck
uv run mypy
Success: no issues found in 172 source files

$ make test
================ 1415 passed, 2 deselected, 1 warning in 21.29s ================

$ uv run pytest -m live -q --collect-only
2/1417 tests collected (1415 deselected) in 0.21s

$ uv run pytest -q -v | grep -c SKIPPED
0
```

**1402 → 1415**, thirteen new: six in `tests/test_dynamo_client.py` (20), five in
`tests/test_load_loop.py` (25), two in `tests/test_deploy_k8s.py` (41). No existing test changed.

```
$ make helm-check
helm lint deploy/k8s/headroom
==> Linting deploy/k8s/headroom
[INFO] Chart.yaml: icon is recommended

1 chart(s) linted, 0 chart(s) failed
helm template headroom deploy/k8s/headroom | kubeconform -strict -summary -kubernetes-version 1.31.0
Summary: 6 resources found parsing stdin - Valid: 6, Invalid: 0, Errors: 0, Skipped: 0
helm template headroom deploy/k8s/headroom --set gateway.service.type=LoadBalancer … --set autoscaling.enabled=true
Summary: 9 resources found parsing stdin - Valid: 9, Invalid: 0, Errors: 0, Skipped: 0
```

**The four sabotage checks for this session's guards**, each run and each restored from a copy:

```
remove MKNOD from the egress pod        → test_dropping_all_capabilities_…                FAILED
chart sets only AWS_REGION              → test_the_gateway_is_told_its_region_under_both… FAILED
dynamo reads only AWS_DEFAULT_REGION    → test_a_deployment_that_states_only_aws_region…  FAILED
load loop accepts an empty key          → test_a_key_that_is_not_a_key_is_refused…        FAILED ×3
```

The fourth one is a finding in its own right: the first time it was run it **hung** rather than
failing, because without the guard `main()` falls through to a loop whose default duration is
"until Ctrl-C". The test now bounds `--duration-s`, so a regression fails CI instead of stalling
it — recorded in H-095 because a sabotage check that hangs is a sabotage check nobody will run
twice.

### The gate, condition by condition

*"the chart in-repo with lint clean; the evidence set committed; the cluster provably gone;
`deploy/k8s/README.md` runbook complete enough that a stranger could repeat it."*

1. **The chart in-repo with lint clean** — ✅, above, and it is now a chart that has been
   installed four times on a real cluster rather than only rendered.
2. **The evidence set committed** — ✅ with five rows reading *not captured* and one *pending*.
   Committed and marked is the condition; committed and complete was never the condition, and
   pretending otherwise would be the one failure mode this discipline exists to prevent.
3. **The cluster provably gone** — ✅. `20`, `21`, `22`: no cluster, no orphaned load balancer,
   no `available` volume, `Destroy complete! Resources: 26 destroyed.`, six final checks empty.
4. **A stranger could repeat it** — ✅, and better than when it was written: §8 carries the drain
   finding and Run 3, §11 carries `--timeout 60` and why, and the two first-contact bugs are
   fixed in the chart the stranger would install rather than described in a log they would read
   afterwards.

**PR #12 stays open for review.** Nothing here has been merged.

## Phase 11 — README, doc-pinning, launch kit (2026-08-11)

Branch `claude/p11-readme`, GitHub #13. BUILD_PLAN §P11's words are the spec: *"a README
whose claims are **pinned by tests** recomputing every number from committed artifacts (the
H1 curve values, the H2 overhead, the parity verdicts); architecture diagram; the decision
log cross-linked; Limits section written with the same honesty … Launch kit: X thread +
LinkedIn post …, the portfolio-site SQL …, recruiter follow-up template v2 now listing
Kubernetes."*

**No gateway behaviour changed in this phase.** `headroom/` is untouched: `git diff
--stat 43dfa3d..HEAD -- headroom/` is empty. The one executable thing that landed is
`scripts/demo.py`, which drives the public HTTP API and writes no SQL — `scripts/`'s rule
since H-054.

### Shipped

- **`README.md`, rewritten as a front door and additively.** Every section the phases wrote
  is kept, in order; what changed is the top, the bottom, and the four story sections the
  evidence had earned and nobody had written down:
  - **The headline block** — the H1 finding in three sentences, then eight measured claims
    in a table, each linked to the artifact behind it, and the sentence that governs the
    rest of the file: *every number above is recomputed from a committed artifact by
    `tests/test_docs.py` on every pull request.*
  - **A one-command quickstart** (`make demo`, below) with the demo's own output quoted.
  - **A Mermaid request-path diagram** — the repo had no diagram convention and no
    `docs/ARCHITECTURE.md`, so it is in-README per the brief — with the gate order and
    *why* it is that order (H-039, H-046) underneath it rather than left to be inferred.
  - **The finding**, with both probe families, the threshold table, the two overlapping
    bands, the 0.999539 pair in full, and the paragraph that matters most: the embedding is
    not broken, it is right, and the failure is in using a similarity measurement as an
    admission decision.
  - **What the gateway costs** — parity, three overhead numbers with the caveat that the
    pre-registered one is the weakest, the admission-cost bench, and the two-meter agreement
    with its one identified residual.
  - **Zero dropped requests, and the two runs that said otherwise** — the find-fix-verify in
    the order it happened, including the loopback-FIN insight that made the laptop
    reproduction measure zero on the broken build, and the residual H-091 names.
  - **The kill demos**, all three venues, with the cooldown visible in the raw attempt
    spacing.
  - **Three things the instruments got wrong** — H-092's mock-tuned timeout, H-095's empty
    key, and H-094, the one the *runtime* got wrong: `AWS_REGION` is a variable Fargate had
    been injecting for free for an entire phase, and Kubernetes injects nothing.
  - **What a human caught that the checks could not** — H-069/H-070/H-071, the two
    systematic generation failures the operator's spot-check found and the mechanical
    checks could not see, with the fact that neither audit was reachable from the sampled
    twenty stated plainly.
  - **A Limits section** in the same voice: one operator's network, one run per experiment
    row and no paired control, the L4 scope cut, the measured drain residual, the
    fourteen-hour window, the five capture-list rows that read *not captured*, and the
    money tables with **the cloud actuals column marked pending**.
- **`tests/test_docs.py` — 32 tests, and the phase's actual deliverable.** Every figure in
  the README is pinned one of three ways, and the tier is named at each site (**H-097**):
  recomputed from the artifact that produced it; held to a constant in the code; or, where
  the primary source is a screenshot or a terminal that no longer exists, held to the
  committed document that recorded it. Plus two structural checks that will fire far more
  often than the numeric ones — **every relative link resolves** and **every `H-NNN` cited
  is a real heading** — and one that tightens itself: while
  `docs/evidence/p10-eks/23-billing.png` is absent the cost table **must** say `pending`,
  and the day it lands the test **fails** until the number replaces the word.
- **`make demo` + `scripts/demo.py`** (**H-098**) — the gate's *"cold clone reaches a
  working keyless demo in one command"*, without undoing Phase 2's refusal to ship a
  no-auth dev mode. It generates a root token into the gitignored `.env` with
  `/dev/urandom`, runs `make up` unchanged, provisions a tenant and a key through
  `/admin/*`, and then **asserts** twenty-two claims across seven acts, exiting 1 if any
  fails. It resets the tenant to the shipped defaults on the way in and clears the limit,
  the budget and the breaker on the way out, so the second run looks like the first.
- **`docs/launch/`** (**H-099**; **withdrawn the same day — see the amendment at the end of
  this entry and H-100**) — the X thread (the H1 curve is the hook, ten posts, with
  the three expected replies and the honest answer to each), the LinkedIn post, the
  portfolio-site SQL, the recruiter follow-up **v2 now listing Kubernetes**, and the third
  blog post — *the cache that lies politely*, closing the trilogy. The index carries the
  GitHub About text and topics to paste, the order to publish in, and a **"what not to
  claim"** section naming the four overstatements this material is one careless edit away
  from.
- **Docs**: H-097 … H-099 in `docs/DECISIONS.md`; this entry.

### Deferred

- **`docs/evidence/p10-eks/23-billing.png` and `docs/evidence/p9-aws/18-billing.png`**, and
  with them the actuals column of the README's cloud-cost table. Unchanged from the Phase 10
  close: Cost Explorer lags up to 24 hours and three of the four cost-allocation tag keys
  had not been offered for activation when the cluster came down. **The difference this
  phase makes is that the follow-up is now enforced rather than remembered** — the doc test
  requires `pending` today and fails the moment the capture appears.
- **`docs/ARCHITECTURE.md`**, which `CLAUDE.md`'s *Where things live* names and no phase has
  ever created. The diagram it would have carried is in the README, where a stranger will
  actually meet it; a second document repeating the request path is a second thing to drift.
  Recorded rather than quietly dropped.
- **The entity-and-period filter H1 argues for**, per-key budgets, concurrency limits,
  prompt-cache tier pricing, latency-based breaker tripping, the `expired_releases` alarm,
  a `provider_open_at` timing mark, HTTPS — all still deferred from Phases 3, 4, 4b, 6, 8, 9
  and 10, all named in the README's *What production adds* rather than omitted.

### Deviations

1. **The README is rewritten, not extended, at the top and the bottom.** Invariant 7 is
   about not stripping shipped *behaviour* to serve a later phase, and every section Phases
   2–10 wrote is present and unedited. What was replaced is the Phase 0 stub block — which
   still read *"🚧 Under construction"*, *"This README is a stub"*, and *"Phase 4 ← here"*
   after six further phases. `test_the_readme_is_not_still_announcing_that_it_is_a_stub`
   exists so that particular drift cannot recur.
2. **`docs/launch/` is a new directory §0.5's repo map does not list**, and the map names a
   `docs/ARCHITECTURE.md` that eleven phases never created. Both are amended in this PR —
   `launch/` added, `ARCHITECTURE.md` removed — the same way Phase 7 amended the map for
   `scripts/`. `CLAUDE.md`'s *Where things live* carries the identical line and is amended
   with it, because two maps that disagree are worse than one that is out of date.
   *(Amended again the same day: `launch/` is out of the map and out of the tree — the
   amendment at the end of this entry, and H-100.)*
3. **One README claim was tightened rather than copied.** `experiments/results/REPORT.md`
   says the cache serves *"99.7% of genuine paraphrases"* correctly; 99.7% is the **hit
   rate** (389 of 390) and the *correct* count is 382. The README states both numbers
   separately and the doc test pins both. REPORT.md is a Phase 8 artifact and is not edited;
   the divergence is recorded here so a reader who spots it knows which is which.
4. **The README's per-threshold table drops REPORT.md's `SWA / hits` column.** At 0.95 the
   artifact's own `swa_rate_of_hits` is 0.09949 — 9.9% — where REPORT.md prints 10.0%.
   Rather than publish a third rounding of a derived column, the README carries the counts
   and the saving (all recomputed) and one sentence of prose for the ratio. The rate is in
   `h1_curve.json` for anyone who wants it.
5. **The mock-price sabotage found the pin going through the price book, which is the
   point.** `test_the_mock_unit_cost_is_the_shipped_price_book` does not compare the README
   to a literal: it prices 11 in and 7 out through `load_price_book()` and
   `usd_for_tokens`, so editing `config/models.yaml` fails the README. Sabotage D below is
   that, executed.
6. **`scripts/` gains a fifth file.** Same rule as the other four (H-054's): it drives the
   public HTTP API, writes no SQL, and spends nothing. It is deliberately **not** in the
   pytest gate — it needs a running stack — so the suite asserts only that the target exists
   and runs the script the README quotes.
7. **Nothing in `ui/` moved**, so `make ui-check` and `make ui-e2e` are unchanged in shape
   and in count and are not re-reported here beyond the CI section.

---

**Gate** — *doc tests green; a stranger's cold clone reaches a working keyless demo in one
command; launch kit delivered.* Plus the session brief's additions: all prior tests green
(1415+), ruff + mypy --strict clean.

### THE COLD CLONE — the headline artifact

`git clone` into an empty directory, **no `.env`, no venv, no image, nothing exported**,
then one command. The main stack was brought down first so the clone owned the ports.

```
$ cd ~/code/headroom && make down
$ git clone -b claude/p11-readme . /tmp/hr-p11-gate
$ cd /tmp/hr-p11-gate && ls -a | head -6
.
..
.dockerignore
.env.example
.git
.gitattributes

$ ls -la .env
ls: cannot access '.env': No such file or directory

$ time make demo
wrote a fresh HEADROOM_ADMIN_TOKEN to .env (gitignored, never committed)
docker compose up -d --build --wait
 Container hr-p11-gate-db-1 Healthy
 Container hr-p11-gate-dynamodb-1 Healthy
 Container hr-p11-gate-gateway-1 Healthy
 Container hr-p11-gate-ui-1 Healthy
docker compose exec -T gateway uv run --no-sync python -m headroom.db.migrate
applied 7 migration(s): 0001_tenants_and_virtual_keys, 0002_usage_ledger,
                        0003_ledger_budget_columns, 0004_rate_limits,
                        0005_response_cache, 0006_ledger_failover, 0007_daily_rollups
uv run python scripts/demo.py
Using CPython 3.12.13
Creating virtual environment at: .venv
   Built headroom @ file:///tmp/hr-p11-gate
Installed 44 packages in 396ms

Headroom — the keyless demo, against http://localhost:8080
No provider key, no network, no GPU, and $0.00. Every fault below is injected
into the MockProvider over HTTP, on the same code path a real provider takes.

1. A request is priced to the picodollar, at the rates it was billed at.
  ok    POST /v1/messages -> 200
  ok    the meter read the usage block, not the text: 11 in / 7 out
  ok    11 x $0.2500000000/MTok + 7 x $1.2500000000/MTok = $0.000011500000 (priced)
  ok    the row keeps the price it was billed at, effective 1970-01-01 — editing
        config/models.yaml cannot re-bill it (H-024)

2. The same question twice is one upstream call, and the saving has a column.
        caching is off for every tenant until somebody switches it on
  ok    the first ask is a miss, and it populates
  ok    the second is cache_hit_exact, served from hr_1a1e66a369ef4c9cb59e1d987c8620ba
        — the request that produced it
  ok    and the bodies are byte-identical: an entry is replayed, never converted (H-043)
  ok    a hit is not an upstream call wearing a hat: provider None, cost $0.000000000000,
        avoided $0.000011500000 in a column of its own

3. The interesting part of a cache is what it refuses.
  ok    a stream cut mid-answer ends in a terminal error event, with no message_stop
  ok    the same words with tools declared: cache_bypass — the model may legitimately
        answer with a tool call instead of prose (H-041)
  ok    and the cache still holds 1 entries: neither was stored. One bad write here is
        served forever (invariant 6)

4. A broken primary is invisible to the caller; a broken stream never is.
  ok    the primary 529s and the fallback answers: 200, hops=1 from=mock
  ok    one request, one row, one reservation: served by mock_fallback, passed over mock
        (upstream_status_529)
  ok    and a fault *after* the first byte is never spliced: 1 message_start, not two (H-048)
  ok    an upstream 400 is forwarded verbatim, not retried: 400 from upstream — the
        fallback would say the same
  ok    the breaker on `mock` was open after 6 samples (3 failures); DELETE
        /admin/providers/mock/health puts it back in rotation immediately — closed

5. A rate limit that cannot be raced, and says when it heals.
  ok    3 requests/minute, 5 fired: [200, 200, 200, 429, 429]
  ok    429 scope=tenant:requests retry-after=20s, and it says whose it is:
        error-source=gateway

6. A budget gate that reads committed spend, before a provider is called.
  ok    a cap of $0.0001150 — about ten mock answers: 8 served, then 12 refused
  ok    402 billing_error / budget_exceeded — not 429, because a budget does not heal
        inside its window (H-032)
        this request would exceed the tenant's monthly (2026-08) budget of
        $0.000115000000: $0.000092000000 settled plus $0.000000000000 reserved leaves
        $0.000023000000, and this request reserves $0.000028500000 (34 prompt + 16
        generated tokens at the current rate)
  ok    committed = spent + reserved = $0.000092000000 + $0.000000000000 =
        $0.000092000000 against $0.000115000000 — the figure the gate compares, never
        landed spend alone

7. And the console renders exactly these numbers.
  ok    tenant demo: 34 requests, $0.000172500000 spent, 1 cache hit(s) worth
        $0.000011500000, 2 failed over, 18 errored
        sign in at http://localhost:3001 with the same HEADROOM_ADMIN_TOKEN

22/22 checks passed in 0.8s

real	0m29.538s
```

**Thirty seconds, from `git clone` to twenty-two asserted claims**, on a machine whose
Docker layer cache already held the base images — a genuinely first-ever build pays for the
`python:3.12-slim` and `pgvector` pulls on top, which is stated here rather than folded into
the number.

Three things in that output are the phase in miniature. The demo **generated its own root
token** rather than asking for one or doing without authentication, which is the whole of
H-098. Act 3's `and the cache still holds 1 entries` is invariant 6 asserted on a stranger's
laptop rather than described. And act 6's message is the budget gate explaining its own
refusal in picodollars — the D-019 scar, on a fresh clone, thirty seconds in.

The same clone, still with nothing exported beyond the two service endpoints:

```
$ DATABASE_URL=… DYNAMODB_ENDPOINT_URL=… make test
================ 1447 passed, 2 deselected, 1 warning in 21.66s ================

$ make lint
uv run ruff check .
All checks passed!
uv run ruff format --check .
180 files already formatted

$ make typecheck
uv run mypy
Success: no issues found in 173 source files
```

Then `docker compose down -v`, `rm -rf /tmp/hr-p11-gate`, and the operator's own stack back
up. Nothing of the clone remains.

### The keyless gate

```
$ make lint
uv run ruff check .
All checks passed!
uv run ruff format --check .
180 files already formatted

$ make typecheck
uv run mypy
Success: no issues found in 173 source files

$ make test
================ 1447 passed, 2 deselected, 1 warning in 22.18s ================

$ uv run pytest -m live -q --collect-only
2/1449 tests collected (1447 deselected) in 0.22s

$ uv run pytest -q -v | grep -c SKIPPED
0
```

**1415 → 1447.** The 32 are `tests/test_docs.py`. **No existing test changed** — the second
phase since Phase 2 where that is true, and it follows from the phase adding no gateway
code.

### THE DOC TESTS, INDIVIDUALLY

```
$ uv run pytest tests/test_docs.py -v
test_the_two_numbers_the_whole_h1_finding_rests_on PASSED
test_tau_zero_does_not_exist_across_the_pre_registered_grid PASSED
test_the_headline_counts_are_the_two_families_at_the_shipped_default PASSED
test_the_threshold_table_is_the_committed_curve_row_for_row PASSED
test_the_corpus_the_curve_was_swept_over PASSED
test_the_mechanism_pair_is_the_one_the_report_names PASSED
test_the_parity_verdict_is_the_committed_adjudication PASSED
test_the_passthrough_overhead_is_the_suites_own_column PASSED
test_each_live_overhead_number_cites_the_row_it_was_measured_on PASSED
test_the_live_rows_price_their_own_arithmetic PASSED
test_the_admission_cost_and_its_share_of_a_request PASSED
test_the_two_meters_agree_and_the_residual_is_one_named_request PASSED
test_the_cache_was_provably_off_for_the_whole_run PASSED
test_the_zero_drop_arc_is_three_committed_runs_in_that_order PASSED
test_the_gpu_kill_from_us_east_1_is_the_run_it_cites PASSED
test_the_two_gpu_kill_on_the_desk_is_the_h3_recording PASSED
test_the_keyless_chaos_numbers_are_the_ones_ci_runs PASSED
test_the_breaker_and_backoff_constants_are_the_shipped_ones PASSED
test_the_console_capture_numbers_come_from_the_evidence_readme PASSED
test_the_drain_repro_and_the_instrument_failures_are_in_the_phase_log PASSED
test_the_mock_unit_cost_is_the_shipped_price_book PASSED
test_the_caches_default_threshold_is_the_constant_the_gateway_uses PASSED
test_the_twelve_question_corpus_bands_are_its_committed_vectors PASSED
test_the_auth_cache_ttl_is_the_documented_number PASSED
test_the_spend_table_is_the_reports_own_arithmetic PASSED
test_the_cloud_cost_table_says_pending_until_the_billing_capture_lands PASSED
test_the_phase_9_cost_read_and_the_phase_10_rate_are_the_logged_ones PASSED
test_every_path_the_readme_links_to_exists PASSED
test_every_decision_the_readme_cites_is_a_real_entry PASSED
test_the_readme_is_not_still_announcing_that_it_is_a_stub PASSED
test_the_quickstart_really_is_one_command PASSED
test_the_claimed_test_count_is_the_number_this_session_collected SKIPPED
```

The last line is the design working: that check compares the README's stated count to
`len(request.session.items)`, and a run of one file is not the whole suite. It **skips
loudly** with the reason, exactly as H-012 requires of a check that cannot be made, and it
passes in the full run above (1447 collected, 1447 claimed).

### THE SABOTAGE RUNS — five, each restored from a file copy

Green on the first attempt is when a suite deserves the most suspicion, and this file
protects against a failure that is **silent by construction**: a number that stopped being
true. So each claim was tested by breaking the thing it protects. All five were restored
from pre-sabotage copies — never with `git checkout --`, which ate an hour of uncommitted
work in Phase 7 — and every file was diffed afterwards.

*Sabotage A — the H1 headline number is rounded* (`0.999539` → `0.9995` in the README,
which is exactly the edit a copy-editor would make and which destroys the claim, since the
whole finding is that the wrong-answer band reaches **above** the correct one):

```
FAILED tests/test_docs.py::test_the_two_numbers_the_whole_h1_finding_rests_on
1 failed, 30 passed, 1 skipped
```

*Sabotage B — a link points at an artifact that is not there* (`09c-load-loop-run3-drain.json`
→ a file name that was never captured). This is the one most likely to happen for real, in a
renumbering:

```
FAILED tests/test_docs.py::test_every_path_the_readme_links_to_exists
1 failed, 30 passed, 1 skipped
```

*Sabotage C — the README cites a decision that does not exist* (`H-091` → `H-191`):

```
FAILED tests/test_docs.py::test_every_decision_the_readme_cites_is_a_real_entry
1 failed, 30 passed, 1 skipped
```

*Sabotage D — a rate moves in the shipped price book* (`config/models.yaml`, the mock
model's `usd_per_mtok_in` from `"0.25"` to `"0.30"`). The README was **not** touched, and it
failed anyway, which is the proof that this pin goes through `load_price_book()` and
`usd_for_tokens` rather than comparing two literals:

```
FAILED tests/test_docs.py::test_the_mock_unit_cost_is_the_shipped_price_book
1 failed, 30 passed, 1 skipped
```

*Sabotage E — the billing capture lands* (`touch docs/evidence/p10-eks/23-billing.png`).
The interesting one, because it fails in the direction nobody expects a test to fail in:
the README is unchanged and still correct-as-written, and the suite goes red **because the
follow-up is now possible and has not been done**:

```
FAILED tests/test_docs.py::test_the_cloud_cost_table_says_pending_until_the_billing_capture_lands
1 failed, 30 passed, 1 skipped
```

```
=== every file identical to its pre-sabotage copy? ===
  identical  README.md
  identical  docs/DECISIONS.md
  identical  config/models.yaml
```

### The launch kit

Delivered as `docs/launch/`: `x-thread.md`, `linkedin.md`, `portfolio-insert.sql`,
`recruiter-followup-v2.md`, `blog-the-cache-that-lies-politely.md`, and an index carrying
the GitHub About line, the topics list, the publishing order, and the *what not to claim*
section. Nothing in it is published by this repo and nothing in it holds a credential.

**Withdrawn the same day** (H-100), which is why the five names above are no longer links —
the directory is gone from the tree. The amendment at the end of this entry says why, and
says plainly that the files remain in this repo's history.

**Assumed-facts register (§0.4)** — nothing was due at this gate and none of A1–A7 was
touched. A7 remains **half open**, unchanged from the Phase 10 close: the tags are on every
resource, the activation lagged, and the actuals are pending Cost Explorer. What this phase
adds is that the gap is now **enforced by a test** rather than carried in a deferred list.

**Spend — $0.00.** No provider API was called, no AWS resource was created, and every
request in the cold-clone run above went to the MockProvider on the operator's own machine.
Against §0.6's $20 project cap the running total is unchanged at **≈ $8.08–8.12**.

### CI

CI on PR-11 ([run 31548028948](https://github.com/sergioavilax/headroom/actions/runs/31548028948))
on `3f5fc70`, all **six** jobs green on the first run:

```
$ gh run view 31548028948 --json status,conclusion,headSha,jobs
completed  success  sha=3f5fc70
  success  lint + typecheck
  success  pytest (postgres + dynamodb-local service containers)
  success  terraform validates, the Lambda packages, and the chart renders
  success  ui lint + typecheck + unit tests + build
  success  ui browser smoke (chromium, stub gateway)
  success  gateway and ui images build and serve

lint + typecheck | All checks passed!
lint + typecheck | Success: no issues found in 173 source files
pytest (…service containers) | ===== 1447 passed, 2 deselected, 1 warning in 34.29s =====
terraform … | Summary: 6 resources found parsing stdin - Valid: 6, Invalid: 0, Errors: 0
terraform … | Summary: 9 resources found parsing stdin - Valid: 9, Invalid: 0, Errors: 0
terraform … | both guards fired
ui lint + typecheck + unit tests + build | ℹ tests 31  ℹ pass 31
ui browser smoke (chromium, stub gateway) |   8 passed (5.6s)
gateway and ui images build and serve | gateway healthy
gateway and ui images build and serve | console healthy
```

**1447 passed, 0 skipped in CI** — `grep -c SKIPPED` over the whole log returns `0`, so the
Postgres and DynamoDB halves of every contract suite executed against the service containers
rather than skipping (H-012).

**All 32 doc tests ran on the runner**, which is the half that matters for this particular
file:

```
$ gh run view 31548028948 --log | grep -c "test_docs.py::.*PASSED"
32
```

Thirty-two rather than thirty-one, and the extra one is
`test_the_claimed_test_count_is_the_number_this_session_collected` — the check that skips
loudly on a partial run and therefore only ever proves itself somewhere the whole keyless
suite is collected. **The README's stated test count is now verified on a machine the
operator does not own, on every pull request**, which is the strongest form the claim can
take. Nothing in this file reads a network, a model, or an account: every artifact it
recomputes from is in the repo.

**Zero annotations across all six jobs** (`check-runs/{id}/annotations` is empty for every
one), and the only `deprecat` line in the whole log is still the
`StarletteDeprecationWarning` from `fastapi.testclient` that Phase 0 deliberately left
visible.

No workflow file changed in this phase — the sixth job's three tools, the chart's two
refusals, and both image smokes are Phase 9's and Phase 10's, running unchanged over a PR
that touched no code they cover.

### Amendment (same day) — `docs/launch/` is withdrawn

**Shipped in Phase 11 above, removed from the tree hours later by operator decision.**
Recorded here rather than quietly rewritten, because a phase log that only ever records
things going in is not a log.

**Why.** Launch copy is backstage material. The recruiter follow-up template is the sharpest
case and the one that decided it: a template whose whole purpose is to read as a personal
note to one reader stops working the moment its source is public — a recipient can find the
form letter their message was cut from. The rest of the kit — the thread, the post, the blog
post, the portfolio SQL — is drafting rather than engineering, and it was sitting in the one
repo a stranger reads to judge the engineering.

**What changed.** `docs/launch/` deleted; the README's *Where everything is* row removed;
`BUILD_PLAN.md` §0.5 and `CLAUDE.md`'s *Where things live* both back to
`DECISIONS.md · PHASE_LOG.md · evidence/`; `docs/launch/` added to `.gitignore`, so the next
draft written in that path cannot be committed by reflex. No test covered the kit's contents
— H-099 said so at the time — so the suite's count is unchanged at **1447**. The one check
that did reach it, `test_every_path_the_readme_links_to_exists`, is what would have caught a
half-done removal, and it is green.

**What is not claimed.** The files remain in this repository's history, reachable by anyone
who runs `git log` — `364dd72` and its parents still carry them. That is **accepted, not
overlooked**: the decision is that this material should not be *presented* as part of the
project, and no history rewrite was performed to pretend it never was. A public repo's
history is public, and treating a `git rm` as though it were a redaction would be exactly
the sort of overstatement the withdrawn kit's own *what not to claim* section warned about.

**Phase 11's gate is unaffected.** It read *"doc tests green; a stranger's cold clone reaches
a working keyless demo in one command; launch kit delivered"* — the kit **was** delivered,
and the gate output above is untouched. §P11's own prose is left as written for the same
reason: it is the plan as pre-registered, and this is a decision taken after it rather than a
correction to it. Only §0.5's repo map moved, because a map describes the tree as it is. The
judgment call is **H-100**.
