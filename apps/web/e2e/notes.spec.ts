import { mkdirSync } from "node:fs";
import path from "node:path";
import type { Browser, BrowserContext, Page, Response, TestInfo } from "@playwright/test";
import { callbackPath, canMintUsers, runUsers, toLink } from "./auth-users";
import {
  type ConsoleGuard,
  expect,
  expectNoAxeViolations,
  expectNoHorizontalOverflow,
  quiet,
  settle,
  TOOL_CSP_NOISE,
  test,
  trackApi,
} from "./fixtures";

/**
 * Segment 13 (WEB-A): highlights, the selection toolbar, the Reader's page notes and the Notes page, against the
 * real API, database and auth server. Each test is its own throwaway student (deleted afterwards).
 *
 * Covers: select text → highlight in each colour (Key fact / Formula / My mistake) → Note (page notes) → Card
 * (create dialog prefilled) → reload keeps them → Notes lists them with their words and sources → filter by colour
 * and by anchor; a highlight made offline waits on the device and syncs; an answered question's explanation takes
 * highlights. Frozen-DOM axe on every screen, zero console errors, no horizontal scroll, one lime action.
 */
test.skip(!canMintUsers(), "signed-in tests need the service role (run pnpm e2e:live-auth against the Space)");

const SHOTS = path.resolve(import.meta.dirname, "../../../.tmp/shots/notes");

async function snap(page: Page, info: TestInfo, guard: ConsoleGuard, state: string, fullPage = false) {
  mkdirSync(SHOTS, { recursive: true });
  await page.evaluate(() => document.fonts.ready);
  await guard.duringTool(async () => {
    await page.screenshot({
      path: path.join(SHOTS, `${state}--${info.project.name}.png`),
      fullPage,
      animations: "disabled",
    });
    await page.waitForTimeout(300);
  });
}

function email(info: TestInfo, kind: string): string {
  const run = String.fromCharCode(97 + (info.repeatEachIndex % 26), 97 + (info.retry % 26));
  const role = `${kind}${info.project.name.replace(/[^a-z]/g, "")}${run}`;
  return `naadi-e2e-${role}-${process.env.E2E_RUN_ID}@example.com`;
}

/** Console errors of pages opened outside the fixture's page, with the fixture's rule for the screenshot tool. */
function watchConsole(page: Page) {
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

async function freshStudent(browser: Browser, baseURL: string, info: TestInfo, kind: string) {
  const who = email(info, kind);
  runUsers(["delete", "--email", who]);
  const contexts: BrowserContext[] = [];
  const open = async (to: string): Promise<{ page: Page; context: BrowserContext }> => {
    const context = await browser.newContext({ ...info.project.use, baseURL });
    contexts.push(context);
    const page = await context.newPage();
    trackApi(page);
    const link = toLink(runUsers(["link", "--email", who]));
    await page.goto(callbackPath(link, to));
    await page.waitForURL((u) => u.pathname === to, { timeout: 30_000 });
    await settle(page);
    return { page, context };
  };
  const done = async () => {
    try {
      for (const c of contexts) await c.close().catch(() => undefined);
    } finally {
      runUsers(["delete", "--email", who]);
    }
  };
  return { open, done };
}

/** Call the API with the signed-in student's own token (what the app sends). */
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

/**
 * Select the first `words` words of the `nth` plain text run of the content (a real DOM selection, as touch,
 * Pencil, mouse or keyboard selection would make; the toolbar follows `selectionchange`). Returns the text.
 */
async function selectWords(page: Page, root: string, nth: number, words = 3): Promise<string> {
  return page.evaluate(
    ({ root, nth, words }) => {
      const spans = [...document.querySelectorAll<HTMLElement>(`${root} [data-run]`)].filter((s) => {
        if (s.closest("mark") || s.querySelector("mark") || s.closest("a, button, th")) return false;
        return (s.textContent ?? "").trim().split(/\s+/).length > words + 1;
      });
      const span = spans[nth];
      const node = span ? [...span.childNodes].find((n) => n.nodeType === 3) : undefined;
      if (!span || !node) throw new Error(`no text run ${nth}`);
      const text = (node as Text).data;
      const start = text.search(/\S/);
      const re = /\S+/g;
      re.lastIndex = start;
      let end = start;
      for (let i = 0; i < words; i++) {
        const m = re.exec(text);
        if (!m) break;
        end = m.index + m[0].length;
      }
      span.scrollIntoView({ block: "center" });
      const range = document.createRange();
      range.setStart(node, start);
      range.setEnd(node, end);
      const sel = document.getSelection();
      sel?.removeAllRanges();
      sel?.addRange(range);
      return text.slice(start, end);
    },
    { root, nth, words },
  );
}

const isNoteWrite = (method: string, status: number) => (r: Response) =>
  new URL(r.url()).pathname.startsWith("/api/notes") && r.request().method() === method && r.status() === status;

/** Buttons and links painted lime (the next-action colour): one per view. */
async function limeCount(page: Page): Promise<number> {
  await page.mouse.move(1, 1);
  return page.evaluate(() => {
    const lime = "rgb(198, 255, 61)";
    return [...document.querySelectorAll<HTMLElement>("a, button")].filter((el) => {
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0 && getComputedStyle(el).backgroundColor === lime;
    }).length;
  });
}

const toolbar = (page: Page) => page.getByRole("toolbar", { name: "Selection tools" });
const READER = "/learn/ch01/ch01-s02";
const CONTENT = "article [data-content-root]";

async function highlight(page: Page, nth: number, word: "Key fact" | "Formula" | "My mistake"): Promise<string> {
  const quote = await selectWords(page, CONTENT, nth);
  await expect(toolbar(page)).toBeVisible();
  const saved = page.waitForResponse(isNoteWrite("POST", 201));
  await toolbar(page).getByRole("button", { name: word, exact: true }).click();
  await saved;
  await expect(toolbar(page)).toHaveCount(0);
  return quote;
}

test.describe("highlights, notes and the selection toolbar", () => {
  test("select → highlight in each colour → note → card → filter by colour and anchor", async ({
    browser,
    baseURL,
  }, info) => {
    test.setTimeout(150_000);
    const student = await freshStudent(browser, baseURL as string, info, "notesflow");
    try {
      const { page } = await student.open(READER);
      const { errors, guard } = watchConsole(page);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();

      // The toolbar on a selection: three words, Note, Card; no Ask while the assistant is off.
      const first = await selectWords(page, CONTENT, 0);
      const bar = toolbar(page);
      await expect(bar).toBeVisible();
      for (const name of ["Key fact", "Formula", "My mistake", "Note", "Card"]) {
        await expect(bar.getByRole("button", { name, exact: true })).toBeVisible();
      }
      await expect(bar.getByRole("button", { name: "Ask" })).toHaveCount(0);
      expect(await limeCount(page)).toBeLessThanOrEqual(1);
      await snap(page, info, guard, "reader-toolbar");
      await expectNoAxeViolations(page);
      await page.keyboard.press("Escape");
      await expect(bar).toHaveCount(0);

      // Each colour, each with its word for screen readers.
      const lime = await highlight(page, 0, "Key fact");
      expect(lime).toBe(first);
      const violet = await highlight(page, 1, "Formula");
      const amber = await highlight(page, 2, "My mistake");
      const marks = page.locator(`${CONTENT} mark[data-highlight-id]`);
      await expect(page.locator(`${CONTENT} mark[data-color=lime]`).first()).toContainText(lime.split(" ")[0] ?? "");
      await expect(page.locator(`${CONTENT} mark[data-color=violet]`).first()).toBeVisible();
      await expect(page.locator(`${CONTENT} mark[data-color=amber]`).first()).toBeVisible();
      await expect(page.locator(`${CONTENT} [data-sr-word]`)).toHaveText(["Key fact: ", "Formula: ", "My mistake: "]);
      await snap(page, info, guard, "reader-highlights");

      // Tapping a highlight offers its colours and Remove; recolouring sends a PATCH.
      await page.locator(`${CONTENT} mark[data-color=amber]`).first().click();
      await expect(bar).toBeVisible();
      await expect(bar.getByRole("button", { name: "My mistake" })).toHaveAttribute("aria-pressed", "true");
      await snap(page, info, guard, "reader-highlight-menu");
      await page.keyboard.press("Escape");

      // Note: the page-notes panel (beside the page on landscape/laptop, a sheet on portrait/phone) gets the quote.
      const noteQuote = await selectWords(page, CONTENT, 3);
      const created = page.waitForResponse(isNoteWrite("POST", 201));
      await bar.getByRole("button", { name: "Note", exact: true }).click();
      const panel = page.locator("[data-page-notes]");
      await expect(panel).toBeVisible();
      await expect(panel.getByRole("heading", { name: "Page notes" })).toBeVisible();
      const editor = panel.getByRole("textbox", { name: /^Your notes on / });
      await expect(editor.locator("blockquote")).toContainText(noteQuote);
      await created;
      await expect(panel.locator("[data-note-save=saved]")).toBeVisible({ timeout: 20_000 });
      await editor.click();
      await page.keyboard.type("Remember the order.");
      await page.waitForResponse(isNoteWrite("PATCH", 200));
      for (const mode of ["Write", "Both", "Type"]) {
        await panel.getByRole("button", { name: mode, exact: true }).click();
        await expect(panel.getByRole("button", { name: mode, exact: true })).toHaveAttribute("aria-pressed", "true");
      }
      await panel.getByRole("button", { name: "Both", exact: true }).click();
      await expect(panel.locator("[data-page-ink]")).toBeVisible();
      await snap(page, info, guard, "page-notes");
      await expectNoAxeViolations(page);
      await page.getByRole("button", { name: "Close page notes" }).click();
      await expect(panel).toHaveCount(0);

      // Card: the S10 dialog, prefilled with the selection.
      const cardQuote = await selectWords(page, CONTENT, 4);
      await bar.getByRole("button", { name: "Card", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: "New card" });
      await expect(dialog).toBeVisible();
      await expect(dialog.getByRole("textbox", { name: "Back" })).toHaveValue(cardQuote);
      await dialog.getByRole("textbox", { name: "Front" }).fill("What does this paragraph say?");
      const card = page.waitForResponse((r) => r.url().endsWith("/api/cards") && r.request().method() === "POST");
      await dialog.getByRole("button", { name: "Save card" }).click();
      expect((await card).status()).toBe(201);
      await expect(dialog).toBeHidden();

      // Reload: the highlights come back from the server, in place.
      await quiet(page);
      await page.reload();
      await settle(page);
      await expect(marks.first()).toBeVisible();
      await expect(page.locator(`${CONTENT} [data-sr-word]`)).toHaveCount(3);
      const listed = await api<{ items: { highlight_color: string; anchor_pos: { quote: string } }[] }>(
        page,
        "GET",
        "/api/notes?kind=highlight",
      );
      expect(listed.json.items.map((n) => n.anchor_pos.quote).sort()).toEqual([amber, lime, violet].sort());

      // Notes: recent highlights with their words and source chips; filter by colour and by anchor.
      await quiet(page);
      await page.goto("/notes");
      await settle(page);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      const rail = page.locator("[data-highlights-rail]");
      const rows = rail.getByRole("list", { name: "Recent highlights" }).getByRole("listitem");
      await expect(rows).toHaveCount(3);
      await expect(rail.getByText("Key fact", { exact: true }).first()).toBeVisible();
      await expect(rows.first().getByRole("link")).toHaveAttribute("href", READER);
      await expect(page.locator("[data-note-row]")).toHaveCount(1); // the page note
      expect(await limeCount(page)).toBe(1); // New note
      await expectNoHorizontalOverflow(page);
      await snap(page, info, guard, "notes-page", true);
      await expectNoAxeViolations(page);

      await rail.getByRole("button", { name: /^Formula/ }).click();
      await expect(page).toHaveURL(/color=violet/);
      await expect(rows).toHaveCount(1);
      await expect(rows.first()).toContainText(violet);
      await snap(page, info, guard, "notes-filter-colour");
      await rail.getByRole("button", { name: /^All colours/ }).click();
      await rail.getByRole("button", { name: /^Questions/ }).click();
      await expect(rail.locator("[data-highlights-empty]")).toBeVisible();
      await rail.getByRole("button", { name: /^Chapters/ }).click();
      await expect(rows).toHaveCount(3);
      await snap(page, info, guard, "notes-filter-anchor");

      // The page note opens in the sheet; its text was saved.
      await page.locator("[data-note-row]").first().click();
      const sheet = page.locator("[data-note-sheet]");
      await expect(sheet).toBeVisible();
      await expect(sheet.getByRole("textbox", { name: "Note text" })).toContainText("Remember the order.");
      await expect(sheet.getByRole("link", { name: "Open in the chapter" })).toBeVisible();
      await snap(page, info, guard, "note-sheet", true);
      await expectNoAxeViolations(page);
      expect(errors).toEqual([]);
    } finally {
      await student.done();
    }
  });

  test("offline: a highlight is kept on the device, says it will sync, and syncs", async ({
    browser,
    baseURL,
  }, info) => {
    const student = await freshStudent(browser, baseURL as string, info, "notesoff");
    try {
      const { page, context } = await student.open(READER);
      const { errors, guard } = watchConsole(page);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await quiet(page);
      await context.setOffline(true);
      const quote = await selectWords(page, CONTENT, 1);
      await toolbar(page).getByRole("button", { name: "Formula", exact: true }).click();
      await expect(page.locator(`${CONTENT} mark[data-color=violet]`).first()).toBeVisible();
      await expect(page.locator("[data-note-save=local]").first()).toContainText("will sync");
      await snap(page, info, guard, "reader-offline");
      const synced = page.waitForResponse(isNoteWrite("POST", 201));
      await context.setOffline(false);
      await page.evaluate(() => window.dispatchEvent(new Event("online")));
      await synced;
      await expect(page.locator("[data-note-save]")).toHaveCount(0, { timeout: 20_000 });
      const listed = await api<{ items: { anchor_pos: { quote: string } }[] }>(
        page,
        "GET",
        "/api/notes?kind=highlight",
      );
      expect(listed.json.items.map((n) => n.anchor_pos.quote)).toEqual([quote]);
      expect(errors.filter((e) => !/Failed to load resource|net::ERR|NetworkError|Load failed/.test(e))).toEqual([]);
    } finally {
      await student.done();
    }
  });

  test("an answered question's explanation takes highlights (never before answering)", async ({
    browser,
    baseURL,
  }, info) => {
    const student = await freshStudent(browser, baseURL as string, info, "notesq");
    try {
      const { page } = await student.open("/learn");
      const { errors, guard } = watchConsole(page);
      // Before an attempt the server refuses a question anchor.
      const refused = await api(page, "POST", "/api/notes", {
        kind: "highlight",
        anchor_type: "question",
        anchor_id: "ch01-q01",
        anchor_pos: { from: 0, to: 4, quote: "text", prefix: "", suffix: "" },
        highlight_color: "violet",
      });
      expect(refused.status).toBe(403);
      await quiet(page);
      await page.goto("/practice/question/ch01-q01");
      await page.waitForURL(/\/practice\/blocks\/[0-9a-f-]{36}\/1$/);
      await expect(page.getByRole("radio")).toHaveCount(5);
      await expect(page.locator("[data-highlightable=question]")).toHaveCount(0);
      await page.keyboard.press("b");
      await page.keyboard.press("Control+Enter");
      const explanation = page.getByRole("region", { name: "Explanation" });
      await expect(explanation).toBeVisible({ timeout: 30_000 });
      await settle(page);
      await selectWords(page, "[data-highlightable=question]", 0);
      const bar = toolbar(page);
      await expect(bar).toBeVisible();
      await expect(bar.getByRole("button", { name: "Card" })).toHaveCount(0); // section selections only
      const saved = page.waitForResponse(isNoteWrite("POST", 201));
      await bar.getByRole("button", { name: "Formula", exact: true }).click();
      await saved;
      await expect(explanation.locator("mark[data-color=violet]").first()).toBeVisible();
      await snap(page, info, guard, "explanation-highlight");
      await expectNoAxeViolations(page);
      const listed = await api<{ items: { anchor_type: string; source: { question_number: number | null } }[] }>(
        page,
        "GET",
        "/api/notes?kind=highlight&anchor_type=question",
      );
      expect(listed.json.items).toHaveLength(1);
      expect(errors.filter((e) => !/status of 403/.test(e))).toEqual([]); // the refusal before the attempt
    } finally {
      await student.done();
    }
  });

  test("APP_ENV=prod (strict CSP): toolbar, highlight, page notes editor and the Notes page with no console errors", async ({
    browser,
    baseURL,
  }, info) => {
    const PROD = process.env.E2E_PROD_URL;
    test.skip(!PROD, "needs the local APP_ENV=prod API (not available against the live Space)");
    const student = await freshStudent(browser, baseURL as string, info, "notesprod");
    try {
      const { context } = await student.open("/learn");
      // Move the session to the https prod origin (as cards/ink-gate do).
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
        await page.goto(READER);
        await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
        await settle(page);
        await highlight(page, 0, "Key fact");
        await expect(page.locator(`${CONTENT} mark[data-color=lime]`).first()).toBeVisible();
        await selectWords(page, CONTENT, 1);
        await toolbar(page).getByRole("button", { name: "Note", exact: true }).click();
        const panel = page.locator("[data-page-notes]");
        const editor = panel.getByRole("textbox", { name: /^Your notes on / });
        await expect(editor.locator("blockquote")).toBeVisible();
        await editor.click();
        await page.keyboard.type("Strict CSP");
        await panel.getByRole("button", { name: "Formula" }).click();
        await expect(panel.locator("[data-note-save=saved]")).toBeVisible({ timeout: 20_000 });
        await quiet(page);
        await page.goto("/notes");
        await expect(page.locator("[data-highlights-rail]").getByRole("listitem")).toHaveCount(1);
        await page.locator("[data-note-row]").first().click();
        await expect(page.locator("[data-note-sheet]").getByRole("textbox", { name: "Note text" })).toContainText(
          "Strict CSP",
        );
        expect(errors).toEqual([]);
      } finally {
        await prod.close();
      }
    } finally {
      await student.done();
    }
  });

  test("search finds a note from the Notes box and in the Search page's Notes group", async ({
    browser,
    baseURL,
  }, info) => {
    test.setTimeout(120_000);
    const student = await freshStudent(browser, baseURL as string, info, "notessearch");
    try {
      const { page } = await student.open("/notes");
      const { errors, guard } = watchConsole(page);
      const word = `zymogen${info.project.name.replace(/[^a-z]/g, "").slice(0, 6)}`;
      const made = await api<{ id: string }>(page, "POST", "/api/notes", {
        kind: "note",
        anchor_type: "free",
        title: "Pancreatic enzymes",
        body: {
          type: "doc",
          content: [{ type: "paragraph", content: [{ type: "text", text: `Trypsinogen is a ${word}.` }] }],
        },
      });
      expect(made.status).toBe(201);
      await page.reload();
      await settle(page);
      await expect(page.locator("[data-note-row]")).toHaveCount(1);
      // The index is written after the save (background job): the box finds it once it is there.
      const box = page.getByRole("searchbox", { name: "Search your notes and handwriting" });
      await box.fill(word);
      const results = page.getByRole("list", { name: "Matching notes and handwriting" });
      await expect(async () => {
        await box.fill("");
        await box.fill(word);
        await expect(results.locator("[data-search-hit=note]")).toHaveCount(1, { timeout: 4_000 });
      }).toPass({ timeout: 60_000 });
      await snap(page, info, guard, "notes-search");
      await results.locator("[data-search-hit=note]").click();
      await expect(page.locator("[data-note-sheet]")).toBeVisible();
      await expect(page).toHaveURL(new RegExp(`note=${made.json.id}`));
      await quiet(page);
      await page.goto(`/search?q=${word}&scope=notes`);
      const group = page.getByRole("region", { name: "Your notes" });
      await expect(group.getByRole("link", { name: /Pancreatic enzymes/ })).toHaveAttribute(
        "href",
        `/notes?note=${made.json.id}`,
      );
      await snap(page, info, guard, "search-notes-group");
      expect(errors).toEqual([]);
    } finally {
      await student.done();
    }
  });
});
