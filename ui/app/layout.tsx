import type { Metadata } from "next";
import { Shell } from "@/components/shell";
import { SignIn } from "@/components/signin";
import { sessionToken } from "@/lib/session";
import "./globals.css";

export const metadata: Metadata = {
  title: "Headroom — control plane",
  description: "Usage, budgets, limits, cache, and provider health for the Headroom gateway.",
};

/**
 * The gate. **No page can render without a session**, because the check is here rather
 * than in each view: a route added by a later phase is behind it by construction.
 *
 * Reading the cookie makes this layout dynamic, which is correct — there is nothing in
 * this console worth caching, and a statically prerendered admin shell would be a shell
 * that had already decided somebody was signed in.
 */
export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const token = await sessionToken();
  return (
    <html lang="en">
      <body>{token ? <Shell>{children}</Shell> : <SignIn />}</body>
    </html>
  );
}
