"use client";

import { useMemo, useState } from "react";
import { Legend, RankBars, StackedBars } from "@/components/charts";
import { PageHead, PollBadge } from "@/components/shell";
import { Card, Empty, ErrorNote, Field, Num, StatTile, TableWrap, Toolbar } from "@/components/ui";
import { admin, type Rollup, type Tenant } from "@/lib/api";
import { ago, count, moneyFromPicos, stamp, sumUsd, toPicos } from "@/lib/format";
import { SLOW_INTERVAL_MS, usePoll } from "@/lib/poll";
import { DAY_MS, dayLabel, NO_PROVIDER, seriesColours, timeSlots, utcDayMs } from "@/lib/series";

/**
 * History — the days behind today, out of `daily_rollups`.
 *
 * Every other view in this console reads the ledger directly, which is right for a window
 * measured in minutes and wrong for one measured in months: `usage_ledger` grows by a row
 * per request forever, and a chart of ninety days would scan every one of them on every
 * poll. So a scheduled Lambda aggregates each day once (`headroom/rollup/`) and this view
 * reads the result — still through `/admin/*` and nothing else (H-054), still one `GET`.
 *
 * **A missing day is ambiguous, and the view says so rather than drawing a confident
 * zero.** No bar means either "no traffic that day" or "that day has not been rolled up",
 * and the two have completely different fixes. What distinguishes them is `computed_at`,
 * which is why the freshness tile is a first-class number here and not a footnote: it is
 * the one thing on screen that says whether the schedule is still firing.
 */

const WINDOWS = [7, 30, 90] as const;

export default function HistoryPage() {
  const [days, setDays] = useState<number>(30);

  const poll = usePoll(
    async () => {
      const [rollups, tenants] = await Promise.all([
        admin.get<Rollup[]>("usage/rollups", { limit: days }),
        admin.get<Tenant[]>("tenants"),
      ]);
      return { rollups, tenants };
    },
    SLOW_INTERVAL_MS,
    [days],
  );

  const data = poll.data;
  const names = useMemo(
    () => new Map((data?.tenants ?? []).map((tenant) => [tenant.id, tenant.name])),
    [data?.tenants],
  );

  // Colour follows the entity — a tenant's slot comes from the gateway's own tenant list,
  // which is creation order and stable — so changing the window never repaints a series
  // the reader has already learned (H-057's rule, one view along).
  const colours = useMemo(
    () => seriesColours((data?.tenants ?? []).map((tenant) => tenant.id)),
    [data?.tenants],
  );

  const totals = useMemo(() => {
    const rows = data?.rollups ?? [];
    const computed = rows
      .map((row) => row.computed_at)
      .filter((at): at is string => at !== null)
      .sort();
    return {
      spend: sumUsd(rows.map((row) => row.usd_cost)),
      saved: sumUsd(rows.map((row) => row.cache_avoided_usd)),
      requests: rows.reduce((sum, row) => sum + row.requests, 0),
      errored: rows.reduce((sum, row) => sum + row.errored_requests, 0),
      failovers: rows.reduce((sum, row) => sum + row.failover_requests, 0),
      hits: rows.reduce((sum, row) => sum + row.cache_hits_exact + row.cache_hits_semantic, 0),
      unpriced: rows.reduce((sum, row) => sum + row.unpriced_requests, 0),
      unknownSavings: rows.reduce((sum, row) => sum + row.cache_avoided_unknown, 0),
      coveredDays: new Set(rows.map((row) => row.day)).size,
      newest: computed.at(-1) ?? null,
      newestDay: rows.length === 0 ? null : rows[rows.length - 1]!.day,
    };
  }, [data?.rollups]);

  // The x-domain is "the last N UTC days", which this view knows and the store deliberately
  // does not — a day with no traffic has no row, and inventing one is the caller's job
  // (`UsageBucket`'s rule). `poll.fetchedAt` rather than `Date.now()`, so the chart is a
  // pure function of the data on screen.
  const buckets = useMemo(() => {
    if (poll.fetchedAt === 0) return [];
    const byDay = new Map<number, Record<string, number>>();
    for (const row of data?.rollups ?? []) {
      const at = utcDayMs(row.day);
      const slot = byDay.get(at) ?? {};
      slot[row.tenant_id] = (slot[row.tenant_id] ?? 0) + row.requests;
      byDay.set(at, slot);
    }
    return timeSlots(poll.fetchedAt, days, DAY_MS).map((at) => ({
      at,
      values: byDay.get(at) ?? {},
      // `at` is exactly UTC midnight (`timeSlots` floors by the slot width), so the first
      // ten characters of its ISO form are the same `YYYY-MM-DD` the API returned.
      label: dayLabel(new Date(at).toISOString().slice(0, 10)),
    }));
  }, [data?.rollups, days, poll.fetchedAt]);

  const series = useMemo(
    () =>
      (data?.tenants ?? []).map((tenant) => ({
        key: tenant.id,
        label: tenant.name,
        colour: colours.get(tenant.id) ?? NO_PROVIDER,
      })),
    [colours, data?.tenants],
  );

  const spendByTenant = useMemo(() => {
    const picos = new Map<string, bigint>();
    for (const row of data?.rollups ?? []) {
      picos.set(row.tenant_id, (picos.get(row.tenant_id) ?? 0n) + toPicos(row.usd_cost));
    }
    return [...picos.entries()]
      .sort((left, right) => (right[1] > left[1] ? 1 : right[1] < left[1] ? -1 : 0))
      .map(([tenantId, amount]) => ({
        key: tenantId,
        label: names.get(tenantId) ?? tenantId,
        value: Number(amount),
        display: moneyFromPicos(amount),
      }));
  }, [data?.rollups, names]);

  // One line per day, tenants folded together: the table answers "which day was expensive",
  // and the chart beside it answers "which tenant made it so".
  const perDay = useMemo(() => {
    const byDay = new Map<string, Rollup[]>();
    for (const row of data?.rollups ?? []) {
      byDay.set(row.day, [...(byDay.get(row.day) ?? []), row]);
    }
    return [...byDay.entries()]
      .sort((left, right) => (left[0] < right[0] ? 1 : -1))
      .map(([day, rows]) => ({
        day,
        tenants: rows.length,
        requests: rows.reduce((sum, row) => sum + row.requests, 0),
        spend: sumUsd(rows.map((row) => row.usd_cost)),
        saved: sumUsd(rows.map((row) => row.cache_avoided_usd)),
        hits: rows.reduce((sum, row) => sum + row.cache_hits_exact + row.cache_hits_semantic, 0),
        errored: rows.reduce((sum, row) => sum + row.errored_requests, 0),
        failovers: rows.reduce((sum, row) => sum + row.failover_requests, 0),
        computed: rows.map((row) => row.computed_at).filter((at): at is string => at !== null),
      }));
  }, [data?.rollups]);

  const ticks = buckets.length > 1 ? [0, Math.floor((buckets.length - 1) / 2), buckets.length - 1] : [0];

  return (
    <>
      <PageHead
        title="History"
        sub="Days, not minutes — read from the rollups a scheduled job writes, so a ninety-day chart is one indexed query rather than a scan of every request ever served."
      >
        <PollBadge intervalMs={SLOW_INTERVAL_MS} refreshing={poll.refreshing} />
      </PageHead>

      {poll.error && <ErrorNote error={poll.error} />}

      <div className="stack">
        <Toolbar>
          <Field label="Window">
            <select value={days} onChange={(event) => setDays(Number(event.target.value))}>
              {WINDOWS.map((option) => (
                <option key={option} value={option}>
                  last {option} days
                </option>
              ))}
            </select>
          </Field>
        </Toolbar>

        <div className="grid cols-4">
          <StatTile
            label="Spend"
            value={moneyFromPicos(totals.spend)}
            foot={
              totals.unpriced > 0 ? (
                <span style={{ color: "var(--warning)" }}>
                  {count(totals.unpriced)} request{totals.unpriced === 1 ? "" : "s"} could not be
                  priced
                </span>
              ) : (
                `${count(totals.coveredDays)} day${totals.coveredDays === 1 ? "" : "s"} with traffic`
              )
            }
          />
          <StatTile
            label="Requests"
            value={count(totals.requests, { compact: true })}
            foot={`${count(totals.errored)} did not end ok · ${count(totals.failovers)} failed over`}
          />
          <StatTile
            label="Cache saved"
            value={moneyFromPicos(totals.saved)}
            foot={
              totals.unknownSavings > 0 ? (
                <span style={{ color: "var(--warning)" }}>
                  {count(totals.hits)} hits · {count(totals.unknownSavings)} could not be priced
                </span>
              ) : (
                `${count(totals.hits)} hits`
              )
            }
          />
          <StatTile
            label="Last rollup"
            value={totals.newest ? ago(totals.newest) : "never"}
            small
            foot={
              totals.newest ? (
                <>
                  covering {totals.newestDay} · computed {stamp(totals.newest)}
                </>
              ) : (
                "nothing has been rolled up yet — run the job, or wait for the schedule"
              )
            }
          />
        </div>

        <Card
          title="Requests per day"
          note="stacked by tenant. An empty column is a day with no traffic — or a day nobody rolled up; the stamp above says which."
          refreshing={poll.refreshing}
          actions={<Legend items={series.map((entry) => ({ label: entry.label, colour: entry.colour }))} />}
        >
          {buckets.length === 0 ? (
            <Empty>Nothing yet.</Empty>
          ) : (
            <StackedBars
              buckets={buckets}
              series={series}
              ticks={ticks}
              ariaLabel={`Requests per day over the last ${days} days, split by tenant`}
            />
          )}
        </Card>

        <div className="grid cols-2">
          <Card
            title="Spend by tenant"
            note={`settled cost across the last ${days} days`}
            refreshing={poll.refreshing}
          >
            {spendByTenant.length === 0 ? (
              <Empty>No rollups in this window.</Empty>
            ) : (
              <RankBars rows={spendByTenant} ariaLabel="Spend by tenant over the window" />
            )}
          </Card>

          <Card title="By day" note="newest first" refreshing={poll.refreshing}>
            {perDay.length === 0 ? (
              <Empty>
                No rollups yet. The schedule writes one a night; `python -m headroom.rollup`
                writes one now.
              </Empty>
            ) : (
              <TableWrap>
                <table className="data">
                  <thead>
                    <tr>
                      <th>Day</th>
                      <th className="right">Requests</th>
                      <th className="right">Spend</th>
                      <th className="right">Hits</th>
                      <th className="right">Saved</th>
                      <th className="right">Errors</th>
                    </tr>
                  </thead>
                  <tbody>
                    {perDay.map((day) => (
                      <tr key={day.day}>
                        <td className="strong">
                          <Num>{day.day}</Num>
                        </td>
                        <td className="right">
                          <Num>{count(day.requests)}</Num>
                        </td>
                        <td className="right strong">
                          <Num>{moneyFromPicos(day.spend)}</Num>
                        </td>
                        <td className="right">
                          <Num>{count(day.hits)}</Num>
                        </td>
                        <td className="right">
                          <Num>{moneyFromPicos(day.saved)}</Num>
                        </td>
                        <td className="right">
                          <Num>{count(day.errored)}</Num>
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
