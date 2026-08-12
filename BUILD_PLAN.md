# HEADROOM — BUILD_PLAN.md

**An LLM gateway and control plane.** Headroom sits between applications and model providers — Anthropic-dialect and OpenAI-dialect, cloud APIs and self-hosted vLLM — and every request flows through it. Because everything flows through it, it does the things every AI-native company needs and either builds badly or buys: virtual keys and per-tenant budgets that actually enforce under concurrency, token-bucket rate limits on atomic primitives, exact + semantic response caching, provider failover with jittered backoff, and per-tenant/per-route/per-model cost attribution with a live dashboard.

> *Headroom* (audio): the space between your peak level and clipping. A gateway whose whole job is keeping tenants under their limits. It sits next to Backline on purpose.

**Why this project exists (read this when scope-lust strikes):** the job-search gap analysis says the portfolio proves *product* (Backline) and *deployment* (the AWS parity experiment) but not *platform*. Headroom is the platform layer — the system-design-interview canon (streaming proxies, rate limiting, failover, cache invalidation, cost metering) made real, public, and measured. It also closes the last two recurring listing gaps: **DynamoDB + Lambda** (Phase 9) and **Kubernetes** (Phase 10, EKS with Helm, evidence captured then torn down). And it turns Backline into a three-time measuring instrument: its exact answer key already measured the product and the deployment; Headroom points it at the platform, producing the finding nobody else can produce — **the semantic-cache safety curve** (§P8.H1).

This plan is the governing document for every build session, in the tradition of Backline's `BUILD_PLAN.md` and `AWS_DEPLOY_PLAN.md`: one Claude Code session per phase, one PR per phase, a human gate closes every phase, every judgment call logged.

---

## 0. Read Me First (governs every session)

### 0.1 Locked decisions (argued elsewhere; do not relitigate mid-build)

| # | Decision | Reason |
|---|---|---|
| L1 | **Python 3.12 + FastAPI + asyncio**, uv, ruff, mypy --strict — the Backline toolchain | Fastest stack for the operator; asyncio handles SSE passthrough; the "shouldn't a gateway be Go" question gets answered in the writeup **with a measured overhead number** (§P8.H2), not dodged |
| L2 | **Postgres 16 + pgvector** for config, virtual keys, the cost ledger, request log, and the semantic cache. **DynamoDB (conditional writes)** for token buckets and budget reservations only | One familiar datastore + exactly one new AWS primitive with a real justification: conditional writes are the *correct* concurrency primitive for atomic buckets, and a better interview story than "I used Redis." Local dev uses the official `amazon/dynamodb-local` container in compose — same code path day one |
| L3 | **Compose-first; AWS is Phase 9; EKS is Phase 10** | Fast local loop, then the deploy playbook the operator now literally owns (Backline's), then Kubernetes as a 3-day evidence window |
| L4 | **Two dialects, passthrough-first: Anthropic-dialect → Anthropic; OpenAI-dialect → any OpenAI-compatible backend (vLLM, etc.)** | Passthrough per dialect is the honest v1. **Cross-dialect translation (OpenAI-in → Anthropic-out) is explicitly OUT of scope** — that translation layer is LiteLLM's entire codebase and a swamp of edge cases; the plan names the cut instead of hiding it. Failover pairs are same-dialect only |
| L5 | **Launch providers: Anthropic + the operator's two independent single-GPU vLLM instances** (Qwen3.6-27B on dual 4090s — deliberately two instances, no tensor parallel, per the operator's own benchmark) | Real failover between two GPUs on one desk is a killer zero-cost chaos demo (§P6): kill one vLLM, watch traffic shift to the other card. Bedrock is deferred, named in "what production adds" |
| L6 | **Embeddings for the semantic cache: `BAAI/bge-small-en-v1.5`, CPU, weights baked in the deploy image** | Consistency with Backline; the baked-weights pattern already proved itself (42-second Fargate pull) |
| L7 | Repo name **`headroom`** under `sergioavilax` (use `headroom-gateway` only if the bare name collides with something the operator cares about — the old Headroom.js header library is a different universe, ignore it) | |

### 0.2 Non-negotiable invariants

1. **Claude Code runs in the LOCAL CLI, in the repo, every phase. Never the web UI.** The cloud sandbox cannot reach Docker, the vLLM boxes, AWS credentials, terraform, or kubectl — this was learned the hard way on Backline's Phase A1 and is not up for debate. `cd ~/code/headroom && claude`.
2. **The human runs every `terraform apply`, `terraform destroy`, `helm install`, `helm uninstall`, and `eksctl`/cluster mutation.** Claude Code writes files and may run `fmt`/`validate`/`plan`/`helm template`/`helm lint`. Two terminals: CC in one, the human's hands in the other.
3. **No API key ever enters the repo, a compose file's committed env, Terraform state, or a task definition's plain environment.** `.env` locally (gitignored), Secrets Manager on AWS, Kubernetes Secrets on EKS — set by the human, out of band, leading-space CLI calls.
4. **Keyless by default.** Every test runs on the MockProvider without a key; CI is fully keyless; live spend sits behind explicit `--budget` flags and `pytest -m live`. This is the Backline discipline, ported.
5. **Money rules.** Every experiment has a pre-committed budget (§0.6). Per-run caps are sized empirically, never guessed (Backline D-020's scar). The budget gate reads **committed** spend — reserved + landed — never landed alone (D-019's scar; in Headroom this isn't just a harness rule, it's the *product*: §P4).
6. **Truncated or partial upstream replies are never cached and never billed as complete** (D-021's scar, one layer down: a semantic cache that stores an amputated answer poisons every future hit).
7. **Additive phases.** Nothing existing is stripped or rewritten to serve a later phase; interfaces are designed so later phases extend (storage interfaces, provider registry, policy hooks). Modular and reusable from Phase 0 — this is also the operator's standing preference.
8. **Pre-registration.** Every experiment in Phase 8 has its hypothesis, metrics, and falsification conditions written in this plan *before* data exists. Both outcomes are publishable. Readings are never re-taken until they flatter.
9. **Evidence lives in the repo, outside every blast radius.** No "evidence bucket" inside a Terraform module (the Backline teardown ate one). Screenshots, curves, and reports commit to `docs/evidence/` and `experiments/results/`.

### 0.3 Session protocol

- One fresh **local CLI** Claude Code session per phase. Session starts by reading this file + `CLAUDE.md`; ends with a `docs/PHASE_LOG.md` entry (shipped / deferred / deviations / gate output verbatim).
- One PR per phase, branch `claude/p<N>-<slug>`, merged by the human after the gate. Tests + ruff + mypy gate every PR from Phase 0 onward.
- `docs/DECISIONS.md` (H-000…) records every judgment call with alternatives and consequences — same format as Backline's D-log.
- `/effort max` for phases 1, 4, 5, 6, 8, 9, 10 (design-heavy); high is fine for the rest.
- The human is the feedback loop on anything that touches AWS/EKS: errors get pasted back, never guessed around.

### 0.4 Assumed-facts register (greenfield adaptation of the verified-facts table)

Backline's deploy plan front-loaded facts verified by execution. Headroom is greenfield, so these are **assumptions each phase must convert to verified at its gate** — if one fails, the plan gets amended in the PR that discovers it, not silently worked around.

| # | Assumption | Verified at |
|---|---|---|
| A1 | `amazon/dynamodb-local` in compose behaves like DynamoDB for `ConditionExpression` conditional writes via boto3 `endpoint_url` | P4 gate (conflict test passes locally, then identically against real DynamoDB in P9) |
| A2 | Anthropic SDKs honor `base_url` override, so Backline can point its Anthropic provider at Headroom unchanged | P8.H2 pre-flight (one smoke call through the gateway before the paid run) |
| A3 | Both dialects expose usage on **streamed** responses (Anthropic: `message_delta.usage`; OpenAI-compat: final chunk with `stream_options: {"include_usage": true}` — vLLM supports it) | P3 gate (metered tokens from a streamed mock + one live smoke match the provider's own reported usage) |
| A4 | SSE passthrough via httpx streaming + FastAPI `StreamingResponse` preserves event order and content; **chunk boundaries are NOT guaranteed identical** and the gate asserts content/event equality, never byte-identical chunking | P1 gate |
| A5 | Tool-use blocks round-trip through Anthropic-dialect passthrough untouched (Backline's agents are tool-heavy; H2 dies without this) | P1 gate (tool-call fixture) + P8.H2 pre-flight |
| A6 | The two local vLLM instances still serve with `--tool-call-parser qwen3_xml --reasoning-parser qwen3` per the operator's known-good config | P6 live demo pre-flight |
| A7 | EKS + Helm on 2 small nodes for 3 days lands ≈ $20–25 (control plane $0.10/hr + nodes) | P10 (billing check, estimate-vs-actual table — and yes, cost-allocation tags get **activated in Billing at P9 day one** this time, the lesson from Backline's cost chase) |

### 0.5 Repo layout (target)

```
headroom/
  headroom/            # the gateway
    api/               # FastAPI app: proxy routes, admin routes, health
    dialects/          # anthropic.py, openai.py — request/response shapes, usage extraction
    providers/         # upstream clients: anthropic, openai_compat, mock (fault-injectable)
    policy/            # auth (virtual keys), rate limits, budgets, routing/failover
    cache/             # exact + semantic; pgvector store; replay-as-stream
    metering/          # dated price schedules (models.yaml), ledger writes, usage parse
    core/              # request context, tracing/log, config, storage interfaces
    db/                # migrations runner
  migrations/          # raw SQL, filename order
  config/routing.yaml  # providers + model-prefix routes, per dialect (Phase 1; H-014)
  config/models.yaml   # model ids, dialects, context windows, DATED prices (D-017 pattern)
  ui/                  # Next.js dashboard — true black #000, cool zinc surfaces, the operator's design language (read the frontend-design skill before touching it)
  scripts/             # operator tools that drive the *public* API — `make seed` (Phase 7; H-054)
  experiments/         # P8: paraphrase corpus (golden, hashed), runners, results/
  deploy/aws/          # P9 Terraform root module (Backline's file-per-concern layout)
  deploy/k8s/          # P10 Helm chart + EKS runbook
  tests/               # keyless; MockProvider; sabotage tests for every scar
  docs/                # DECISIONS.md · PHASE_LOG.md · evidence/
  CLAUDE.md            # invariants for agent sessions (write at P0)
```

### 0.6 Money (whole-project caps, set before Phase 0)

| Bucket | Cap | Notes |
|---|---|---|
| Live API spend, entire build (P1–P7 smokes) | **$3** | Everything meaningful runs on mock/vLLM |
| P8.H1 paraphrase generation (haiku) | **$1** | ~400 short generations |
| P8.H2 suite-through-gateway run (sonnet + judge) | **$10** | Backline's measured full-run cost ≈ $8.09 + slack |
| P8 contingency / heal passes | **$6** | |
| **Total live API cap** | **$20** | Hard stop; raising it is a logged decision |
| P9 AWS infra (ECS+RDS+DynamoDB+Lambda, ~1–2 days live) | ~$5–8 | The Backline playbook; destroy when idle |
| P10 EKS (3-day evidence window) | ~$20–25 | A7; **raise the AWS Budget to $60 for this month, deliberately, before P9** |

---

## Phase 0 — Bootstrap [PR-0] (~1.5 h)

Repo created by the human on GitHub (`headroom`, public, MIT), cloned locally. CC session writes: uv project (py3.12, fastapi, httpx, asyncpg, pydantic v2, boto3, sentence-transformers as an `embed` extra with the CPU-torch index pattern lifted from Backline's pyproject), ruff + mypy --strict config, pytest with `live` marker, `docker-compose.yml` (postgres:16 + pgvector, `amazon/dynamodb-local`, the gateway, later the ui), Makefile (`up`, `test`, `lint`, `typecheck`, `migrate`), CI workflow (keyless: lint, typecheck, pytest with a Postgres service container + dynamodb-local service), `CLAUDE.md` (the §0.2 invariants, venue rule #1 verbatim), empty `docs/DECISIONS.md` + `PHASE_LOG.md`, this file committed at root.

**Gate:** fresh clone + `make up && make test` green and keyless on the operator's machine; CI green on PR-0.

## Phase 1 — The proxy core: two dialects, real streaming [PR-1] (~4–5 h, /effort max)

The heart. Routes: `POST /v1/messages` (Anthropic dialect) and `POST /v1/chat/completions` (OpenAI dialect), streaming and non-streaming, proxied to a provider chosen by the routing table (static in P1: model-prefix → provider). A fault-injectable **MockProvider** speaks both dialects with scripted responses, scripted usage, scripted stream chunking, and scripted faults (429/529/timeout/mid-stream-cut) — it is the load-bearing test double for the whole project.

Non-negotiables in this phase: SSE passthrough that never buffers the whole response (first-token latency is the product); **tool_use / tool_result blocks pass through the Anthropic dialect untouched** (A5); upstream errors map to honest downstream errors with the upstream's status preserved; a request context object (request id, tenant placeholder, timings) threads through everything from day one so P3/P7 don't retrofit tracing.

**Gate:** keyless tests — streamed content equality vs mock fixtures across both dialects (A4's nuance: content/event equality, not chunk-identical); a tool-call round-trip fixture; a mid-stream-cut fixture proving the client sees a terminal error event, never a silent truncation. Plus one manual live smoke each way: a real Anthropic call and a real vLLM call through the gateway, eyeballed.

## Phase 2 — Tenancy: virtual keys and admin surface [PR-2] (~2–3 h)

Tenants and virtual keys in Postgres (`hk_...` keys, hashed at rest, scoped to allowed models/providers, active/revoked). Admin API (`/admin/tenants`, `/admin/keys`) behind a root admin token. Every proxy request authenticates a virtual key and stamps the tenant into the request context. 401/403 semantics exact.

**Gate:** auth matrix tests (missing/revoked/wrong-scope keys); admin CRUD; a revoked key is dead on the very next request (no cache of auth decisions beyond a short TTL, and the TTL is a documented number).

## Phase 3 — Metering: the ledger that matches the invoice [PR-3] (~3 h)

`config/models.yaml` with **dated price schedules** — the D-017 lesson is a founding feature here, not a bugfix: prices carry effective-date ranges, and the meter resolves the price for the request's date. Usage extracted from both dialects **including streamed responses** (A3). Every request writes a ledger row: tenant, key, route, model, provider, tokens in/out, cost at dated price, latency, cache disposition (P5 fills it), failover hops (P6 fills it), stop reason. The ledger is the dashboard's and the experiments' single source of truth.

**Gate:** a scripted mock conversation's metered cost matches a hand-computed figure to the cent; a dated-price boundary test (same request, two dates, two prices); one live smoke where Headroom's metered usage equals the provider's own reported usage exactly.

## Phase 4 — Limits and budgets: the D-019 lesson as a product [PR-4] (~4 h, /effort max)

Token-bucket rate limits (requests/min and tokens/min per key and per tenant) and **monthly budget caps** — both enforced on DynamoDB conditional writes (A1), against dynamodb-local in compose. The budget gate is reservation-based: admission reserves the request's worst-case cost (from max_tokens × dated price), the conditional write fails atomically if committed (reserved + landed) spend would exceed the cap, and reservations settle to actuals on completion — including on error and on client disconnect (settlement in a `finally`, tested).

**Gate:** the concurrency hammer — N parallel requests against a bucket sized for fewer, asserting zero oversubscription — plus the **sabotage test**: a deliberately naive landed-only gate implementation must FAIL the same hammer, proving the test can catch the bug this design exists to prevent (Backline's proven-to-fail-against-old-code discipline). DynamoDB conditional-conflict test. Clean 429 responses with retry-after.

## Phase 5 — The cache: exact, semantic, and never poisoned [PR-5] (~4 h, /effort max)

Two layers behind one interface. **Exact**: normalized-request hash → stored response, near-free wins. **Semantic**: embed the user-content of eligible requests (bge-small, CPU), pgvector cosine search over the tenant's cache namespace, hit if similarity ≥ the *tenant-configurable* threshold — the threshold is a first-class config precisely because P8.H1 is going to measure what it costs. Cached responses replay as a simulated stream when the caller asked for streaming (first token effectively instant — a demo moment). Eligibility rules are conservative and documented: single-turn user content, no tool_use in the conversation, temperature ≤ a bound, per-tenant + per-model namespacing, TTLs. **Invariant 6 enforced here:** a response with a truncation stop reason, an error, or a mid-stream cut is never written to cache — with a test that tries.

**Gate:** exact hit/miss matrix; semantic hit on a committed paraphrase fixture and *miss* on a committed hard-negative fixture (same template, different entity — the shape of danger P8 will quantify); streamed replay fixture; the poison-attempt test; ledger rows correctly marked `cache_hit_exact` / `cache_hit_semantic` with cost $0 and the *avoided* cost recorded (the dashboard's savings number needs it).

## Phase 6 — Failover and resilience: kill a GPU on camera [PR-6] (~3–4 h, /effort max)

Provider health tracking (rolling error/latency windows), retry with **jittered exponential backoff** on 429/5xx, and same-dialect failover chains from the routing table (L4): if the primary fails **before first token**, the request replays against the fallback transparently; if a stream dies **after first token**, the caller gets a terminal error event and failover does *not* silently splice mid-answer — that semantic is documented as a decision (H-xxx), because splicing is how gateways serve Frankenstein responses. A circuit breaker trips a provider out of rotation after a threshold and probes it back in.

Two proofs. **Keyless chaos test:** MockProvider fault schedules (bursts of 529s, timeouts, mid-stream cuts) driven through the full stack, asserting zero caller-visible 5xx for pre-first-token faults, correct backoff timing bounds, breaker trip/recovery, and honest terminal events for mid-stream faults. **The live demo (evidence, not a scored claim):** OpenAI-dialect chain across the operator's two vLLM instances — one per 4090 — send a sustained stream of requests, `kill` one vLLM mid-run, watch the dashboard show traffic shift to the second GPU with zero failed requests, screenshot it, bring the instance back, watch the breaker re-admit it. Total API cost: $0.00. (Pre-flight A6: confirm both instances serve with the known-good qwen3_xml parser flags.)

**Gate:** chaos suite green in CI; the two-GPU kill demo captured to `docs/evidence/` with the ledger rows showing the hop counts.

## Phase 7 — The dashboard [PR-7] (~4 h)

Next.js, the operator's design language — true black `#000000`, cool zinc surfaces, mono for numbers — and read the frontend-design skill before writing a line. Surfaces: **Overview** (live spend by tenant/model, cache savings counter, provider health tiles), **Requests** (the ledger as an explorer — filters, per-request detail with timings, cache disposition, failover hops), **Tenants & Keys** (admin CRUD over the P2 API), **Limits** (bucket fill visualizations — the audio-meter metaphor is sitting right there; a tenant's budget as a channel strip with headroom is the one flourish this UI is allowed). SSE-fed live tiles where it's cheap.

**Gate:** dashboard renders a seeded compose environment truthfully (numbers cross-checked against psql); Playwright smoke; the P6 kill demo re-run once *watching the dashboard* — that's the hero GIF.

## Phase 8 — The experiments [PR-8] (~5–6 h, /effort max) — the reason the repo exists

Three pre-registered experiments. Hypotheses, metrics, and falsification below are the pre-registration; they were written before any data existed and get adjudicated in `experiments/results/REPORT.md` with the same verdict discipline as Backline's BENCHMARK_NOTES.

### H1 — The semantic-cache safety curve (the headline)

**The gap in the world:** everyone ships semantic caching; almost nobody measures how often it silently returns the wrong answer, because measuring that requires a large question set with exact ground truth. Backline is one.

**Corpus (a golden artifact, content-hashed, committed):**
- The 133 canonical Backline questions, each with its exact expected answer from the answer key.
- **~3 paraphrases per question** (~400 probes), generated by claude-haiku-4-5 under a rubric: preserve every entity, period, figure, and intent exactly; vary only surface form. Mechanically checked (entity/period tokens must survive) + operator spot-check of a sample. Provenance recorded: every paraphrase knows its source question — **the provenance is the answer key for cache correctness.**
- **Hard negatives for free:** Backline's suite is built from templates across 150 artists and 12 periods — different questions in the same category are already each other's near-misses ("What's X's streaming rate" vs "What's Y's streaming rate"). The dangerous collision class ships with the corpus.

**Method (one payment, infinite sweep):** seed the cache with the 133 canonical entries (reference answers derived from the answer key / a prior scored Backline run). Embed every probe and every cache entry **once**, record the full similarity matrix, then replay the cache-admission decision **offline across the entire threshold range** (0.70 → 0.99, fine steps). Zero marginal API cost for the sweep; the paraphrase generation (~$1) is the only spend.

**Metrics per threshold:** hit rate · **silent-wrong-answer rate** (hit resolved to a *different* source question — with the returned answer provably wrong for the asked question, courtesy of the answer key) · modeled $ saved (hits × Backline's measured ~$0.06/query) · the resulting **savings-vs-poison curve** and its knee.

**Falsification / both-outcomes clause:** if wrong-hits are zero across the whole range even at 0.70, the finding is "semantic caching is safer than feared on entity-dense corpora" and that gets published with equal enthusiasm. If the curve has no usable knee (poison appears before meaningful savings), the finding is "semantic caching is unsafe for this workload class, here's the threshold-by-threshold proof." Every outcome is a table someone currently argues about by vibes.

### H2 — Gateway overhead + the third parity experiment

Point Backline's provider at Headroom (A2: `base_url` override, nothing else changes) and run the full 133-question suite **through the gateway** — the same suite that scored 93.3 direct-local and 92.5 on AWS now gets its third treatment: local, through one extra hop.

**Pre-registered:** (1) overall score within the documented ≤3.0 same-model noise bound of the fresh-run reference points (93.3 / 92.5 / 91.6) — the strict gate will be run and reported per the Backline §A5.5 rules, including the known variance failure modes; (2) **added latency**: per-request gateway overhead measured from Headroom's own ledger timings, pre-registered target p50 overhead **< 50 ms** (report the real number either way — this is the number that answers "why is a gateway in Python defensible"). Budget $10, `--retry-errors` heal loop available, nothing re-rolled for a friendlier draw.

> **AMENDMENT (Phase 5, 2026-08-09 — logged as H-047).** **H2 runs against a tenant with caching disabled entirely; overhead is measured on pure passthrough.** Phase 5 shipped the response cache, and a cached hit answers in microseconds without touching a provider — so a suite run against a cache-enabled tenant would report an "overhead" figure that is really a hit-rate figure, and would flatter the gateway by exactly the amount the corpus happens to repeat itself. The H2 tenant is therefore created with `cache_mode: disabled` (the shipped default, so this is a statement of what *not* to change rather than a step to remember), and `experiments/` asserts it via `GET /admin/cache/{tenant}` in the pre-flight. The ledger makes the claim checkable after the fact as well: every row carries `cache_disposition`, and the H2 report states the count of non-`cache_disabled` rows, which must be zero. Amended **before any data exists**, per invariant 8. Measuring what the cache *saves* is §P8.H1's job and it uses a different tenant.

### H3 — Failover under load (chaos, formalized)

The P6 chaos suite promoted to a reported experiment: scripted fault schedules at three intensities against the mock chain, plus the two-GPU live kill. **Pre-registered:** zero caller-visible 5xx for pre-first-token faults at every intensity; recovery (breaker re-admission) within a stated bound; mid-stream faults surface as terminal error events 100% of the time, never as silent truncation. Falsification is any silent truncation reaching a caller — that would be a real bug and a real (unflattering, still published) finding.

**Gate for P8:** all three adjudicated in REPORT.md with verdicts; the H1 curve as both committed JSON and a chart in the repo's visual language; the corpus hashed and drift-pinned; PHASE_LOG records spend vs the §0.6 caps.

## Phase 9 — AWS: the playbook, second run [PR-9] (~1 day incl. waiting)

Backline's `deploy/aws` pattern re-executed with the differences that matter: ECS Fargate (gateway + ui) behind a two-listener ALB locked to the home /32, RDS Postgres 16 + pgvector, **real DynamoDB** (on-demand, pennies — the A1 code path unchanged), **one Lambda**: the nightly cost-rollup (EventBridge schedule → aggregate the day's ledger into `daily_rollups` → the dashboard's history view reads it) — a genuine, small, defensible Lambda, not decoration — plus CloudWatch alarms that would actually page (5xx rate, provider-down, budget-gate failures). Terraform file-per-concern, all the destroy flags from day one, secrets out of state, **cost-allocation tags activated in Billing before the first apply** (the lesson), evidence in the repo not in a bucket (invariant 9).

**Gate:** human applies; smoke = a live streamed request through the ALB + the chaos test's keyless subset against the deployed stack + one Lambda rollup fired manually and verified in the dashboard; screenshots; **destroy the same day** unless P10 follows immediately; per-service empty checks (not the tag scan — tombstones lie).

## Phase 10 — Kubernetes: the three-day EKS window [PR-10] (~1 day active + 3-day window)

The gap this project was partly chosen to close. A **Helm chart** for the gateway + ui (values for image, secrets refs, resource requests, HPA optional-off), documented against the compose parity. EKS via `eksctl` (2 small managed nodes), RDS/DynamoDB reused from P9's Terraform (don't rebuild the data layer as k8s pods — using managed services from k8s IS the realistic architecture and the writeup says so). The human runs every `eksctl`/`helm` mutation; CC writes charts and runs `helm lint`/`template`.

Evidence window: three days. Capture `kubectl get pods/svc/events`, a rolling `helm upgrade` with zero dropped requests from a background load loop, the dashboard served from the cluster, the two-vLLM failover demo pointed at the cluster gateway (tailscale reaches home). Then `helm uninstall`, `eksctl delete cluster`, per-service empty checks, billing estimate-vs-actual (A7).

**Gate:** the chart in-repo with lint clean; the evidence set committed; the cluster provably gone; `deploy/k8s/README.md` runbook complete enough that a stranger could repeat it.

## Phase 11 — README, doc-pinning, launch kit [PR-11] (~3 h)

The Backline front-door standard: a README whose claims are **pinned by tests** recomputing every number from committed artifacts (the H1 curve values, the H2 overhead, the parity verdicts); architecture diagram; the decision log cross-linked; Limits section written with the same honesty (single operator's network, one run per experiment row, the L4 scope cut stated plainly). Launch kit: X thread + LinkedIn post (the H1 curve is the hook — "everyone ships semantic caching; here's how often it silently lies, measured"), the portfolio-site SQL (project insert + a third blog post if the itch strikes: the trilogy closes as *zeros → reds → the cache that lies politely*), recruiter follow-up template v2 now listing Kubernetes.

**Gate:** doc tests green; a stranger's cold clone reaches a working keyless demo in one command; launch kit delivered.

---

## Timeline & shape

~30–40 focused hours: P0–P7 ≈ 22–26 h of building (the Backline pace says this is 4–6 evening/weekend sessions or one deranged long weekend), P8 ≈ 5–6 h, P9 ≈ a day with waits, P10 ≈ a day active spread over its 3-day window, P11 ≈ 3 h. Twelve PRs. If interviews land mid-build: **P0–P8 is a complete, launchable artifact** (the finding exists before any cloud phase); P9–P10 are the skill-gap closers and can trail by a week without weakening the story.

## Risk register (top five, pre-answered)

1. **SSE passthrough subtleties** (client disconnects, upstream keepalives, backpressure) — P1 budgets real time for it; the mid-stream-cut fixture is written first.
2. **H2 tool-block passthrough breaks Backline's agents** — A5 fixture in P1 + a mandatory $0.50 pre-flight smoke before the $10 run.
3. **Paraphrase quality poisons H1** — mechanical entity checks + operator spot-check + the corpus is versioned; a bad batch is regenerated *before* any measurement, never after.
4. **EKS cost drift** — budget raised deliberately to $60 for the month, per-day billing check during the window, hard 3-day limit, teardown is a calendar event not a vibe.
5. **Scope-lust** (Bedrock, cross-dialect translation, OAuth, multi-region) — all live in "what production adds," none in v1; re-read §0's second paragraph when tempted.
