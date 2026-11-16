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
 * Segment 09: timed and exam blocks on the server's clock, against the real API, the seeded mock exams
 * (Mock A: 2 x 40 questions, 60 minutes per block) and the real auth server. Each test signs in as its own
 * throwaway student, because blocks, sections and attempts are the thing under test.
 */
test.skip(!canMintUsers(), "signed-in tests need the service role (run pnpm e2e:live-auth against the Space)");

const LEAK = /explanation_html|why_not_html|answer_html|educational_objective|"answer"/;

const snap = (page: Page, info: TestInfo, guard: ConsoleGuard, state: string, full = false) =>
  guard.duringTool(async () => {
    await snapSegment(page, info, guard, "09", state, full);
    await page.waitForTimeout(300);
  });

function studentEmail(info: TestInfo): string {
  const run = String.fromCharCode(97 + (info.repeatEachIndex % 26), 97 + (info.retry % 26));
  const role = `exam${info.project.name.replace(/[^a-z]/g, "")}${run}${info.title
    .replace(/[^a-z]/gi, "")
    .slice(0, 8)
    .toLowerCase()}`;
  return `naadi-e2e-${role}-${process.env.E2E_RUN_ID}@example.com`;
}

async function freshStudent(browser: Browser, baseURL: string, info: TestInfo) {
  const email = studentEmail(info);
  runUsers(["delete", "--email", email]);
  const contexts: BrowserContext[] = [];
  const open = async (path = "/practice", before?: (ctx: BrowserContext) => Promise<void>) => {
    const context = await browser.newContext({ ...info.project.use, baseURL });
    contexts.push(context);
    await before?.(context);
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

/** Console errors of pages opened outside the fixture's own page, with the fixture's screenshot rule. */
function watchConsole(page: Page) {
  const errors: string[] = [];
  let tool = 0;
  page.on("console", (m) => {
    if (m.type() !== "error") return;
    const text = m.text();
    if (tool > 0 && text.startsWith("Refused to apply a stylesheet")) return;
    // the exam's own 409 probes (pause in exam mode) are expected responses, logged by the browser
    if (/409 \(Conflict\)|status of 409/.test(text)) return;
    errors.push(text);
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

/** Call the API from the page with the student's own token (what the app sends). */
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
      return { status: res.status, json: (await res.json()) as unknown };
    },
    { method, url, body },
  ) as Promise<{ status: number; json: T }>;
}

interface Clock {
  server_now: string;
  state: string;
  section_n: number | null;
  deadline_at: string | null;
  remaining_ms: number | null;
  break_remaining_ms: number | null;
}
interface Block {
  id: string;
  total: number;
  config: { mode: string; mock: string | null; sections: { n: number; count: number; time_limit_s: number }[] };
  clock: Clock;
}

/** Visible elements in <main> painted with the lime role colour (one next action per view). */
async function limeCount(page: Page): Promise<number> {
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
const isPhone = (page: Page) => (page.viewportSize()?.width ?? 1000) < 640;
const isLaptop = (page: Page) => (page.viewportSize()?.width ?? 0) > 1100;

function seconds(text: string): number {
  const parts = text.trim().split(":").map(Number);
  return parts.reduce((acc, p) => acc * 60 + p, 0);
}

/**
 * The displayed time left against the server's: the page reads its timer and the clock endpoint in one go
 * and corrects for the request's own duration. The display shows the nearest whole second, so it is never more
 * than 1 s away.
 */
async function drift(page: Page, blockId: string): Promise<number> {
  const out = await page.evaluate(async (id) => {
    const raw = localStorage.getItem("naadi.auth") ?? "{}";
    const token = (JSON.parse(raw) as { access_token?: string }).access_token ?? "";
    const t0 = performance.now();
    const res = await fetch(`/api/blocks/${id}/clock`, { headers: { authorization: `Bearer ${token}` } });
    const c = (await res.json()) as { remaining_ms: number };
    const t1 = performance.now();
    const shown = document.querySelector('[role="timer"] [aria-hidden="true"]')?.textContent ?? "";
    return { remaining: c.remaining_ms - (t1 - t0) / 2, shown };
  }, blockId);
  return seconds(out.shown) - out.remaining / 1000;
}

async function expectTimerAccurate(page: Page, blockId: string) {
  await expect(page.getByRole("timer")).toBeVisible();
  const d = await drift(page, blockId);
  expect(Math.abs(d), `timer drift ${d.toFixed(3)} s`).toBeLessThanOrEqual(1);
}

async function startMock(page: Page): Promise<string> {
  const made = await api<Block>(page, "POST", "/api/blocks/mock", { chapter_id: "mock1" });
  expect(made.status).toBe(201);
  return made.json.id;
}

test.describe("mock exam A", () => {
  test("runs as a 2 x 40 exam: start, 40 answers, end block, break, block 2, results", async ({
    browser,
    baseURL,
  }, info) => {
    test.setTimeout(300_000);
    const student = await freshStudent(browser, baseURL as string, info);
    try {
      const { page } = await student.open();
      const { errors, guard } = watchConsole(page);
      // the Builder's Mock exams group (Step 1 by default) starts Mock A; Start block stays the one lime CTA
      const mockButton = page.getByRole("button", { name: /Mock Exam A/ });
      await mockButton.scrollIntoViewIfNeeded();
      await expect(mockButton).toBeVisible();
      await snap(page, info, guard, "builder-mock-group");
      await mockButton.click();
      await page.waitForURL(/\/practice\/blocks\/[0-9a-f-]{36}\/1$/);
      const blockId = /blocks\/([0-9a-f-]{36})/.exec(page.url())?.[1] ?? "";
      const created = await api<Block>(page, "GET", `/api/blocks/${blockId}`);
      expect(created.json.config.mode).toBe("exam");
      expect(created.json.config.sections.map((s) => [s.count, s.time_limit_s])).toEqual([
        [40, 3600],
        [40, 3600],
      ]);

      await expect(page.getByRole("heading", { level: 1, name: "Ready when you are." })).toBeVisible();
      await expect(page.getByText("Pause is off in exam mode, as on test day.")).toBeVisible();
      await expectNoAxeViolations(page);
      await expectNoHorizontalOverflow(page);
      await snap(page, info, guard, "exam-start");

      // leak probe before the start: no question is served at all
      const early = await api<{ error: { code: string } }>(page, "GET", `/api/blocks/${blockId}/questions/1`);
      expect(early.status).toBe(409);
      expect(early.json.error.code).toBe("not_started");

      await page.getByRole("button", { name: "Start block 1" }).click();
      await expect(page.getByRole("timer")).toBeVisible();
      await expect(page.getByText(isPhone(page) ? "1 / 40" : "Block 1 of 2 · Question 1 of 40")).toBeVisible();
      await expectTimerAccurate(page, blockId);
      if (isLaptop(page)) await expect(page.getByText("Pause is off in exam mode", { exact: true })).toBeVisible();
      if (!isPhone(page)) await expect(page.getByText("The assistant is off during exam mode.")).toBeVisible();

      // pause is refused by the server in exam mode
      const pause = await api<{ error: { code: string } }>(page, "POST", `/api/blocks/${blockId}/sections/1/pause`);
      expect(pause.status).toBe(409);
      expect(pause.json.error.code).toBe("pause_exam_mode");

      // Ctrl/Cmd+K does not open Search during the exam
      await page.keyboard.press("ControlOrMeta+k");
      await page.waitForTimeout(300);
      expect(new URL(page.url()).pathname).toBe(`/practice/blocks/${blockId}/1`);

      await expectNoAxeViolations(page);
      await expectNoHorizontalOverflow(page);
      await snap(page, info, guard, "exam-question");

      // 40 answers with the keyboard (B, then N), the first one changed (A -> B)
      await expect(page.getByRole("group", { name: "Answer options" }).getByRole("button")).toHaveCount(5);
      await page.keyboard.press("a");
      await expect(page.getByRole("button", { name: /^A, your answer/ })).toHaveAttribute("aria-pressed", "true");
      for (let i = 1; i <= 40; i++) {
        // the new question is on screen (five options, none chosen yet, except question 1 chosen above)
        const options = page.getByRole("group", { name: "Answer options" }).getByRole("button");
        await expect(options).toHaveCount(5);
        if (i > 1) await expect(page.getByRole("button", { pressed: true, name: /your answer/ })).toHaveCount(0);
        const sent = page.waitForResponse((r) => r.url().endsWith(`/api/blocks/${blockId}/attempts`), {
          timeout: 20_000,
        });
        await page.keyboard.press("b");
        expect((await sent).status()).toBe(200);
        await expect(page.getByRole("button", { name: /^B, your answer/ })).toHaveAttribute("aria-pressed", "true");
        if (i === 7) {
          await page.keyboard.press("f"); // flag question 7
          await expect(page.getByRole("button", { name: /Remove flag|Flagged/ }).first()).toBeVisible();
        }
        if (i < 40) {
          await page.keyboard.press("n");
          await page.waitForURL(new RegExp(`/practice/blocks/${blockId}/${i + 1}$`));
        }
      }
      // the leak probe while the block runs: answers are recorded, never graded or explained
      const probe = await api<unknown>(page, "GET", `/api/blocks/${blockId}/questions/40`);
      expect(probe.status).toBe(200);
      expect(JSON.stringify(probe.json)).not.toMatch(LEAK);
      const detail = await api<{ answered: number; correct: unknown }>(page, "GET", `/api/blocks/${blockId}`);
      expect(detail.json.answered).toBe(40);
      expect(detail.json.correct).toBeNull();
      expect(JSON.stringify(detail.json)).not.toMatch(LEAK);

      if (!isPhone(page)) {
        const nav = page.getByRole("navigation", { name: "Questions in this block" });
        await expect(nav.getByRole("link", { name: "Question 7, answered, flagged" })).toBeVisible();
        await expect(nav.getByRole("link", { name: "Question 40, current" })).toHaveAttribute("aria-current", "page");
        await snap(page, info, guard, "exam-navigator");
      }

      await page.getByRole("button", { name: "End block" }).first().click();
      const dialog = page.getByRole("dialog", { name: "End this block?" });
      await expect(dialog).toBeVisible();
      await snap(page, info, guard, "exam-end-confirm");
      await dialog.getByRole("button", { name: "End block" }).click();

      await expect(page.getByRole("heading", { level: 1, name: "Block 1 of 2 done." })).toBeVisible();
      await expect(page.getByRole("timer")).toBeVisible();
      const clock = await api<Clock>(page, "GET", `/api/blocks/${blockId}/clock`);
      expect(clock.json.state).toBe("break");
      expect(clock.json.break_remaining_ms).toBeGreaterThan(44 * 60_000);
      // block 1 is closed: no going back
      const back = await api<{ error: { code: string } }>(page, "GET", `/api/blocks/${blockId}/questions/1`);
      expect(back.json.error.code).toBe("section_ended");
      await expectNoAxeViolations(page);
      await expectNoHorizontalOverflow(page);
      await snap(page, info, guard, "exam-break");

      await page.getByRole("button", { name: "Start block 2" }).click();
      await page.waitForURL(new RegExp(`/practice/blocks/${blockId}/41$`));
      await expect(page.getByText(isPhone(page) ? "1 / 40" : "Block 2 of 2 · Question 1 of 40")).toBeVisible();
      await expectTimerAccurate(page, blockId);
      await page.keyboard.press("c");
      await expect(page.getByRole("button", { name: /^C, your answer/ })).toHaveAttribute("aria-pressed", "true");

      // the last block ends the exam: graded on the server, results follow
      await page.getByRole("button", { name: "End block" }).first().click();
      const last = page.getByRole("dialog", { name: "End the exam?" });
      await expect(last.getByText("Unanswered: 39")).toBeVisible();
      await last.getByRole("button", { name: "End and see results" }).click();
      await page.waitForURL(new RegExp(`/practice/blocks/${blockId}/results$`));
      await expect(page.getByRole("heading", { level: 1 })).toContainText("%");
      const results = await api<{ total: number; omitted: number; changes: { changed: number } }>(
        page,
        "GET",
        `/api/blocks/${blockId}/results`,
      );
      expect(results.status).toBe(200);
      expect(results.json.total).toBe(80);
      expect(results.json.omitted).toBe(39);
      expect(results.json.changes.changed).toBe(1);
      // only now do explanations unlock
      const review = await api<{ explanation: unknown }>(page, "GET", `/api/blocks/${blockId}/questions/1`);
      expect(review.json.explanation).not.toBeNull();

      // Results (board): one lime CTA, weak topics in words, no axe violations; then review of the misses
      await expect(page.getByText(/ of 80\./).first()).toBeVisible();
      expect(await limeCount(page)).toBe(1);
      await expectNoAxeViolations(page);
      await expectNoHorizontalOverflow(page);
      await snap(page, info, guard, "results", true);
      await page.getByRole("link", { name: /^Review \d+ miss/ }).click();
      await page.waitForURL(new RegExp(`/practice/blocks/${blockId}/review/\\d+`));
      await expect(page.getByText("Correct answer").first()).toBeVisible();
      expect(await limeCount(page)).toBe(1);
      await expectNoAxeViolations(page);
      await expectNoHorizontalOverflow(page);
      await snap(page, info, guard, "review");
      await loadAllFonts(page);
      expect(errors).toEqual([]);
    } finally {
      await student.done();
    }
  });
});

test.describe("timed block", () => {
  test("pauses and resumes; an answer made offline syncs; a lost connection keeps the server's time", async ({
    browser,
    baseURL,
  }, info) => {
    test.setTimeout(150_000);
    const student = await freshStudent(browser, baseURL as string, info);
    try {
      const { page, context } = await student.open();
      const { errors, guard } = watchConsole(page);
      const made = await api<Block>(page, "POST", "/api/blocks", { exam: "step1", count: 5, mode: "timed", seed: 9 });
      expect(made.status).toBe(201);
      const blockId = made.json.id;
      expect(made.json.config.sections.map((s) => s.time_limit_s)).toEqual([450]);
      await page.goto(`/practice/blocks/${blockId}/1`);
      await page.getByRole("button", { name: "Start the block" }).click();
      await expect(page.getByRole("group", { name: "Answer options" }).getByRole("button")).toHaveCount(5);
      await expectTimerAccurate(page, blockId);

      // pause: the questions hide, the time freezes, the server refuses answers
      await page.getByRole("button", { name: "Pause" }).click();
      await expect(page.getByRole("heading", { level: 1, name: "The block is paused." })).toBeVisible();
      await expect(page.getByRole("group", { name: "Answer options" })).toHaveCount(0);
      const paused = await api<{ error: { code: string } }>(page, "GET", `/api/blocks/${blockId}/questions/1`);
      expect(paused.json.error.code).toBe("paused");
      await expectNoAxeViolations(page);
      await expectNoHorizontalOverflow(page);
      await snap(page, info, guard, "timed-paused");
      await page.getByRole("button", { name: "Resume" }).click();
      await expect(page.getByRole("group", { name: "Answer options" }).getByRole("button")).toHaveCount(5);

      // offline: the answer is kept on this device and sent when the connection returns
      await loadAllFonts(page);
      await page.waitForLoadState("networkidle");
      await context.setOffline(true);
      await page.keyboard.press("c");
      await expect(page.getByText(/Saved on this device/)).toBeVisible();
      await page.evaluate(() => window.dispatchEvent(new Event("focus"))); // a re-sync attempt fails offline
      await expect(page.getByText("Connection lost. The timer keeps running on the server's time.")).toBeVisible();
      await expect(page.getByRole("timer")).toBeVisible();
      await snap(page, info, guard, "timed-offline");
      const sent = page.waitForResponse((r) => r.url().endsWith(`/api/blocks/${blockId}/attempts`));
      await context.setOffline(false);
      expect((await sent).status()).toBe(200);
      const detail = await api<{ answered: number }>(page, "GET", `/api/blocks/${blockId}`);
      expect(detail.json.answered).toBe(1);
      await expect(page.getByText(/Saved on this device/)).toHaveCount(0);
      expect(
        errors.filter(
          (e) =>
            !/Failed to fetch|Load failed|NetworkError|ERR_INTERNET_DISCONNECTED|WebKit encountered an internal error/.test(
              e,
            ),
        ),
      ).toEqual([]);
    } finally {
      await student.done();
    }
  });
});

test.describe("timer integrity", () => {
  test("stays within 1 s of the server after a reload and a 30 s background", async ({ browser, baseURL }, info) => {
    test.setTimeout(150_000);
    const student = await freshStudent(browser, baseURL as string, info);
    try {
      const { page, context } = await student.open();
      const { errors } = watchConsole(page);
      const blockId = await startMock(page);
      expect((await api(page, "POST", `/api/blocks/${blockId}/sections/1/start`)).status).toBe(200);
      await page.goto(`/practice/blocks/${blockId}/1`);
      await expectTimerAccurate(page, blockId);

      await loadAllFonts(page);
      await page.reload();
      await expectTimerAccurate(page, blockId);

      // 30 s in the background: frozen page lifecycle in Chromium, a hidden document elsewhere
      if (info.project.use.browserName === "chromium") {
        const cdp = await context.newCDPSession(page);
        await cdp.send("Page.setWebLifecycleState", { state: "frozen" });
        await page.waitForTimeout(30_000);
        await cdp.send("Page.setWebLifecycleState", { state: "active" });
      } else {
        await page.evaluate(() => {
          Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "hidden" });
          document.dispatchEvent(new Event("visibilitychange"));
        });
        await page.waitForTimeout(30_000);
        await page.evaluate(() => {
          Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "visible" });
          document.dispatchEvent(new Event("visibilitychange"));
        });
      }
      await page.waitForTimeout(1_000);
      await expectTimerAccurate(page, blockId);
      expect(errors).toEqual([]);
    } finally {
      await student.done();
    }
  });

  test("shows the server's time when the device clock is 10 minutes off", async ({ browser, baseURL }, info) => {
    test.setTimeout(120_000);
    const student = await freshStudent(browser, baseURL as string, info);
    try {
      const { page } = await student.open();
      const blockId = await startMock(page);
      expect((await api(page, "POST", `/api/blocks/${blockId}/sections/1/start`)).status).toBe(200);
      for (const skew of [10 * 60_000, -10 * 60_000]) {
        const { page: skewed } = await student.open("/practice");
        await skewed.waitForLoadState("networkidle"); // WebKit reports a fetch cut off by navigation as an error
        await skewed.clock.install({ time: Date.now() + skew });
        await skewed.clock.resume();
        const { errors } = watchConsole(skewed);
        await skewed.goto(`/practice/blocks/${blockId}/1`);
        await expect(skewed.getByRole("timer")).toBeVisible();
        // the first sample may come from a cold start: let the 60 s re-sync logic take its best sample
        await skewed.waitForTimeout(1_500);
        const d = await drift(skewed, blockId);
        expect(Math.abs(d), `drift with ${skew / 60_000} min skew: ${d.toFixed(3)} s`).toBeLessThanOrEqual(1);
        expect(errors).toEqual([]);
      }
    } finally {
      await student.done();
    }
  });
});

test.describe("exam view on a production server", () => {
  const PROD = process.env.E2E_PROD_URL;
  test.skip(!PROD, "needs the local APP_ENV=prod API");

  test("runs under the production CSP with no console errors", async ({ browser, baseURL }, info) => {
    test.setTimeout(120_000);
    const student = await freshStudent(browser, baseURL as string, info);
    try {
      const { page, context } = await student.open();
      const blockId = await startMock(page);
      const state = await context.storageState();
      const origin = new URL(PROD as string).origin;
      const prod = await browser.newContext({
        ...info.project.use,
        baseURL: PROD,
        ignoreHTTPSErrors: true,
        storageState: { cookies: [], origins: state.origins.map((o) => ({ ...o, origin })) },
      });
      try {
        const p = await prod.newPage();
        const { errors } = watchConsole(p);
        const violations: string[] = [];
        p.on("console", (m) => {
          if (/Content Security Policy|Refused to/.test(m.text())) violations.push(m.text());
        });
        await p.goto(`/practice/blocks/${blockId}/1`);
        await expect(p.getByRole("heading", { level: 1, name: "Ready when you are." })).toBeVisible();
        await p.getByRole("button", { name: "Start block 1" }).click();
        await expect(p.getByRole("timer")).toBeVisible();
        await expect(p.getByRole("group", { name: "Answer options" }).getByRole("button")).toHaveCount(5);
        await p.keyboard.press("d");
        await expect(p.getByRole("button", { name: /^D, your answer/ })).toHaveAttribute("aria-pressed", "true");
        await loadAllFonts(p);
        expect(violations).toEqual([]);
        expect(errors).toEqual([]);
      } finally {
        await prod.close();
      }
    } finally {
      await student.done();
    }
  });
});
