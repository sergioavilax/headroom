"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { MeterMark } from "@/components/shell";

/**
 * The token entry screen (H-055).
 *
 * The operator types the gateway's `HEADROOM_ADMIN_TOKEN` once; the server probes it,
 * and on success keeps it in an httpOnly cookie. Nothing about the token is stored in
 * this component beyond the keystroke — no `localStorage`, no `sessionStorage`, nothing
 * a later script could read back. `autoComplete="off"` and `type="password"` keep it out
 * of a browser's saved-password list and off a screen being recorded, which matters
 * because this screen is on camera during the kill demo.
 */
export function SignIn() {
  const router = useRouter();
  const [token, setToken] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const response = await fetch("/api/session", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ token }),
      });
      if (response.ok) {
        // `refresh`, not `push`: the session is read in the root *server* layout, so the
        // tree only changes shape when the server renders it again. A client-side
        // navigation would move the URL and leave the sign-in screen sitting there.
        router.refresh();
        return;
      }
      const body = (await response.json()) as { error?: { message?: string } };
      setError(body.error?.message ?? "sign-in failed");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="signin">
      <form className="signin-card" onSubmit={submit}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 22 }}>
          <MeterMark size={26} />
          <div>
            <div className="wordmark-name" style={{ fontSize: 14 }}>
              Headroom
            </div>
            <div className="wordmark-sub">control plane</div>
          </div>
        </div>

        <h1 style={{ fontSize: 17, margin: "0 0 6px", fontWeight: 600 }}>Root admin token</h1>
        <p style={{ margin: "0 0 18px", fontSize: 12.5, color: "var(--ink-3)", lineHeight: 1.55 }}>
          The same value the gateway was started with as{" "}
          <code className="num" style={{ color: "var(--ink-2)" }}>
            HEADROOM_ADMIN_TOKEN
          </code>
          . It is exchanged for a server-side session; the browser never holds it and no
          build of this console contains it.
        </p>

        <input
          className="control"
          style={{ width: "100%", fontFamily: "var(--mono)" }}
          type="password"
          value={token}
          onChange={(event) => setToken(event.target.value)}
          placeholder="…"
          autoComplete="off"
          spellCheck={false}
          aria-label="Root admin token"
          autoFocus
        />

        {error && (
          <div className="notice" style={{ marginTop: 14 }}>
            {error}
          </div>
        )}

        <button
          type="submit"
          className="btn primary"
          style={{ marginTop: 18, width: "100%", justifyContent: "center" }}
          disabled={busy || token.trim() === ""}
        >
          {busy ? "Checking…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
