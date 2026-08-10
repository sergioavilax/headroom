"use client";

import { useMemo } from "react";
import { Legend, StackedBars } from "@/components/charts";
import { PageHead, PollBadge } from "@/components/shell";
import { Badge, Card, Empty, ErrorNote, StatTile, TableWrap } from "@/components/ui";
import { admin, type LedgerRow, type Provider, type Tenant } from "@/lib/api";
import { ago, count, ms, money, requestId } from "@/lib/format";
import { LIVE_INTERVAL_MS, usePoll } from "@/lib/poll";
import { NO_PROVIDER, colourFor, seriesColours, timeSlots } from "@/lib/series";

/**
 * **The kill-demo view.** Requests arriving, and which upstream served each one.
 *
 * BUILD_PLAN §P6 asks to "watch the dashboard show traffic shift to the second GPU with
 * zero failed requests", and §P7's gate re-runs that demo with this page on screen. So
 * the page is built around exactly that moment: a bar chart of the last ten minutes
 * stacked **by provider**, so the colour of the stack changes the instant traffic moves;
 * a row per request with the provider it named and the hop it took; and the breaker
 * tiles above, so the primary going `open` is visible in the same glance.
 *
 * Two calls, polled every two seconds. The chart and the table are the *same* rows —
 * `/admin/usage?limit=200` — rather than a series call beside a list call, because two
 * queries against a moving table can disagree with each other, and a chart that
 * disagreed with the rows beneath it would be the least trustworthy thing on the screen.
 * The cost of that choice is stated in the card: this is the last 200 requests, not all
 * of them.
 */

const WINDOW_SLOTS = 60;
const SLOT_MS = 10_000;
const ROW_LIMIT = 200;

export default function LivePage() {
  const poll = usePoll(
    async () => {
      const [rows, providers, tenants] = await Promise.all([
        admin.get<LedgerRow[]>("usage", { limit: ROW_LIMIT }),
        admin.get<Provider[]>("providers"),
        admin.get<Tenant[]>("tenants"),
      ]);
      return { rows, providers, tenants };
    },
    LIVE_INTERVAL_MS,
    [],
  );

  const data = poll.data;
  const rows = useMemo(() => data?.rows ?? [], [data?.rows]);
  const names = useMemo(
    () => new Map((data?.tenants ?? []).map((tenant) => [tenant.id, tenant.name])),
    [data?.tenants],
  );

  // Colour follows the entity: a provider's slot comes from its index in the gateway's
  // own provider list, so a provider that goes silent mid-demo does not repaint the one
  // that took over.
  const colours = useMemo(
    () => seriesColours((data?.providers ?? []).map((provider) => provider.name)),
    [data?.providers],
  );

  const active = useMemo(() => {
    const seen = new Set<string>();
    for (const row of rows) if (row.provider) seen.add(row.provider);
    return (data?.providers ?? []).map((provider) => provider.name).filter((name) => seen.has(name));
  }, [rows, data?.providers]);

  const series = useMemo(
    () => [
      ...active.map((name) => ({ key: name, label: name, colour: colourFor(colours, name) })),
      { key: "__none", label: "no upstream", colour: NO_PROVIDER },
    ],
    [active, colours],
  );

  // Every window on this page ends at the moment the rows were read, not at render time:
  // the chart, the "last ten minutes" counters, and the relative timestamps in the table
  // all share one clock, so they cannot disagree with each other mid-poll.
  const buckets = useMemo(() => {
    if (poll.fetchedAt === 0) return [];
    const slots = timeSlots(poll.fetchedAt, WINDOW_SLOTS, SLOT_MS);
    const first = slots[0] ?? 0;
    const index = new Map(slots.map((at) => [at, {} as Record<string, number>]));
    for (const row of rows) {
      const at = Math.floor(new Date(row.started_at).getTime() / SLOT_MS) * SLOT_MS;
      if (at < first) continue;
      const values = index.get(at);
      if (!values) continue;
      const key = row.provider ?? "__none";
      values[key] = (values[key] ?? 0) + 1;
    }
    return slots.map((at) => ({
      at,
      values: index.get(at) ?? {},
      label: new Date(at).toLocaleTimeString("en-GB", { hour12: false }),
    }));
  }, [rows, poll.fetchedAt]);

  const recent = useMemo(() => {
    if (poll.fetchedAt === 0) return [];
    const cutoff = poll.fetchedAt - WINDOW_SLOTS * SLOT_MS;
    return rows.filter((row) => new Date(row.started_at).getTime() >= cutoff);
  }, [rows, poll.fetchedAt]);

  const hops = recent.filter((row) => row.failover_hops > 0).length;
  const failed = recent.filter((row) => (row.status_code ?? 0) >= 500).length;

  return (
    <>
      <PageHead
        title="Live traffic"
        sub="The last ten minutes, by the upstream that served each request. When a provider dies, the colour of the stack is the thing that moves."
      >
        <PollBadge intervalMs={LIVE_INTERVAL_MS} refreshing={poll.refreshing} />
      </PageHead>

      {poll.error && <ErrorNote error={poll.error} />}

      <div className="stack">
        <div className="grid cols-4">
          <StatTile
            label="Requests (10 min)"
            value={count(recent.length)}
            foot={recent.length === ROW_LIMIT ? `capped at the last ${ROW_LIMIT}` : "in the window"}
          />
          <StatTile
            label="Failed over"
            value={count(hops)}
            foot={hops > 0 ? "served by a fallback" : "primary served everything"}
          />
          <StatTile
            label="Caller-visible 5xx"
            value={count(failed)}
            foot={failed === 0 ? "the claim, holding" : "these reached a caller"}
          />
          <StatTile
            label="Breakers"
            value={`${(data?.providers ?? []).filter((provider) => provider.state === "closed").length}/${(data?.providers ?? []).length}`}
            small
            foot="closed / configured"
          />
        </div>

        <Card
          title="Requests by upstream"
          note={`ten-second buckets · the last ${ROW_LIMIT} requests`}
          refreshing={poll.refreshing}
          actions={
            <Legend
              items={series.map((entry) => ({ label: entry.label, colour: entry.colour }))}
            />
          }
        >
          <StackedBars
            buckets={buckets}
            series={series}
            height={168}
            ticks={[0, 30, WINDOW_SLOTS - 1]}
            ariaLabel="Requests per ten seconds, stacked by the provider that served them"
          />
        </Card>

        <div className="grid cols-4">
          {(data?.providers ?? []).map((provider) => (
            <ProviderTile
              key={provider.name}
              provider={provider}
              colour={colourFor(colours, provider.name)}
              served={recent.filter((row) => row.provider === provider.name).length}
            />
          ))}
        </div>

        <Card
          title="Recent requests"
          note="newest first · a hop is called out where it happened"
          refreshing={poll.refreshing}
        >
          {recent.length === 0 ? (
            <Empty>Nothing in the last ten minutes. Run `make seed`, or send a request.</Empty>
          ) : (
            <TableWrap>
              <table className="data">
                <thead>
                  <tr>
                    <th>When</th>
                    <th>Request</th>
                    <th>Tenant</th>
                    <th>Model</th>
                    <th>Upstream</th>
                    <th>Outcome</th>
                    <th className="right">TTFT</th>
                    <th className="right">Cost</th>
                  </tr>
                </thead>
                <tbody>
                  {recent.slice(0, 40).map((row) => (
                    <tr key={row.request_id}>
                      <td className="num">{ago(row.started_at, poll.fetchedAt)}</td>
                      <td className="num">{requestId(row.request_id)}</td>
                      <td>{names.get(row.tenant_id) ?? "—"}</td>
                      <td>{row.model}</td>
                      <td>
                        {row.provider ? (
                          <span className="row" style={{ gap: 6, flexWrap: "nowrap" }}>
                            <span
                              className="legend-swatch"
                              aria-hidden="true"
                              style={{ background: colourFor(colours, row.provider) }}
                            />
                            <span className="strong">{row.provider}</span>
                            {row.failover_hops > 0 && (
                              <Badge tone="warning">
                                ← {row.failover_from} · {row.failover_error}
                              </Badge>
                            )}
                          </span>
                        ) : (
                          <span className="muted">
                            {row.cache_disposition?.startsWith("cache_hit")
                              ? "cache"
                              : "none"}
                          </span>
                        )}
                      </td>
                      <td>
                        <Badge
                          tone={
                            row.outcome === "ok"
                              ? "good"
                              : (row.status_code ?? 0) >= 500
                                ? "critical"
                                : "warning"
                          }
                        >
                          {row.outcome} · {row.status_code ?? "—"}
                        </Badge>
                      </td>
                      <td className="right num">{ms(row.ttft_ms)}</td>
                      <td className="right num">{money(row.usd_cost)}</td>
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

function ProviderTile({
  provider,
  colour,
  served,
}: {
  provider: Provider;
  colour: string;
  served: number;
}) {
  const tone =
    provider.state === "closed" ? "good" : provider.state === "half_open" ? "warning" : "critical";
  return (
    <section className="card">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <span className="row" style={{ gap: 7, flexWrap: "nowrap" }}>
          <span className="legend-swatch" aria-hidden="true" style={{ background: colour }} />
          <span className="strong">{provider.name}</span>
        </span>
        <Badge tone={tone}>{provider.state}</Badge>
      </div>
      <div className="tile-value small" style={{ marginTop: 10 }}>
        {count(served)}
      </div>
      <div className="tile-foot">served in the window</div>
      <div className="tile-foot" style={{ marginTop: 6 }}>
        p50 <span className="num">{ms(provider.p50_latency_ms)}</span> · failures{" "}
        <span className="num">
          {provider.failures}/{provider.samples}
        </span>
        {provider.reopen_in_s !== null && (
          <>
            {" "}
            · probes in <span className="num">{provider.reopen_in_s.toFixed(0)}s</span>
          </>
        )}
      </div>
      {provider.last_error && (
        <div className="tile-foot" style={{ color: "var(--serious)" }}>
          last error: {provider.last_error}
        </div>
      )}
    </section>
  );
}
