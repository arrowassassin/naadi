import { execFileSync } from "node:child_process";
import path from "node:path";
import type { Browser, BrowserContext, Page, TestInfo } from "@playwright/test";
import { callbackPath, canMintUsers, runUsers, toLink } from "./auth-users";
import {
  type ConsoleGuard,
  expect,
  expectNoAxeViolations,
  expectNoHorizontalOverflow,
  quiet,
  settle,
  snapSegment,
  TOOL_CSP_NOISE,
  test,
  trackApi,
} from "./fixtures";
import { ensureOnboarded } from "./session";

/**
 * Segment 16: the planner (week calendar, rebalance after missed days with a confirm, offline status changes), the
 * calendar feed and the push service worker. Throwaway users per test, real sessions, the real API and database;
 * missed days come from apps/api/tests/plan/e2e_planner.py (the plan the real onboarding made, moved back in time).
 * The rebalance runs through the real `planner` graph on the Aegra runtime.
 */
test.skip(!canMintUsers(), "needs the service role to mint throwaway users");

const API_DIR = path.resolve(import.meta.dirname, "../../api");
const snap = (page: Page, info: TestInfo, guard: ConsoleGuard, state: string, full = false) =>
  snapSegment(page, info, guard, "16", state, full);

function userEmail(info: TestInfo, kind: string): string {
  const run = String.fromCharCode(97 + (info.repeatEachIndex % 26), 97 + (info.retry % 26));
  return `naadi-e2e-${kind}${info.project.name.replace(/[^a-z]/g, "")}${run}-${process.env.E2E_RUN_ID}@example.com`;
}

function shiftPlan(email: string, days: number): void {
  const db = process.env.E2E_DATABASE_URL ?? process.env.DATABASE_URL;
  if (!db) throw new Error("planner.spec needs the local database (E2E_DATABASE_URL or DATABASE_URL)");
  execFileSync(
    "uv",
    ["--directory", API_DIR, "run", "--frozen", "python", "-m", "tests.plan.e2e_planner", "shift"].concat([
      "--email",
      email,
      "--days",
      String(days),
      "--db",
      db,
    ]),
    { encoding: "utf8", timeout: 60_000 },
  );
}

interface Fresh {
  page: Page;
  context: BrowserContext;
  email: string;
  errors: string[];
  guard: ConsoleGuard;
  done: () => Promise<void>;
}

/** A signed-in page for a throwaway user (deleted in `done`), optionally with a plan that started `missed` days ago. */
async function fresh(
  browser: Browser,
  baseURL: string,
  info: TestInfo,
  kind: string,
  opts: { plan: boolean; missed?: number } = { plan: true, missed: 3 },
): Promise<Fresh> {
  const email = userEmail(info, kind);
  runUsers(["delete", "--email", email]);
  // The page's own service worker (production build) would bypass page.route in WebKit; these flows test the app.
  const context = await browser.newContext({ ...info.project.use, baseURL, serviceWorkers: "block" });
  const page = await context.newPage();
  trackApi(page);
  const errors: string[] = [];
  let tool = 0;
  page.on("console", (m) => {
    if (m.type() === "error" && !(tool > 0 && m.text() === TOOL_CSP_NOISE)) {
      errors.push(`${m.text()} @ ${m.location().url}`);
    }
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
  const link = toLink(runUsers(["link", "--email", email]));
  await page.goto(callbackPath(link, "/settings/account"));
  await page.waitForURL((u) => u.pathname === "/settings/account", { timeout: 30_000 });
  if (opts.plan) {
    await ensureOnboarded(page);
    if (opts.missed) shiftPlan(email, opts.missed);
  }
  const done = async () => {
    try {
      await context.close();
    } finally {
      runUsers(["delete", "--email", email]);
    }
  };
  return { page, context, email, errors, guard, done };
}

async function token(page: Page): Promise<string> {
  return page.evaluate(() => {
    const raw = localStorage.getItem("naadi.auth");
    return raw ? (JSON.parse(raw) as { access_token: string }).access_token : "";
  });
}

interface WeekJson {
  behind: number;
  rebalance: {
    id: string;
    proposal: { moves: { item_id: string; title: string; from_day: string; to_day: string }[] };
  } | null;
  days: {
    date: string;
    assessment: boolean;
    items: { id: string; day: string; status: string; moved_from: string | null }[];
  }[];
}

async function week(page: Page, start?: string): Promise<WeekJson> {
  const res = await page.request.get(`/api/plan/week${start ? `?start=${start}` : ""}`, {
    headers: { authorization: `Bearer ${await token(page)}` },
  });
  expect(res.ok()).toBe(true);
  return ((await res.json()) as { week: WeekJson }).week;
}

/** The whole plan's items as `day|status|id` lines (what a decline must leave untouched). */
async function planFingerprint(page: Page): Promise<string[]> {
  const res = await page.request.get("/api/plan", { headers: { authorization: `Bearer ${await token(page)}` } });
  expect(res.ok()).toBe(true);
  const plan = (await res.json()) as {
    days: { items: { id: string; day: string; status: string; sort: number | null }[] }[];
  };
  return plan.days.flatMap((d) => d.items.map((i) => `${i.day}|${i.status}|${i.sort}|${i.id}`)).sort();
}

/** Background colour of every visible element that is lime (the next-action colour). */
async function limeCount(page: Page): Promise<number> {
  const found = await page.evaluate(() => {
    const lime = "rgb(198, 255, 61)";
    return Array.from(document.querySelectorAll<HTMLElement>("body *:not([data-brand-mark])"))
      .filter((el) => {
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0 && getComputedStyle(el).backgroundColor === lime;
      })
      .map((el) => `${el.tagName.toLowerCase()} "${(el.textContent ?? "").trim().slice(0, 40)}"`);
  });
  if (found.length !== 1) console.log(`lime elements: ${found.join(" | ")}`);
  return found.length;
}

const planner = (page: Page) => page.getByTestId("planner");

async function openPlan(page: Page): Promise<void> {
  await quiet(page);
  await page.goto("/plan");
  await expect(planner(page)).toBeVisible();
  await expect(page.getByRole("heading", { name: "Plan", level: 1 })).toBeVisible();
  await settle(page);
}

test.describe("planner states", () => {
  test("a student without a plan is sent to set one up", async ({ browser, baseURL }, info) => {
    const { page, errors, guard, done } = await fresh(browser, baseURL as string, info, "pnone", { plan: false });
    try {
      await quiet(page);
      await page.goto("/plan");
      await expect(page.getByRole("heading", { name: "No plan yet" })).toBeVisible();
      await expect(page.getByRole("link", { name: "Set up your plan" })).toHaveAttribute("href", "/welcome");
      await settle(page);
      await expectNoHorizontalOverflow(page);
      await expectNoAxeViolations(page);
      await snap(page, info, guard, "planner-empty", true);
      expect(errors).toEqual([]);
    } finally {
      await done();
    }
  });

  test("the plan loads with a skeleton first, and a failed load offers a retry", async ({ browser, baseURL }, info) => {
    const { page, errors, guard, done } = await fresh(browser, baseURL as string, info, "pload", {
      plan: true,
      missed: 0,
    });
    try {
      let fail = true;
      let release: () => void = () => undefined;
      const gate = new Promise<void>((r) => {
        release = r;
      });
      await page.route("**/api/plan/week**", async (route) => {
        await gate;
        if (fail)
          await route.fulfill({
            status: 500,
            contentType: "application/json",
            body: '{"error":{"code":"internal","message":"x"}}',
          });
        else await route.continue();
      });
      await quiet(page);
      await page.goto("/plan");
      await expect(page.getByRole("status").filter({ hasText: "Loading your plan" })).toBeAttached();
      await snap(page, info, guard, "planner-loading");
      release();
      await expect(page.getByRole("alert")).toContainText("Your plan could not be loaded", { timeout: 30_000 });
      await settle(page);
      await expectNoAxeViolations(page);
      await snap(page, info, guard, "planner-error");
      fail = false;
      await page.getByRole("button", { name: "Try again" }).click();
      await expect(planner(page)).toBeVisible();
      await page.unroute("**/api/plan/week**");
      await settle(page);
      // the expected 500 shows up as the browser's own console line; nothing else may
      expect(errors.filter((e) => !e.includes("500"))).toEqual([]);
    } finally {
      await done();
    }
  });
});

test.describe("rebalance", () => {
  test("two missed days → suggestion → Show changes → Accept moves the items; assessment days stay", async ({
    browser,
    baseURL,
  }, info) => {
    const { page, errors, guard, done } = await fresh(browser, baseURL as string, info, "pacc", {
      plan: true,
      missed: 3,
    });
    try {
      const before = await week(page);
      expect(before.behind).toBeGreaterThan(0);
      await openPlan(page);
      // Behind items carry the word, today's next item is the one lime action
      await expect(page.getByText("Behind", { exact: true }).first()).toBeVisible();
      await expect(page.getByTestId("cta-start")).toBeVisible();
      expect(await limeCount(page)).toBe(1);
      await expectNoHorizontalOverflow(page);
      await snap(page, info, guard, "planner-behind", true);

      await page.getByRole("button", { name: "Rebalance", exact: true }).click();
      const card = page.getByTestId("rebalance-card");
      await expect(card).toBeVisible({ timeout: 60_000 });
      await expect(card.getByRole("heading", { level: 2 })).toContainText("missed item");
      await expect(card.getByText("Naadi suggests")).toBeVisible();
      expect(await limeCount(page)).toBe(1); // the suggestion is violet: still only the next-up action
      await card.getByRole("button", { name: "Show changes" }).click();
      await expect(card.getByTestId("diff-moves").getByRole("listitem").first()).toContainText("→");
      await settle(page);
      await expectNoAxeViolations(page);
      await snap(page, info, guard, "planner-suggestion", true);

      // The proposal is stored and nothing in the plan has changed yet
      const proposed = await week(page);
      expect(proposed.rebalance?.proposal.moves.length).toBeGreaterThan(0);
      const moves = proposed.rebalance?.proposal.moves ?? [];

      await card.getByRole("button", { name: "Accept" }).click();
      await expect(page.getByTestId("planner-notice")).toContainText("Plan updated", { timeout: 60_000 });
      await expect(page.getByTestId("rebalance-card")).toHaveCount(0);
      await settle(page);

      // The moved items sit on their new days, remember where they came from; assessment days did not change
      const after = await week(page);
      expect(after.rebalance).toBeNull();
      expect((await planFingerprint(page)).length).toBeGreaterThan(0);
      // every proposed move happened: the item sits on its new day and remembers where it came from
      for (const m of moves) {
        const there = (await week(page, m.to_day)).days.flatMap((d) => d.items).find((i) => i.id === m.item_id);
        expect(there?.day).toBe(m.to_day);
        expect(there?.moved_from).toBe(m.from_day);
      }
      const assessmentDays = new Set([...before.days, ...after.days].filter((d) => d.assessment).map((d) => d.date));
      for (const d of after.days) {
        if (!assessmentDays.has(d.date)) continue;
        const was = before.days.find((b) => b.date === d.date);
        expect(d.items.map((i) => i.id).sort()).toEqual((was?.items ?? d.items).map((i) => i.id).sort());
      }
      expect(await limeCount(page)).toBe(1);
      await expectNoAxeViolations(page);
      await snap(page, info, guard, "planner-after-accept", true);
      expect(errors).toEqual([]);
    } finally {
      await done();
    }
  });

  test("Decline leaves the plan exactly as it was", async ({ browser, baseURL }, info) => {
    const { page, errors, guard, done } = await fresh(browser, baseURL as string, info, "pdec", {
      plan: true,
      missed: 3,
    });
    try {
      const planBefore = await planFingerprint(page);
      await openPlan(page);
      await page.getByRole("button", { name: "Rebalance", exact: true }).click();
      const card = page.getByTestId("rebalance-card");
      await expect(card).toBeVisible({ timeout: 60_000 });
      await card.getByRole("button", { name: "Decline" }).click();
      await expect(page.getByTestId("planner-notice")).toContainText("Your plan is unchanged", { timeout: 60_000 });
      await expect(page.getByTestId("rebalance-card")).toHaveCount(0);
      await settle(page);
      expect(await planFingerprint(page)).toEqual(planBefore);
      await snap(page, info, guard, "planner-after-decline");
      expect(errors).toEqual([]);
    } finally {
      await done();
    }
  });

  test("a student who is on track is told so, and nothing is proposed", async ({ browser, baseURL }, info) => {
    const { page, errors, done } = await fresh(browser, baseURL as string, info, "ptrack", { plan: true, missed: 0 });
    try {
      await openPlan(page);
      await page.getByRole("button", { name: "Rebalance", exact: true }).click();
      await expect(page.getByTestId("planner-notice")).toContainText("You are on track", { timeout: 60_000 });
      await expect(page.getByTestId("rebalance-card")).toHaveCount(0);
      expect(errors).toEqual([]);
    } finally {
      await done();
    }
  });
});

test.describe("offline", () => {
  test("a status change made offline says it will sync, and syncs when the connection returns", async ({
    browser,
    baseURL,
  }, info) => {
    const { page, context, errors, guard, done } = await fresh(browser, baseURL as string, info, "poff", {
      plan: true,
      missed: 0,
    });
    try {
      await openPlan(page);
      await quiet(page);
      await context.setOffline(true);
      await expect(page.getByTestId("offline-note")).toBeVisible();
      await expect(page.getByRole("button", { name: "Rebalance", exact: true })).toBeDisabled();
      await expect(page.getByText("Rebalancing needs a connection.").first()).toBeVisible();
      await snap(page, info, guard, "planner-offline", true);
      // open today's next item and mark it done while offline
      const next = page.getByTestId("cta-start");
      await expect(next).toBeVisible();
      const title = (await page.locator("#next-up-h").locator("xpath=../following-sibling::p[1]").textContent()) ?? "";
      await page
        .getByRole("button", { name: new RegExp(title.slice(0, 30).replace(/[.*+?^${}()|[\]\\]/g, "\\$&")) })
        .first()
        .click();
      await page.getByRole("dialog").getByRole("button", { name: "Mark done" }).click();
      await expect(page.getByText("Will sync").first()).toBeVisible();
      await context.setOffline(false);
      await expect(page.getByText("Will sync")).toHaveCount(0, { timeout: 30_000 });
      await settle(page);
      const w = await week(page);
      const done1 = w.days.flatMap((d) => d.items).filter((i) => i.status === "done");
      expect(done1.length).toBe(1);
      expect(errors.filter((e) => !/ERR_INTERNET_DISCONNECTED|Failed to load resource/.test(e))).toEqual([]);
    } finally {
      await done();
    }
  });
});

test.describe("calendar feed", () => {
  test("the ICS URL parses, carries no personal data, and stops working when turned off", async ({
    browser,
    baseURL,
  }, info) => {
    const { page, errors, guard, done } = await fresh(browser, baseURL as string, info, "pics", {
      plan: true,
      missed: 0,
    });
    try {
      await settle(page);
      await expect(page.getByRole("heading", { name: "Calendar feed" })).toBeVisible();
      await page.getByRole("button", { name: "Create calendar link" }).click();
      const field = page.locator("#feed-url");
      await expect(field).toBeVisible();
      const url = await field.inputValue();
      expect(url).toMatch(/\/api\/plan\/ics\/[A-Za-z0-9_-]{40,}$/);
      await expect(page.getByRole("link", { name: "Subscribe in Calendar" })).toHaveAttribute("href", /^webcal:\/\//);
      await settle(page);
      await expectNoAxeViolations(page);
      await snap(page, info, guard, "settings-feed", true);

      // A calendar app has no session: fetch without any credentials
      const noAuth = { headers: {} as Record<string, string> };
      const res = await page.request.get(url, noAuth);
      expect(res.status()).toBe(200);
      expect(res.headers()["content-type"]).toContain("text/calendar");
      expect(res.headers()["cache-control"]).toBe("private, max-age=900");
      const ics = await res.text();
      expect(ics.startsWith("BEGIN:VCALENDAR")).toBe(true);
      expect(ics).toContain("X-WR-CALNAME:Naadi study plan");
      const events = ics.split("BEGIN:VEVENT").length - 1;
      expect(events).toBeGreaterThan(10);
      const uids = [...ics.matchAll(/^UID:(.+)$/gm)].map((m) => m[1]);
      expect(new Set(uids).size).toBe(uids.length);
      expect(ics.toLowerCase()).not.toContain("@example.com");
      const again = await (await page.request.get(url, noAuth)).text();
      expect([...again.matchAll(/^UID:(.+)$/gm)].map((m) => m[1])).toEqual(uids);

      // Turn it off: the URL answers 404
      await page.getByRole("button", { name: "Turn off" }).click();
      await page.getByRole("button", { name: "Yes" }).click();
      await expect(page.getByText("The calendar link is off.")).toBeVisible();
      expect((await page.request.get(url, noAuth)).status()).toBe(404);
      expect(errors.filter((e) => !e.includes("404"))).toEqual([]);
    } finally {
      await done();
    }
  });
});

test.describe("push service worker", () => {
  test("the worker shows a notification for a push and opens the deep link on click", async ({ browser }, info) => {
    test.skip(
      info.project.name.startsWith("webkit") || info.project.name.startsWith("firefox"),
      "Chromium delivers synthetic push events",
    );
    const prod = process.env.E2E_PROD_URL;
    test.skip(!prod, "the service worker is registered by the production build only");
    const context = await browser.newContext({ ...info.project.use, baseURL: prod, ignoreHTTPSErrors: true });
    const page = await context.newPage();
    try {
      await page.goto("/sign-in");
      await expect
        .poll(
          async () => page.evaluate(() => navigator.serviceWorker.getRegistration().then((r) => Boolean(r?.active))),
          {
            timeout: 30_000,
          },
        )
        .toBe(true);
      const worker =
        context.serviceWorkers().find((w) => w.url().endsWith("/sw.js")) ??
        (await context.waitForEvent("serviceworker"));
      const payload = {
        k: "push.reminder_body",
        n: { items: 4, hours: 3.5 },
        title: "Naadi",
        body: "4 items to do today, about 3.5 h.",
        url: "/plan",
        tag: "naadi-reminder-test",
      };
      await worker.evaluate(async (p) => {
        const scope = self as unknown as ServiceWorkerGlobalScope;
        for (const n of await scope.registration.getNotifications()) n.close();
        const ev = new PushEvent("push", { data: JSON.stringify(p) });
        scope.dispatchEvent(ev);
        await new Promise((r) => setTimeout(r, 500));
      }, payload);
      const shown = await worker.evaluate(async () => {
        const list = await (self as unknown as ServiceWorkerGlobalScope).registration.getNotifications();
        return list.map((n) => ({
          title: n.title,
          body: n.body,
          tag: n.tag,
          url: (n.data as { url?: string } | null)?.url,
        }));
      });
      expect(shown).toEqual([{ title: "Naadi", body: payload.body, tag: payload.tag, url: "/plan" }]);
      // The payload carries numbers and a link, never note text
      expect(JSON.stringify(shown)).not.toMatch(/note|question/i);
    } finally {
      await context.close();
    }
  });
});
