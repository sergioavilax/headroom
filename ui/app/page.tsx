"use client";

import { useMemo } from "react";
import Link from "next/link";
import { Legend, RankBars, Sparkbars, StackedBars, ProportionBar } from "@/components/charts";
import { PageHead, PollBadge } from "@/components/shell";
import { Badge, Card, Empty, ErrorNote, Num, StatTile, TableWrap } from "@/components/ui";
import { admin, type Provider, type SeriesPoint, type Tenant, type Totals } from "@/lib/api";
import { count, money, moneyFromPicos, percent, sumUsd, toPicos } from "@/lib/format";
import { PAGE_INTERVAL_MS, usePoll } from "@/lib/poll";
import { NO_PROVIDER, timeSlots } from "@/lib/series";

/**
 * What the gateway has been doing. One screen, four questions: what has it cost, what is
 * it serving, what did the cache save, and is anything unwell.
 *
 * Every figure is `/admin/usage/totals`, `/admin/usage/series`, `/admin/tenants`, or
 * `/admin/providers` — four calls, no local arithmetic beyond summing a column that the
 * API already reports per tenant, and that sum is done in integer picodollars so it
 * matches the ledger to the last digit rather than to a float's idea of it.
 */
export default function OverviewPage() {
  const poll = usePoll(
    async () => {
      const [totals, tenants, providers, series] = await Promise.all([
        admin.get<Totals[]>("usage/totals"),
        admin.get<Tenant[]>("tenants"),
        admin.get<Provider[]>("providers"),
        admin.get<SeriesPoint[]>("usage/series", { bucket: "minute", limit: 60 }),
      ]);
      return { totals, tenants, providers, series };
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
    const requests = totals.reduce((sum, row) => sum + row.requests, 0);
    const hits = totals.reduce(
      (sum, row) => sum + row.cache_hits_exact + row.cache_hits_semantic,
      0,
    );
    return {
      requests,
      spend: sumUsd(totals.map((row) => row.usd_cost)),
      saved: sumUsd(totals.map((row) => row.cache_avoided_usd)),
      hits,
      errored: totals.reduce((sum, row) => sum + row.errored_requests, 0),
      unpriced: totals.reduce((sum, row) => sum + row.unpriced_requests, 0),
      failovers: totals.reduce((sum, row) => sum + row.failover_requests, 0),
      lookups: totals.reduce(
        (sum, row) => sum + row.cache_hits_exact + row.cache_hits_semantic + row.cache_misses,
        0,
      ),
      dispositions: [
        {
          key: "hit_exact",
          label: "exact hit",
          value: totals.reduce((sum, row) => sum + row.cache_hits_exact, 0),
          colour: "var(--series-3)",
        },
        {
          key: "hit_semantic",
          label: "semantic hit",
          value: totals.reduce((sum, row) => sum + row.cache_hits_semantic, 0),
          colour: "var(--series-1)",
        },
        {
          key: "miss",
          label: "miss",
          value: totals.reduce((sum, row) => sum + row.cache_misses, 0),
          colour: "var(--series-4)",
        },
        {
          key: "bypass",
          label: "bypass",
          value: totals.reduce((sum, row) => sum + row.cache_bypasses, 0),
          colour: "var(--series-2)",
        },
        {
          key: "disabled",
          label: "disabled",
          value: totals.reduce((sum, row) => sum + row.cache_disabled, 0),
          colour: NO_PROVIDER,
        },
      ],
    };
  }, [data?.totals]);

  // The window ends when the data was *read*, not when React happened to render — which
  // keeps this a pure function of the poll's result, and is also the honest reading: a
  // chart whose last bucket is "now" against four-second-old rows draws a gap that looks
  // like an outage and is not one.
  const buckets = useMemo(() => {
    if (poll.fetchedAt === 0) return [];
    const points = new Map(
      (data?.series ?? []).map((point) => [new Date(point.bucket_start).getTime(), point]),
    );
    return timeSlots(poll.fetchedAt, 60, 60_000).map((at) => {
      const point = points.get(at);
      return {
        at,
        values: {
          served: (point?.requests ?? 0) - (point?.errored_requests ?? 0),
          errored: point?.errored_requests ?? 0,
        },
      };
    });
  }, [data?.series, poll.fetchedAt]);

  const spendRows = useMemo(() => {
    const byTenant = new Map<string, bigint>();
    for (const row of data?.totals ?? []) {
      byTenant.set(row.tenant_id, (byTenant.get(row.tenant_id) ?? 0n) + toPicos(row.usd_cost));
    }
    return [...byTenant.entries()]
      .sort((left, right) => (right[1] > left[1] ? 1 : right[1] < left[1] ? -1 : 0))
      .slice(0, 8)
      .map(([tenantId, picos]) => ({
        key: tenantId,
        label: names.get(tenantId) ?? tenantId,
        value: Number(picos),
        display: moneyFromPicos(picos),
      }));
  }, [data?.totals, names]);

  const unwell = (data?.providers ?? []).filter((provider) => provider.state !== "closed");

  return (
    <>
      <PageHead
        title="Overview"
        sub="Everything below is read from the gateway's own admin API — the same numbers the ledger holds, not a copy of them."
      >
        <PollBadge intervalMs={PAGE_INTERVAL_MS} refreshing={poll.refreshing} />
      </PageHead>

      {poll.error && <ErrorNote error={poll.error} />}

      <div className="stack">
        <div className="grid cols-5">
          <StatTile
            label="Spend"
            value={moneyFromPicos(rollup.spend)}
            foot={
              rollup.unpriced > 0 ? (
                <span style={{ color: "var(--warning)" }}>
                  {count(rollup.unpriced)} request{rollup.unpriced === 1 ? "" : "s"} could not be
                  priced
                </span>
              ) : (
                "every request priced"
              )
            }
          />
          <StatTile
            label="Requests"
            value={count(rollup.requests, { compact: true })}
            foot={`${count(rollup.errored)} did not end ok`}
          >
            <Sparkbars
              values={buckets.map((bucket) => bucket.values.served + bucket.values.errored)}
              ariaLabel="Requests per minute over the last hour"
            />
          </StatTile>
          <StatTile
            label="Cache saved"
            value={moneyFromPicos(rollup.saved)}
            foot={
              rollup.lookups > 0
                ? `${count(rollup.hits)} hits · ${percent(rollup.hits, rollup.lookups)} of lookups`
                : "no tenant is caching yet"
            }
          />
          <StatTile
            label="Failed over"
            value={count(rollup.failovers)}
            foot={
              rollup.failovers > 0
                ? "requests the primary did not serve"
                : "every request served where it was routed"
            }
          />
          <StatTile
            label="Providers"
            value={`${(data?.providers ?? []).length - unwell.length}/${(data?.providers ?? []).length}`}
            small
            foot={unwell.length === 0 ? "all breakers closed" : `${unwell.length} tripped`}
          >
            <div className="row" style={{ gap: 6 }}>
              {(data?.providers ?? []).map((provider) => (
                <Badge
                  key={provider.name}
                  tone={
                    provider.state === "closed"
                      ? "good"
                      : provider.state === "half_open"
                        ? "warning"
                        : "critical"
                  }
                >
                  {provider.name}
                </Badge>
              ))}
            </div>
          </StatTile>
        </div>

        <div className="grid cols-2">
          <Card
            title="Requests per minute"
            note="the last hour, from the ledger's own timestamps"
            refreshing={poll.refreshing}
            actions={
              <Legend
                items={[
                  { label: "ok", colour: "var(--series-1)" },
                  { label: "not ok", colour: "var(--critical)" },
                ]}
              />
            }
          >
            <StackedBars
              buckets={buckets}
              series={[
                { key: "served", label: "ok", colour: "var(--series-1)" },
                { key: "errored", label: "not ok", colour: "var(--critical)" },
              ]}
              ticks={[0, 30, 59]}
              ariaLabel="Requests per minute over the last hour, split by outcome"
            />
          </Card>

          <Card
            title="Spend by tenant"
            note="settled cost, priced at the rates each request was billed at"
            refreshing={poll.refreshing}
          >
            {spendRows.length === 0 ? (
              <Empty>No metered requests yet. Run `make seed`.</Empty>
            ) : (
              <RankBars rows={spendRows} ariaLabel="Spend by tenant" />
            )}
          </Card>
        </div>

        <div className="grid cols-2">
          <Card
            title="Cache dispositions"
            note="five values, because “switched off” and “never applicable” have different fixes"
            refreshing={poll.refreshing}
          >
            <ProportionBar parts={rollup.dispositions} ariaLabel="Cache dispositions" />
            <div style={{ marginTop: 12 }}>
              <Legend
                items={rollup.dispositions.map((part) => ({
                  label: `${part.label} · ${count(part.value)}`,
                  colour: part.colour,
                }))}
              />
            </div>
          </Card>

          <Card title="Tenants" note="spend, traffic, and what the cache did for each" refreshing={poll.refreshing}>
            {(data?.totals ?? []).length === 0 ? (
              <Empty>Nothing metered yet.</Empty>
            ) : (
              <TableWrap>
                <table className="data">
                  <thead>
                    <tr>
                      <th>Tenant</th>
                      <th className="right">Requests</th>
                      <th className="right">Spend</th>
                      <th className="right">Hits</th>
                      <th className="right">Saved</th>
                      <th className="right">Errors</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...(data?.totals ?? [])]
                      .sort((left, right) => right.requests - left.requests)
                      .map((row) => (
                        <tr key={row.tenant_id}>
                          <td className="strong">
                            <Link href={`/requests?tenant_id=${row.tenant_id}`}>
                              {names.get(row.tenant_id) ?? row.tenant_id}
                            </Link>
                          </td>
                          <td className="right">
                            <Num>{count(row.requests)}</Num>
                          </td>
                          <td className="right strong">
                            <Num>{money(row.usd_cost)}</Num>
                          </td>
                          <td className="right">
                            <Num>{count(row.cache_hits_exact + row.cache_hits_semantic)}</Num>
                          </td>
                          <td className="right">
                            <Num>{money(row.cache_avoided_usd)}</Num>
                          </td>
                          <td className="right">
                            <Num>{count(row.errored_requests)}</Num>
                          </td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              </TableWrap>
            )}
          </Card>
        </div>
      </div>
    </>
  );
}
