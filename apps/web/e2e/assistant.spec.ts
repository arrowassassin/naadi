import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import type { Browser, BrowserContext, Locator, Page, Route, TestInfo } from "@playwright/test";
import { callbackPath, canMintUsers, runUsers, toLink } from "./auth-users";
import {
  type ConsoleGuard,
  expect,
  expectNoAxeViolations,
  expectNoHorizontalOverflow,
  quiet,
  settle,
  snapSegment,
  test,
  trackApi,
} from "./fixtures";
import { ensureOnboarded, signedInState } from "./session";

/**
 * Segment 14: the tutor assistant against the real API, the real Aegra runtime (graph `tutor`) and real Supabase
 * sessions. The e2e server answers with the scripted fake chat model (keyword in the student's message):
 * - "e2e:answer" calls search_knowledge, then streams a short answer (tokens + a sources block);
 * - "e2e:save" calls save_note (an interrupt: Accept writes the note, Cancel writes nothing);
 * - "e2e:canvas" calls open_in_canvas(kind "document").
 * Gated like agent.spec.ts: the runtime needs a local database and throwaway users.
 */
const hasRuntime = Boolean(process.env.E2E_DATABASE_URL ?? process.env.DATABASE_URL) && !process.env.E2E_BASE_URL;
test.skip(!canMintUsers() || !hasRuntime, "the tutor runtime needs a local database and throwaway users");

const STREAM_TIMEOUT = 60_000;

/**
 * A student of this test's own, onboarded and deleted afterwards: projects, workers and repeats run in parallel, and
 * one shared student would share one per-token run limit and one set of notes (review14 r2 #3). Where users cannot
 * be minted (a deployed target), the shared student.
 */
const asOwnStudent = {
  storageState: async (
    { browser, baseURL }: { browser: Browser; baseURL: string | undefined },
    use: (p: string) => Promise<void>,
    info: TestInfo,
  ) => {
    if (!canMintUsers()) return use(await signedInState(browser, baseURL as string, "student"));
    const slug = createHash("sha1")
      .update(`${info.titlePath.join("/")}|${info.project.name}`)
      .digest("hex");
    // naadi-e2e-<letters>-<run>: the helper only accepts that test-address shape.
    const email = `naadi-e2e-tutorown-${slug.slice(0, 10)}r${info.repeatEachIndex}t${info.retry}-${process.env.E2E_RUN_ID}@example.com`;
    runUsers(["delete", "--email", email]);
    const context = await browser.newContext({ baseURL });
    try {
      const page = await context.newPage();
      await page.goto(callbackPath(toLink(runUsers(["link", "--email", email])), "/settings/account"));
      await page.waitForURL((u) => u.pathname === "/settings/account", { timeout: 30_000 });
      await ensureOnboarded(page);
      const file = info.outputPath("student-state.json");
      await context.storageState({ path: file });
      await context.close();
      await use(file);
    } finally {
      await context.close().catch(() => undefined);
      runUsers(["delete", "--email", email]);
    }
  },
};

const snap = (page: Page, info: TestInfo, guard: ConsoleGuard, state: string) =>
  snapSegment(page, info, guard, "14", state);

const isPhone = (page: Page) => (page.viewportSize()?.width ?? 1024) < 640;

async function go(page: Page, url: string): Promise<void> {
  await quiet(page);
  await page.goto(url);
  await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
  await settle(page);
}

/** The first chapter section a student can read (the Courses page's single "Start/Continue/Next" link). */
async function openFirstSection(page: Page): Promise<void> {
  await go(page, "/learn");
  await page.getByRole("link", { name: /^(Start|Continue|Next): / }).click();
  await expect(page).toHaveURL(/\/learn\/[^/]+\/[^/]+$/);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await settle(page);
}

/** The visible "Ask Naadi" input: the Dock (tablet and up) or, on phone, the full-screen sheet. */
async function input(page: Page): Promise<Locator> {
  if (isPhone(page) && !(await page.locator('[data-assistant="fullscreen"]').isVisible())) {
    await page.locator('[data-assistant="tab"]').click();
    await expect(page.locator('[data-assistant="fullscreen"]')).toBeVisible();
  }
  const field = page.getByRole("textbox", { name: "Ask Naadi" }).filter({ visible: true }).first();
  await expect(field).toBeEnabled();
  return field;
}

async function ask(page: Page, text: string): Promise<void> {
  const field = await input(page);
  await field.fill(text);
  await field.press("Enter");
}

/** The open conversation: the side panel, the bottom sheet or the full-screen sheet, by viewport. */
const conversation = (page: Page) =>
  page.locator('[data-assistant="panel"], [data-assistant="sheet"], [data-assistant="fullscreen"]');

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
      const text = await res.text();
      return { status: res.status, json: (text ? JSON.parse(text) : null) as unknown };
    },
    { method, url, body },
  ) as Promise<{ status: number; json: T }>;
}

/** The student's notes (S13 notes API): the count is what a confirmed save_note changes. */
/** How many times the fake model's note text is stored across the student's notes (a save appends to the
 * section's page note when one exists, so counting notes would miss it). */
async function noteCount(page: Page, marker = "Winters' formula"): Promise<number> {
  const res = await api<{ items: { body_text?: string | null }[] }>(page, "GET", "/api/notes?limit=200");
  expect(res.status, "GET /api/notes").toBe(200);
  return res.json.items.reduce((n, item) => n + ((item.body_text ?? "").split(marker).length - 1), 0);
}

test.describe("tutor assistant", () => {
  test.use(asOwnStudent);

  test("ask on a section: thinking, tokens, then the sources block stream in", async ({
    page,
    consoleErrors,
  }, info) => {
    trackApi(page);
    await openFirstSection(page);
    await expectNoHorizontalOverflow(page);
    await ask(page, "e2e:answer What does the anion gap tell me?");
    const panel = conversation(page);
    await expect(panel).toBeVisible();
    // the student's message shows at once; the answer streams after the knowledge search
    await expect(panel.locator('[data-message-role="user"]').last()).toContainText("What does the anion gap tell me?");
    const answer = panel.locator('[data-message-role="assistant"]').last();
    await expect(answer).not.toBeEmpty({ timeout: STREAM_TIMEOUT });
    await expect(answer.getByRole("region", { name: "Sources" })).toBeVisible({ timeout: STREAM_TIMEOUT });
    await expect(panel.locator('[data-status="thinking"], [data-status="tool"]')).toHaveCount(0, {
      timeout: STREAM_TIMEOUT,
    });
    // each source once (the tool's citations and the answer's final ones render under one message)
    const sourceNames = await answer.getByRole("region", { name: "Sources" }).locator("a, button").allInnerTexts();
    expect(sourceNames.length).toBeGreaterThan(0);
    expect(new Set(sourceNames).size, sourceNames.join(" | ")).toBe(sourceNames.length);
    // every word comes from ui_copy; the one lime action of the view is Send (disabled while the input is empty)
    await expect(panel.getByRole("button", { name: "Send" })).toBeDisabled();
    await expectNoAxeViolations(page);
    await expectNoHorizontalOverflow(page);
    await snap(page, info, consoleErrors, "answer-with-sources");

    // §6.5: the first answer of a new thread carries the disclaimer, once, under the answer that used a tool
    // (assistant-ui renders the tool call and the final answer as one message); the answer's sources stay with it.
    await expect(answer.locator('[data-notice="disclaimer"]')).toHaveCount(1);
    await expect(panel.locator('[data-notice="disclaimer"]')).toHaveCount(1);
    await ask(page, "e2e:answer And what about the osmolal gap?");
    const second = panel.locator('[data-message-role="assistant"]').nth(1);
    await expect(second.getByRole("region", { name: "Sources" })).toBeVisible({ timeout: STREAM_TIMEOUT });
    await expect(panel.locator('[data-status="thinking"], [data-status="tool"]')).toHaveCount(0, {
      timeout: STREAM_TIMEOUT,
    });
    await expect(second.locator('[data-notice="disclaimer"]')).toHaveCount(0);
    await expect(panel.locator('[data-notice="disclaimer"]')).toHaveCount(1);

    // the thread is kept: it is listed under Past chats, titled from the first question
    await panel.getByRole("button", { name: "Past chats" }).click();
    await expect(panel.getByRole("button", { name: /What does the anion gap tell me/ }).first()).toBeVisible();
  });

  test("save_note: Accept writes the note; Cancel leaves nothing", async ({ page, consoleErrors }, info) => {
    trackApi(page);
    await openFirstSection(page);
    // Projects and repeats run in parallel on one student and one section note: count only this run's saves.
    const tok = `t${info.project.name.replace(/[^a-z0-9]/g, "")}${info.repeatEachIndex}${Date.now().toString(36)}`;
    const mine = `(ref ${tok})`;
    const before = await noteCount(page, mine);

    await ask(page, `e2e:save#${tok} Save the key points of this section as a note`);
    const panel = conversation(page);
    const chip = panel.locator('[data-confirm="note"]');
    await expect(chip).toBeVisible({ timeout: STREAM_TIMEOUT });
    // A section keeps one page note per student: a first save proposes a new note, a later one (another project of
    // the matrix, same test student) proposes adding to it.
    await expect(
      chip.getByRole("heading", { name: /^(Save this as a new note\?|Add this to your note\?)$/ }),
    ).toBeVisible();
    await expect(chip.getByText("Waiting for you")).toBeVisible();
    await expectNoAxeViolations(page);
    await snap(page, info, consoleErrors, "confirm-note");
    await chip.getByRole("button", { name: "Cancel" }).click();
    await expect(chip).toBeHidden({ timeout: STREAM_TIMEOUT });
    await expect.poll(() => noteCount(page, mine)).toBe(before);
    // The cancel is a run of its own: wait until it has answered and the bar is idle again (a send while a run is
    // still going is not accepted), then ask again.
    await expect(panel.getByText("Okay, I did not save anything.")).toBeVisible({ timeout: STREAM_TIMEOUT });
    await expect(panel.getByRole("button", { name: "Stop the answer" })).toHaveCount(0, { timeout: STREAM_TIMEOUT });

    await ask(page, `e2e:save#${tok} Save the key points of this section as a note`);
    const again = panel.locator('[data-confirm="note"]');
    await expect(again).toBeVisible({ timeout: STREAM_TIMEOUT });
    await again.getByRole("button", { name: "Accept" }).click();
    await expect(again).toBeHidden({ timeout: STREAM_TIMEOUT });
    await expect.poll(() => noteCount(page, mine), { timeout: STREAM_TIMEOUT }).toBe(before + 1);
  });

  test("suggest_video: the answer carries a video block with Open in Videos", async ({ page, consoleErrors }, info) => {
    trackApi(page);
    await go(page, "/videos");
    await ask(page, "e2e:video Is there a video on pressure-volume loops?");
    const panel = conversation(page);
    const block = panel.locator('[data-block="video"]').first();
    await expect(block).toBeVisible({ timeout: STREAM_TIMEOUT });
    await expect(block.getByRole("button", { name: /^Play video:/ })).toBeVisible();
    await expectNoAxeViolations(page);
    await expectNoHorizontalOverflow(page);
    await snap(page, info, consoleErrors, "video-block");
    await block.getByRole("link", { name: "Open in Videos" }).click();
    await expect(page).toHaveURL(/\/videos\?.*v=yt-/);
  });

  for (const view of [
    { name: "panel", width: 1180, height: 820 },
    { name: "sheet", width: 820, height: 1180 },
  ] as const) {
    test(`open a document in the canvas at ${view.width}×${view.height} (${view.name})`, async ({
      page,
      consoleErrors,
    }, info) => {
      test.skip(isPhone(page), "the iPad layouts are set explicitly; phone has its own matrix entry");
      await page.setViewportSize({ width: view.width, height: view.height });
      trackApi(page);
      await openFirstSection(page);
      await ask(page, "e2e:canvas Draft a one-page summary of this section");
      const canvas = page.locator(`[data-canvas="${view.name}"]`);
      await expect(canvas).toBeVisible({ timeout: STREAM_TIMEOUT });
      const item = canvas.locator('[data-canvas-kind="document"]');
      await expect(item).toHaveAttribute("data-origin", "assistant");
      await expect(item.getByRole("textbox")).toBeVisible();
      if (view.name === "panel") {
        // the side panel sits next to the page, inside the assistant panel, with the chat one tab away
        await expect(page.locator('[data-assistant="panel"] [data-canvas="panel"]')).toBeVisible();
        await expect(page.getByRole("button", { name: "Chat", exact: true })).toBeVisible();
      } else {
        await expect(page.getByRole("dialog", { name: "Canvas" })).toBeVisible();
      }
      await expectNoAxeViolations(page);
      await expectNoHorizontalOverflow(page);
      await snap(page, info, consoleErrors, `canvas-${view.name}`);
    });
  }
});

/** Exam state is per user: this test signs in as its own throwaway student and deletes it afterwards. */
async function freshStudent(browser: Browser, baseURL: string, info: TestInfo) {
  const run = String.fromCharCode(97 + (info.repeatEachIndex % 26), 97 + (info.retry % 26));
  const email = `naadi-e2e-tutorexam${info.project.name.replace(/[^a-z]/g, "")}${run}-${process.env.E2E_RUN_ID}@example.com`;
  runUsers(["delete", "--email", email]);
  let context: BrowserContext | null = null;
  const open = async (path: string) => {
    context = await browser.newContext({ ...info.project.use, baseURL });
    const page = await context.newPage();
    trackApi(page);
    await page.goto(callbackPath(toLink(runUsers(["link", "--email", email])), path));
    await page.waitForURL((u) => u.pathname === path, { timeout: 30_000 });
    await settle(page);
    return page;
  };
  const done = async () => {
    try {
      await (context as BrowserContext | null)?.close().catch(() => undefined);
    } finally {
      runUsers(["delete", "--email", email]);
    }
  };
  return { open, done };
}

test.describe("exam mode", () => {
  test("an exam block running on the server: no bar, the notice instead, and the API refuses", async ({
    browser,
    baseURL,
  }, info) => {
    test.setTimeout(120_000);
    const student = await freshStudent(browser, baseURL as string, info);
    try {
      const page = await student.open("/settings/account");
      await ensureOnboarded(page);
      await go(page, "/");
      // before the exam: the assistant is there
      if (isPhone(page)) await expect(page.locator('[data-assistant="tab"]')).toBeVisible();
      else await expect(page.locator('[data-assistant="dock"]')).toBeVisible();

      const made = await api<{ id: string }>(page, "POST", "/api/blocks/mock", { chapter_id: "mock1" });
      expect(made.status).toBe(201);
      const started = await api<unknown>(page, "POST", `/api/blocks/${made.json.id}/sections/1/start`);
      expect(started.status).toBeLessThan(300);

      await go(page, "/");
      await expect(page.locator('[data-assistant="dock"], [data-assistant="tab"]')).toHaveCount(0);
      await expect(page.getByRole("textbox", { name: "Ask Naadi" })).toHaveCount(0);
      if (!isPhone(page))
        await expect(page.locator('[data-assistant="exam"]')).toHaveText("The assistant is off during exam mode.");
      const refused = await api<{ error: { code: string } }>(page, "GET", "/api/assistant/threads");
      expect(refused.status).toBe(409);
      expect(refused.json.error.code).toBe("exam_active");
      await expectNoAxeViolations(page);
      // this test's page is not under the console guard fixture: a pass-through guard for the screenshot
      const passThrough: ConsoleGuard = { allowHttpError: () => {}, duringTool: (fn) => fn() };
      if (!isPhone(page)) await snapSegment(page, info, passThrough, "14", "exam-notice");
    } finally {
      await student.done();
    }
  });
});

test.describe("assistant rate limited", () => {
  test.use(asOwnStudent);

  test("a 429 from the runtime shows Busy with the time to come back, and keeps the question", async ({
    page,
    consoleErrors,
  }, info) => {
    await openFirstSection(page);
    await page.route(/\/threads\/[^/]+\/runs\/stream/, (route) =>
      route.fulfill({
        status: 429,
        contentType: "application/json",
        headers: { "retry-after": "30" },
        body: JSON.stringify({ error: { code: "rate_limited", message: "Too many requests.", request_id: "req-e2e" } }),
      }),
    );
    consoleErrors.allowHttpError({ urlPart: "/runs/stream", status: 429, agentSdk: true });
    const question = "e2e:answer What does the anion gap tell me?";
    await ask(page, question);
    const panel = conversation(page);
    await expect(panel.locator('[data-notice="rate_limited"]')).toBeVisible({ timeout: STREAM_TIMEOUT });
    await expect(panel.getByText(/The assistant is busy\. Try again after/)).toBeVisible();
    await expect(panel.getByText("Unavailable")).toHaveCount(0);
    await expect(await input(page)).toHaveValue(question);
    await snap(page, info, consoleErrors, "rate-limited");
  });
});

test.describe("assistant offline", () => {
  test.use(asOwnStudent);

  test("an answer renders as plain text while the markdown renderer cannot load, then as markdown once back online", async ({
    page,
    consoleErrors,
  }, info) => {
    test.setTimeout(120_000);
    await openFirstSection(page);
    // From here on, hold the lazily loaded scripts the chat asks for (the markdown renderer's chunks), as if the
    // connection went before they arrived.
    const held: Route[] = [];
    let holding = true;
    const hold = async (route: Route) => {
      if (holding) held.push(route);
      else await route.continue();
    };
    await page.route(/\/assets\/[^/]+\.js(\?.*)?$/, hold);
    await ask(page, "e2e:answer What does the anion gap tell me?");
    const panel = conversation(page);
    const answer = panel.locator('[data-message-role="assistant"]').last();
    await expect(answer.locator('[data-markdown="plain"]').first()).toBeVisible({ timeout: STREAM_TIMEOUT });
    await expect(answer).toContainText("RTA");
    await expect(panel.locator('[data-status="thinking"], [data-status="tool"]')).toHaveCount(0, {
      timeout: STREAM_TIMEOUT,
    });

    await quiet(page);
    await page.context().setOffline(true);
    await page.evaluate(() => window.dispatchEvent(new Event("offline")));
    await expect(page.getByText("You're offline. The assistant needs a connection.").first()).toBeVisible();
    await expect(answer.locator('[data-markdown="plain"]').first()).toBeVisible();
    await expect(answer).toContainText("RTA");
    await snap(page, info, consoleErrors, "offline-plain-answer");

    await page.context().setOffline(false);
    await page.evaluate(() => window.dispatchEvent(new Event("online")));
    holding = false;
    // Release first, then unroute: unrouting a handler with routes still pending marks them handled.
    for (const r of held.splice(0)) await r.continue();
    await page.unroute(/\/assets\/[^/]+\.js(\?.*)?$/, hold);
    await expect(answer.locator('[data-markdown="plain"]')).toHaveCount(0, { timeout: STREAM_TIMEOUT });
    await expect(answer.getByRole("link", { name: /^Source 1/ }).first()).toBeVisible();
  });
});

test.describe("assistant states (screens)", () => {
  test.use(asOwnStudent);

  test("dock idle, thinking, past chats, offline and Settings → Assistant", async ({ page, consoleErrors }, info) => {
    test.setTimeout(120_000);
    await openFirstSection(page);
    await snap(page, info, consoleErrors, "dock-idle");

    // thinking: the run's stream is held for a moment so the state before the first token can be seen
    let release: () => void = () => {};
    const held = new Promise<void>((r) => {
      release = r;
    });
    const stream = /\/threads\/[^/]+\/runs\/stream/;
    const holdStream = async (route: Route) => {
      await held;
      await route.continue();
    };
    await page.route(stream, holdStream);
    await ask(page, "e2e:answer What does the anion gap tell me?");
    const panel = conversation(page);
    await expect(panel.locator('[data-status="thinking"]')).toBeVisible({ timeout: STREAM_TIMEOUT });
    await snap(page, info, consoleErrors, "thinking");
    // Unroute only after the held request went through: unrouting a pending handler marks its route handled.
    const released = page.waitForResponse(stream);
    release();
    await released;
    await page.unroute(stream, holdStream);
    await expect(panel.locator('[data-status="thinking"], [data-status="tool"]')).toHaveCount(0, {
      timeout: STREAM_TIMEOUT,
    });

    await panel.getByRole("button", { name: "Past chats" }).click();
    await expect(panel.getByRole("button", { name: /What does the anion gap tell me/ }).first()).toBeVisible();
    await snap(page, info, consoleErrors, "past-chats");
    // Back to the conversation: the offline state lives on the composer, which the past-chats view replaces.
    await panel.getByRole("button", { name: "Past chats" }).click();
    await expect(panel.getByRole("button", { name: "Past chats" })).toHaveAttribute("aria-pressed", "false");

    await quiet(page);
    await page.context().setOffline(true);
    await page.evaluate(() => window.dispatchEvent(new Event("offline")));
    await expect(page.getByText("You're offline. The assistant needs a connection.").first()).toBeVisible();
    await snap(page, info, consoleErrors, "offline");
    await page.context().setOffline(false);
    await page.evaluate(() => window.dispatchEvent(new Event("online")));

    await go(page, "/settings/assistant");
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await expectNoAxeViolations(page);
    await snap(page, info, consoleErrors, "settings-assistant");
  });
});

test.describe("production server (APP_ENV=prod, strict CSP)", () => {
  test("the assistant surface, a turn and Settings → Assistant render with zero console errors", async ({
    browser,
  }, info) => {
    test.setTimeout(120_000);
    const prod = process.env.E2E_PROD_URL;
    test.skip(!prod, "needs the APP_ENV=prod server of the local/CI webServer set");
    // The session moves to the https prod origin (self-signed loopback certificate), as in search.spec.ts.
    const file = await signedInState(browser, info.project.use.baseURL as string, "student");
    const saved = JSON.parse(readFileSync(file, "utf8")) as {
      origins: { origin: string; localStorage: { name: string; value: string }[] }[];
    };
    const origin = new URL(prod as string).origin;
    const context = await browser.newContext({
      ...info.project.use,
      baseURL: prod,
      ignoreHTTPSErrors: true,
      storageState: { cookies: [], origins: saved.origins.map((o) => ({ ...o, origin })) },
    });
    const page = await context.newPage();
    const errors: string[] = [];
    page.on("console", (m) => {
      // Trusted Types ship report-only (segment 22): csp.spec.ts collects those reports; they are not errors.
      if (m.type() === "error" && !/\[Report Only\]|report-only/i.test(m.text())) errors.push(m.text());
    });
    page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
    try {
      await openFirstSection(page);
      // No provider in prod mode here (the loopback fake is refused outside non-prod): the turn degrades to the
      // course sources plus a notice, which still renders the lazy surface, markdown and blocks under the CSP.
      await ask(page, "What does the anion gap tell me?");
      const panel = conversation(page);
      await expect(panel.locator('[data-notice]:not([data-notice="disclaimer"])').first()).toBeVisible({
        timeout: STREAM_TIMEOUT,
      });
      await expect(panel.locator('[data-status="thinking"], [data-status="tool"]')).toHaveCount(0, {
        timeout: STREAM_TIMEOUT,
      });
      await page.goto("/settings/assistant");
      await expect(page.getByRole("heading", { level: 1, name: "Assistant" })).toBeVisible();
      await page.evaluate(() => document.fonts.ready.then(() => undefined));
      expect(errors, "console errors under the production CSP").toEqual([]);
    } finally {
      await context.close();
    }
  });
});
