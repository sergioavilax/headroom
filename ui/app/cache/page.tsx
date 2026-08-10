"use client";

import { useMemo, useState } from "react";
import { Legend, ProportionBar } from "@/components/charts";
import { PageHead, PollBadge } from "@/components/shell";
import {
  Badge,
  Card,
  Empty,
  ErrorNote,
  Field,
  StatTile,
  TableWrap,
  Toolbar,
} from "@/components/ui";
import { admin, type CachePolicy, type LedgerRow, type Tenant, type Totals } from "@/lib/api";
import { count, money, moneyFromPicos, percent, sumUsd } from "@/lib/format";
import { PAGE_INTERVAL_MS, usePoll } from "@/lib/poll";
import { NO_PROVIDER } from "@/lib/series";

/** The five dispositions, in the order a reader wants them: best outcome first. */
const DISPOSITIONS = [
  { key: "cache_hits_exact", label: "exact hit", colour: "var(--series-3)" },
  { key: "cache_hits_semantic", label: "semantic hit", colour: "var(--series-1)" },
  { key: "cache_misses", label: "miss", colour: "var(--series-4)" },
  { key: "cache_bypasses", label: "bypass", colour: "var(--series-2)" },
  { key: "cache_disabled", label: "disabled", colour: NO_PROVIDER },
] as const;

/**
 * What the cache is doing, what it has saved, and — the part that matters — *which
 * question each semantic hit actually answered.*
 *
 * The last table is this project's own headline experiment in miniature. §P8.H1 measures
 * how often a semantic cache silently returns the wrong answer; the mechanism that makes
 * that measurable is `cache_source_request_id` on every hit, and this view is where a
 * human can follow one. A similarity score beside a provenance link is the difference
 * between "the cache hit" and "the cache answered a question I did not ask".
 */
export default function CachePage() {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [tenantId, setTenantId] = useState("");
  const [mode, setMode] = useState("exact");
  const [threshold, setThreshold] = useState("");

  const poll = usePoll(
    async () => {
      const [tenants, totals, policies, hits] = await Promise.all([
        admin.get<Tenant[]>("tenants"),
        admin.get<Totals[]>("usage/totals"),
        admin.get<CachePolicy[]>("cache"),
        admin.get<LedgerRow[]>("usage", { limit: 400 }),
      ]);
      return { tenants, totals, policies, hits };
    },
    PAGE_INTERVAL_MS,
    [],
  );

  const data = poll.data;
  const names = useMemo(
    () => new Map((data?.tenants ?? []).map((tenant) => [tenant.id, tenant.name])),
    [data?.tenants],
  );

  const rollup = useMemo(() => {
    const totals = data?.totals ?? [];
    const parts = DISPOSITIONS.map((disposition) => ({
      key: disposition.key,
      label: disposition.label,
      colour: disposition.colour,
      value: totals.reduce((sum, row) => sum + row[disposition.key], 0),
    }));
    const hits = (parts[0]?.value ?? 0) + (parts[1]?.value ?? 0);
    const lookups = hits + (parts[2]?.value ?? 0);
    return {
      parts,
      hits,
      lookups,
      saved: sumUsd(totals.map((row) => row.cache_avoided_usd)),
      spent: sumUsd(totals.map((row) => row.usd_cost)),
      unknown: totals.reduce((sum, row) => sum + row.cache_avoided_unknown, 0),
    };
  }, [data?.totals]);

  const semanticHits = useMemo(
    () => (data?.hits ?? []).filter((row) => row.cache_disposition === "cache_hit_semantic"),
    [data?.hits],
  );

  async function act(work: () => Promise<unknown>) {
    setBusy(true);
    setProblem(null);
    try {
      await work();
      poll.refresh();
    } catch (error) {
      setProblem(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHead
        title="Cache"
        sub="Off by default for every tenant, which is why switching it on is a decision somebody made rather than a default nobody noticed."
      >
        <PollBadge intervalMs={PAGE_INTERVAL_MS} refreshing={poll.refreshing} />
      </PageHead>

      {poll.error && <ErrorNote error={poll.error} />}
      {problem && <ErrorNote error={new Error(problem)} />}

      <div className="stack">
        <div className="grid cols-4">
          <StatTile
            label="Avoided"
            value={moneyFromPicos(rollup.saved)}
            foot={
              rollup.unknown > 0 ? (
                // The sum alone cannot say a saving was left out — zero is the additive
                // identity — so the count beside it is the only thing that can.
                <span style={{ color: "var(--warning)" }}>
                  {count(rollup.unknown)} hit{rollup.unknown === 1 ? "" : "s"} could not be
                  priced and are not in this figure
                </span>
              ) : (
                "from each entry's own recorded cost, not a re-pricing"
              )
            }
          />
          <StatTile
            label="Hit rate"
            value={rollup.lookups > 0 ? percent(rollup.hits, rollup.lookups) : "—"}
            foot={`${count(rollup.hits)} hits of ${count(rollup.lookups)} lookups`}
          />
          <StatTile
            label="Would have cost"
            value={moneyFromPicos(rollup.saved + rollup.spent)}
            foot="settled spend plus what the hits avoided"
          />
          <StatTile
            label="Caching tenants"
            value={`${(data?.policies ?? []).length}/${(data?.tenants ?? []).length}`}
            small
            foot="the rest are switched off"
          />
        </div>

        <Card title="Dispositions" note="every proxied request that reached the gate has exactly one" refreshing={poll.refreshing}>
          <ProportionBar parts={rollup.parts} height={12} ariaLabel="Cache dispositions" />
          <div style={{ marginTop: 12 }}>
            <Legend
              items={rollup.parts.map((part) => ({
                label: `${part.label} · ${count(part.value)}`,
                colour: part.colour,
              }))}
            />
          </div>
          <p className="card-note" style={{ marginTop: 12 }}>
            A <strong>bypass</strong> is an eligible-looking request the cache refused —
            most often because it declared tools, which makes it ineligible even when
            nothing was called. A <strong>disabled</strong> row is a tenant that never asked
            for a cache at all. Collapsing the two would hide the more common one, and they
            have completely different fixes.
          </p>
        </Card>

        <Card title="Policy" note="mode, TTL, threshold, and what each tenant has stored">
          <Toolbar>
            <Field label="Tenant" style={{ minWidth: 160 }}>
              <select
                className="control"
                value={tenantId}
                onChange={(event) => setTenantId(event.target.value)}
              >
                <option value="">choose…</option>
                {(data?.tenants ?? []).map((tenant) => (
                  <option key={tenant.id} value={tenant.id}>
                    {tenant.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Mode">
              <select
                className="control"
                value={mode}
                onChange={(event) => setMode(event.target.value)}
              >
                <option value="disabled">disabled</option>
                <option value="exact">exact</option>
                <option value="semantic">semantic</option>
              </select>
            </Field>
            <Field label="Similarity threshold" style={{ minWidth: 130 }}>
              <input
                className="control num"
                value={threshold}
                placeholder="default 0.90"
                onChange={(event) => setThreshold(event.target.value)}
              />
            </Field>
            <button
              type="button"
              className="btn primary"
              disabled={busy || !tenantId}
              onClick={() =>
                void act(() =>
                  admin.put(`cache/${tenantId}`, {
                    mode,
                    similarity_threshold: threshold.trim() ? Number(threshold) : null,
                  }),
                )
              }
            >
              Apply
            </button>
          </Toolbar>

          <div style={{ marginTop: 16 }}>
            {(data?.policies ?? []).length === 0 ? (
              <Empty>
                No tenant is caching. Switching `semantic` on loads the embedding model in
                the request that asks for it, so a missing extra is a 503 naming the fix
                rather than a silent bypass on somebody&rsquo;s traffic an hour later.
              </Empty>
            ) : (
              <TableWrap>
                <table className="data">
                  <thead>
                    <tr>
                      <th>Tenant</th>
                      <th>Mode</th>
                      <th className="right">TTL</th>
                      <th className="right">Threshold</th>
                      <th>Embedding model</th>
                      <th className="right">Entries</th>
                      <th className="right">Semantic</th>
                      <th className="right">Bytes</th>
                      <th className="right">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(data?.policies ?? []).map((policy) => (
                      <tr key={policy.tenant_id}>
                        <td className="strong">{policy.tenant_name}</td>
                        <td>
                          <Badge tone={policy.mode === "semantic" ? "accent" : "good"}>
                            {policy.mode}
                          </Badge>
                        </td>
                        <td className="right num">
                          {policy.effective_ttl_s}s
                          {policy.ttl_s === null && <span className="muted"> (default)</span>}
                        </td>
                        <td className="right num">
                          {policy.effective_similarity_threshold}
                          {policy.similarity_threshold === null && (
                            <span className="muted"> (default)</span>
                          )}
                        </td>
                        <td className="muted num">{policy.embedding_model}</td>
                        <td className="right num">{count(policy.entries)}</td>
                        <td className="right num">{count(policy.semantic_entries)}</td>
                        <td className="right num">{count(policy.body_bytes)}</td>
                        <td className="right">
                          <button
                            type="button"
                            className="btn small danger"
                            disabled={busy}
                            onClick={() => void act(() => admin.del(`cache/${policy.tenant_id}`))}
                          >
                            Disable &amp; purge
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableWrap>
            )}
          </div>
        </Card>

        <Card
          title="Semantic hits, and which question each answered"
          note="the provenance §P8.H1 measures silent wrong answers with"
          refreshing={poll.refreshing}
        >
          {semanticHits.length === 0 ? (
            <Empty>
              No semantic hits in the last 400 requests. Every hit that does land is listed
              here with its similarity and the request whose answer was served, because a
              score without a provenance is not auditable.
            </Empty>
          ) : (
            <TableWrap>
              <table className="data">
                <thead>
                  <tr>
                    <th>Request</th>
                    <th>Tenant</th>
                    <th>Model</th>
                    <th className="right">Similarity</th>
                    <th>Answered from</th>
                    <th className="right">Avoided</th>
                  </tr>
                </thead>
                <tbody>
                  {semanticHits.slice(0, 25).map((row) => (
                    <tr key={row.request_id}>
                      <td className="num">{row.request_id.slice(0, 14)}…</td>
                      <td>{names.get(row.tenant_id) ?? "—"}</td>
                      <td>{row.model}</td>
                      <td className="right num strong">{row.cache_similarity ?? "—"}</td>
                      <td className="num muted">{row.cache_source_request_id ?? "—"}</td>
                      <td className="right num">{money(row.cache_avoided_usd)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableWrap>
          )}
        </Card>
      </div>
    </>
  );
}
