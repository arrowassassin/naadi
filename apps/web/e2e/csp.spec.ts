/**
 * Production CSP, one test per main route (segment 22): each page is opened on the local APP_ENV=prod server
 * (E2E_PROD_URL, https, the real production headers) and must cause zero CSP violations and zero console
 * errors. Violations are read from the page's own `securitypolicyviolation` events, so a refused inline style
 * counts even where a browser prints no console line for it.
 *
 * Trusted Types ship report-only (security.trusted_types_report_only_policy): their reports are collected and
 * attached to the test (`trusted-types-reports`) for the enforcement step; they fail nothing yet. Enforced
 * violations always fail.
 */
import { readFileSync } from "node:fs";
import type { Browser, BrowserContext, Page } from "@playwright/test";
import { expect, isBenignRequestFailure, quiet, settle, TOOL_CSP_NOISE, test } from "./fixtures";
import { signedInState } from "./session";

interface Violation {
  directive: string;
  blocked: string;
  disposition: string;
  source: string;
}

const PROD = process.env.E2E_PROD_URL;

declare global {
  interface Window {
    __cspViolations?: Violation[];
  }
}

async function prodContext(
  browser: Browser,
  baseURL: string,
  role: "student" | "admin" | null,
): Promise<BrowserContext> {
  const origin = new URL(PROD as string).origin;
  let origins: { origin: string; localStorage: { name: string; value: string }[] }[] = [];
  if (role) {
    const file = await signedInState(browser, baseURL, role);
    const state = JSON.parse(readFileSync(file, "utf8")) as { origins: typeof origins };
    origins = state.origins.map((o) => ({ ...o, origin }));
  }
  const ctx = await browser.newContext({
    baseURL: PROD,
    ignoreHTTPSErrors: true,
    storageState: { cookies: [], origins },
  });
  // Runs before any page script, in every frame of the app's origin (the init script itself is not subject to CSP).
  await ctx.addInitScript(() => {
    window.__cspViolations = [];
    document.addEventListener("securitypolicyviolation", (e) => {
      window.__cspViolations?.push({
        directive: e.effectiveDirective,
        blocked: e.blockedURI,
        disposition: e.disposition,
        source: `${e.sourceFile}:${e.lineNumber}`,
      });
    });
  });
  return ctx;
}

function watch(page: Page): string[] {
  const errors: string[] = [];
  page.on("console", (m) => {
    if (m.type() !== "error") return;
    const text = m.text();
    // Report-only lines are collected from the violation events below, not judged as console errors.
    if (/\[Report Only\]|report-only|Report-Only/i.test(text)) return;
    if (text === TOOL_CSP_NOISE) return; // WebKit's screenshot <style>; no screenshots are taken here anyway
    errors.push(text);
  });
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  page.on("crash", () => errors.push("crash"));
  // Name the resource behind a bare "Failed to load resource" line (the console line has no URL in Chromium).
  page.on("requestfailed", (r) => {
    const why = r.failure()?.errorText ?? "failed";
    if (!isBenignRequestFailure(why)) errors.push(`request failed: ${why} ${r.url()}`);
  });
  return errors;
}

async function violations(page: Page): Promise<Violation[]> {
  return page.evaluate(() => window.__cspViolations ?? []);
}

interface RouteCase {
  name: string;
  path: string;
  role: "student" | "admin" | null;
  ready: (page: Page) => Promise<void>;
}

const h1 = (page: Page) => expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();

const ROUTES: RouteCase[] = [
  { name: "sign-in", path: "/sign-in", role: null, ready: h1 },
  {
    name: "today",
    path: "/",
    role: "student",
    ready: (p) => expect(p.locator("[data-state=populated]")).toBeVisible(),
  },
  { name: "learn", path: "/learn", role: "student", ready: h1 },
  {
    name: "learn chapter",
    path: "/learn",
    role: "student",
    ready: async (p) => {
      await h1(p);
      await quiet(p);
      // The first chapter of the course map: the reader with figures, tables and math (KaTeX).
      await p.locator('a[href^="/learn/"]').first().click();
      await expect(p.locator("[data-learn-page]")).toBeVisible();
    },
  },
  { name: "practice", path: "/practice", role: "student", ready: h1 },
  { name: "question", path: "/practice/question/ch01-q01", role: "student", ready: h1 },
  { name: "cards", path: "/cards", role: "student", ready: h1 },
  { name: "notes", path: "/notes", role: "student", ready: h1 },
  { name: "notebooks", path: "/notes/notebooks", role: "student", ready: h1 },
  { name: "search", path: "/search", role: "student", ready: h1 },
  { name: "library", path: "/learn/library", role: "student", ready: h1 },
  { name: "plan (onboarding)", path: "/welcome", role: "student", ready: h1 },
  { name: "settings", path: "/settings/account", role: "student", ready: h1 },
  { name: "system", path: "/system", role: null, ready: h1 },
  { name: "admin agent", path: "/admin/agent", role: "admin", ready: h1 },
];

test.describe("production CSP: zero violations on every main route", () => {
  test.skip(!PROD, "needs the local APP_ENV=prod API (playwright.config.ts)");

  for (const r of ROUTES) {
    test(r.name, async ({ browser, baseURL }, testInfo) => {
      const ctx = await prodContext(browser, baseURL as string, r.role);
      try {
        const page = await ctx.newPage();
        const errors = watch(page);
        await page.goto(r.path);
        await r.ready(page);
        await quiet(page);
        await settle(page);
        const all = await violations(page);
        const reportOnly = all.filter((v) => v.disposition === "report");
        if (reportOnly.length) {
          await testInfo.attach("trusted-types-reports", {
            body: JSON.stringify(reportOnly, null, 2),
            contentType: "application/json",
          });
        }
        expect(
          all.filter((v) => v.disposition !== "report"),
          "CSP violations under the production policy",
        ).toEqual([]);
        expect(errors, "console errors under the production CSP").toEqual([]);
      } finally {
        await ctx.close();
      }
    });
  }

  test("violation reports reach the endpoint and are accepted", async ({ browser }) => {
    const ctx = await browser.newContext({ baseURL: PROD, ignoreHTTPSErrors: true });
    try {
      const res = await ctx.request.post("/api/csp-report", {
        headers: { "content-type": "application/csp-report" },
        data: { "csp-report": { "effective-directive": "img-src", "blocked-uri": "https://example.com/x.png" } },
      });
      expect(res.status()).toBe(204);
      const page = await ctx.newPage();
      const resp = await page.goto("/sign-in");
      const h = resp?.headers() ?? {};
      expect(h["content-security-policy"]).toContain("report-uri /api/csp-report");
      expect(h["content-security-policy-report-only"]).toContain("require-trusted-types-for 'script'");
      expect(h["content-security-policy"]).not.toContain("unsafe-inline");
      expect(h["content-security-policy"]).not.toContain("unsafe-eval");
    } finally {
      await ctx.close();
    }
  });
});
