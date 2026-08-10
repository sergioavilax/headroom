"use client";

import type { ReactNode } from "react";
import { AdminRequestError } from "@/lib/api";

/** The pieces every view is built from. Small, unstyled beyond the tokens, reusable. */

export function Card({
  title,
  note,
  actions,
  children,
  refreshing,
}: {
  title?: string;
  note?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  refreshing?: boolean;
}) {
  return (
    <section className="card">
      {(title || actions) && (
        <header className="card-head">
          <div>
            {title && <h2 className="card-title">{title}</h2>}
            {note && <div className="card-note">{note}</div>}
          </div>
          {actions}
        </header>
      )}
      <div className={refreshing ? "refreshing" : undefined}>{children}</div>
    </section>
  );
}

export function StatTile({
  label,
  value,
  foot,
  small,
  children,
}: {
  label: string;
  value: ReactNode;
  foot?: ReactNode;
  small?: boolean;
  children?: ReactNode;
}) {
  return (
    <section className="card">
      <div className="tile-label">{label}</div>
      <div className={small ? "tile-value small" : "tile-value"}>{value}</div>
      {foot && <div className="tile-foot">{foot}</div>}
      {children && <div style={{ marginTop: 10 }}>{children}</div>}
    </section>
  );
}

export type Tone = "good" | "warning" | "serious" | "critical" | "neutral" | "accent";

/**
 * A status, as a coloured dot **and a word**. Never the dot alone: the status palette is
 * four fixed hues that must not be read as identity, and colour-only state is exactly
 * what the icon-plus-label pairing exists to prevent.
 */
export function Badge({ tone, children }: { tone: Tone; children: ReactNode }) {
  return (
    <span className={`badge ${tone}`}>
      <span className="dot" aria-hidden="true" />
      {children}
    </span>
  );
}

/** The tone an outcome deserves. One place, so no two views disagree about what "bad" is. */
export function outcomeTone(outcome: string, status: number | null): Tone {
  if (outcome === "ok") return "good";
  if (status === 429 || status === 402) return "warning";
  if (outcome.startsWith("upstream_stream")) return "serious";
  return "critical";
}

export function Num({ children }: { children: ReactNode }) {
  return <span className="num">{children}</span>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

/**
 * A failed poll, said plainly. The admin API answers in its own envelope with a reason
 * and a message that usually names the fix (H-009's rule, one layer up), so the message
 * is shown rather than replaced with "something went wrong".
 */
export function ErrorNote({ error }: { error: Error }) {
  const reason = error instanceof AdminRequestError ? error.reason : "console_error";
  return (
    <div className="notice">
      <strong>{reason}</strong> — {error.message}
    </div>
  );
}

export function Field({
  label,
  children,
  style,
}: {
  label: string;
  children: ReactNode;
  style?: React.CSSProperties;
}) {
  return (
    <div className="field" style={style}>
      <label>{label}</label>
      {children}
    </div>
  );
}

export function Toolbar({ children }: { children: ReactNode }) {
  return (
    <div className="row" style={{ alignItems: "flex-end", gap: 12 }}>
      {children}
    </div>
  );
}

export function KeyValue({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="kv">
      {rows.map(([key, value]) => (
        <div key={key} style={{ display: "contents" }}>
          <dt>{key}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function TableWrap({ children }: { children: ReactNode }) {
  return <div className="table-wrap">{children}</div>;
}
