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

const projects: PlaywrightTestConfig["projects"] = BROWSERS.flatMap((browserName) =>
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

export default defineConfig({
  testDir: "./e2e",
  outputDir: path.join(repoRoot, "test-results/playwright"),
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
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
  webServer: liveBaseUrl
    ? undefined
    : {
        command: `uv --directory ${path.join(repoRoot, "apps/api")} run uvicorn naadi_api.main:app --host 127.0.0.1 --port ${port} --no-access-log`,
        url: `${baseURL}/`,
        reuseExistingServer: !process.env.CI,
        timeout: 120_000,
        stdout: "ignore",
        stderr: "pipe",
        env: {
          NAADI_STATIC_DIR: path.join(here, "dist"),
          APP_ENV: "local",
          APP_VERSION: process.env.APP_VERSION ?? "e2e",
        },
      },
});
