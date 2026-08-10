import "server-only";

export {
  ALLOWED_METHODS,
  ALLOWED_PREFIXES,
  buildAdminHeaders as adminHeaders,
  resolveUpstreamPath as upstreamPath,
} from "./proxy";

/**
 * The console's half of the admin API contract. **Server side only.**
 *
 * The `server-only` import at the top is load-bearing rather than decorative: it makes
 * importing this module from a client component a *build* error, so the gateway's address
 * and the code that speaks to it cannot end up in a browser bundle by somebody's
 * accident. Together with `lib/session.ts` that is BUILD_PLAN §0.2 invariant 3 enforced
 * by the compiler instead of by a reviewer — the same move `config/routing.yaml` makes
 * with `extra="forbid"` one language over (H-014).
 *
 * The rules themselves live in `./proxy`, which imports nothing, so they can be tested as
 * the pure functions they are.
 */

/** Where the gateway is. Compose sets it to the service name; the default is H-006's host port. */
export const GATEWAY_URL = (process.env.HEADROOM_GATEWAY_URL ?? "http://localhost:8080").replace(
  /\/+$/,
  "",
);

/** The full upstream URL for a validated path, query string included. */
export function upstreamUrl(path: string, search: string): string {
  return `${GATEWAY_URL}${path}${search}`;
}
