"use client";

/**
 * The shapes `/admin/*` returns, and the one function that fetches them.
 *
 * These types are hand-written against the FastAPI response models rather than generated
 * from the OpenAPI document, deliberately: the generator would have to run somewhere, its
 * output would be a build artifact nobody reads, and the fields this console uses are few
 * enough that writing them down is also the act of deciding which ones a view is allowed
 * to depend on. When the API adds a field, nothing here breaks; when it *removes* one,
 * `tsc` says so — which is the direction that matters.
 */

export type ApiError = { status: number; reason: string; message: string };

export class AdminRequestError extends Error {
  readonly status: number;
  readonly reason: string;

  constructor({ status, reason, message }: ApiError) {
    super(message);
    this.name = "AdminRequestError";
    this.status = status;
    this.reason = reason;
  }

  /** The session is gone or the gateway rejected the token — sign in again. */
  get needsSignIn(): boolean {
    return this.status === 401;
  }
}

export type Query = Record<string, string | number | boolean | undefined | null>;

function queryString(query?: Query): string {
  if (!query) return "";
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === "") continue;
    params.set(key, String(value));
  }
  const rendered = params.toString();
  return rendered ? `?${rendered}` : "";
}

async function call<T>(method: string, path: string, query?: Query, body?: unknown): Promise<T> {
  const response = await fetch(`/api/admin/${path}${queryString(query)}`, {
    method,
    headers: body === undefined ? undefined : { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  const text = await response.text();
  const parsed: unknown = text ? JSON.parse(text) : null;
  if (!response.ok) {
    const envelope = parsed as { error?: { type?: string; message?: string } } | null;
    throw new AdminRequestError({
      status: response.status,
      reason: envelope?.error?.type ?? "request_failed",
      message: envelope?.error?.message ?? `${method} ${path} failed with ${response.status}`,
    });
  }
  return parsed as T;
}

export const admin = {
  get: <T,>(path: string, query?: Query) => call<T>("GET", path, query),
  post: <T,>(path: string, body: unknown) => call<T>("POST", path, undefined, body),
  put: <T,>(path: string, body: unknown) => call<T>("PUT", path, undefined, body),
  patch: <T,>(path: string, body: unknown) => call<T>("PATCH", path, undefined, body),
  del: <T,>(path: string) => call<T>("DELETE", path),
};

/**
 * Drop the session cookie. The caller follows with `router.refresh()`, which is what
 * makes the shell disappear: the session is read in the root *server* layout, so the tree
 * changes shape only when the server renders it again.
 */
export async function endSession(): Promise<void> {
  await fetch("/api/session", { method: "DELETE" });
}

// --- response shapes -----------------------------------------------------------------

export type Tenant = {
  id: string;
  name: string;
  active: boolean;
  created_at: string;
  updated_at: string;
};

export type Key = {
  id: string;
  tenant_id: string;
  name: string;
  key_prefix: string;
  allowed_models: string[];
  allowed_providers: string[];
  status: "active" | "revoked";
  created_at: string;
  updated_at: string;
  revoked_at: string | null;
};

/** The one response in the whole API that carries a plaintext key. Shown once, never stored. */
export type KeyCreated = Key & { key: string };

export type LedgerRow = {
  request_id: string;
  tenant_id: string;
  key_id: string;
  route: string;
  dialect: string;
  model: string;
  provider: string | null;
  streamed: boolean;
  outcome: string;
  status_code: number | null;
  upstream_status: number | null;
  error_source: string | null;
  error_reason: string | null;
  stop_reason: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  reasoning_tokens: number | null;
  cache_read_tokens: number | null;
  cache_write_tokens: number | null;
  price_effective_from: string | null;
  usd_per_mtok_in: string | null;
  usd_per_mtok_out: string | null;
  usd_cost: string | null;
  cost_status: string;
  budget_status: string | null;
  budget_reserved_usd: string | null;
  budget_settled_usd: string | null;
  upstream_latency_ms: number | null;
  ttft_ms: number | null;
  passthrough_overhead_ms: number | null;
  total_ms: number | null;
  cache_disposition: string | null;
  cache_avoided_usd: string | null;
  cache_similarity: string | null;
  cache_source_request_id: string | null;
  failover_hops: number;
  failover_from: string | null;
  failover_error: string | null;
  started_at: string;
};

export type Totals = {
  tenant_id: string;
  model: string | null;
  requests: number;
  input_tokens: number;
  output_tokens: number;
  reasoning_tokens: number;
  usd_cost: string;
  unpriced_requests: number;
  errored_requests: number;
  cache_hits_exact: number;
  cache_hits_semantic: number;
  cache_misses: number;
  cache_bypasses: number;
  cache_disabled: number;
  cache_avoided_usd: string;
  cache_avoided_unknown: number;
  failover_requests: number;
};

export type SeriesPoint = {
  bucket_start: string;
  requests: number;
  input_tokens: number;
  output_tokens: number;
  usd_cost: string;
  cache_avoided_usd: string;
  cache_hits: number;
  errored_requests: number;
  unpriced_requests: number;
  failover_requests: number;
};

/**
 * One (UTC day, tenant) row of `daily_rollups`, written by the nightly Lambda (Phase 9).
 *
 * Field-for-field `Totals` minus the model split, plus the day and the stamp — because it
 * *is* a total, over a fixed window, computed once instead of on every poll.
 * `computed_at` is the operational field: the gap between it and `day` is how late the
 * schedule ran, and a stale one is how a schedule that stopped firing announces itself.
 */
export type Rollup = {
  day: string;
  tenant_id: string;
  requests: number;
  input_tokens: number;
  output_tokens: number;
  reasoning_tokens: number;
  usd_cost: string;
  unpriced_requests: number;
  errored_requests: number;
  cache_hits_exact: number;
  cache_hits_semantic: number;
  cache_misses: number;
  cache_bypasses: number;
  cache_disabled: number;
  cache_avoided_usd: string;
  cache_avoided_unknown: number;
  failover_requests: number;
  computed_at: string | null;
};

export type Budget = {
  scope: string;
  scope_kind: string;
  scope_id: string;
  window: string;
  window_id: string;
  usd: string;
  spent: string;
  reserved: string;
  remaining: string;
  committed: string;
  reservations: number;
  expired_releases: number;
  expired_released: string;
  created_at: string | null;
  updated_at: string | null;
};

export type Bucket = {
  dimension: string;
  limit_per_min: number;
  available: number;
  reset_after_s: number;
  reset_at: string;
};

export type Limit = {
  scope: string;
  scope_kind: string;
  scope_id: string;
  name: string;
  requests_per_min: number | null;
  tokens_per_min: number | null;
  buckets: Bucket[];
};

export type CachePolicy = {
  tenant_id: string;
  tenant_name: string;
  mode: string;
  ttl_s: number | null;
  similarity_threshold: number | null;
  effective_ttl_s: number;
  effective_similarity_threshold: number;
  embedding_model: string;
  entries: number;
  semantic_entries: number;
  body_bytes: number;
};

export type ProviderRoute = {
  dialect: string;
  prefix: string;
  chain: string[];
  attempts: string[];
};

export type Provider = {
  name: string;
  kind: string;
  state: "closed" | "open" | "half_open";
  samples: number;
  failures: number;
  failure_ratio: number;
  consecutive_failures: number;
  total_successes: number;
  total_failures: number;
  p50_latency_ms: number | null;
  p95_latency_ms: number | null;
  last_error: string | null;
  reopen_in_s: number | null;
  routes: ProviderRoute[];
};
