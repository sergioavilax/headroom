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
