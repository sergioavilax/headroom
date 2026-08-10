import { defineConfig, devices } from "@playwright/test";

/**
 * The browser smoke BUILD_PLAN §P7's gate asks for, kept hermetic and kept short.
 *
 * Two servers, both started here: a stub of the gateway's `/admin/*` surface
 * (`tests/stub/gateway.mjs`) and the console built against it. Nothing else — no compose
 * stack, no Postgres, no DynamoDB, no provider, no key. Chromium only, because this job
 * exists to prove the console renders and its session works, and three engines would
 * triple a CI job to answer a question nobody asked.
 */
export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? "list" : "html",
  use: {
    baseURL: "http://127.0.0.1:3101",
    trace: "on-first-retry",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      // Waited on at `/healthz`, not at an admin path: the stub answers 401 to an
      // unauthenticated `/admin/*` call, which is the point of it.
      command: "node tests/stub/gateway.mjs",
      url: "http://127.0.0.1:8099/healthz",
      reuseExistingServer: !process.env.CI,
    },
    {
      // The **standalone** server, not `next start`: that is the one the shipped image
      // runs, and a smoke against a different server than the one that deploys is a
      // smoke that can pass while the artifact is broken.
      command: "npm run e2e:server",
      url: "http://127.0.0.1:3101/api/healthz",
      reuseExistingServer: !process.env.CI,
      env: {
        HEADROOM_GATEWAY_URL: "http://127.0.0.1:8099",
        PORT: "3101",
        HOSTNAME: "127.0.0.1",
      },
    },
  ],
});
