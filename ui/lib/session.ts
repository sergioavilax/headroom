import "server-only";
import { cookies } from "next/headers";

/**
 * How the console holds the root admin token. **The whole of H-055, in one file.**
 *
 * The operator types the token into the sign-in screen; this server exchanges it for an
 * `httpOnly` cookie and attaches it to every `/admin/*` call from then on. Three
 * properties fall out, and each one is why this shape was chosen over the obvious
 * alternatives:
 *
 * - **Nothing is baked into a bundle.** The token never crosses into client code at all,
 *   so there is no `NEXT_PUBLIC_*` to leak and no build artifact to scrub.
 * - **The `ui` service needs no secret in its environment.** Compose passes it a gateway
 *   URL and nothing else, which removes one whole place invariant 3 could be violated.
 * - **`httpOnly` means the page's own JavaScript cannot read it back**, so an injected
 *   script cannot exfiltrate the credential even though the session it rides is live.
 *
 * The trade, stated rather than buried: the token sits in the browser's cookie jar for
 * the session, and anyone who can already reach this console can act as the operator
 * until they close the browser. That is the same trust boundary the gateway's own admin
 * API has (one root token, no roles — H-019), so the console does not invent a weaker or
 * a stronger one.
 */

export const SESSION_COOKIE = "headroom_admin_session";

/**
 * Eight hours: long enough that a demo, an incident, or an evening of building never
 * signs itself out mid-thought, short enough that a walked-away laptop is not a
 * permanent grant. `sameSite: strict` because nothing should ever navigate into this
 * console from somewhere else, and a cross-site request that arrived with a session
 * attached is exactly the CSRF this closes.
 */
export const SESSION_MAX_AGE_S = 8 * 60 * 60;

export function sessionCookieOptions(secure: boolean) {
  return {
    httpOnly: true,
    sameSite: "strict" as const,
    // Secure only over HTTPS: setting it unconditionally would make the cookie silently
    // fail to stick on `http://localhost:3001`, which is where this console lives.
    secure,
    path: "/",
    maxAge: SESSION_MAX_AGE_S,
  };
}

/** The token this request may act with, or `null` — the only way to read it. */
export async function sessionToken(): Promise<string | null> {
  const store = await cookies();
  return store.get(SESSION_COOKIE)?.value ?? null;
}
