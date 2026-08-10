"use client";

import { useState } from "react";
import Link from "next/link";
import { PageHead, PollBadge } from "@/components/shell";
import {
  Badge,
  Card,
  Empty,
  ErrorNote,
  Field,
  Num,
  TableWrap,
  Toolbar,
} from "@/components/ui";
import { admin, type Key, type KeyCreated, type Tenant } from "@/lib/api";
import { stamp } from "@/lib/format";
import { SLOW_INTERVAL_MS, usePoll } from "@/lib/poll";

/**
 * Tenants and virtual keys — the view `migrations/0001`'s comment promised by name.
 *
 * Two things about this page are the whole of Phase 2 made visible rather than described.
 *
 * **Nothing is deleted.** "Revoke" and "deactivate" are what the buttons say, because
 * that is what the API does: `DELETE /admin/keys/{id}` sets a timestamp and returns the
 * object in its new state (H-022). Every ledger row ever written points at these ids
 * forever, so a row that vanished would turn a historical invoice into an orphan — and a
 * console whose button said "Delete" would be lying about a design decision the schema
 * enforces.
 *
 * **A minted key is shown exactly once.** The plaintext exists in one response and is
 * never recoverable (H-017), so the panel below says so in as many words and offers to
 * copy it. Closing that panel is the last time that value exists anywhere.
 */
export default function TenantsPage() {
  const [minted, setMinted] = useState<KeyCreated | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [newTenant, setNewTenant] = useState("");
  const [keyTenant, setKeyTenant] = useState("");
  const [keyName, setKeyName] = useState("");
  const [keyModels, setKeyModels] = useState("");

  const poll = usePoll(
    async () => {
      const [tenants, keys] = await Promise.all([
        admin.get<Tenant[]>("tenants"),
        admin.get<Key[]>("keys"),
      ]);
      return { tenants, keys };
    },
    SLOW_INTERVAL_MS,
    [],
  );

  const tenants = poll.data?.tenants ?? [];
  const keys = poll.data?.keys ?? [];

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
        title="Tenants & keys"
        sub="The control plane. Keys are revoked and tenants deactivated — never deleted, because the ledger points at both forever."
      >
        <PollBadge intervalMs={SLOW_INTERVAL_MS} refreshing={poll.refreshing} />
      </PageHead>

      {poll.error && <ErrorNote error={poll.error} />}
      {problem && <ErrorNote error={new Error(problem)} />}

      {minted && (
        <div className="notice" style={{ marginBottom: 14, borderColor: "var(--accent)" }}>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <strong>
              This is the only time {minted.name} will ever be shown. It was never stored and
              cannot be recomputed.
            </strong>
            <button type="button" className="btn small" onClick={() => setMinted(null)}>
              Done
            </button>
          </div>
          <div
            className="num"
            style={{
              marginTop: 10,
              padding: "8px 10px",
              background: "var(--surface-2)",
              borderRadius: 6,
              overflowWrap: "anywhere",
            }}
          >
            {minted.key}
          </div>
        </div>
      )}

      <div className="stack">
        <div className="grid cols-2">
          <Card title="New tenant" note="the name must be unique — a second “acme” is a spend-attribution bug waiting for a quarter to end">
            <Toolbar>
              <Field label="Name" style={{ flex: 1 }}>
                <input
                  className="control"
                  value={newTenant}
                  placeholder="acme"
                  onChange={(event) => setNewTenant(event.target.value)}
                />
              </Field>
              <button
                type="button"
                className="btn primary"
                disabled={busy || newTenant.trim() === ""}
                onClick={() =>
                  void act(async () => {
                    await admin.post<Tenant>("tenants", { name: newTenant.trim() });
                    setNewTenant("");
                  })
                }
              >
                Create
              </button>
            </Toolbar>
          </Card>

          <Card title="Mint a key" note="scope it to the models it should reach; empty is unrestricted">
            <Toolbar>
              <Field label="Tenant" style={{ minWidth: 150 }}>
                <select
                  className="control"
                  value={keyTenant}
                  onChange={(event) => setKeyTenant(event.target.value)}
                >
                  <option value="">choose…</option>
                  {tenants
                    .filter((tenant) => tenant.active)
                    .map((tenant) => (
                      <option key={tenant.id} value={tenant.id}>
                        {tenant.name}
                      </option>
                    ))}
                </select>
              </Field>
              <Field label="Name" style={{ minWidth: 130 }}>
                <input
                  className="control"
                  value={keyName}
                  placeholder="laptop"
                  onChange={(event) => setKeyName(event.target.value)}
                />
              </Field>
              <Field label="Allowed models" style={{ minWidth: 150 }}>
                <input
                  className="control"
                  value={keyModels}
                  placeholder="mock-*, claude-*"
                  onChange={(event) => setKeyModels(event.target.value)}
                />
              </Field>
              <button
                type="button"
                className="btn primary"
                disabled={busy || !keyTenant || keyName.trim() === ""}
                onClick={() =>
                  void act(async () => {
                    const created = await admin.post<KeyCreated>("keys", {
                      tenant_id: keyTenant,
                      name: keyName.trim(),
                      allowed_models: keyModels
                        .split(",")
                        .map((entry) => entry.trim())
                        .filter(Boolean),
                    });
                    setMinted(created);
                    setKeyName("");
                    setKeyModels("");
                  })
                }
              >
                Mint
              </button>
            </Toolbar>
          </Card>
        </div>

        <Card title="Tenants" refreshing={poll.refreshing}>
          {tenants.length === 0 ? (
            <Empty>No tenants yet.</Empty>
          ) : (
            <TableWrap>
              <table className="data">
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Id</th>
                    <th>State</th>
                    <th className="right">Keys</th>
                    <th>Created</th>
                    <th className="right">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {tenants.map((tenant) => {
                    const owned = keys.filter((key) => key.tenant_id === tenant.id);
                    return (
                      <tr key={tenant.id}>
                        <td className="strong">
                          <Link href={`/requests?tenant_id=${tenant.id}`}>{tenant.name}</Link>
                        </td>
                        <td className="num muted">{tenant.id}</td>
                        <td>
                          <Badge tone={tenant.active ? "good" : "neutral"}>
                            {tenant.active ? "active" : "inactive"}
                          </Badge>
                        </td>
                        <td className="right num">
                          {owned.filter((key) => key.status === "active").length}/{owned.length}
                        </td>
                        <td className="num muted">{stamp(tenant.created_at)}</td>
                        <td className="right">
                          <button
                            type="button"
                            className={tenant.active ? "btn small danger" : "btn small"}
                            disabled={busy}
                            onClick={() =>
                              void act(() =>
                                tenant.active
                                  ? admin.del(`tenants/${tenant.id}`)
                                  : admin.patch(`tenants/${tenant.id}`, { active: true }),
                              )
                            }
                          >
                            {tenant.active ? "Deactivate" : "Reactivate"}
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </TableWrap>
          )}
        </Card>

        <Card
          title="Virtual keys"
          note="the stored prefix is `hk_` plus eight characters — enough to recognise, ~208 bits short of enough to use"
          refreshing={poll.refreshing}
        >
          {keys.length === 0 ? (
            <Empty>No keys yet.</Empty>
          ) : (
            <TableWrap>
              <table className="data">
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Prefix</th>
                    <th>Tenant</th>
                    <th>Models</th>
                    <th>Providers</th>
                    <th>State</th>
                    <th>Created</th>
                    <th className="right">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {keys.map((key) => (
                    <tr key={key.id}>
                      <td className="strong">{key.name}</td>
                      <td className="num">{key.key_prefix}…</td>
                      <td>{tenants.find((tenant) => tenant.id === key.tenant_id)?.name ?? "—"}</td>
                      <td className="muted">
                        {key.allowed_models.length ? key.allowed_models.join(", ") : "unrestricted"}
                      </td>
                      <td className="muted">
                        {key.allowed_providers.length
                          ? key.allowed_providers.join(", ")
                          : "unrestricted"}
                      </td>
                      <td>
                        <Badge tone={key.status === "active" ? "good" : "neutral"}>
                          {key.status}
                        </Badge>
                      </td>
                      <td className="num muted">{stamp(key.created_at)}</td>
                      <td className="right">
                        {key.status === "active" ? (
                          <button
                            type="button"
                            className="btn small danger"
                            disabled={busy}
                            onClick={() => void act(() => admin.del(`keys/${key.id}`))}
                          >
                            Revoke
                          </button>
                        ) : (
                          <span className="muted num">
                            {key.revoked_at ? stamp(key.revoked_at) : "—"}
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableWrap>
          )}
        </Card>

        <p className="card-note">
          A revoked key is dead on the very next request in the process that revoked it, and
          within <Num>5s</Num> everywhere else — the auth cache&rsquo;s documented TTL, which is a
          cross-process bound rather than a delay you are waiting out here.
        </p>
      </div>
    </>
  );
}
