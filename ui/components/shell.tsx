"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import type { ReactNode } from "react";
import { endSession } from "@/lib/api";

/**
 * The console's frame: a rail, a page head, and the sign-out.
 *
 * The mark beside the wordmark is a three-bar level meter with the top segment unlit —
 * the space between the peak and clipping, which is what "headroom" means and what this
 * whole control plane is for. It is drawn rather than imported so the console needs no
 * image asset and renders identically with no network.
 */

const NAV = [
  { href: "/", label: "Overview" },
  { href: "/live", label: "Live traffic" },
  { href: "/requests", label: "Requests" },
  { href: "/tenants", label: "Tenants & keys" },
  { href: "/limits", label: "Limits & budgets" },
  { href: "/cache", label: "Cache" },
  { href: "/providers", label: "Providers" },
] as const;

export function Shell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  return (
    <div className="shell">
      <nav className="rail" aria-label="Sections">
        <div className="wordmark">
          <MeterMark />
          <div>
            <div className="wordmark-name">Headroom</div>
            <div className="wordmark-sub">control plane</div>
          </div>
        </div>
        {NAV.map((item) => {
          const current = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className="nav-link"
              aria-current={current ? "page" : undefined}
            >
              <span>{item.label}</span>
              <span className="nav-mark" aria-hidden="true" />
            </Link>
          );
        })}
        <div className="rail-foot">
          <span>
            Signed in with the root admin token. It is held server-side in an httpOnly
            cookie and never reaches this page.
          </span>
          <button
            type="button"
            className="btn small"
            onClick={() =>
              void endSession().then(() => {
                router.replace("/");
                router.refresh();
              })
            }
          >
            Sign out
          </button>
        </div>
      </nav>
      <main className="main">{children}</main>
    </div>
  );
}

export function PageHead({
  title,
  sub,
  children,
}: {
  title: string;
  sub?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <header className="page-head">
      <div>
        <h1 className="page-title">{title}</h1>
        {sub && <p className="page-sub">{sub}</p>}
      </div>
      {children}
    </header>
  );
}

export function MeterMark({ size = 22 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 22 22" aria-hidden="true" style={{ flex: "none" }}>
      <rect x={2} y={13} width={5} height={7} rx={1.5} fill="var(--series-1)" />
      <rect x={8.5} y={8} width={5} height={12} rx={1.5} fill="var(--series-1)" />
      <rect x={15} y={4} width={5} height={16} rx={1.5} fill="var(--surface-3)" />
      <rect x={15} y={11} width={5} height={9} rx={1.5} fill="var(--series-1)" />
    </svg>
  );
}

/** The dot that says a view is polling — and, when it is not, why. */
export function PollBadge({ intervalMs, refreshing }: { intervalMs: number; refreshing: boolean }) {
  return (
    <span className="badge neutral" title={`polls every ${intervalMs / 1000}s`}>
      <span
        className="dot"
        aria-hidden="true"
        style={{ background: refreshing ? "var(--accent)" : "var(--ink-4)" }}
      />
      live · {intervalMs / 1000}s
    </span>
  );
}
