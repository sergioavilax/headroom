import assert from "node:assert/strict";
import { test } from "node:test";

/**
 * The console's proxy is the one place a credential is attached, so it is the one place
 * worth testing directly — and `lib/proxy.ts` is written to make that possible without a
 * framework: no `server-only`, no environment, no request object, just the rules.
 *
 * These run under Node's own test runner with native TypeScript stripping (Node 24), so
 * the console's unit tests cost zero dependencies and no build step.
 */
import {
  ALLOWED_METHODS,
  ALLOWED_PREFIXES,
  buildAdminHeaders,
  resolveUpstreamPath,
} from "../../lib/proxy.ts";

test("a known prefix resolves to an /admin path", () => {
  const result = resolveUpstreamPath(["usage", "totals"]);
  assert.deepEqual(result, { ok: true, path: "/admin/usage/totals" });
});

test("every allow-listed prefix is reachable", () => {
  for (const prefix of ALLOWED_PREFIXES) {
    assert.equal(resolveUpstreamPath([prefix]).ok, true, prefix);
  }
});

test("a prefix nobody allow-listed is refused", () => {
  const result = resolveUpstreamPath(["rollups", "run"]);
  assert.equal(result.ok, false);
  assert.match(result.ok === false ? result.reason : "", /does not proxy/);
});

test("an empty path is refused rather than becoming /admin/", () => {
  assert.equal(resolveUpstreamPath([]).ok, false);
});

test("path traversal cannot climb out of /admin", () => {
  for (const segments of [
    ["usage", "..", "..", "v1", "messages"],
    ["usage", "."],
    ["usage", ""],
  ]) {
    assert.equal(resolveUpstreamPath(segments).ok, false, segments.join("/"));
  }
});

test("a segment that would change the path is encoded, not interpolated", () => {
  const result = resolveUpstreamPath(["usage", "hr_1/../../etc"]);
  assert.equal(result.ok, true);
  assert.equal(result.ok && result.path, "/admin/usage/hr_1%2F..%2F..%2Fetc");
});

test("the headers carry the token and nothing from the caller", () => {
  const headers = buildAdminHeaders("root-token", "application/json");
  assert.equal(headers.get("authorization"), "Bearer root-token");
  assert.equal(headers.get("content-type"), "application/json");
  assert.equal(headers.get("cookie"), null);
  assert.deepEqual([...headers.keys()].sort(), ["accept", "authorization", "content-type"]);
});

test("a bodyless request sends no content-type", () => {
  const headers = buildAdminHeaders("root-token", null);
  assert.equal(headers.get("content-type"), null);
});

test("only the five verbs the console uses are proxied", () => {
  assert.deepEqual([...ALLOWED_METHODS], ["GET", "POST", "PUT", "PATCH", "DELETE"]);
  for (const method of ["HEAD", "OPTIONS", "TRACE", "CONNECT"]) {
    assert.equal((ALLOWED_METHODS as readonly string[]).includes(method), false, method);
  }
});
