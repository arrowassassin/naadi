import { execFileSync } from "node:child_process";
import { createHash, randomBytes, X509Certificate } from "node:crypto";
import { existsSync, mkdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig, type PlaywrightTestConfig } from "@playwright/test";
import { e2eTargets } from "./e2e/databaseUrl";

const here = path.dirname(fileURLToPath(import.meta.url));

// The local storage-api trusts its own key, not the auth server's: with a storage URL and no key given, mint the
// same one the stack's tests use (a bare run of the RUNBOOK's stack would otherwise end every export as "failed").
function localStorageKey(): string | undefined {
  if (process.env.NAADI_STORAGE_SERVICE_KEY) return process.env.NAADI_STORAGE_SERVICE_KEY;
  if (!process.env.NAADI_STORAGE_URL) return undefined;
  try {
    const out = execFileSync(
      "uv",
      [
        "--directory",
        path.join(here, "../api"),
        "run",
        "--frozen",
        "python",
        "-c",
        "from tests.db.storage import local_service_key; print(local_service_key())",
      ],
      { encoding: "utf8", timeout: 60_000, stdio: ["ignore", "pipe", "ignore"] },
    );
    return out.trim().split("\n").pop() || undefined;
  } catch {
    return undefined;
  }
}
const storageServiceKey = localStorageKey();
const repoRoot = path.resolve(here, "../..");

/**
 * Local / CI: builds are served by uvicorn from apps/web/dist on :7860 (same as production).
 * Live: E2E_BASE_URL=https://<ns>-naadi.hf.space targets the public smoke. Private Space
 * authentication is handled by the dedicated playwright.live-auth.config.ts.
 */
const liveBaseUrl = process.env.E2E_BASE_URL?.replace(/\/$/, "");

// Auth test users are named per run (workers inherit the id), so parallel CI jobs never collide. Locally the
// id is fixed, so a reused dev server (reuseExistingServer) still has the matching ADMIN_EMAILS.
process.env.E2E_RUN_ID ??= process.env.GITHUB_RUN_ID
  ? `${process.env.GITHUB_RUN_ID}-${process.env.GITHUB_RUN_ATTEMPT ?? "1"}-${randomBytes(3).toString("hex")}`
  : "local";
const port = Number(process.env.E2E_PORT ?? 7860);
const baseURL = liveBaseUrl ?? `http://127.0.0.1:${port}`;

/**
 * Local / CI: list + HTML report (with traces). Live: list + a JUnit summary only; the HTML report
 * embeds traces and attachments, so it is never written for a live run.
 */
function reporters(): PlaywrightTestConfig["reporter"] {
  if (liveBaseUrl) {
    return [["list"], ["junit", { outputFile: path.join(repoRoot, "test-results/live-smoke/junit.xml") }]];
  }
  if (process.env.CI) {
    return [["list"], ["html", { open: "never", outputFolder: path.join(repoRoot, "playwright-report") }]];
  }
  return "list";
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
    // Flows that must run exactly once per run (they revoke sessions or read the mail catcher) have their own
    // project below, so they never show up as skips in the matrix.
    testIgnore: /auth-once\.spec\.ts/,
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

// Once per run, in the primary target (WebKit, iPad portrait): the real magic-link email, global sign-out
// across two devices and a full sign-in, reload, sign-out round trip. Only where throwaway users and the
// local mail catcher exist (local stack, CI); never against the live Space.
if (!liveBaseUrl && process.env.MAILPIT_URL && process.env.SUPABASE_SERVICE_ROLE_KEY) {
  projects.push({
    name: "webkit-auth-once",
    testMatch: /auth-once\.spec\.ts/,
    use: { browserName: "webkit", viewport: { width: 820, height: 1180 }, hasTouch: true, isMobile: true },
  });
}

/** Port of the second API server, whose LLM budget is forced to zero (the degradation path, segment 04). */
const BUDGET_ZERO_PORT = port + 1;
/**
 * Port of the third API server, in APP_ENV=prod (same build and database), for production-only behaviour
 * such as the ink lab being admin-only (ink-gate.spec). Specs find it through E2E_PROD_URL.
 */
const PROD_PORT = port + 2;
/** Port of the scripted fake LLM the main server's tutor talks to (S14, tests/e2e_fake_llm.py). */
const FAKE_LLM_PORT = port + 3;
/**
 * The prod-mode API is served over https with a throwaway self-signed certificate for 127.0.0.1: production's
 * CSP carries upgrade-insecure-requests, which WebKit applies to http loopback too, so an http prod server
 * could not load its own assets in WebKit. The certificate is made here, when the config loads (Playwright
 * starts web servers before global setup), and only test contexts that opt in ignore its HTTPS errors.
 */
const TLS_DIR = path.join(repoRoot, "test-results/.tls");
const TLS_KEY = path.join(TLS_DIR, "loopback-key.pem");
const TLS_CERT = path.join(TLS_DIR, "loopback-cert.pem");
function ensureLoopbackCert(): void {
  if (existsSync(TLS_KEY) && existsSync(TLS_CERT)) return;
  mkdirSync(TLS_DIR, { recursive: true });
  execFileSync(
    "openssl",
    ["req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-subj", "/CN=127.0.0.1"].concat([
      "-addext",
      "subjectAltName=IP:127.0.0.1,DNS:localhost",
      "-keyout",
      TLS_KEY,
      "-out",
      TLS_CERT,
    ]),
    { stdio: "ignore" },
  );
}
/**
 * Local runs only: decide the database and Supabase project now, at config load, before Playwright starts
 * any webServer (they migrate the database). A hosted DATABASE_URL or SUPABASE_URL throws here unless it
 * came as E2E_DATABASE_URL / E2E_SUPABASE_URL or E2E_ALLOW_HOSTED=1 is set (see e2e/databaseUrl.ts).
 * The vetted values are written back, so the servers, the test-user helpers and the workers all see them.
 */
const targets = liveBaseUrl ? null : e2eTargets(process.env);
if (targets) {
  process.env.DATABASE_URL = targets.databaseUrl;
  process.env.E2E_DATABASE_URL = targets.databaseUrl;
  process.env.SUPABASE_URL = targets.supabaseUrl;
  process.env.VITE_SUPABASE_URL = targets.supabaseUrl;
}
if (!liveBaseUrl) {
  ensureLoopbackCert();
  process.env.E2E_PROD_URL = `https://127.0.0.1:${PROD_PORT}`;
  // Chromium: trust exactly this throwaway certificate (its SPKI hash). With only the per-context
  // ignoreHTTPSErrors, Chromium asks the context again for every new connection, and a credential-less
  // (crossorigin) one such as the stylesheet's could loop on that certificate decision until it failed with
  // ERR_TOO_MANY_RETRIES (seen in the net log, segment 22). The flag names this one key, so every other
  // certificate is still checked.
  const spki = createHash("sha256")
    .update(new X509Certificate(readFileSync(TLS_CERT)).publicKey.export({ type: "spki", format: "der" }))
    .digest("base64");
  for (const p of projects) {
    if (p.use?.browserName !== "chromium") continue;
    const args = p.use.launchOptions?.args ?? [];
    p.use.launchOptions = { ...p.use.launchOptions, args: [...args, `--ignore-certificate-errors-spki-list=${spki}`] };
  }
}

/**
 * With a database the API under test is the production entrypoint: Aegra hosting the Naadi app
 * (naadi_api.agent.host:app, after migrate_all), so agent pages run against the real runtime. Without one
 * (a quick local run) it is the plain Naadi app, and the agent specs skip themselves.
 */
function webServers(): PlaywrightTestConfig["webServer"] {
  const api = path.join(repoRoot, "apps/api");
  const dbUrl = targets?.databaseUrl ?? "";
  const env = (extra: Record<string, string> = {}) => ({
    NAADI_STATIC_DIR: path.join(here, "dist"),
    APP_ENV: "local",
    // Links the API builds for itself (the calendar feed URL) point at the server under test, never at an ambient
    // APP_BASE_URL (in an agent sandbox that is the hosted Space).
    APP_BASE_URL: "",
    // Aegra reads DEBUG as a boolean; Playwright's own DEBUG=pw:* must not reach the API process.
    DEBUG: "false",
    // Generation runs against the in-process fake (no Space call, in-memory media served by the app itself). The
    // production-mode server ignores this and reports `not configured`, like a deploy without HF_GEN_TOKEN.
    GENERATION_FAKE: "1",
    // Question authoring (segment 21) answers its model calls from a scripted fake (never in production).
    AUTHORING_FAKE: "1",
    // Export-all talks to the local storage-api (its own service key) when the e2e stack provides one.
    ...(process.env.NAADI_STORAGE_URL ? { NAADI_STORAGE_URL: process.env.NAADI_STORAGE_URL } : {}),
    ...(storageServiceKey ? { NAADI_STORAGE_SERVICE_KEY: storageServiceKey } : {}),
    HF_GEN_TOKEN: "",
    APP_VERSION: process.env.APP_VERSION ?? "e2e",
    // The local API is checked against throwaway test accounts, never the owner's address.
    ADMIN_EMAILS: ["admin", "adminb", "adminc"]
      .map((r) => `naadi-e2e-${r}-${process.env.E2E_RUN_ID}@example.com`)
      .join(","),
    // Always explicit (empty when there is none), so a .env file can never supply another database or project.
    DATABASE_URL: dbUrl,
    SUPABASE_URL: targets?.supabaseUrl ?? "",
    VITE_SUPABASE_URL: targets?.supabaseUrl ?? "",
    // No provider keys unless asked for (E2E_LIVE_LLM=1): the suite must not spend model or search quota,
    // and the agent runtime then records "unavailable" instead of calling a model.
    ...(process.env.E2E_LIVE_LLM === "1" ? {} : { NVIDIA_API_KEY: "", OPENROUTER_API_KEY: "", TAVILY_API_KEY: "" }),
    ...extra,
  });
  // E2E_SERVER_LOG_DIR: keep each API server's JSON log (one file per port) for diagnosing a failed run.
  const logTo = (p: number) =>
    process.env.E2E_SERVER_LOG_DIR ? ` >> ${path.join(process.env.E2E_SERVER_LOG_DIR, `api-${p}.log`)} 2>&1` : "";
  const tls = ` --ssl-keyfile ${TLS_KEY} --ssl-certfile ${TLS_CERT}`;
  const serve = (p: number, app: string, https = false) =>
    `uv --directory ${api} run uvicorn ${app} --host 127.0.0.1 --port ${p} --workers 1 --no-access-log${https ? tls : ""}${logTo(p)}`;
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
        command: serve(PROD_PORT, "naadi_api.main:app", true),
        url: `https://127.0.0.1:${PROD_PORT}/api/public-config`,
        ignoreHTTPSErrors: true,
        timeout: 120_000,
        env: env({ APP_ENV: "prod" }),
      },
    ];
  }
  const migrate = `uv --directory ${api} run python -m naadi_api.agent.migrate_all`;
  // S14: the main server's OpenRouter calls go to a scripted fake on loopback (tests/e2e_fake_llm.py), so the tutor
  // streams deterministic answers, tool calls and confirms with no provider key; anything that is not the tutor
  // gets 404 from it, exactly as with no provider. Skipped when E2E_LIVE_LLM=1 (real providers).
  const fakeLlm: Record<string, string> =
    process.env.E2E_LIVE_LLM === "1"
      ? {}
      : {
          OPENROUTER_API_KEY: "e2e-fake",
          NAADI_OPENROUTER_BASE_URL: `http://127.0.0.1:${FAKE_LLM_PORT}/api/v1`,
          // The fake costs nothing: the free-tier daily cap (50 until the one-time top-up) would otherwise be spent
          // after a few projects of the matrix. The budget-zero server keeps testing the cap itself.
          OPENROUTER_DAILY_CAP: "100000",
        };
  return [
    ...(process.env.E2E_LIVE_LLM === "1"
      ? []
      : [
          {
            ...common,
            command: `uv --directory ${api} run python tests/e2e_fake_llm.py --port ${FAKE_LLM_PORT}`,
            url: `http://127.0.0.1:${FAKE_LLM_PORT}/health`,
            timeout: 60_000,
            env: { DEBUG: "false" },
          },
        ]),
    {
      ...common,
      command: `${migrate} && ${serve(port, "naadi_api.agent.host:app")}`,
      url: `${baseURL}/`,
      timeout: 180_000,
      env: env(fakeLlm),
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
      command: `${migrate} && ${serve(PROD_PORT, "naadi_api.agent.host:app", true)}`,
      url: `https://127.0.0.1:${PROD_PORT}/api/public-config`,
      ignoreHTTPSErrors: true,
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
  retries: 0,
  workers: process.env.CI ? 2 : 4,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  reporter: reporters(),
  use: {
    baseURL,
    // Keep public live artifacts limited to screenshots and the JUnit summary.
    trace: liveBaseUrl ? "off" : "retain-on-failure",
    video: "off",
    screenshot: "only-on-failure",
    // The app registers /sw.js (push handlers). In WebKit a page that worker controls sends its fetches past
    // page.route, so a stubbed /api failure or delay would never reach the page and its error and loading
    // states could not be tested. Every context blocks service workers unless a test opts in with
    // `serviceWorkers: "allow"` (the push and installability flows).
    serviceWorkers: "block",
  },
  projects,
  webServer: liveBaseUrl ? undefined : webServers(),
});
