import { NextResponse, type NextRequest } from "next/server";
import { adminHeaders, upstreamUrl } from "@/lib/admin";
import { SESSION_COOKIE, sessionCookieOptions } from "@/lib/session";

/**
 * Sign in and out. The token arrives once, in a POST body, and never leaves this process.
 *
 * **The token is probed before it is accepted.** A console that stored whatever was typed
 * and let every subsequent view fail with a 401 would be blaming the operator's next
 * action for their previous typo. `GET /admin/tenants` is the cheapest read on the admin
 * surface and it distinguishes all three answers that matter: 200 (the token is right),
 * 401 (it is wrong), 503 (the *gateway's* `HEADROOM_ADMIN_TOKEN` is unset, so no token
 * can be right — H-019). Each gets its own message, because "unauthorized" would send an
 * operator hunting for a better token when the fix is on the other side.
 */

export const dynamic = "force-dynamic";

export async function POST(request: NextRequest): Promise<NextResponse> {
  let token: unknown;
  try {
    token = (await request.json())?.token;
  } catch {
    token = undefined;
  }
  if (typeof token !== "string" || token.trim() === "") {
    return NextResponse.json(
      { error: { type: "missing_token", message: "enter the root admin token" } },
      { status: 400 },
    );
  }
  const candidate = token.trim();

  let probe: Response;
  try {
    probe = await fetch(upstreamUrl("/admin/tenants", ""), {
      headers: adminHeaders(candidate),
      cache: "no-store",
    });
  } catch (error) {
    return NextResponse.json(
      {
        error: {
          type: "gateway_unreachable",
          message: `could not reach the gateway: ${error instanceof Error ? error.message : String(error)}`,
        },
      },
      { status: 502 },
    );
  }

  if (probe.status === 503) {
    return NextResponse.json(
      {
        error: {
          type: "admin_api_disabled",
          message:
            "the gateway's admin API is switched off — start it with HEADROOM_ADMIN_TOKEN set",
        },
      },
      { status: 503 },
    );
  }
  if (!probe.ok) {
    return NextResponse.json(
      { error: { type: "admin_unauthorized", message: "the gateway rejected that token" } },
      { status: 401 },
    );
  }

  const response = NextResponse.json({ ok: true });
  response.cookies.set(
    SESSION_COOKIE,
    candidate,
    sessionCookieOptions(request.nextUrl.protocol === "https:"),
  );
  return response;
}

export async function DELETE(): Promise<NextResponse> {
  const response = NextResponse.json({ ok: true });
  response.cookies.delete(SESSION_COOKIE);
  return response;
}
