"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { PageHead, PollBadge } from "@/components/shell";
import {
  Badge,
  Card,
  Empty,
  ErrorNote,
  Field,
  KeyValue,
  Num,
  TableWrap,
  Toolbar,
  outcomeTone,
} from "@/components/ui";
import { admin, type LedgerRow, type Provider, type Tenant } from "@/lib/api";
import { clock, count, money, ms, stamp } from "@/lib/format";
import { SLOW_INTERVAL_MS, usePoll } from "@/lib/poll";

/**
 * The ledger as an explorer. Filters above, rows below, and one request's whole story in
 * a drawer beside it.
 *
 * The detail panel is where the last six phases become visible on one screen: what it
 * cost **and the rates it was billed at** (which are copied onto the row, so they are
 * what it was actually charged rather than today's price — H-024); what the budget gate
 * held and what the hold settled at, including the one case where those two deliberately
 * disagree (H-031); what the cache did and, for a hit, which request's answer was served
 * (H-045's provenance); and what failover did — who was passed over, and why.
 *
 * Five seconds rather than two: this is a page somebody reads, and rows moving under a
 * cursor mid-sentence is worse than a figure being five seconds old. Filters live in the
 * URL, so a row an operator found is a link they can paste.
 */
export default function RequestsPage() {
  return (
    <Suspense fallback={<PageHead title="Requests" />}>
      <RequestsExplorer />
    </Suspense>
  );
}

function RequestsExplorer() {
  const params = useSearchParams();
  const [tenantId, setTenantId] = useState(params.get("tenant_id") ?? "");
  const [provider, setProvider] = useState(params.get("provider") ?? "");
  const [outcome, setOutcome] = useState(params.get("outcome") ?? "");
  const [model, setModel] = useState(params.get("model") ?? "");
  const [limit, setLimit] = useState(100);
  const [selected, setSelected] = useState<LedgerRow | null>(null);

  useEffect(() => {
    const search = new URLSearchParams();
    if (tenantId) search.set("tenant_id", tenantId);
    if (provider) search.set("provider", provider);
    if (outcome) search.set("outcome", outcome);
    if (model) search.set("model", model);
    const query = search.toString();
    window.history.replaceState(null, "", query ? `/requests?${query}` : "/requests");
  }, [tenantId, provider, outcome, model]);

  const poll = usePoll(
    async () => {
      const [rows, tenants, providers] = await Promise.all([
        admin.get<LedgerRow[]>("usage", {
          tenant_id: tenantId || undefined,
          provider: provider || undefined,
          outcome: outcome || undefined,
          model: model || undefined,
          limit,
        }),
        admin.get<Tenant[]>("tenants"),
        admin.get<Provider[]>("providers"),
      ]);
      return { rows, tenants, providers };
    },
    SLOW_INTERVAL_MS,
    [tenantId, provider, outcome, model, limit],
  );

  const data = poll.data;
  const rows = useMemo(() => data?.rows ?? [], [data?.rows]);
  const names = useMemo(
    () => new Map((data?.tenants ?? []).map((tenant) => [tenant.id, tenant.name])),
    [data?.tenants],
  );
  const outcomes = useMemo(
    () => [...new Set(rows.map((row) => row.outcome))].sort(),
    [rows],
  );

  return (
    <>
      <PageHead
        title="Requests"
        sub="Every request that authenticated and named a model. Anonymous 401s are deliberately absent — they have no tenant to attribute, and an unattributable row in an attribution table is worse than none."
      >
        <PollBadge intervalMs={SLOW_INTERVAL_MS} refreshing={poll.refreshing} />
      </PageHead>

      {poll.error && <ErrorNote error={poll.error} />}

      <div className="stack">
        <Card>
          <Toolbar>
            <Field label="Tenant" style={{ minWidth: 180 }}>
              <select
                className="control"
                value={tenantId}
                onChange={(event) => setTenantId(event.target.value)}
              >
                <option value="">all tenants</option>
                {(data?.tenants ?? []).map((tenant) => (
                  <option key={tenant.id} value={tenant.id}>
                    {tenant.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Provider" style={{ minWidth: 150 }}>
              <select
                className="control"
                value={provider}
                onChange={(event) => setProvider(event.target.value)}
              >
                <option value="">any</option>
                {(data?.providers ?? []).map((entry) => (
                  <option key={entry.name} value={entry.name}>
                    {entry.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Outcome" style={{ minWidth: 170 }}>
              <select
                className="control"
                value={outcome}
                onChange={(event) => setOutcome(event.target.value)}
              >
                <option value="">any</option>
                {outcomes.map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Model" style={{ minWidth: 190 }}>
              <input
                className="control"
                value={model}
                placeholder="exact model id"
                onChange={(event) => setModel(event.target.value)}
              />
            </Field>
            <Field label="Rows">
              <select
                className="control"
                value={limit}
                onChange={(event) => setLimit(Number(event.target.value))}
              >
                {[50, 100, 250, 500, 1000].map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
            </Field>
            <button
              type="button"
              className="btn"
              onClick={() => {
                setTenantId("");
                setProvider("");
                setOutcome("");
                setModel("");
              }}
            >
              Clear
            </button>
          </Toolbar>
        </Card>

        <Card
          title={`${count(rows.length)} row${rows.length === 1 ? "" : "s"}`}
          note="newest first · click a row for its whole story"
          refreshing={poll.refreshing}
        >
          {rows.length === 0 ? (
            <Empty>No rows match. Widen the filters, or run `make seed`.</Empty>
          ) : (
            <TableWrap>
              <table className="data">
                <thead>
                  <tr>
                    <th>Started</th>
                    <th>Request</th>
                    <th>Tenant</th>
                    <th>Model</th>
                    <th>Provider</th>
                    <th>Cache</th>
                    <th>Outcome</th>
                    <th className="right">In</th>
                    <th className="right">Out</th>
                    <th className="right">TTFT</th>
                    <th className="right">Total</th>
                    <th className="right">Cost</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr
                      key={row.request_id}
                      className={`clickable${selected?.request_id === row.request_id ? " selected" : ""}`}
                      onClick={() => setSelected(row)}
                    >
                      <td className="num">{clock(row.started_at)}</td>
                      <td className="num">{row.request_id.slice(0, 11)}…</td>
                      <td>{names.get(row.tenant_id) ?? "—"}</td>
                      <td>{row.model}</td>
                      <td>
                        {row.provider ?? <span className="muted">—</span>}
                        {row.failover_hops > 0 && (
                          <span className="muted"> ←{row.failover_from}</span>
                        )}
                      </td>
                      <td className="muted">{shortDisposition(row.cache_disposition)}</td>
                      <td>
                        <Badge tone={outcomeTone(row.outcome, row.status_code)}>
                          {row.status_code ?? "—"}
                        </Badge>
                      </td>
                      <td className="right num">{row.input_tokens ?? "—"}</td>
                      <td className="right num">{row.output_tokens ?? "—"}</td>
                      <td className="right num">{ms(row.ttft_ms)}</td>
                      <td className="right num">{ms(row.total_ms)}</td>
                      <td className="right num strong">{money(row.usd_cost)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableWrap>
          )}
        </Card>
      </div>

      {selected && (
        <RequestDrawer
          row={selected}
          tenantName={names.get(selected.tenant_id) ?? selected.tenant_id}
          onClose={() => setSelected(null)}
        />
      )}
    </>
  );
}

function shortDisposition(disposition: string | null): string {
  if (!disposition) return "—";
  return disposition.replace("cache_hit_", "hit·").replace("cache_", "");
}

function RequestDrawer({
  row,
  tenantName,
  onClose,
}: {
  row: LedgerRow;
  tenantName: string;
  onClose: () => void;
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <>
      <div className="scrim" onClick={onClose} role="presentation" />
      <aside className="drawer" aria-label={`Request ${row.request_id}`}>
        <div className="row" style={{ justifyContent: "space-between", marginBottom: 18 }}>
          <div>
            <div className="tile-label">Request</div>
            <div className="num" style={{ fontSize: 15, marginTop: 4 }}>
              {row.request_id}
            </div>
          </div>
          <button type="button" className="btn small" onClick={onClose}>
            Close
          </button>
        </div>

        <div className="stack" style={{ gap: 18 }}>
          <Section title="What happened">
            <KeyValue
              rows={[
                ["Started", <Num key="s">{stamp(row.started_at)}</Num>],
                ["Tenant", tenantName],
                ["Route", `${row.route} · ${row.dialect}`],
                ["Model", row.model],
                ["Streamed", row.streamed ? "yes" : "no"],
                [
                  "Outcome",
                  <Badge key="o" tone={outcomeTone(row.outcome, row.status_code)}>
                    {row.outcome} · {row.status_code ?? "—"}
                  </Badge>,
                ],
                ["Stop reason", row.stop_reason ?? "—"],
                [
                  "Error",
                  row.error_reason ? `${row.error_reason} (${row.error_source})` : "—",
                ],
              ]}
            />
          </Section>

          <Section title="Timings">
            <KeyValue
              rows={[
                ["Time to first token", <Num key="t">{ms(row.ttft_ms)}</Num>],
                ["Upstream latency", <Num key="u">{ms(row.upstream_latency_ms)}</Num>],
                [
                  "Gateway overhead",
                  <span key="o">
                    <Num>{ms(row.passthrough_overhead_ms)}</Num>{" "}
                    <span className="muted">first upstream byte → first byte out</span>
                  </span>,
                ],
                ["Total", <Num key="tt">{ms(row.total_ms)}</Num>],
              ]}
            />
          </Section>

          <Section title="What it cost">
            <KeyValue
              rows={[
                [
                  "Cost",
                  <span key="c">
                    <Num>{money(row.usd_cost)}</Num>{" "}
                    <Badge tone={row.cost_status === "priced" ? "good" : "warning"}>
                      {row.cost_status}
                    </Badge>
                  </span>,
                ],
                [
                  "Billed at",
                  row.usd_per_mtok_in ? (
                    <Num key="r">
                      ${row.usd_per_mtok_in} in · ${row.usd_per_mtok_out} out /MTok, from{" "}
                      {row.price_effective_from}
                    </Num>
                  ) : (
                    "—"
                  ),
                ],
                [
                  "Tokens",
                  <Num key="tk">
                    {row.input_tokens ?? "—"} in · {row.output_tokens ?? "—"} out
                    {row.reasoning_tokens ? ` (${row.reasoning_tokens} reasoning)` : ""}
                  </Num>,
                ],
                [
                  "Prompt cache",
                  row.cache_read_tokens || row.cache_write_tokens ? (
                    <Num key="pc">
                      {row.cache_read_tokens ?? 0} read · {row.cache_write_tokens ?? 0} written
                    </Num>
                  ) : (
                    "—"
                  ),
                ],
              ]}
            />
          </Section>

          <Section title="Budget gate">
            <KeyValue
              rows={[
                ["Status", row.budget_status ?? "never reached the gate"],
                ["Reserved", <Num key="br">{money(row.budget_reserved_usd)}</Num>],
                ["Settled", <Num key="bs">{money(row.budget_settled_usd)}</Num>],
              ]}
            />
            {row.budget_settled_usd !== null &&
              row.usd_cost !== null &&
              row.budget_settled_usd !== row.usd_cost && (
                <p className="card-note" style={{ marginTop: 8 }}>
                  The settled figure and the cost differ on purpose: a ledger row is an
                  invoice and states facts, a budget is a guard rail and states bounds.
                </p>
              )}
          </Section>

          <Section title="Cache">
            <KeyValue
              rows={[
                ["Disposition", row.cache_disposition ?? "never reached the cache"],
                ["Avoided", <Num key="a">{money(row.cache_avoided_usd)}</Num>],
                ["Similarity", row.cache_similarity ? <Num key="s">{row.cache_similarity}</Num> : "—"],
                [
                  "Served from",
                  row.cache_source_request_id ? (
                    <Num key="src">{row.cache_source_request_id}</Num>
                  ) : (
                    "—"
                  ),
                ],
              ]}
            />
          </Section>

          <Section title="Failover">
            <KeyValue
              rows={[
                ["Served by", row.provider ?? "no upstream was called"],
                ["Hops", <Num key="h">{row.failover_hops}</Num>],
                ["Passed over", row.failover_from ?? "—"],
                ["Because", row.failover_error ?? "—"],
                ["Upstream status", row.upstream_status ?? "—"],
              ]}
            />
          </Section>
        </div>
      </aside>
    </>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h3 className="card-title" style={{ marginBottom: 10 }}>
        {title}
      </h3>
      {children}
    </section>
  );
}
