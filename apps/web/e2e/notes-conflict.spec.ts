import type { Browser, BrowserContext, Page, Response, TestInfo } from "@playwright/test";
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

/**
 * Segment 13 rework: bounded last-writer-wins between two devices of the same student, against the real API,
 * database and auth server (one throwaway student, deleted afterwards).
 *
 * Device A goes offline and edits a page note; device B edits the same note online, later. When A reconnects its
 * older edit is refused (409 note_conflict): B's text stays on the server and on A's screen, A's text is kept as a
 * "Conflict copy" note, and A shows a notice (icon + words, role=status) with a link to the copy that survives a
 * reload until it is dismissed. Frozen-DOM axe, no horizontal scroll, zero console errors (the one expected 409).
 */
test.skip(!canMintUsers(), "signed-in tests need the service role (run pnpm e2e:live-auth against the Space)");

const SECTION = "ch01-s02";

function email(info: TestInfo): string {
  const run = String.fromCharCode(97 + (info.repeatEachIndex % 26), 97 + (info.retry % 26));
  return `naadi-e2e-lww${info.project.name.replace(/[^a-z]/g, "")}${run}-${process.env.E2E_RUN_ID}@example.com`;
}

/** Console errors of a page this spec opened, minus the screenshot tool's CSP line and what `allow` lets through. */
function watch(page: Page) {
  const errors: string[] = [];
  const allowed: RegExp[] = [];
  let tool = 0;
  page.on("console", (m) => {
    if (m.type() !== "error") return;
    const text = m.text();
    if (tool > 0 && text === TOOL_CSP_NOISE) return;
    if (allowed.some((r) => r.test(text))) return;
    errors.push(text);
  });
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  const guard: ConsoleGuard = {
    allowHttpError: () => undefined,
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
  return { errors, guard, allow: (r: RegExp) => allowed.push(r) };
}

async function device(browser: Browser, baseURL: string, info: TestInfo, who: string, to: string) {
  const context = await browser.newContext({ ...info.project.use, baseURL });
  const page = await context.newPage();
  trackApi(page);
  const w = watch(page);
  const link = toLink(runUsers(["link", "--email", who]));
  await page.goto(callbackPath(link, to.split("?")[0] as string));
  await page.waitForURL((u) => u.pathname === to.split("?")[0], { timeout: 30_000 });
  await settle(page);
  return { context, page, ...w };
}

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

interface NoteJson {
  id: string;
  title: string | null;
  anchor_type: string | null;
  anchor_id: string | null;
  body_text: string;
}

const isNoteWrite = (method: string, status: number) => (r: Response) =>
  new URL(r.url()).pathname.startsWith("/api/notes") && r.request().method() === method && r.status() === status;

const editor = (page: Page) => page.getByRole("textbox", { name: "Note text" });

async function typeAtEnd(page: Page, text: string): Promise<void> {
  const box = editor(page);
  await expect(box).toBeVisible();
  await box.click();
  await page.keyboard.press(process.platform === "darwin" ? "Meta+End" : "Control+End");
  await page.keyboard.press("Enter");
  await page.keyboard.type(text);
}

test.describe("notes: two devices, one note", () => {
  test("a stale offline edit never overwrites a newer one: conflict copy and a notice that survives reload", async ({
    browser,
    baseURL,
  }, info) => {
    test.setTimeout(180_000);
    const who = email(info);
    runUsers(["delete", "--email", who]);
    const contexts: BrowserContext[] = [];
    try {
      const a = await device(browser, baseURL as string, info, who, "/notes");
      contexts.push(a.context);
      const doc = (t: string) => ({
        type: "doc",
        content: [{ type: "paragraph", content: [{ type: "text", text: t }] }],
      });
      const made = await api<NoteJson>(a.page, "POST", "/api/notes", {
        kind: "note",
        anchor_type: "section",
        anchor_id: SECTION,
        body: doc("Original line."),
      });
      expect(made.status).toBe(201);
      const id = made.json.id;
      const url = `/notes?note=${id}`;
      await a.page.goto(url);
      await expect(editor(a.page)).toContainText("Original line.");
      await quiet(a.page);

      const b = await device(browser, baseURL as string, info, who, "/notes");
      contexts.push(b.context);
      await b.page.goto(url);
      await expect(editor(b.page)).toContainText("Original line.");
      await quiet(b.page);

      // A: offline, edits the page note; it waits on the device.
      a.allow(/Failed to load resource|Load failed|NetworkError|ERR_INTERNET_DISCONNECTED|net::/);
      await a.context.setOffline(true);
      await typeAtEnd(a.page, "Written on A offline.");
      await expect(a.page.locator("[data-note-save=local]").first()).toContainText("will sync", { timeout: 20_000 });

      // B: online, later, edits the same note; saved at once.
      const saved = b.page.waitForResponse(isNoteWrite("PATCH", 200));
      await typeAtEnd(b.page, "Written on B online.");
      await saved;
      await expect(b.page.locator("[data-note-save=saved]").first()).toBeVisible({ timeout: 20_000 });

      // A reconnects: its older edit is refused (the one expected 409), the copy is made, the notice shows.
      a.allow(/status of 409/);
      const refused = a.page.waitForResponse(isNoteWrite("PATCH", 409));
      const copied = a.page.waitForResponse(isNoteWrite("POST", 201));
      await a.context.setOffline(false);
      await a.page.evaluate(() => window.dispatchEvent(new Event("online")));
      const conflict = (await (await refused).json()) as { error: { code: string }; current: NoteJson };
      expect(conflict.error.code).toBe("note_conflict");
      expect(conflict.current.body_text).toContain("Written on B online.");
      await copied;
      const notice = a.page.locator("[data-conflict-notice=note_copy]");
      await expect(notice).toBeVisible({ timeout: 20_000 });
      await expect(notice).toHaveAttribute("role", "status");
      await expect(notice).toContainText("Changed on another device");
      await expect(notice).toContainText("kept as a separate note");
      // A's screen now shows the newer text (the sheet reloaded the server's copy).
      await expect(editor(a.page)).toContainText("Written on B online.");
      await expect(editor(a.page)).not.toContainText("Written on A offline.");

      // The server: B's text stands; A's is in a free "Conflict copy" note.
      const stored = await api<NoteJson>(a.page, "GET", `/api/notes/${id}`);
      expect(stored.json.body_text).toContain("Written on B online.");
      expect(stored.json.body_text).not.toContain("Written on A offline.");
      const list = await api<{ items: NoteJson[] }>(a.page, "GET", "/api/notes?kind=note");
      const copy = list.json.items.find((n) => n.id !== id);
      expect(copy?.title).toMatch(/^Conflict copy/);
      expect(copy?.anchor_type).toBe("free");
      expect(copy?.anchor_id).toBeNull();
      expect(copy?.body_text).toContain("Written on A offline.");
      expect(list.json.items).toHaveLength(2);

      await quiet(a.page);
      await expect(a.page.locator("[data-note-save]").first()).not.toHaveAttribute("data-note-save", "local");
      await snapSegment(a.page, info, a.guard, "13-notes", "conflict-notice");
      await expectNoAxeViolations(a.page);
      await expectNoHorizontalOverflow(a.page);

      // Reload: the notice is still there (kept on the device until dismissed); its link opens the copy.
      await a.page.reload();
      await settle(a.page);
      const again = a.page.locator("[data-conflict-notice=note_copy]");
      await expect(again).toBeVisible({ timeout: 20_000 });
      await again.getByRole("link", { name: "Open your copy" }).click();
      await a.page.waitForURL((u) => u.searchParams.get("note") === copy?.id);
      await expect(editor(a.page)).toContainText("Written on A offline.");
      await expect(a.page.getByRole("textbox", { name: "Note title" })).toHaveValue(/^Conflict copy/);
      await expectNoAxeViolations(a.page);

      // Dismissed: gone, and stays gone after a reload.
      await a.page.locator("[data-conflict-notice]").getByRole("button", { name: "Dismiss this message" }).click();
      await expect(a.page.locator("[data-conflict-notice]")).toHaveCount(0);
      await quiet(a.page);
      await a.page.reload();
      await settle(a.page);
      await expect(editor(a.page)).toBeVisible();
      await expect(a.page.locator("[data-conflict-notice]")).toHaveCount(0);

      // B never saw a conflict.
      await b.page.reload();
      await settle(b.page);
      await expect(editor(b.page)).toContainText("Written on B online.");
      await expect(b.page.locator("[data-conflict-notice]")).toHaveCount(0);
      await quiet(a.page);
      await quiet(b.page);
      expect(a.errors).toEqual([]);
      expect(b.errors).toEqual([]);
    } finally {
      for (const c of contexts) await c.close().catch(() => undefined);
      runUsers(["delete", "--email", who]);
    }
  });
});
