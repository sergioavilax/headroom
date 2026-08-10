/**
 * The proxy's rules, as pure functions.
 *
 * Split out of `lib/admin.ts` on purpose: that module carries `server-only`, which throws
 * outside a server graph and would take a plain `node --test` run with it. The decisions
 * worth testing directly are all here and depend on nothing — no environment, no request,
 * no framework — so the test is the ordinary kind rather than a harness.
 */

/**
 * The `/admin/*` prefixes this console will forward to, and nothing else.
 *
 * The proxy exists to attach a credential the browser must never hold, which makes it a
 * thing that turns *any* request it accepts into an authenticated one. Enumerating the
 * seven surfaces the console actually reads keeps it from being a general-purpose relay
 * to whatever a later phase happens to mount under `/admin` — a Phase 9 rollup trigger,
 * say, which nothing in this UI should be able to fire because somebody guessed a path.
 *
 * Adding a view means adding its prefix here, deliberately, in the diff that adds it.
 */
export const ALLOWED_PREFIXES = [
  "tenants",
  "keys",
  "usage",
  "budgets",
  "limits",
  "cache",
  "providers",
] as const;

/** Methods the console may use. `HEAD`/`OPTIONS` are not needed and are not forwarded. */
export const ALLOWED_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"] as const;

export type PathResult = { ok: true; path: string } | { ok: false; reason: string };

/**
 * Turn the catch-all route's segments into an upstream path, or refuse with a reason.
 *
 * Every segment is percent-encoded rather than interpolated, so a request id containing a
 * slash cannot climb out of `/admin/usage/` — and `.`/`..` segments are refused outright
 * rather than encoded, because a path that *looks* relative is worth failing loudly on
 * even when the encoding would have made it harmless.
 */
export function resolveUpstreamPath(segments: readonly string[]): PathResult {
  if (segments.length === 0) {
    return { ok: false, reason: "no admin path was given" };
  }
  const [head] = segments;
  if (!head || !(ALLOWED_PREFIXES as readonly string[]).includes(head)) {
    return {
      ok: false,
      reason: `the console does not proxy /admin/${head ?? ""} (allowed: ${ALLOWED_PREFIXES.join(", ")})`,
    };
  }
  if (segments.some((segment) => segment === "" || segment === "." || segment === "..")) {
    return { ok: false, reason: "an admin path segment was empty or relative" };
  }
  return { ok: true, path: `/admin/${segments.map(encodeURIComponent).join("/")}` };
}

/**
 * The headers the gateway gets — built from nothing but the token and the content type.
 *
 * Deliberately *constructed* rather than derived from the incoming request: forwarding
 * the browser's own headers is how a cookie, an origin, or a stale `content-length` ends
 * up somewhere it was never meant to go. The proxy adds exactly one credential and says
 * exactly what it is sending.
 */
export function buildAdminHeaders(token: string, contentType?: string | null): Headers {
  const headers = new Headers({ authorization: `Bearer ${token}`, accept: "application/json" });
  if (contentType) {
    headers.set("content-type", contentType);
  }
  return headers;
}
