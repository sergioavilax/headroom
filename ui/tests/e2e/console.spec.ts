import { expect, test, type Page } from "@playwright/test";

/**
 * The browser smoke. Six checks, each one a property the console would be worthless
 * without — not a screenshot diff, and not a tour of every field.
 *
 * The one that matters most is the first: **an unauthenticated visitor sees a sign-in
 * screen and no data.** A console whose views rendered before a session existed would be
 * an unauthenticated tenant-and-key CRUD on any deployment that published its port, which
 * is the failure H-019 exists to prevent one layer down.
 */

const TOKEN = "stub-root-token";

async function signIn(page: Page) {
  await page.goto("/");
  await page.getByLabel("Root admin token").fill(TOKEN);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
}

test("an unauthenticated visitor is asked for the token and shown nothing else", async ({
  page,
}) => {
  await page.goto("/requests");

  await expect(page.getByRole("heading", { name: "Root admin token" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Sections" })).toHaveCount(0);
  await expect(page.getByText("backline")).toHaveCount(0);
});

test("a wrong token is refused and does not sign anybody in", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Root admin token").fill("not-the-token");
  await page.getByRole("button", { name: "Sign in" }).click();

  await expect(page.getByText("the gateway rejected that token")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Overview" })).toHaveCount(0);
});

test("the session cookie is httpOnly, so the page cannot read the token back", async ({
  page,
  context,
}) => {
  await signIn(page);

  const [session] = (await context.cookies()).filter(
    (cookie) => cookie.name === "headroom_admin_session",
  );
  expect(session?.httpOnly).toBe(true);
  expect(session?.sameSite).toBe("Strict");

  // The whole point, from the page's own side: `document.cookie` cannot see it, so an
  // injected script cannot exfiltrate the credential the session rides on.
  expect(await page.evaluate(() => document.cookie)).not.toContain(TOKEN);
});

test("the overview renders the numbers the admin API reported", async ({ page }) => {
  await signIn(page);

  // Spend is summed in picodollars across both tenants: 0.0000230 + 0 = $0.000023, and
  // the hit avoided $0.0000115. Both are rendered by the same formatter the unit tests
  // pin, so what this checks is the wiring: the API's strings reached the screen intact.
  await expect(page.getByText("$0.000023").first()).toBeVisible();
  await expect(page.getByText("$0.000012").first()).toBeVisible();
  await expect(page.getByRole("heading", { name: "Tenants" })).toBeVisible();
  await expect(page.getByRole("link", { name: "backline" })).toBeVisible();
  await expect(page.getByRole("link", { name: "atlas-research" })).toBeVisible();
});

test("the live view names the upstream that served each request, and the hop", async ({
  page,
}) => {
  await signIn(page);
  await page.getByRole("link", { name: "Live traffic" }).click();

  await expect(page.getByRole("heading", { name: "Live traffic" })).toBeVisible();
  await expect(page.getByRole("img", { name: /stacked by the provider/ })).toBeVisible();
  // The failover, legible at a glance: which provider was passed over, and why.
  await expect(page.getByText("← vllm_a · upstream_unavailable")).toBeVisible();
  // The breaker, in the same glance.
  await expect(page.getByText("open", { exact: true }).first()).toBeVisible();
});

test("a request's detail shows what it cost, what the budget held, and what failed over", async ({
  page,
}) => {
  await signIn(page);
  await page.getByRole("link", { name: "Requests", exact: true }).click();
  // The explorer shows a truncated id — enough to match a terminal against, never all of
  // it (`lib/format.ts`), so the locator matches the prefix the table actually renders.
  await page.getByRole("cell", { name: /hr_9a56791a/ }).click();

  const drawer = page.getByRole("complementary");
  await expect(drawer).toBeVisible();
  await expect(drawer.getByText("upstream_unavailable")).toBeVisible();
  await expect(drawer.getByText("vllm_b")).toBeVisible();
  // The rates the row was billed at, not today's — the D-017 guarantee, on screen.
  await expect(drawer.getByText(/\$0\.25 in · \$1\.25 out/)).toBeVisible();
});

test("the history view renders the days the rollup Lambda wrote", async ({ page }) => {
  await signIn(page);
  await page.getByRole("link", { name: "History" }).click();

  await expect(page.getByRole("heading", { name: "History" })).toBeVisible();
  // 0.004738 + 0.0103845 + 0.000851 + 0.001357 = $0.0173305, summed in picodollars across
  // four rollup rows and two tenants and rendered at the four places `moneyFromPicos`
  // gives an amount this size — the same arithmetic and the same formatter the Overview
  // uses over totals, on a different window and a different table.
  await expect(page.getByText("$0.0173").first()).toBeVisible();
  await expect(page.getByRole("img", { name: /Requests per day/ })).toBeVisible();
  // The tile the schedule is watched through: a rollup that stopped firing shows here as
  // an ageing stamp, which is the only thing on screen that can tell "no traffic that day"
  // from "nobody rolled that day up".
  await expect(page.getByText("Last rollup")).toBeVisible();
  await expect(page.getByText(/covering \d{4}-\d{2}-\d{2}/)).toBeVisible();
});

test("signing out clears the session", async ({ page, context }) => {
  await signIn(page);
  await page.getByRole("button", { name: "Sign out" }).click();

  await expect(page.getByRole("heading", { name: "Root admin token" })).toBeVisible();
  expect(
    (await context.cookies()).some((cookie) => cookie.name === "headroom_admin_session"),
  ).toBe(false);
});
