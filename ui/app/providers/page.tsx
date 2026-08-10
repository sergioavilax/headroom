"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { PageHead, PollBadge } from "@/components/shell";
import { Badge, Card, Empty, ErrorNote, Num, StatTile, TableWrap } from "@/components/ui";
import { admin, type LedgerRow, type Provider } from "@/lib/api";
import { count, ms, percent, requestId, stamp } from "@/lib/format";
import { LIVE_INTERVAL_MS, usePoll } from "@/lib/poll";
import { colourFor, seriesColours } from "@/lib/series";

/**
 * Provider health, breaker state, and where traffic goes when one of them is down.
 *
 * **Everything here is one process's opinion, and the page says so.** A breaker is not a
 * fact about the world — it is a record of what *this* gateway has been able to reach — so
 * a Phase 9 deployment with four Fargate tasks has four independent verdicts, deliberately.
 * A console that presented one task's window as "provider health" would be the first thing
 * to mislead somebody during an incident.
 *
 * The chain is reported beside the health because the two questions an operator actually
 * has are *is vllm_a healthy* and *where does traffic go when it is not*, and neither
 * answers the other.
 */
export default function ProvidersPage() {
  const [busy, setBusy] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  const poll = usePoll(
    async () => {
      const [providers, rows] = await Promise.all([
        admin.get<Provider[]>("providers"),
        admin.get<LedgerRow[]>("usage", { limit: 200 }),
      ]);
      return { providers, rows };
    },
    LIVE_INTERVAL_MS,
    [],
  );

  const providers = poll.data?.providers ?? [];
  const rows = useMemo(() => poll.data?.rows ?? [], [poll.data?.rows]);
  const colours = useMemo(
    () => seriesColours((poll.data?.providers ?? []).map((provider) => provider.name)),
    [poll.data?.providers],
  );
  const hops = useMemo(() => rows.filter((row) => row.failover_hops > 0), [rows]);

  async function clearHealth(name: string) {
    setBusy(name);
    setProblem(null);
    try {
      await admin.del(`providers/${name}/health`);
      poll.refresh();
    } catch (error) {
      setProblem(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(null);
    }
  }

  const open = providers.filter((provider) => provider.state !== "closed");

  return (
    <>
      <PageHead
        title="Providers"
        sub="One gateway's experience of its upstreams — rolling windows, breaker state, and the chains each provider sits in."
      >
        <PollBadge intervalMs={LIVE_INTERVAL_MS} refreshing={poll.refreshing} />
      </PageHead>

      {poll.error && <ErrorNote error={poll.error} />}
      {problem && <ErrorNote error={new Error(problem)} />}

      <div className="stack">
        <div className="grid cols-4">
          <StatTile
            label="Configured"
            value={count(providers.length)}
            foot="from config/routing.yaml, fixed for this process's life"
          />
          <StatTile
            label="Breakers open"
            value={count(open.length)}
            foot={open.length === 0 ? "everything in rotation" : open.map((p) => p.name).join(", ")}
          />
          <StatTile
            label="Failed over"
            value={count(hops.length)}
            foot={`of the last ${rows.length} requests`}
          />
          <StatTile
            label="Observations"
            value={count(providers.reduce((sum, p) => sum + p.total_successes + p.total_failures, 0))}
            foot="one attempt, one observation — since this process started"
          />
        </div>

        <div className="grid cols-2">
          {providers.map((provider) => (
            <ProviderCard
              key={provider.name}
              provider={provider}
              colour={colourFor(colours, provider.name)}
              busy={busy === provider.name}
              onClear={() => void clearHealth(provider.name)}
            />
          ))}
          {providers.length === 0 && !poll.loading && (
            <Card>
              <Empty>No providers are configured.</Empty>
            </Card>
          )}
        </div>

        <Card
          title="Recent failovers"
          note="who was passed over, and why — the question error_reason cannot answer, because it describes the last thing that happened rather than the first"
          refreshing={poll.refreshing}
        >
          {hops.length === 0 ? (
            <Empty>
              Nothing has failed over in the last {rows.length} requests. Zero is the
              overwhelmingly common answer here, which is exactly why a non-zero one is worth
              a page.
            </Empty>
          ) : (
            <TableWrap>
              <table className="data">
                <thead>
                  <tr>
                    <th>Started</th>
                    <th>Request</th>
                    <th>Model</th>
                    <th>Passed over</th>
                    <th>Because</th>
                    <th>Served by</th>
                    <th className="right">Hops</th>
                    <th>Outcome</th>
                  </tr>
                </thead>
                <tbody>
                  {hops.slice(0, 30).map((row) => (
                    <tr key={row.request_id}>
                      <td className="num muted">{stamp(row.started_at)}</td>
                      <td className="num">
                        <Link href={`/requests?model=${encodeURIComponent(row.model)}`}>
                          {requestId(row.request_id)}
                        </Link>
                      </td>
                      <td>{row.model}</td>
                      <td>
                        <span className="row" style={{ gap: 6, flexWrap: "nowrap" }}>
                          <span
                            className="legend-swatch"
                            aria-hidden="true"
                            style={{ background: colourFor(colours, row.failover_from) }}
                          />
                          {row.failover_from}
                        </span>
                      </td>
                      <td>
                        <Badge tone={row.failover_error === "breaker_open" ? "warning" : "serious"}>
                          {row.failover_error}
                        </Badge>
                      </td>
                      <td>
                        <span className="row" style={{ gap: 6, flexWrap: "nowrap" }}>
                          <span
                            className="legend-swatch"
                            aria-hidden="true"
                            style={{ background: colourFor(colours, row.provider) }}
                          />
                          <span className="strong">{row.provider}</span>
                        </span>
                      </td>
                      <td className="right num">{row.failover_hops}</td>
                      <td>
                        <Badge tone={row.outcome === "ok" ? "good" : "critical"}>
                          {row.outcome}
                        </Badge>
                      </td>
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

function ProviderCard({
  provider,
  colour,
  busy,
  onClear,
}: {
  provider: Provider;
  colour: string;
  busy: boolean;
  onClear: () => void;
}) {
  const tone =
    provider.state === "closed" ? "good" : provider.state === "half_open" ? "warning" : "critical";
  const observed = provider.total_successes + provider.total_failures;

  return (
    <section className="card">
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 14 }}>
        <span className="row" style={{ gap: 8, flexWrap: "nowrap" }}>
          <span className="legend-swatch" aria-hidden="true" style={{ background: colour }} />
          <span>
            <span className="strong" style={{ fontSize: 14 }}>
              {provider.name}
            </span>
            <span className="muted"> · {provider.kind}</span>
          </span>
        </span>
        <Badge tone={tone}>{provider.state.replace("_", "-")}</Badge>
      </div>

      <div className="grid cols-3" style={{ gap: 10, marginBottom: 14 }}>
        <Metric label="p50" value={ms(provider.p50_latency_ms)} />
        <Metric label="p95" value={ms(provider.p95_latency_ms)} />
        <Metric
          label="window"
          value={
            provider.samples > 0
              ? `${provider.failures}/${provider.samples} · ${percent(provider.failures, provider.samples, 0)}`
              : "no samples"
          }
        />
      </div>

      <dl className="kv" style={{ gridTemplateColumns: "112px 1fr", fontSize: 12 }}>
        <dt>Lifetime</dt>
        <dd>
          <Num>{count(provider.total_successes)}</Num> ok ·{" "}
          <Num>{count(provider.total_failures)}</Num> failed
          {observed > 0 && (
            <span className="muted">
              {" "}
              ({percent(provider.total_failures, observed, 1)} failure rate)
            </span>
          )}
        </dd>
        <dt>Consecutive</dt>
        <dd>
          <Num>{provider.consecutive_failures}</Num> failures
        </dd>
        {provider.last_error && (
          <>
            <dt>Last error</dt>
            <dd style={{ color: "var(--serious)" }}>{provider.last_error}</dd>
          </>
        )}
        {provider.reopen_in_s !== null && (
          <>
            <dt>Probes in</dt>
            <dd style={{ color: "var(--warning)" }}>
              <Num>{provider.reopen_in_s.toFixed(1)}s</Num>
            </dd>
          </>
        )}
        <dt>Chains</dt>
        <dd>
          {provider.routes.length === 0 ? (
            <span className="muted">
              configured but unreachable by any rule — worth seeing, and easy to miss
            </span>
          ) : (
            provider.routes.map((route) => (
              <div key={`${route.dialect}:${route.prefix}`} className="num" style={{ fontSize: 11.5 }}>
                {route.dialect}:{route.prefix === "" ? "*" : route.prefix} →{" "}
                {route.chain.map((name, index) => (
                  <span key={name}>
                    {index > 0 && <span className="muted"> → </span>}
                    <span className={name === provider.name ? "strong" : "muted"}>{name}</span>
                  </span>
                ))}
              </div>
            ))
          )}
        </dd>
      </dl>

      <div className="row" style={{ marginTop: 14, justifyContent: "flex-end" }}>
        <button type="button" className="btn small" disabled={busy} onClick={onClear}>
          Clear health
        </button>
      </div>
    </section>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="tile-label">{label}</div>
      <div className="num" style={{ marginTop: 4, fontSize: 15 }}>
        {value}
      </div>
    </div>
  );
}
