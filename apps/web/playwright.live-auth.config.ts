import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "@playwright/test";

/**
 * `pnpm e2e:live-auth`: real-project sign-in through the deployed Space (WebKit, iPad portrait), run by the
 * lead from the sandbox after every deploy. Needs E2E_BASE_URL, HF_TOKEN + HF_SPACE_ID (private Space),
 * SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY and ADMIN_EMAILS. No traces, videos or reports: they could hold
 * tokens or cookies. Requests with secrets are made in Node (e2e-live/net.ts), never by Playwright, and the global
 * teardown scans test-results for every secret's value afterwards.
 */
const here = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(here, "../..");
const baseURL = process.env.E2E_BASE_URL?.replace(/\/$/, "");
if (!baseURL) throw new Error("E2E_BASE_URL must point at the deployed Space");

export default defineConfig({
  testDir: "./e2e-live",
  globalTeardown: "./e2e-live/global-teardown.ts",
  outputDir: path.join(repoRoot, "test-results/playwright-live-auth"),
  workers: 1,
  retries: 0,
  timeout: 120_000,
  expect: { timeout: 20_000 },
  reporter: "list",
  use: {
    baseURL,
    browserName: "webkit",
    viewport: { width: 820, height: 1180 },
    hasTouch: true,
    isMobile: true,
    trace: "off",
    screenshot: "off",
    video: "off",
  },
});
