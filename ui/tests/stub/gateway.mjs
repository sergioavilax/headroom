/**
 * A stand-in for the gateway's `/admin/*` surface, for the browser smoke.
 *
 * **Hermetic by construction.** No compose stack, no Postgres, no DynamoDB, no provider,
 * no key — one Node process serving canned JSON in exactly the shapes
 * `headroom/api/*.py` returns. That is what makes the Playwright job something CI can run
 * on every pull request rather than a thing that only works on the operator's desk.
 *
 * The fixtures are deliberately *interesting*: a tenant at 83% of its cap, a failover with
 * its `failover_from` and `failover_error`, a semantic hit with a similarity and a source
 * request id, a breaker that is open with a cooldown counting down. A smoke against an
 * empty database would prove the pages render and nothing about whether they render the
 * things they exist for.
 *
 * It also checks the credential: a request without `Authorization: Bearer <token>` gets a
 * 401, so the smoke exercises the console's token path rather than routing around it.
 */

import { createServer } from "node:http";

const TOKEN = process.env.STUB_ADMIN_TOKEN ?? "stub-root-token";
const PORT = Number(process.env.STUB_PORT ?? 8099);

const TENANT_A = "11111111-1111-4111-8111-111111111111";
const TENANT_B = "22222222-2222-4222-8222-222222222222";
const KEY_A = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa";

// Timestamps are relative to when the stub started, not fixed: every window in the
// console ends at "now", so fixed dates would put the whole fixture outside the last ten
// minutes and the smoke would be asserting against empty charts it thought were full.
// The *values* are all fixed; only the clock moves.
const now = Date.now();
const iso = (offsetMs) => new Date(now - offsetMs).toISOString();

const TENANTS = [
  {
    id: TENANT_A,
    name: "backline",
    active: true,
    created_at: iso(86_400_000),
    updated_at: iso(86_400_000),
  },
  {
    id: TENANT_B,
    name: "atlas-research",
    active: true,
    created_at: iso(43_200_000),
    updated_at: iso(43_200_000),
  },
];

const KEYS = [
  {
    id: KEY_A,
    tenant_id: TENANT_A,
    name: "suite",
    key_prefix: "hk_s1WmIE2d",
    allowed_models: ["mock-*"],
    allowed_providers: [],
    status: "active",
    created_at: iso(86_400_000),
    updated_at: iso(86_400_000),
    revoked_at: null,
  },
];

const row = (over) => ({
  request_id: "hr_0000000000000000",
  tenant_id: TENANT_A,
  key_id: KEY_A,
  route: "/v1/chat/completions",
  dialect: "openai",
  model: "cyankiwi/Qwen3.6-27B-AWQ-INT4",
  provider: "vllm_a",
  streamed: false,
  outcome: "ok",
  status_code: 200,
  upstream_status: 200,
  error_source: null,
  error_reason: null,
  stop_reason: "end_turn",
  input_tokens: 15,
  output_tokens: 64,
  reasoning_tokens: null,
  cache_read_tokens: null,
  cache_write_tokens: null,
  price_effective_from: "1970-01-01",
  usd_per_mtok_in: "0.25",
  usd_per_mtok_out: "1.25",
  usd_cost: "0.000011500000",
  cost_status: "priced",
  budget_status: "reserved",
  budget_reserved_usd: "0.000047000000",
  budget_settled_usd: "0.000011500000",
  upstream_latency_ms: 41.2,
  ttft_ms: 41.5,
  passthrough_overhead_ms: 0.019,
  total_ms: 41.6,
  cache_disposition: "cache_miss",
  cache_avoided_usd: null,
  cache_similarity: null,
  cache_source_request_id: null,
  failover_hops: 0,
  failover_from: null,
  failover_error: null,
  started_at: iso(20_000),
  ...over,
});

const ROWS = [
  row({ request_id: "hr_bc06ff1352f047f6", started_at: iso(4_000) }),
  row({
    request_id: "hr_9a56791ae62c4117",
    provider: "vllm_b",
    failover_hops: 1,
    failover_from: "vllm_a",
    failover_error: "upstream_unavailable",
    started_at: iso(9_000),
  }),
  row({
    request_id: "hr_5dc9a94feb2c4001",
    provider: null,
    upstream_status: null,
    input_tokens: null,
    output_tokens: null,
    usd_cost: "0.000000000000",
    cost_status: "not_billable",
    cache_disposition: "cache_hit_semantic",
    cache_avoided_usd: "0.000011500000",
    cache_similarity: "0.97643",
    cache_source_request_id: "hr_a2f7dac0691941f0",
    passthrough_overhead_ms: null,
    ttft_ms: 0.893,
    total_ms: 0.9,
    started_at: iso(14_000),
  }),
  row({
    request_id: "hr_1bf7c56093924820",
    tenant_id: TENANT_B,
    outcome: "budget_exceeded",
    status_code: 402,
    upstream_status: null,
    provider: null,
    error_source: "gateway",
    error_reason: "budget_exceeded",
    usd_cost: "0.000000000000",
    cost_status: "not_billable",
    budget_status: "exceeded",
    budget_settled_usd: null,
    cache_disposition: "cache_disabled",
    started_at: iso(30_000),
  }),
];

const TOTALS = [
  {
    tenant_id: TENANT_A,
    model: null,
    requests: 3,
    input_tokens: 30,
    output_tokens: 128,
    reasoning_tokens: 0,
    usd_cost: "0.000023000000",
    unpriced_requests: 0,
    errored_requests: 0,
    cache_hits_exact: 0,
    cache_hits_semantic: 1,
    cache_misses: 2,
    cache_bypasses: 0,
    cache_disabled: 0,
    cache_avoided_usd: "0.000011500000",
    failover_requests: 1,
  },
  {
    tenant_id: TENANT_B,
    model: null,
    requests: 1,
    input_tokens: 0,
    output_tokens: 0,
    reasoning_tokens: 0,
    usd_cost: "0.000000000000",
    unpriced_requests: 0,
    errored_requests: 1,
    cache_hits_exact: 0,
    cache_hits_semantic: 0,
    cache_misses: 0,
    cache_bypasses: 0,
    cache_disabled: 1,
    cache_avoided_usd: "0",
    failover_requests: 0,
  },
];

const SERIES = [0, 1, 2].map((back) => ({
  bucket_start: new Date(Math.floor((now - back * 60_000) / 60_000) * 60_000).toISOString(),
  requests: 4 - back,
  input_tokens: 30,
  output_tokens: 128,
  usd_cost: "0.000011500000",
  cache_avoided_usd: "0.000011500000",
  cache_hits: 1,
  errored_requests: back === 0 ? 1 : 0,
  unpriced_requests: 0,
  failover_requests: back === 0 ? 1 : 0,
}));

const BUDGETS = [
  {
    scope: `tenant#${TENANT_B}`,
    scope_kind: "tenant",
    scope_id: TENANT_B,
    window: "monthly",
    window_id: "2026-08",
    usd: "0.000207000000",
    spent: "0.000172500000",
    reserved: "0.000000000000",
    remaining: "0.000034500000",
    committed: "0.000172500000",
    reservations: 0,
    expired_releases: 0,
    expired_released: "0",
    created_at: iso(3_600_000),
    updated_at: iso(30_000),
  },
];

const LIMITS = [
  {
    scope: `tenant#${TENANT_A}`,
    scope_kind: "tenant",
    scope_id: TENANT_A,
    name: "backline",
    requests_per_min: 120,
    tokens_per_min: null,
    buckets: [
      {
        dimension: "requests",
        limit_per_min: 120,
        available: 117,
        reset_after_s: 2,
        reset_at: iso(-2_000),
      },
    ],
  },
];

const CACHE = [
  {
    tenant_id: TENANT_A,
    tenant_name: "backline",
    mode: "semantic",
    ttl_s: null,
    similarity_threshold: null,
    effective_ttl_s: 86400,
    effective_similarity_threshold: 0.9,
    embedding_model: "BAAI/bge-small-en-v1.5",
    entries: 12,
    semantic_entries: 12,
    body_bytes: 4821,
  },
];

const PROVIDERS = [
  {
    name: "vllm_a",
    kind: "openai_compat",
    state: "open",
    samples: 5,
    failures: 4,
    failure_ratio: 0.8,
    consecutive_failures: 4,
    total_successes: 1,
    total_failures: 4,
    p50_latency_ms: 412.5,
    p95_latency_ms: 980.1,
    last_error: "upstream_unavailable",
    reopen_in_s: 6.477,
    routes: [{ dialect: "openai", prefix: "", chain: ["vllm_a", "vllm_b"], attempts: ["vllm_a", "vllm_b"] }],
  },
  {
    name: "vllm_b",
    kind: "openai_compat",
    state: "closed",
    samples: 6,
    failures: 0,
    failure_ratio: 0,
    consecutive_failures: 0,
    total_successes: 6,
    total_failures: 0,
    p50_latency_ms: 388.4,
    p95_latency_ms: 701.2,
    last_error: null,
    reopen_in_s: null,
    routes: [{ dialect: "openai", prefix: "", chain: ["vllm_a", "vllm_b"], attempts: ["vllm_a", "vllm_b"] }],
  },
];

const ROUTES = new Map([
  ["/admin/tenants", () => TENANTS],
  ["/admin/keys", () => KEYS],
  ["/admin/usage", () => ROWS],
  ["/admin/usage/totals", () => TOTALS],
  ["/admin/usage/series", () => SERIES],
  ["/admin/budgets", () => BUDGETS],
  ["/admin/limits", () => LIMITS],
  ["/admin/cache", () => CACHE],
  ["/admin/providers", () => PROVIDERS],
]);

createServer((request, response) => {
  const url = new URL(request.url ?? "/", "http://stub");
  const authorized = request.headers.authorization === `Bearer ${TOKEN}`;
  const send = (status, body) => {
    response.writeHead(status, { "content-type": "application/json" });
    response.end(JSON.stringify(body));
  };

  // Liveness, unauthenticated — the real gateway's `/healthz` is the same (H-000), and it
  // is what the test runner waits on before starting the console.
  if (url.pathname === "/healthz") {
    return send(200, { status: "ok" });
  }

  if (!authorized) {
    return send(401, {
      error: { type: "admin_unauthorized", message: "send the root admin token" },
      headroom: { reason: "admin_unauthorized", request_id: "hr_stub" },
    });
  }

  const handler = ROUTES.get(url.pathname);
  if (!handler) {
    return send(404, { error: { type: "not_found", message: url.pathname } });
  }
  return send(200, handler());
}).listen(PORT, () => {
  process.stdout.write(`stub gateway on :${PORT}\n`);
});
