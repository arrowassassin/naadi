import type { Browser, BrowserContext, Page, TestInfo } from "@playwright/test";
import { callbackPath, canMintUsers, runUsers, toLink } from "./auth-users";
import {
  type ConsoleGuard,
  expect,
  expectNoAxeViolations,
  expectNoHorizontalOverflow,
  snapSegment,
  test,
} from "./fixtures";

/**
 * Segment 10: flashcards against the real API (server-scheduled FSRS-6), the real database and auth server.
 * Each test signs in as its own throwaway student (deleted afterwards): cards and reviews are what is tested.
 */
test.skip(!canMintUsers(), "signed-in tests need the service role (run pnpm e2e:live-auth against the Space)");

const snap = (page: Page, info: TestInfo, guard: ConsoleGuard, state: string, full = false) =>
  guard.duringTool(async () => {
    await snapSegment(page, info, guard, "10", state, full);
    await page.waitForTimeout(300);
  });

function cardsEmail(info: TestInfo): string {
  const run = String.fromCharCode(97 + (info.repeatEachIndex % 26), 97 + (info.retry % 26));
  const role = `cards${info.project.name.replace(/[^a-z]/g, "")}${run}${info.title
    .replace(/[^a-z]/gi, "")
    .slice(0, 8)
    .toLowerCase()}`;
  return `naadi-e2e-${role}-${process.env.E2E_RUN_ID}@example.com`;
}

async function freshStudent(browser: Browser, baseURL: string, info: TestInfo) {
  const email = cardsEmail(info);
  runUsers(["delete", "--email", email]);
  const contexts: BrowserContext[] = [];
  const open = async (path = "/cards"): Promise<{ page: Page; context: BrowserContext }> => {
    const context = await browser.newContext({ ...info.project.use, baseURL });
    contexts.push(context);
    const page = await context.newPage();
    const link = toLink(runUsers(["link", "--email", email]));
    await page.goto(callbackPath(link, path));
    await page.waitForURL((u) => u.pathname === path, { timeout: 30_000 });
    return { page, context };
  };
  const done = async () => {
    try {
      for (const c of contexts) await c.close().catch(() => undefined);
    } finally {
      runUsers(["delete", "--email", email]);
    }
  };
  return { open, done };
}

/** Console errors of pages opened outside the fixture's own page, with the fixture's rule for the screenshot tool. */
function watchConsole(page: Page) {
  const errors: string[] = [];
  let tool = 0;
  page.on("console", (m) => {
    if (m.type() === "error" && !(tool > 0 && m.text().startsWith("Refused to apply a stylesheet"))) {
      errors.push(m.text());
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
  return { errors, guard };
}

/** Call the API from the page with the signed-in student's own token (what the app itself sends). */
async function api<T>(page: Page, method: string, url: string, body?: unknown): Promise<{ status: number; json: T }> {
  return page.evaluate(
    async ({ method, url, body }) => {
      const raw = localStorage.getItem("naadi.auth") ?? "{}";
      const token = (JSON.parse(raw) as { access_token?: string }).access_token ?? "";
      const res = await fetch(url, {
        method,
        headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      return { status: res.status, json: res.status === 204 ? null : ((await res.json()) as unknown) };
    },
    { method, url, body },
  ) as Promise<{ status: number; json: T }>;
}

/** Elements inside <main> painted with the lime role colour. */
async function limeCount(page: Page): Promise<number> {
  await page.mouse.move(1, 1); // a hovered lime button paints lime-hover
  return page.evaluate(() => {
    const lime = "rgb(198, 255, 61)";
    return Array.from(document.querySelectorAll<HTMLElement>("main *")).filter((el) => {
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0 && getComputedStyle(el).backgroundColor === lime;
    }).length;
  });
}

const loadAllFonts = (page: Page) =>
  page.evaluate(() => Promise.all([...document.fonts].map((f) => f.load().catch(() => f))).then(() => undefined));

interface ReviewOut {
  reviewed_at: string;
  scheduled_days: number;
  replayed: boolean;
  schedule: { state: number; due: string; reps: number };
}
interface Stats {
  due_today: number;
  reviewed_today: number;
  new: number;
  total: number;
  streak_days: number;
}

/** The interval in a rating button's label ("Good · 10 min") in milliseconds. */
function labelMs(label: string): number {
  const m = /·\s*([\d.]+)\s*(min|h|day|days|mo|yr)\b/.exec(label);
  if (!m) throw new Error(`no interval in ${label}`);
  const n = Number(m[1]);
  const unit = {
    min: 60_000,
    h: 3_600_000,
    day: 86_400_000,
    days: 86_400_000,
    mo: 30 * 86_400_000,
    yr: 365 * 86_400_000,
  }[m[2] as "min"];
  return n * unit;
}

const rating = (page: Page, name: string) => page.getByRole("button", { name: new RegExp(`^${name} · `) });

test.describe("review", () => {
  test("create a card, flip with Space, rate with 1-4, next due equals the preview, stats", async ({
    browser,
    baseURL,
  }, info) => {
    const student = await freshStudent(browser, baseURL as string, info);
    try {
      const { page } = await student.open();
      const { errors, guard } = watchConsole(page);

      // empty: one lime action (make a card)
      await expect(page.getByRole("heading", { level: 2, name: "No cards due" })).toBeVisible();
      await expect(page.getByText("You have no cards yet.")).toBeVisible();
      await expect.poll(() => limeCount(page)).toBe(1);
      await expectNoAxeViolations(page);
      await expectNoHorizontalOverflow(page);
      await snap(page, info, guard, "empty");

      // create: validation first, then a real card
      await page.getByRole("button", { name: "New card" }).click();
      const dialog = page.getByRole("dialog", { name: "New card" });
      await expect(dialog).toBeVisible();
      await dialog.getByRole("button", { name: "Save card" }).click();
      await expect(dialog.getByText("Write something on this side.")).toHaveCount(2);
      await snap(page, info, guard, "dialog-invalid");
      // shortcuts never fire inside the dialog: Space and 3 are typed into the fields
      await dialog.getByLabel("Front").press("Space");
      await dialog.getByLabel("Back").press("3");
      await expect(dialog.getByLabel("Front")).toHaveValue(" ");
      await expect(dialog.getByLabel("Back")).toHaveValue("3");
      await dialog.getByLabel("Front").fill("Which RTA causes hyperkalemia?");
      await dialog.getByLabel("Back").fill("Type 4 — too little aldosterone, or resistance to it.");
      await expectNoAxeViolations(page);
      await snap(page, info, guard, "dialog-new");
      await dialog.getByRole("button", { name: "Save card" }).click();
      await expect(dialog).toBeHidden();

      // front: progress, the card, one lime CTA
      const card = page.getByRole("region", { name: "Flashcard" });
      await expect(card.getByText("Which RTA causes hyperkalemia?")).toBeVisible();
      await expect(card.getByText("Question")).toBeVisible();
      await expect(page.getByRole("img", { name: "0 of 1 cards reviewed" })).toBeVisible();
      await expect(page.getByText("My cards · your card")).toBeVisible();
      await expect.poll(() => limeCount(page)).toBe(1); // Show answer
      await expectNoAxeViolations(page);
      await expectNoHorizontalOverflow(page);
      await loadAllFonts(page);
      await snap(page, info, guard, "front", true);

      // Space flips; the ratings show the scheduler's preview
      await page.locator("body").press("Space");
      await expect(card.getByText("Answer", { exact: true })).toBeVisible();
      await expect(card.getByText(/Type 4/)).toBeVisible();
      await expect(rating(page, "Again")).toHaveText("Again · 1 min");
      await expect(rating(page, "Hard")).toHaveText("Hard · 6 min");
      await expect(rating(page, "Good")).toHaveText("Good · 10 min");
      await expect(rating(page, "Easy")).toHaveText(/^Easy · \d+ days?$/);
      await expect.poll(() => limeCount(page)).toBe(1); // Easy
      await expectNoAxeViolations(page);
      await snap(page, info, guard, "back", true);

      // 3 = Good: the server's due is the preview's 10 minutes after the review
      const good = page.waitForResponse((r) => r.url().includes("/review") && r.request().method() === "POST");
      await page.locator("body").press("3");
      const goodOut = (await (await good).json()) as ReviewOut;
      expect(goodOut.replayed).toBe(false);
      expect(new Date(goodOut.schedule.due).getTime() - new Date(goodOut.reviewed_at).getTime()).toBe(600_000);

      // a learning step due within 20 minutes comes back in this session
      await expect(card.getByText("Question")).toBeVisible();
      await page.locator("body").press("Space");
      const easyLabel = (await rating(page, "Easy").textContent()) ?? "";
      const easy = page.waitForResponse((r) => r.url().includes("/review") && r.request().method() === "POST");
      await page.locator("body").press("4");
      const easyOut = (await (await easy).json()) as ReviewOut;
      expect(easyOut.schedule.state).toBe(2);
      expect(easyOut.scheduled_days * 86_400_000).toBe(labelMs(easyLabel));

      // done
      await expect(page.getByRole("heading", { level: 2, name: "All caught up" })).toBeVisible();
      await expect(page.getByText("You reviewed 1 card this session.")).toBeVisible();
      await expect(page.getByText(/Next card due /)).toBeVisible();
      await expect.poll(() => limeCount(page)).toBe(1);
      await expectNoAxeViolations(page);
      await snap(page, info, guard, "done");

      const stats = await api<Stats>(page, "GET", `/api/cards/stats?tz=${encodeURIComponent("Asia/Kolkata")}`);
      expect(stats.status).toBe(200);
      expect(stats.json).toMatchObject({ total: 1, new: 0, reviewed_today: 2, streak_days: 1 });
      expect(errors).toEqual([]);
    } finally {
      await student.done();
    }
  });

  test("edit and delete a card from the review screen", async ({ browser, baseURL }, info) => {
    const student = await freshStudent(browser, baseURL as string, info);
    try {
      const { page } = await student.open();
      const { errors, guard } = watchConsole(page);
      await expect(page.getByRole("heading", { level: 2, name: "No cards due" })).toBeVisible();
      for (const front of ["First card", "Second card"]) {
        const res = await api(page, "POST", "/api/cards", { front_text: front, back_text: "Back" });
        expect(res.status).toBe(201);
      }
      await page.reload();
      const card = page.getByRole("region", { name: "Flashcard" });
      await expect(card.getByText("First card")).toBeVisible();
      await expect(page.getByRole("img", { name: "0 of 2 cards reviewed" })).toBeVisible();

      await page.getByRole("button", { name: "Edit card" }).click();
      const dialog = page.getByRole("dialog", { name: "Edit card" });
      await expect(dialog.getByLabel("Front")).toHaveValue("First card");
      await dialog.getByLabel("Front").fill("First card, edited");
      await snap(page, info, guard, "dialog-edit");
      await dialog.getByRole("button", { name: "Save card" }).click();
      await expect(dialog).toBeHidden();
      await expect(card.getByText("First card, edited")).toBeVisible();
      await expect(page.getByRole("status").filter({ hasText: "Card saved" })).toBeVisible();

      await page.getByRole("button", { name: "Edit card" }).click();
      await dialog.getByRole("button", { name: "Delete card" }).click();
      await expect(dialog.getByText(/Delete this card and its review history/)).toBeVisible();
      await snap(page, info, guard, "dialog-delete");
      await dialog.getByRole("button", { name: "Delete", exact: true }).click();
      await expect(dialog).toBeHidden();
      await expect(card.getByText("Second card")).toBeVisible();
      await expect(page.getByRole("img", { name: "0 of 1 cards reviewed" })).toBeVisible();
      const due = await api<{ cards: { front_text: string }[] }>(page, "GET", "/api/cards/due");
      expect(due.json.cards.map((c) => c.front_text)).toEqual(["Second card"]);
      expect(errors).toEqual([]);
    } finally {
      await student.done();
    }
  });

  test("offline: review continues on the saved cards, ratings sync when back online", async ({
    browser,
    baseURL,
  }, info) => {
    const student = await freshStudent(browser, baseURL as string, info);
    try {
      const { page, context } = await student.open();
      const { errors, guard } = watchConsole(page);
      await expect(page.getByRole("heading", { level: 2, name: "No cards due" })).toBeVisible();
      await api(page, "POST", "/api/cards", { front_text: "Offline card", back_text: "Still works" });
      await page.reload();
      const card = page.getByRole("region", { name: "Flashcard" });
      await expect(card.getByText("Offline card")).toBeVisible();
      await loadAllFonts(page);
      await page.waitForLoadState("networkidle");

      await context.setOffline(true);
      await page.evaluate(() => window.dispatchEvent(new Event("offline")));
      await expect(page.getByText(/Reviewing the cards saved on this device/)).toBeVisible();
      await expect(page.getByRole("button", { name: "New card" })).toBeDisabled();
      await expect(page.getByRole("button", { name: "Edit card" })).toBeDisabled();
      await page.locator("body").press("Space");
      await expect(rating(page, "Good")).toHaveText("Good · 10 min");
      await page.locator("body").press("3");
      await expect(page.getByText("1 rating will sync")).toBeVisible();
      await snap(page, info, guard, "offline-pending", true);

      await context.setOffline(false);
      await page.evaluate(() => window.dispatchEvent(new Event("online")));
      await expect(page.getByText("1 rating will sync")).toBeHidden();
      await expect
        .poll(async () => (await api<Stats>(page, "GET", "/api/cards/stats?tz=UTC")).json.reviewed_today)
        .toBe(1);
      expect(
        errors.filter((e) => !/Failed to load resource|ERR_INTERNET_DISCONNECTED|NetworkError|Load failed/.test(e)),
      ).toEqual([]);
    } finally {
      await student.done();
    }
  });

  test("a load failure shows the error with its request id and retries", async ({ browser, baseURL }, info) => {
    const student = await freshStudent(browser, baseURL as string, info);
    try {
      const { page } = await student.open();
      const { errors, guard } = watchConsole(page);
      await expect(page.getByRole("heading", { level: 2, name: "No cards due" })).toBeVisible();
      let fail = true;
      await page.route("**/api/cards/due**", async (route) => {
        if (!fail) return route.continue();
        await route.fulfill({
          status: 503,
          contentType: "application/json",
          headers: { "x-request-id": "e2e-cards-503" },
          body: JSON.stringify({
            error: { code: "cards_unavailable", message: "cards_unavailable", request_id: "e2e-cards-503" },
          }),
        });
      });
      await loadAllFonts(page);
      await page.reload();
      await expect(page.getByText("Your cards could not be loaded")).toBeVisible();
      await expect(page.getByText("Flashcards cannot be reached right now.")).toBeVisible();
      await expect(page.getByText("Request ID e2e-cards-503")).toBeVisible();
      await expectNoAxeViolations(page);
      await snap(page, info, guard, "error");
      fail = false;
      await page.getByRole("button", { name: "Try again" }).click();
      await expect(page.getByRole("heading", { level: 2, name: "No cards due" })).toBeVisible();
      expect(errors.filter((e) => !/503/.test(e))).toEqual([]);
    } finally {
      await student.done();
    }
  });
});

test.describe("production server (strict CSP)", () => {
  const PROD = process.env.E2E_PROD_URL;
  test.skip(!PROD, "needs the local APP_ENV=prod API (not available against the live Space)");

  test("review on APP_ENV=prod with no console errors", async ({ browser, baseURL }, info) => {
    const student = await freshStudent(browser, baseURL as string, info);
    try {
      const { context } = await student.open();
      // Move the session to the https prod origin (see ink-gate.spec: signing in there would go through
      // upgrade-insecure-requests to the http local auth stack).
      const origin = new URL(PROD as string).origin;
      const state = await context.storageState();
      const prod = await browser.newContext({
        ...info.project.use,
        baseURL: PROD,
        ignoreHTTPSErrors: true,
        storageState: { cookies: [], origins: state.origins.map((o) => ({ ...o, origin })) },
      });
      try {
        const page = await prod.newPage();
        const { errors } = watchConsole(page);
        await page.goto("/cards");
        await expect(page.getByRole("heading", { level: 2, name: "No cards due" })).toBeVisible();
        expect((await api(page, "POST", "/api/cards", { front_text: "CSP card", back_text: "Strict" })).status).toBe(
          201,
        );
        await page.reload();
        await expect(page.getByRole("region", { name: "Flashcard" }).getByText("CSP card")).toBeVisible();
        await page.locator("body").press("Space");
        await expect(rating(page, "Easy")).toBeVisible();
        const reviewed = page.waitForResponse((r) => r.url().includes("/review"));
        await page.locator("body").press("4");
        expect((await reviewed).status()).toBe(200);
        await expect(page.getByRole("heading", { level: 2, name: "All caught up" })).toBeVisible();
        expect(errors).toEqual([]);
      } finally {
        await prod.close();
      }
    } finally {
      await student.done();
    }
  });
});
