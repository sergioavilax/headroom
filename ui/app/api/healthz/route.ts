import { NextResponse } from "next/server";

/**
 * Liveness only — the same rule the gateway's `/healthz` follows (H-000).
 *
 * It reports that this process is serving and nothing else. It deliberately does *not*
 * probe the gateway: compose starts the console after the gateway is healthy, and a
 * console healthcheck that failed because its upstream was restarting would take a
 * perfectly good container down with it. A health check that lies is worse than no
 * health check; so is one that reports somebody else's health as its own.
 */
export const dynamic = "force-dynamic";

export function GET() {
  return NextResponse.json({ status: "ok" }, { headers: { "cache-control": "no-store" } });
}
