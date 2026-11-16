import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import type { Browser, BrowserContext, Page, TestInfo } from "@playwright/test";
import { callbackPath, canMintUsers, runUsers, toLink } from "./auth-users";
import {
  type ConsoleGuard,
  expect,
  expectNoAxeViolations,
  expectNoHorizontalOverflow,
  snapSegment,
  TOOL_CSP_NOISE,
  test,
} from "./fixtures";
import { ensureOnboarded, signedInState } from "./session";

/**
 * Segment 11: onboarding (/welcome) → generated plan → Today with real numbers, the coach card, item ticks, the
 * Today states (loading, error, offline) and the warm-load budget. Throwaway users per test and project, real
 * Supabase sessions, the real API and database; practice history comes from apps/api/tests/plan/e2e_today.py.
 */
test.skip(!canMintUsers(), "needs the service role to mint throwaway users");

const API_DIR = path.resolve(import.meta.dirname, "../../api");
const snap = (page: Page, info: TestInfo, guard: ConsoleGuard, state: string, full = false) =>
  snapSegment(page, info, guard, "11", state, full);

function userEmail(info: TestInfo, kind: string): string {
  const run = String.fromCharCode(97 + (info.repeatEachIndex % 26), 97 + (info.retry % 26));
  return `naadi-e2e-${kind}${info.project.name.replace(/[^a-z]/g, "")}${run}-${process.env.E2E_RUN_ID}@example.com`;
}

function helper(action: "history" | "coach" | "delete", email: string): Record<string, unknown> {
  const db = process.env.E2E_DATABASE_URL ?? process.env.DATABASE_URL;
  if (!db) throw new Error("today.spec needs the local database (E2E_DATABASE_URL or DATABASE_URL)");
  const args = ["--directory", API_DIR, "run", "--frozen", "python", "-m", "tests.plan.e2e_today", action];
  const out = execFileSync("uv", [...args, "--email", email, "--db", db], { encoding: "utf8", timeout: 60_000 });
  return JSON.parse(out.trim().split("\n").pop() ?? "{}") as Record<string, unknown>;
}

/** A fresh, signed-in page for a throwaway user (deleted in `done`). */
async function fresh(browser: Browser, baseURL: string, info: TestInfo, kind: string, next = "/") {
  const email = userEmail(info, kind);
  runUsers(["delete", "--email", email]);
  const context: BrowserContext = await browser.newContext({ ...info.project.use, baseURL });
  const page = await context.newPage();
  const link = toLink(runUsers(["link", "--email", email]));
  await page.goto(callbackPath(link, next));
  const done = async () => {
    try {
      await context.close();
    } finally {
      runUsers(["delete", "--email", email]);
    }
  };
  return { page, email, done };
}

/** Background colour of every visible button/link that is lime (the next-action colour). */
async function limeCount(page: Page): Promise<number> {
  return page.evaluate(() => {
    const lime = "rgb(198, 255, 61)";
    return Array.from(document.querySelectorAll<HTMLElement>("a, button")).filter((el) => {
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0 && getComputedStyle(el).backgroundColor === lime;
    }).length;
  });
}

/**
 * Console errors of a page outside the fixture's own (fresh contexts), with the fixture's rule: WebKit's
 * screenshot <style> refused by our CSP is the tool's noise, ignored only while snapping.
 */
function watchConsole(page: Page): { errors: string[]; guard: ConsoleGuard } {
  const errors: string[] = [];
  let tool = 0;
  page.on("console", (m) => {
    if (m.type() === "error" && !(tool > 0 && m.text() === TOOL_CSP_NOISE)) errors.push(m.text());
  });
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  const guard: ConsoleGuard = {
    allowHttpError: () => {},
    duringTool: async (fn) => {
      tool++;
      try {
        return await fn();
      } finally {
        await page.waitForTimeout(300);
        tool--;
      }
    },
  };
  return { errors, guard };
}

async function settle(page: Page): Promise<void> {
  await page.waitForLoadState("networkidle");
  await page.evaluate(() => document.fonts.ready);
}

function addDays(iso: string, n: number): string {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

const preview = (page: Page) => page.locator("[data-preview]");

test.describe("onboarding", () => {
  test("a new student sets up a plan in under three minutes and lands on Today", async ({ browser, baseURL }, info) => {
    const started = Date.now();
    const { page, done } = await fresh(browser, baseURL as string, info, "welcome");
    const { errors, guard } = watchConsole(page);
    try {
      await page.waitForURL((u) => u.pathname === "/welcome", { timeout: 30_000 });
      await expect(page.getByRole("heading", { level: 1 })).toHaveText("When is the big day?");
      await settle(page);
      // Only exams with a plan template are offered (Step 1 today); the diagnostic step is absent until
      // timed blocks ship.
      await expect(page.getByRole("button", { name: "Step 1", exact: true })).toHaveAttribute("aria-pressed", "true");
      await expect(page.getByRole("button", { name: "Step 3" })).toHaveCount(0);
      await expect(page.getByRole("button", { name: "OET Medicine" })).toHaveCount(0);
      await expect(page.getByRole("list", { name: "Setup steps" }).getByRole("listitem")).toHaveCount(4);
      await expect(page.getByRole("button", { name: "Take the diagnostic" })).toHaveCount(0);
      await expect(preview(page)).toContainText("Pick an exam date to see your plan.");
      await expectNoHorizontalOverflow(page);
      await expectNoAxeViolations(page);
      await snap(page, info, guard, "welcome-empty", true);

      const date = page.getByLabel("Exam date");
      const min = (await date.getAttribute("min")) as string;
      await date.fill(addDays(min, 76)); // 77 dates from today to the eve → 11 weeks
      const hours = page.getByRole("slider", { name: "Hours you can study on a normal day" });
      await hours.focus();
      for (let i = 0; i < 4; i++) await page.keyboard.press("ArrowLeft"); // 8 h → 6 h
      await expect(page.getByText("6 h", { exact: true })).toBeVisible();
      // Study days: the profile default (Mon–Sat) is pre-selected; selected chips carry a check, not colour only.
      const sun = page.getByRole("button", { name: "Sun", exact: true });
      await expect(sun).toHaveAttribute("aria-pressed", "false");
      await expect(page.getByRole("button", { name: "Mon", exact: true }).locator("svg")).toHaveCount(1);
      await page.getByRole("button", { name: "A question bank" }).click();
      await page.getByRole("button", { name: "Anki decks" }).click();
      await page.getByLabel("Medical school").fill("Test Medical College");
      await expect(preview(page)).toHaveAttribute("data-preview", "11");
      await expect(preview(page)).toContainText("weeks, six days a week.");
      await settle(page);
      await expectNoHorizontalOverflow(page);
      await expectNoAxeViolations(page);
      await snap(page, info, guard, "welcome-filled", true);

      const start = page.getByRole("button", { name: "Start my plan" });
      await expect(start).toBeEnabled();
      await start.click();
      await page.waitForURL((u) => u.pathname === "/", { timeout: 30_000 });
      await expect(page.locator("[data-state=populated]")).toBeVisible();
      expect(Date.now() - started, "onboarding takes at most three minutes").toBeLessThan(180_000);

      // Today from the generated plan: countdown, the baseline day's items in plan order, one lime action.
      await expect(page.getByText("77 days", { exact: false })).toBeVisible();
      await expect(page.getByText("Today: Baseline + setup")).toBeVisible();
      const flow = page.locator("section[aria-labelledby=today-flow] li");
      await expect(flow.first()).toContainText("BASELINE: NBME Free 120");
      await expect(flow.first()).toContainText("Outside Naadi");
      await expect(flow.first()).toContainText("Now");
      await settle(page);
      expect(await limeCount(page)).toBe(1);
      await expectNoHorizontalOverflow(page);
      await expectNoAxeViolations(page);
      await snap(page, info, guard, "today-first-day", true);
      expect(errors).toEqual([]);
    } finally {
      await done();
    }
  });

  test("too few study days shows why and keeps the plan button off", async ({ browser, baseURL }, info) => {
    const { page, done } = await fresh(browser, baseURL as string, info, "short", "/welcome");
    const { errors, guard } = watchConsole(page);
    try {
      await page.waitForURL((u) => u.pathname === "/welcome", { timeout: 30_000 });
      const date = page.getByLabel("Exam date");
      const min = (await date.getAttribute("min")) as string;
      await date.fill(addDays(min, 29));
      for (const d of ["Mon", "Tue", "Wed", "Thu", "Fri"])
        await page.getByRole("button", { name: d, exact: true }).click();
      await expect(preview(page)).toContainText("A plan needs at least 21");
      await expect(page.getByRole("button", { name: "Start my plan" })).toBeDisabled();
      await settle(page);
      await expectNoAxeViolations(page);
      await snap(page, info, guard, "welcome-too-short", true);
      expect(errors).toEqual([]);
    } finally {
      await done();
    }
  });
});

test.describe("Today with practice history", () => {
  test("shows real numbers, the coach card, and ticks items", async ({ browser, baseURL }, info) => {
    const { page, email, done } = await fresh(browser, baseURL as string, info, "today");
    const { errors, guard } = watchConsole(page);
    try {
      await page.waitForURL((u) => u.pathname === "/welcome", { timeout: 30_000 });
      await ensureOnboarded(page);
      const history = helper("history", email) as {
        systems: Record<string, { attempts: number; correct: number; percent: number | null }>;
        answered: number;
      };
      helper("coach", email);
      await page.goto("/");
      await expect(page.locator("[data-state=populated]")).toBeVisible();

      // By system: the numbers equal direct SQL on the same rows; status is a word, not only colour.
      for (const [id, want] of Object.entries(history.systems)) {
        const row = page.locator(`[data-system="${id}"]`);
        if (want.percent === null) {
          await expect(row).toHaveAttribute("data-state", "insufficient");
          await expect(row).toContainText("not enough data");
        } else {
          await expect(row).toContainText(`${want.percent}%`);
          if (want.percent < 55) {
            await expect(row).toHaveAttribute("data-state", "weak");
            await expect(row).toContainText("weak");
          }
        }
      }
      await expect(page.locator("[data-readiness=pending]")).toContainText(`So far: ${history.answered} answers`);

      // The coach card is the assistant's (violet), worded from ui_copy with the stored facts.
      const card = page.locator("section[aria-labelledby=today-coach] article");
      await expect(card).toContainText("Catch up on Respiratory & Renal/Urinary Systems");
      await expect(card).toContainText("You got 4 of 10 right lately (40%)");
      await settle(page);
      expect(await limeCount(page)).toBe(1);
      await expectNoHorizontalOverflow(page);
      await expectNoAxeViolations(page);
      await snap(page, info, guard, "today-coach", true);
      await card.getByRole("button", { name: "Show me why" }).click();
      await expect(card.locator("dl")).toContainText("Attempts");
      await snap(page, info, guard, "today-coach-why");
      await card.getByRole("button", { name: "Add to plan" }).click();
      await expect(page.locator("section[aria-labelledby=today-coach]")).toHaveCount(0);
      const added = page.locator("section[aria-labelledby=today-flow] li").filter({ hasText: "Added by Naadi" });
      await expect(added).toContainText("Weak-spot block · Respiratory & Renal/Urinary Systems");

      // Tick the current item: it is done after a reload too, and the next one becomes "Now".
      const first = page.locator("section[aria-labelledby=today-flow] li").first();
      await first.getByRole("button", { name: /^Mark done:/ }).click();
      await expect(first).toContainText("Done");
      await settle(page);
      await page.reload();
      await expect(page.locator("section[aria-labelledby=today-flow] li").first()).toContainText("Done");
      await expect(page.locator("[data-now]")).toHaveCount(1);
      await settle(page);
      await snap(page, info, guard, "today-ticked", true);
      expect(errors).toEqual([]);
    } finally {
      helper("delete", email);
      await done();
    }
  });
});

test.describe("Today states (shared student)", () => {
  test.use({
    storageState: async (
      { browser, baseURL }: { browser: Browser; baseURL: string | undefined },
      use: (p: string) => Promise<void>,
    ) => use(await signedInState(browser, baseURL as string, "student")),
  });

  test("warm loads stay under 1.5 s", async ({ page }) => {
    await page.goto("/");
    await expect(page.locator("[data-state=populated]")).toBeVisible();
    await settle(page);
    const phone = (page.viewportSize()?.width ?? 1000) < 640;
    const nav = phone ? page.getByRole("navigation", { name: "Tabs" }) : page.getByRole("navigation", { name: "Main" });
    const times: number[] = [];
    for (let i = 0; i < 5; i++) {
      await nav.getByRole("link", { name: "Learn" }).click();
      await expect(page).toHaveURL(/\/learn$/);
      await settle(page);
      const t0 = Date.now();
      await nav.getByRole("link", { name: "Today", exact: true }).click();
      await expect(page.locator("[data-state=populated]")).toBeVisible();
      times.push(Date.now() - t0);
    }
    times.sort((a, b) => a - b);
    const p95 = times[Math.ceil(0.95 * times.length) - 1] as number;
    expect(p95, `warm Today loads ${times.join(", ")} ms`).toBeLessThan(1500);
  });

  test("offline keeps today on screen and moving around works", async ({ page, context, consoleErrors }, info) => {
    await page.goto("/");
    await expect(page.locator("[data-state=populated]")).toBeVisible();
    await settle(page);
    await page.evaluate(() =>
      Promise.all([...document.fonts].filter((f) => f.family.includes("JetBrains")).map((f) => f.load())),
    );
    await context.setOffline(true);
    await expect(page.getByRole("status").filter({ hasText: "Offline" })).toBeVisible();
    await expect(page.locator("[data-state=populated]")).toBeVisible();
    await expectNoAxeViolations(page);
    await snap(page, info, consoleErrors, "today-offline");
    await context.setOffline(false);
  });
});

test.describe("production CSP", () => {
  test("Today and onboarding render with zero console errors", async ({ browser, baseURL }) => {
    const PROD = process.env.E2E_PROD_URL;
    test.skip(!PROD, "needs the local APP_ENV=prod API (playwright.config.ts)");
    // The student session from the main origin, moved to the https prod origin (as in agent.spec).
    const file = await signedInState(browser, baseURL as string, "student");
    const state = JSON.parse(readFileSync(file, "utf8")) as {
      origins: { origin: string; localStorage: { name: string; value: string }[] }[];
    };
    const origin = new URL(PROD as string).origin;
    const ctx = await browser.newContext({
      baseURL: PROD,
      ignoreHTTPSErrors: true,
      storageState: { cookies: [], origins: state.origins.map((o) => ({ ...o, origin })) },
    });
    try {
      const page = await ctx.newPage();
      const { errors } = watchConsole(page);
      await page.goto("/");
      await expect(page.locator("[data-state=populated]")).toBeVisible();
      await settle(page);
      await page.goto("/welcome");
      await expect(page.getByRole("heading", { level: 1 })).toHaveText("When is the big day?");
      await expect(preview(page)).toHaveAttribute("data-preview", /\d+/);
      await settle(page);
      expect(errors, "console errors under the production CSP").toEqual([]);
    } finally {
      await ctx.close();
    }
  });
});
