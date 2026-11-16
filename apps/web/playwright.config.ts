import { randomBytes } from "node:crypto";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig, type PlaywrightTestConfig } from "@playwright/test";

const here = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(here, "../..");

/**
 * Local / CI: builds are served by uvicorn from apps/web/dist on :7860 (same as production).
 * Live: E2E_BASE_URL=https://<ns>-naadi.hf.space; a private Space needs HF_TOKEN, sent as a Bearer header.
 */
const liveBaseUrl = process.env.E2E_BASE_URL?.replace(/\/$/, "");

// Auth test users are named per run (workers inherit the id), so parallel CI jobs never collide. Locally the
// id is fixed, so a reused dev server (reuseExistingServer) still has the matching ADMIN_EMAILS.
process.env.E2E_RUN_ID ??= process.env.GITHUB_RUN_ID
  ? `${process.env.GITHUB_RUN_ID}-${process.env.GITHUB_RUN_ATTEMPT ?? "1"}-${randomBytes(3).toString("hex")}`
  : "local";
const port = Number(process.env.E2E_PORT ?? 7860);
const baseURL = liveBaseUrl ?? `http://127.0.0.1:${port}`;

function authHeaders(): Record<string, string> | undefined {
  if (!liveBaseUrl) return undefined;
  const host = new URL(liveBaseUrl).hostname;
  if (!host.endsWith(".hf.space")) return undefined;
  const token = process.env.HF_TOKEN;
  if (!token) throw new Error("E2E_BASE_URL points at a Hugging Face Space but HF_TOKEN is not set");
  return { Authorization: `Bearer ${token}` };
}

const VIEWPORTS = [
  { name: "ipad-portrait", width: 820, height: 1180, touch: true },
  { name: "ipad-landscape", width: 1180, height: 820, touch: true },
  { name: "phone-390", width: 390, height: 844, touch: true },
  { name: "phone-412", width: 412, height: 915, touch: true },
  { name: "laptop-1440", width: 1440, height: 900, touch: false },
  { name: "desktop-1920", width: 1920, height: 1080, touch: false },
] as const;

const BROWSERS = ["webkit", "chromium", "firefox"] as const;

const projects: NonNullable<PlaywrightTestConfig["projects"]> = BROWSERS.flatMap((browserName) =>
  VIEWPORTS.map((v) => ({
    name: `${browserName}-${v.name}`,
    use: {
      browserName,
      viewport: { width: v.width, height: v.height },
      hasTouch: v.touch,
      // Firefox has no mobile emulation; WebKit and Chromium emulate the mobile viewport + touch.
      isMobile: v.touch && browserName !== "firefox",
      deviceScaleFactor: 1,
    },
  })),
);

// The real iPad renders at device pixel ratio 2: ink canvases hold 4x the pixels, which the canvas memory
// budget and the ink scroll test must hold up to.
projects.push({
  name: "webkit-ipad-portrait-dpr2",
  testMatch: /ink.*\.spec\.ts/,
  use: {
    browserName: "webkit",
    viewport: { width: 820, height: 1180 },
    hasTouch: true,
    isMobile: true,
    deviceScaleFactor: 2,
  },
});

/** Port of the second API server, whose LLM budget is forced to zero (the degradation path, segment 04). */
const BUDGET_ZERO_PORT = port + 1;
/**
 * Port of the third API server, in APP_ENV=prod (same build and database), for production-only behaviour
 * such as the ink lab being admin-only (ink-gate.spec). Specs find it through E2E_PROD_URL.
 */
const PROD_PORT = port + 2;
if (!liveBaseUrl) process.env.E2E_PROD_URL = `http://127.0.0.1:${PROD_PORT}`;

/**
 * With a database the API under test is the production entrypoint: Aegra hosting the Naadi app
 * (naadi_api.agent.host:app, after migrate_all), so agent pages run against the real runtime. Without one
 * (a quick local run) it is the plain Naadi app, and the agent specs skip themselves.
 */
function webServers(): PlaywrightTestConfig["webServer"] {
  const api = path.join(repoRoot, "apps/api");
  const dbUrl = process.env.E2E_DATABASE_URL ?? process.env.DATABASE_URL;
  const env = (extra: Record<string, string> = {}) => ({
    NAADI_STATIC_DIR: path.join(here, "dist"),
    APP_ENV: "local",
    // Aegra reads DEBUG as a boolean; Playwright's own DEBUG=pw:* must not reach the API process.
    DEBUG: "false",
    APP_VERSION: process.env.APP_VERSION ?? "e2e",
    // The local API is checked against throwaway test accounts, never the owner's address.
    ADMIN_EMAILS: `naadi-e2e-admin-${process.env.E2E_RUN_ID}@example.com`,
    ...(dbUrl ? { DATABASE_URL: dbUrl } : {}),
    ...extra,
  });
  const serve = (p: number, app: string) =>
    `uv --directory ${api} run uvicorn ${app} --host 127.0.0.1 --port ${p} --workers 1 --no-access-log`;
  const common = { reuseExistingServer: !process.env.CI, stdout: "ignore" as const, stderr: "pipe" as const };
  if (!dbUrl) {
    return [
      {
        ...common,
        command: `uv --directory ${api} run python -m naadi_api.migrate --if-configured && ${serve(port, "naadi_api.main:app")}`,
        url: `${baseURL}/`,
        timeout: 120_000,
        env: env(),
      },
      {
        ...common,
        command: serve(PROD_PORT, "naadi_api.main:app"),
        url: `http://127.0.0.1:${PROD_PORT}/api/public-config`,
        timeout: 120_000,
        env: env({ APP_ENV: "prod" }),
      },
    ];
  }
  const migrate = `uv --directory ${api} run python -m naadi_api.agent.migrate_all`;
  return [
    {
      ...common,
      command: `${migrate} && ${serve(port, "naadi_api.agent.host:app")}`,
      url: `${baseURL}/`,
      timeout: 180_000,
      env: env(),
    },
    {
      ...common,
      // Same database and code; OPENROUTER_DAILY_CAP=0 and no NVIDIA key, so every model call is refused by
      // the budget guard (BudgetExhausted), never by a missing provider. Aegra claims each cron firing, so two schedulers never fire one twice.
      command: `${migrate} && ${serve(BUDGET_ZERO_PORT, "naadi_api.agent.host:app")}`,
      url: `http://127.0.0.1:${BUDGET_ZERO_PORT}/`,
      timeout: 180_000,
      env: env({ OPENROUTER_DAILY_CAP: "0", NVIDIA_API_KEY: "" }),
    },
    {
      ...common,
      // Same database and code in production mode (the migrations above have run or are idempotent).
      command: `${migrate} && ${serve(PROD_PORT, "naadi_api.agent.host:app")}`,
      url: `http://127.0.0.1:${PROD_PORT}/api/public-config`,
      timeout: 180_000,
      env: env({ APP_ENV: "prod" }),
    },
  ];
}

export default defineConfig({
  testDir: "./e2e",
  // Real Supabase Auth: test users are created and signed in once per run, and deleted afterwards.
  globalSetup: "./e2e/global-setup.ts",
  globalTeardown: "./e2e/global-teardown.ts",
  outputDir: path.join(repoRoot, "test-results/playwright"),
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  // One retry in CI only, for browser-process crashes on shared runners; Playwright still reports such tests as flaky.
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 2 : 4,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  reporter: process.env.CI
    ? [["list"], ["html", { open: "never", outputFolder: path.join(repoRoot, "playwright-report") }]]
    : "list",
  use: {
    baseURL,
    extraHTTPHeaders: authHeaders(),
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects,
  webServer: liveBaseUrl ? undefined : webServers(),
});
