"use client";

import { useMemo, useState } from "react";
import { BucketMeter, ChannelStrip } from "@/components/charts";
import { PageHead, PollBadge } from "@/components/shell";
import { Badge, Card, Empty, ErrorNote, Field, Num, TableWrap, Toolbar } from "@/components/ui";
import { admin, type Budget, type Limit, type Tenant } from "@/lib/api";
import { count, money, moneyFromPicos, percent, seconds, toPicos } from "@/lib/format";
import { PAGE_INTERVAL_MS, usePoll } from "@/lib/poll";

/**
 * Budgets as channel strips, rate limits as buckets. **The one flourish this UI is
 * allowed**, and it earns its keep: the audio metaphor is not a costume on a progress
 * bar, it is what the numbers actually are.
 *
 * A strip stacks **settled spend** and **live reservations** from the bottom, with the cap
 * as a line across the top and the gap between them as headroom. That the two stack is the
 * whole of BUILD_PLAN §0.2 rule 5 rendered: the gate compares *committed* spend — landed
 * plus reserved — against the cap, never landed alone, and a dashboard that drew only the
 * landed bar would be D-019 with a nicer font.
 *
 * The buckets underneath are read the same way: how much is left, and how long until full.
 * Both come from `/admin/limits`, which joins the Postgres row that says what the limit is
 * with the DynamoDB item that says what is left of it — because "why did that request get a
 * 429" is answered by neither alone.
 */
export default function LimitsPage() {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [budgetTenant, setBudgetTenant] = useState("");
  const [budgetUsd, setBudgetUsd] = useState("");
  const [budgetWindow, setBudgetWindow] = useState("monthly");
  const [limitTenant, setLimitTenant] = useState("");
  const [rpm, setRpm] = useState("");
  const [tpm, setTpm] = useState("");

  const poll = usePoll(
    async () => {
      const [tenants, budgets, limits] = await Promise.all([
        admin.get<Tenant[]>("tenants"),
        admin.get<Budget[]>("budgets"),
        admin.get<Limit[]>("limits"),
      ]);
      return { tenants, budgets, limits };
    },
    PAGE_INTERVAL_MS,
    [],
  );

  const tenants = poll.data?.tenants ?? [];
  const budgets = poll.data?.budgets ?? [];
  const limits = poll.data?.limits ?? [];
  const names = useMemo(
    () => new Map((poll.data?.tenants ?? []).map((tenant) => [tenant.id, tenant.name])),
    [poll.data?.tenants],
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
        title="Limits & budgets"
        sub="A budget is a stock and a rate limit is a flow. One is settled and corrected; the other refills on its own and is never refunded."
      >
        <PollBadge intervalMs={PAGE_INTERVAL_MS} refreshing={poll.refreshing} />
      </PageHead>

      {poll.error && <ErrorNote error={poll.error} />}
      {problem && <ErrorNote error={new Error(problem)} />}

      <div className="stack">
        <div className="grid cols-2">
          <Card title="Set a budget" note="money is a quoted string all the way down — a JSON number is a double, and a double is not money">
            <Toolbar>
              <Field label="Tenant" style={{ minWidth: 150 }}>
                <select
                  className="control"
                  value={budgetTenant}
                  onChange={(event) => setBudgetTenant(event.target.value)}
                >
                  <option value="">choose…</option>
                  {tenants.map((tenant) => (
                    <option key={tenant.id} value={tenant.id}>
                      {tenant.name}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="USD" style={{ minWidth: 110 }}>
                <input
                  className="control num"
                  value={budgetUsd}
                  placeholder="25.00"
                  onChange={(event) => setBudgetUsd(event.target.value)}
                />
              </Field>
              <Field label="Window">
                <select
                  className="control"
                  value={budgetWindow}
                  onChange={(event) => setBudgetWindow(event.target.value)}
                >
                  <option value="monthly">monthly</option>
                  <option value="total">total</option>
                </select>
              </Field>
              <button
                type="button"
                className="btn primary"
                disabled={busy || !budgetTenant || budgetUsd.trim() === ""}
                onClick={() =>
                  void act(async () => {
                    await admin.put(`budgets/${budgetTenant}`, {
                      usd: budgetUsd.trim(),
                      window: budgetWindow,
                    });
                    setBudgetUsd("");
                  })
                }
              >
                Set
              </button>
            </Toolbar>
          </Card>

          <Card title="Set a rate limit" note="PUT replaces: an empty dimension is unlimited, not unchanged">
            <Toolbar>
              <Field label="Tenant" style={{ minWidth: 150 }}>
                <select
                  className="control"
                  value={limitTenant}
                  onChange={(event) => setLimitTenant(event.target.value)}
                >
                  <option value="">choose…</option>
                  {tenants.map((tenant) => (
                    <option key={tenant.id} value={tenant.id}>
                      {tenant.name}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Requests / min" style={{ minWidth: 110 }}>
                <input
                  className="control num"
                  value={rpm}
                  placeholder="none"
                  onChange={(event) => setRpm(event.target.value)}
                />
              </Field>
              <Field label="Tokens / min" style={{ minWidth: 110 }}>
                <input
                  className="control num"
                  value={tpm}
                  placeholder="none"
                  onChange={(event) => setTpm(event.target.value)}
                />
              </Field>
              <button
                type="button"
                className="btn primary"
                disabled={busy || !limitTenant}
                onClick={() =>
                  void act(() =>
                    admin.put(`limits/tenant/${limitTenant}`, {
                      requests_per_min: rpm.trim() ? Number(rpm) : null,
                      tokens_per_min: tpm.trim() ? Number(tpm) : null,
                    }),
                  )
                }
              >
                Apply
              </button>
            </Toolbar>
          </Card>
        </div>

        <Card
          title="Budgets"
          note="settled + reserved is what the gate compares against the cap — never settled alone"
          refreshing={poll.refreshing}
        >
          {budgets.length === 0 ? (
            <Empty>
              No tenant is capped. Every request is admitted on the budget&rsquo;s account.
            </Empty>
          ) : (
            <div
              className="grid"
              // `auto-fill` rather than `auto-fit`: one budget should be a card, not a
              // card stretched across the whole page.
              style={{ gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))" }}
            >
              {budgets.map((budget) => (
                <BudgetStrip
                  key={budget.scope}
                  budget={budget}
                  name={names.get(budget.scope_id) ?? budget.scope_id}
                  busy={busy}
                  onClear={() => void act(() => admin.del(`budgets/${budget.scope_id}`))}
                />
              ))}
            </div>
          )}
        </Card>

        <Card
          title="Rate limits"
          note="the configured limit from Postgres, the live bucket from DynamoDB, joined"
          refreshing={poll.refreshing}
        >
          {limits.length === 0 ? (
            <Empty>
              Nobody is limited. An unconfigured scope is skipped entirely rather than
              treated as a limit of infinity, so this path does no work at all.
            </Empty>
          ) : (
            <TableWrap>
              <table className="data">
                <thead>
                  <tr>
                    <th>Scope</th>
                    <th>Name</th>
                    <th>Dimension</th>
                    <th className="right">Limit / min</th>
                    <th className="right">Available</th>
                    <th style={{ width: 220 }}>Bucket</th>
                    <th className="right">Full again in</th>
                    <th className="right">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {limits.flatMap((limit) =>
                    (limit.buckets.length > 0
                      ? limit.buckets
                      : [null]
                    ).map((bucket, index) => (
                      <tr key={`${limit.scope}-${bucket?.dimension ?? index}`}>
                        <td>
                          <Badge tone={limit.scope_kind === "tenant" ? "accent" : "neutral"}>
                            {limit.scope_kind}
                          </Badge>
                        </td>
                        <td className="strong">{limit.name}</td>
                        <td>{bucket?.dimension ?? "—"}</td>
                        <td className="right num">{bucket ? count(bucket.limit_per_min) : "—"}</td>
                        <td className="right num strong">
                          {bucket ? count(bucket.available) : "—"}
                        </td>
                        <td>
                          {bucket && (
                            <BucketMeter
                              available={bucket.available}
                              limit={bucket.limit_per_min}
                              ariaLabel={`${limit.name} ${bucket.dimension}: ${bucket.available} of ${bucket.limit_per_min} available`}
                            />
                          )}
                        </td>
                        <td className="right num">
                          {bucket ? seconds(bucket.reset_after_s) : "—"}
                        </td>
                        <td className="right">
                          {index === 0 && (
                            <button
                              type="button"
                              className="btn small danger"
                              disabled={busy}
                              onClick={() =>
                                void act(() =>
                                  admin.del(`limits/${limit.scope_kind}/${limit.scope_id}`),
                                )
                              }
                            >
                              Clear
                            </button>
                          )}
                        </td>
                      </tr>
                    )),
                  )}
                </tbody>
              </table>
            </TableWrap>
          )}
        </Card>
      </div>
    </>
  );
}

function BudgetStrip({
  budget,
  name,
  busy,
  onClear,
}: {
  budget: Budget;
  name: string;
  busy: boolean;
  onClear: () => void;
}) {
  const cap = toPicos(budget.usd);
  const spent = toPicos(budget.spent);
  const reserved = toPicos(budget.reserved);
  const committed = toPicos(budget.committed);
  const fraction = cap > 0n ? Number(committed) / Number(cap) : 0;
  const tone = fraction >= 1 ? "critical" : fraction >= 0.8 ? "warning" : "good";

  return (
    <section className="card" style={{ background: "var(--surface-2)" }}>
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 14 }}>
        <div>
          <div className="strong">{name}</div>
          <div className="card-note">
            {budget.window} · {budget.window_id}
          </div>
        </div>
        <Badge tone={tone}>
          {fraction >= 1 ? "at the cap" : `${percent(Number(committed), Number(cap), 0)} used`}
        </Badge>
      </div>

      <ChannelStrip
        segments={[
          { key: "spent", label: "settled", value: Number(spent), colour: "var(--series-1)" },
          {
            key: "reserved",
            label: "reserved",
            value: Number(reserved),
            colour: "var(--series-4)",
          },
        ]}
        capLabel={moneyFromPicos(cap - committed > 0n ? cap - committed : 0n)}
        fraction={fraction}
        ariaLabel={`${name}: ${budget.committed} committed of ${budget.usd}`}
      />

      <dl className="kv" style={{ marginTop: 14, gridTemplateColumns: "94px 1fr", fontSize: 12 }}>
        <dt>Cap</dt>
        <dd>
          <Num>{money(budget.usd)}</Num>
        </dd>
        <dt>Settled</dt>
        <dd>
          <Num>{money(budget.spent)}</Num>
        </dd>
        <dt>Reserved</dt>
        <dd>
          <Num>{money(budget.reserved)}</Num>{" "}
          <span className="muted">
            {budget.reservations} hold{budget.reservations === 1 ? "" : "s"} in flight
          </span>
        </dd>
        <dt>Committed</dt>
        <dd className="strong">
          <Num>{money(budget.committed)}</Num>
        </dd>
        {budget.expired_releases > 0 && (
          <>
            <dt>Stranded</dt>
            <dd style={{ color: "var(--warning)" }}>
              <Num>{budget.expired_releases}</Num> hold
              {budget.expired_releases === 1 ? "" : "s"} released ·{" "}
              <Num>{money(budget.expired_released)}</Num>
            </dd>
          </>
        )}
      </dl>

      <div className="row" style={{ marginTop: 12, justifyContent: "flex-end" }}>
        <button type="button" className="btn small danger" disabled={busy} onClick={onClear}>
          Remove cap
        </button>
      </div>
    </section>
  );
}
