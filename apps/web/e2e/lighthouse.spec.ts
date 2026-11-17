/**
 * Lighthouse + installability (segment 22, HANDOFF §12). Opt-in: E2E_LIGHTHOUSE=1, Chromium only (the CI job
 * `lighthouse`). Lighthouse 12 has no PWA category, so installability is asserted directly (lead decision).
 *
 * Profile: iPad portrait 820x1180 at DPR 2, Slow 4G (150 ms RTT, 1.6 Mbps), 4x CPU slowdown, simulated throttling.
 * Pages: Today, a Learn chapter, Practice, Plan, as the signed-in test student, service worker warm, data cold.
 * Thresholds: performance >= 90, accessibility = 100, best practices >= 90 on each; Today interactive < 2 s.
 * Every report is attached (HTML) to the test.
 */
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { type BrowserContext, chromium, type Page } from "@playwright/test";
import lighthouse from "lighthouse";
import { expect, test } from "./fixtures";
import { signedInState } from "./session";

const ENABLED = process.env.E2E_LIGHTHOUSE === "1";

const IPAD_SLOW_4G = {
  extends: "lighthouse:default",
  settings: {
    onlyCategories: ["performance", "accessibility", "best-practices"],
    formFactor: "mobile" as const,
    screenEmulation: { mobile: true, width: 820, height: 1180, deviceScaleFactor: 2, disabled: false },
    throttlingMethod: "simulate" as const,
    throttling: {
      rttMs: 150,
      throughputKbps: 1638.4,
      requestLatencyMs: 562.5,
      downloadThroughputKbps: 1474.56,
      uploadThroughputKbps: 675,
      cpuSlowdownMultiplier: 4,
    },
    // The test student's session lives in localStorage: keep it between runs.
    disableStorageReset: true,
  },
};

const THRESHOLDS = { performance: 0.9, accessibility: 1, "best-practices": 0.9 } as const;
const TODAY_INTERACTIVE_MS = 2000;

interface Result {
  page: string;
  scores: Record<string, number | null>;
  interactiveMs: number | null;
}

async function signIn(ctx: BrowserContext, page: Page, baseURL: string, file: string): Promise<void> {
  const state = JSON.parse(readFileSync(file, "utf8")) as {
    origins: { origin: string; localStorage: { name: string; value: string }[] }[];
  };
  const origin = new URL(baseURL).origin;
  const items = state.origins.flatMap((o) => o.localStorage);
  await page.goto(`${origin}/sign-in`);
  await page.evaluate((all) => {
    for (const { name, value } of all) localStorage.setItem(name, value);
  }, items);
  void ctx;
}

test.describe("lighthouse and installability (iPad, Slow 4G, 4x CPU)", () => {
  test.skip(!ENABLED, "set E2E_LIGHTHOUSE=1 (the CI lighthouse job)");

  test("Today, a chapter, Practice and Plan meet the budgets; the app is installable", async ({
    browser,
    baseURL,
    browserName,
  }, info) => {
    test.skip(browserName !== "chromium", "Lighthouse drives Chromium");
    test.setTimeout(900_000);
    const base = (baseURL as string).replace(/\/$/, "");
    const stateFile = await signedInState(browser, base, "student");
    const port = 9300 + info.workerIndex;
    const ctx = await chromium.launchPersistentContext(mkdtempSync(path.join(tmpdir(), "naadi-lh-")), {
      args: [`--remote-debugging-port=${port}`],
    });
    try {
      const page = ctx.pages()[0] ?? (await ctx.newPage());
      await signIn(ctx, page, base, stateFile);

      // Warm the service worker (installed and controlling) before measuring; data stays cold per run.
      await page.goto(`${base}/`);
      await expect(page.locator("[data-state=populated]")).toBeVisible();
      await page.reload();
      await expect.poll(() => page.evaluate(() => navigator.serviceWorker?.controller !== null)).toBe(true);

      await page.goto(`${base}/learn`);
      const chapter = await page.locator('a[href^="/learn/"]').first().getAttribute("href");
      expect(chapter, "a chapter link on the course map").toBeTruthy();

      const pages: [string, string][] = [
        ["today", "/"],
        ["learn chapter", chapter as string],
        ["practice", "/practice"],
        ["plan", "/plan"],
      ];
      const results: Result[] = [];
      for (const [name, url] of pages) {
        const run = await lighthouse(`${base}${url}`, { port, output: "html", logLevel: "error" }, IPAD_SLOW_4G);
        if (!run) throw new Error(`lighthouse returned nothing for ${url}`);
        const lhr = run.lhr;
        const scores: Record<string, number | null> = {};
        for (const k of Object.keys(THRESHOLDS)) scores[k] = lhr.categories[k]?.score ?? null;
        const interactive = lhr.audits.interactive?.numericValue ?? null;
        results.push({ page: name, scores, interactiveMs: interactive === null ? null : Math.round(interactive) });
        await info.attach(`lighthouse-${name.replace(/\s+/g, "-")}.html`, {
          body: String(Array.isArray(run.report) ? run.report[0] : run.report),
          contentType: "text/html",
        });
      }
      await info.attach("lighthouse-summary.json", {
        body: JSON.stringify(results, null, 2),
        contentType: "application/json",
      });
      console.log(JSON.stringify(results));

      const misses = results.flatMap((r) =>
        Object.entries(THRESHOLDS)
          .filter(([k, min]) => (r.scores[k] ?? 0) < min)
          .map(([k, min]) => `${r.page}: ${k} ${r.scores[k]} < ${min}`),
      );
      const today = results.find((r) => r.page === "today");
      if (today?.interactiveMs == null) misses.push("today: no interactive (TTI) measurement");
      else if (today.interactiveMs >= TODAY_INTERACTIVE_MS) {
        misses.push(`today: interactive ${today.interactiveMs} ms >= ${TODAY_INTERACTIVE_MS} ms`);
      }

      // Installability, asserted directly (Lighthouse 12 has no PWA category).
      await page.goto(`${base}/`);
      await expect(page.locator("[data-state=populated]")).toBeVisible();
      const cdp = await ctx.newCDPSession(page);
      const { installabilityErrors } = (await cdp.send("Page.getInstallabilityErrors")) as {
        installabilityErrors: { errorId: string }[];
      };
      expect(
        installabilityErrors.map((e) => e.errorId),
        "Chromium installability errors",
      ).toEqual([]);
      await expect(page.locator('link[rel="manifest"]'), "exactly one manifest link").toHaveCount(1);
      const manifestHref = await page.locator('link[rel="manifest"]').getAttribute("href");
      expect(manifestHref, "manifest link").toBeTruthy();
      const manifest = (await (await page.request.get(new URL(manifestHref as string, base).href)).json()) as Record<
        string,
        unknown
      >;
      for (const field of ["name", "short_name", "start_url", "display", "theme_color", "background_color", "icons"]) {
        expect(manifest[field], `manifest.${field}`).toBeTruthy();
      }
      const sizes = (manifest.icons as { sizes?: string }[]).map((i) => i.sizes ?? "");
      expect(sizes.some((s) => s.includes("192x192")) && sizes.some((s) => s.includes("512x512"))).toBe(true);
      expect(await page.evaluate(() => navigator.serviceWorker.controller !== null), "SW controls the page").toBe(true);
      await ctx.setOffline(true);
      await page.reload();
      await expect(page.locator("#root"), "offline reload renders the app shell").not.toBeEmpty();
      await ctx.setOffline(false);

      expect(misses, "Lighthouse budgets").toEqual([]);
    } finally {
      await ctx.close();
    }
  });
});
