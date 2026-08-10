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
