import { NextResponse, type NextRequest } from "next/server";
import { ALLOWED_METHODS, adminHeaders, upstreamPath, upstreamUrl } from "@/lib/admin";
import { SESSION_COOKIE, sessionToken } from "@/lib/session";

/**
 * The console's only route to the gateway: browser → here → `/admin/*`.
 *
 * It exists for one reason — to attach a credential the browser must never hold (H-055)
 * — and it does nothing else. It adds no data, aggregates nothing, and reads no
 * database: every number this console renders came out of the gateway's own admin API,
 * which is what makes the dashboard a *client* of the system rather than a second, less
 * tested reader of it (H-054). If a view needs a figure, the figure gets published on
 * `/admin/*` properly, with tests, and arrives through here.
 *
 * Errors are forwarded as-is. Headroom's admin API already answers in its own envelope
 * with a reason and a request id, and a proxy that rewrote a 402 into a generic 500 would
 * throw away the only thing that makes a refusal debuggable.
 */

export const dynamic = "force-dynamic";

async function proxy(request: NextRequest, segments: string[]): Promise<NextResponse> {
  if (!(ALLOWED_METHODS as readonly string[]).includes(request.method)) {
    return refuse(405, "method_not_allowed", `${request.method} is not proxied`);
  }

  const resolved = upstreamPath(segments);
  if (!resolved.ok) {
    return refuse(404, "path_not_proxied", resolved.reason);
  }

  const token = await sessionToken();
  if (!token) {
    // The session is gone (expired, cleared, never established). 401 rather than a
    // redirect: the caller is `fetch`, not a browser navigation, and the client turns
    // this into the sign-in screen itself.
    return refuse(401, "no_session", "sign in with the root admin token");
  }

  const body = request.method === "GET" || request.method === "DELETE" ? undefined : await request.text();

  let upstream: Response;
  try {
    upstream = await fetch(upstreamUrl(resolved.path, request.nextUrl.search), {
      method: request.method,
      headers: adminHeaders(token, body === undefined ? null : "application/json"),
      body,
      cache: "no-store",
    });
  } catch (error) {
    // The gateway is unreachable — a stopped container, a wrong `HEADROOM_GATEWAY_URL`.
    // 502 and say where we tried: a console that reported "failed to fetch" would send
    // an operator looking at their browser instead of at their stack.
    return refuse(
      502,
      "gateway_unreachable",
      `could not reach the gateway: ${error instanceof Error ? error.message : String(error)}`,
    );
  }

  const response = new NextResponse(upstream.body, {
    status: upstream.status,
    headers: {
      "content-type": upstream.headers.get("content-type") ?? "application/json",
      "cache-control": "no-store",
    },
  });

  // A token the gateway itself rejects is a dead session, and leaving the cookie in
  // place would loop the operator through a sign-in screen that appears to work.
  if (upstream.status === 401) {
    response.cookies.delete(SESSION_COOKIE);
  }
  return response;
}

function refuse(status: number, reason: string, message: string): NextResponse {
  return NextResponse.json(
    { error: { type: reason, message }, headroom: { reason, source: "console" } },
    { status, headers: { "cache-control": "no-store" } },
  );
}

type Context = { params: Promise<{ path: string[] }> };

export async function GET(request: NextRequest, context: Context) {
  return proxy(request, (await context.params).path);
}
export async function POST(request: NextRequest, context: Context) {
  return proxy(request, (await context.params).path);
}
export async function PUT(request: NextRequest, context: Context) {
  return proxy(request, (await context.params).path);
}
export async function PATCH(request: NextRequest, context: Context) {
  return proxy(request, (await context.params).path);
}
export async function DELETE(request: NextRequest, context: Context) {
  return proxy(request, (await context.params).path);
}
