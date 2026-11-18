import { readFileSync } from "node:fs";
import type { Browser, BrowserContext, BrowserContextOptions, Page, TestInfo } from "@playwright/test";
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
 * Segment 13 (WEB-B): notebooks against the real API, database, auth and storage. Each test signs in its own
 * throwaway student (deleted afterwards). Covers: the shelf, a notebook with each of the five papers and ink on
 * every page that survives a reload, a whiteboard page round trip, the PDF export parsed with pdf.js (text and
 * the ink's vector path operators), offline edits that sync, frozen-DOM axe and zero console errors, plus one run
 * on the APP_ENV=prod server (strict CSP: Excalidraw's fonts and the PDF fonts come from 'self').
 */
test.skip(!canMintUsers(), "signed-in tests need the service role (run pnpm e2e:live-auth against the Space)");

const PAPERS = ["ruled", "dotted", "grid", "cornell", "blank"] as const;
const PAPER_LABEL: Record<(typeof PAPERS)[number], string> = {
  ruled: "Ruled",
  dotted: "Dotted",
  grid: "Grid",
  cornell: "Cornell",
  blank: "Blank",
};

const snap = (page: Page, info: TestInfo, guard: ConsoleGuard, state: string, full = false) =>
  guard.duringTool(async () => {
    await snapSegment(page, info, guard, "13-notebooks", state, full);
    await page.waitForTimeout(200);
  });

function emailFor(info: TestInfo, tag: string): string {
  const run = String.fromCharCode(97 + (info.repeatEachIndex % 26), 97 + (info.retry % 26));
  const role = `nb${tag}${info.project.name.replace(/[^a-z]/g, "")}${run}`;
  return `naadi-e2e-${role}-${process.env.E2E_RUN_ID}@example.com`;
}

/**
 * Console errors and failed requests of a page this spec opened itself (the fixture's guard watches only its own
 * `page`). The screenshot tool's injected <style> is refused by our CSP in WebKit: ignored while a tool runs.
 * `allowOffline` covers requests the test cut off on purpose.
 */
function watch(page: Page) {
  const errors: string[] = [];
  let tool = 0;
  let offlineOk = false;
  page.on("console", (m) => {
    if (m.type() !== "error") return;
    const text = m.text();
    if (tool > 0 && text === TOOL_CSP_NOISE) return;
    if (offlineOk && /Failed to load resource|Load failed|NetworkError|ERR_INTERNET_DISCONNECTED|net::/.test(text))
      return;
    errors.push(`${text} @ ${m.location().url}`);
  });
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  page.on("response", (r) => {
    if (r.status() >= 400 && r.frame() === page.mainFrame()) errors.push(`HTTP ${r.status()} ${r.url()}`);
  });
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
  return {
    errors,
    guard,
    allowOffline: (on = true) => {
      offlineOk = on;
    },
  };
}

async function student(browser: Browser, baseURL: string, info: TestInfo, tag: string) {
  const email = emailFor(info, tag);
  runUsers(["delete", "--email", email]);
  const contexts: BrowserContext[] = [];
  const open = async (path: string, base = baseURL, extra: BrowserContextOptions = {}) => {
    const context = await browser.newContext({ ...info.project.use, baseURL: base, acceptDownloads: true, ...extra });
    contexts.push(context);
    const page = await context.newPage();
    trackApi(page);
    const w = watch(page);
    const link = toLink(runUsers(["link", "--email", email]));
    await page.goto(callbackPath(link, path));
    await page.waitForURL((u) => u.pathname === path, { timeout: 30_000 });
    return { page, context, ...w };
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

/**
 * Navigate only once every font face is loaded: Firefox logs a font download that a navigation cancels as a page
 * error, and the unicode-range faces start loading as soon as new text paints.
 */
async function visit(page: Page, url: string): Promise<void> {
  // No request of the app's own /api open when the navigation cancels it (WebKit logs a cancelled fetch as a page error).
  await quiet(page);
  await page.goto(url);
}

async function reload(page: Page): Promise<void> {
  await quiet(page);
  await page.reload();
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

interface Detail {
  id: string;
  title: string;
  page_count: number;
  pages: { id: string; paper: string | null; canvas: string; body_omitted?: boolean }[];
}

/** Everything the page started is done: fonts, the notebook queue and the page's ink. */
async function quietNotebook(page: Page): Promise<void> {
  await settle(page);
  await expect(page.locator("[data-notebook-save]").first()).toHaveAttribute("data-notebook-save", "saved", {
    timeout: 20_000,
  });
  const ink = page.locator("[data-page-ink]");
  if (await ink.count()) await expect(ink).toHaveAttribute("data-ink-idle", "", { timeout: 20_000 });
}

/** A pen stroke across the page's ink layer at fractions of its width/height. */
async function penStroke(page: Page, y: number, x0 = 0.2, x1 = 0.7): Promise<void> {
  const layer = page.locator("[data-page-ink] [data-ink-layer]");
  await layer.scrollIntoViewIfNeeded();
  await page.evaluate(
    async ({ y, x0, x1 }) => {
      const el = document.querySelector("[data-page-ink] [data-ink-layer]") as HTMLElement | null;
      if (!el) throw new Error("ink layer missing");
      const r = el.getBoundingClientRect();
      const target = r.top + y * r.height;
      window.scrollTo({ top: window.scrollY + target - window.innerHeight / 2, behavior: "instant" });
      await new Promise((res) => requestAnimationFrame(() => requestAnimationFrame(res)));
      const rr = el.getBoundingClientRect();
      const mk = (kind: string, fx: number, buttons: number) =>
        new PointerEvent(kind, {
          pointerId: 7,
          pointerType: "pen",
          isPrimary: true,
          bubbles: true,
          cancelable: true,
          composed: true,
          clientX: rr.left + fx * rr.width,
          clientY: rr.top + y * rr.height + Math.sin(fx * 12) * 6,
          pressure: buttons ? 0.6 : 0,
          buttons,
          button: kind === "pointermove" ? -1 : 0,
          width: 1,
          height: 1,
        });
      const n = 24;
      el.dispatchEvent(mk("pointerdown", x0, 1));
      for (let i = 1; i < n; i++) {
        el.dispatchEvent(mk("pointermove", x0 + ((x1 - x0) * i) / (n - 1), 1));
        await new Promise((res) => setTimeout(res, 8));
      }
      el.dispatchEvent(mk("pointerup", x1, 0));
      await new Promise((res) => requestAnimationFrame(() => requestAnimationFrame(res)));
    },
    { y, x0, x1 },
  );
}

async function liveInk(page: Page, pageId: string): Promise<number> {
  const r = await api<{ items: { deleted?: boolean }[] }>(page, "GET", `/api/ink/notebook_page/${pageId}`);
  expect(r.status).toBe(200);
  return r.json.items.filter((i) => !i.deleted).length;
}

const notebookId = (page: Page) => new URL(page.url()).pathname.split("/")[3] as string;

test.describe("notebooks", () => {
  test("a notebook on each of the five papers keeps its ink across a reload", async ({ browser, baseURL }, info) => {
    test.setTimeout(180_000);
    const s = await student(browser, baseURL as string, info, "pp");
    try {
      const { page, guard, errors } = await s.open("/notes/notebooks");
      await expect(page.locator("[data-notebooks-empty]")).toBeVisible();
      await expect(page.getByRole("heading", { level: 1, name: "Notebooks" })).toBeVisible();
      await settle(page);
      await snap(page, info, guard, "shelf-empty");
      await expectNoAxeViolations(page);

      await page.locator("[data-new-notebook]").click();
      const dialog = page.getByRole("dialog", { name: "New notebook" });
      await expect(dialog).toBeVisible();
      await dialog.getByRole("button", { name: "Create notebook" }).click();
      await expect(dialog.getByText("Give the notebook a title.")).toBeVisible();
      await dialog.getByRole("textbox", { name: "Title" }).fill("Renal");
      // The radios are visually hidden behind their swatch/preview: choose them through their labels.
      await dialog.locator("label", { has: page.getByRole("radio", { name: "Violet", exact: true }) }).click();
      await expect(dialog.getByRole("radio", { name: "Violet", exact: true })).toBeChecked();
      await dialog.locator("label", { has: page.getByRole("radio", { name: "Ruled", exact: true }) }).click();
      await settle(page);
      await snap(page, info, guard, "new-notebook-dialog");
      await dialog.getByRole("button", { name: "Create notebook" }).click();

      await page.waitForURL(/\/notes\/notebooks\/[0-9a-f-]{36}\/1$/);
      await expect(page.locator("[data-page-position]")).toHaveText("page 1 of 1");
      await expect(page.locator("[data-paper]").first()).toHaveAttribute("data-paper", "ruled");
      await quietNotebook(page);

      for (const [i, paper] of PAPERS.entries()) {
        if (i > 0) {
          await page.locator("[data-add-page]").click();
          await page.waitForURL(new RegExp(`/${i + 1}$`));
          await expect(page.locator("[data-page-position]")).toHaveText(`page ${i + 1} of ${i + 1}`);
          await page.locator("[data-paper-choice]").getByRole("button", { name: PAPER_LABEL[paper] }).click();
        }
        await expect(page.locator("[data-page-ink] [data-paper]")).toHaveAttribute("data-paper", paper);
        await expect(page.locator("[data-page-ink]")).toHaveAttribute("data-ink-ready", "");
        await penStroke(page, 0.3);
        await penStroke(page, 0.5, 0.3, 0.9);
        await quietNotebook(page);
        await expect(page.locator("[data-ink-save]")).toHaveAttribute("data-ink-save", "saved");
        await snap(page, info, guard, `page-${paper}`, true);
      }
      await expectNoHorizontalOverflow(page);
      await expectNoAxeViolations(page);

      const id = notebookId(page);
      const detail = (await api<Detail>(page, "GET", `/api/notebooks/${id}`)).json;
      expect(detail.pages.map((p) => p.paper ?? "ruled")).toEqual([...PAPERS]);
      for (const p of detail.pages) expect(await liveInk(page, p.id)).toBeGreaterThanOrEqual(2);

      // Reload each page: the paper and the ink come back from the server.
      for (const [i, paper] of PAPERS.entries()) {
        await visit(page, `/notes/notebooks/${id}/${i + 1}`);
        await expect(page.locator("[data-page-position]")).toHaveText(`page ${i + 1} of 5`);
        await expect(page.locator("[data-page-ink] [data-paper]")).toHaveAttribute("data-paper", paper);
        await expect(page.locator("[data-page-ink]")).toHaveAttribute("data-ink-idle", "", { timeout: 20_000 });
        // The engine painted the stored strokes: the ink canvas is no longer blank.
        await expect
          .poll(
            () =>
              page.evaluate(() => {
                const imgs = [...document.querySelectorAll<HTMLImageElement>("[data-page-ink] img[data-ink-image]")];
                if (imgs.some((i) => i.src && i.complete)) return true;
                return [...document.querySelectorAll<HTMLCanvasElement>("[data-page-ink] canvas")].some((c) => {
                  const ctx = c.getContext("2d");
                  if (!ctx || !c.width || !c.height) return false;
                  const d = ctx.getImageData(0, 0, c.width, c.height).data;
                  for (let k = 3; k < d.length; k += 4 * 3) if ((d[k] ?? 0) > 0) return true;
                  return false;
                });
              }),
            { message: `ink drawn on page ${i + 1}`, timeout: 10_000 },
          )
          .toBe(true);
        await quietNotebook(page);
      }

      // Reorder and delete: page 5 (blank) moves first, then is deleted.
      await page.getByRole("button", { name: "Move page earlier" }).click();
      await page.waitForURL(/\/4$/);
      await quietNotebook(page);
      await page.locator("[data-delete-page]").click();
      const confirm = page.getByRole("dialog", { name: "Delete page 4?" });
      await expect(confirm).toBeVisible();
      await snap(page, info, guard, "delete-page-confirm");
      await confirm.getByRole("button", { name: "Delete page" }).click();
      await expect(page.locator("[data-page-position]")).toHaveText("page 4 of 4");
      await quietNotebook(page);
      const after = (await api<Detail>(page, "GET", `/api/notebooks/${id}`)).json;
      expect(after.pages.map((p) => p.paper ?? "ruled")).toEqual(["ruled", "dotted", "grid", "cornell"]);

      // The shelf counts the pages.
      await visit(page, "/notes/notebooks");
      await expect(page.locator(`[data-notebook="${id}"]`)).toContainText("4 pages");
      await expect(page.locator(`[data-notebook="${id}"]`)).toContainText("Renal");
      await settle(page);
      await snap(page, info, guard, "shelf");
      await expectNoAxeViolations(page);
      expect(errors).toEqual([]);
    } finally {
      await s.done();
    }
  });

  test("a whiteboard page saves its scene and opens it again", async ({ browser, baseURL }, info) => {
    test.setTimeout(150_000);
    const s = await student(browser, baseURL as string, info, "wb");
    try {
      const { page, guard, errors } = await s.open("/notes/notebooks");
      const created = await api<Detail>(page, "POST", "/api/notebooks", {
        title: "Pharm",
        cover: "amber",
        paper: "grid",
      });
      expect(created.status).toBe(201);
      await visit(page, `/notes/notebooks/${created.json.id}/1`);
      await quietNotebook(page);
      await page.locator("[data-add-whiteboard]").click();
      await page.waitForURL(/\/2$/);
      const board = page.locator("[data-whiteboard]");
      await expect(board).toBeVisible({ timeout: 30_000 });
      const canvas = board.locator("canvas.interactive");
      await expect(canvas).toBeVisible();
      const tools = page.getByRole("button", { name: "Ink tools", exact: true });
      await tools.click();
      await expect(board.locator('[data-testid="toolbar-rectangle"]')).toBeHidden();
      await expect(canvas).toBeVisible();
      await tools.click();
      // A rectangle, drawn with Excalidraw's own tool.
      // Excalidraw's tool radios sit under their icons: click the tool's label.
      await board.locator("label", { has: page.locator('[data-testid="toolbar-rectangle"]') }).click();
      const box = await canvas.boundingBox();
      if (!box) throw new Error("no whiteboard canvas");
      await page.mouse.move(box.x + box.width * 0.3, box.y + box.height * 0.3);
      await page.mouse.down();
      await page.mouse.move(box.x + box.width * 0.55, box.y + box.height * 0.5, { steps: 8 });
      await page.mouse.up();
      // A text element (searchable on the server).
      await board.locator("label", { has: page.locator('[data-testid="toolbar-text"]') }).click();
      await page.mouse.click(box.x + box.width * 0.35, box.y + box.height * 0.7);
      await page.keyboard.type("Loop diuretics");
      await page.keyboard.press("Escape");
      await expect(board).toHaveAttribute("data-elements", "2");
      await expect(page.locator("[data-notebook-save]").first()).toHaveAttribute("data-notebook-save", "saved", {
        timeout: 20_000,
      });
      await settle(page);
      await snap(page, info, guard, "whiteboard");

      const detail = (await api<Detail>(page, "GET", `/api/notebooks/${created.json.id}`)).json;
      const wb = detail.pages.find((p) => p.canvas === "whiteboard");
      expect(wb).toBeTruthy();
      const note = await api<{ body: { elements: { type: string }[] }; body_text: string }>(
        page,
        "GET",
        `/api/notes/${wb?.id}`,
      );
      expect(note.json.body.elements.map((e) => e.type).sort()).toEqual(["rectangle", "text"]);
      expect(note.json.body_text).toContain("Loop diuretics");

      await reload(page);
      await expect(page.locator("[data-whiteboard]")).toHaveAttribute("data-elements", "2", { timeout: 30_000 });
      await settle(page);
      await expectNoHorizontalOverflow(page);
      expect(errors).toEqual([]);
    } finally {
      await s.done();
    }
  });

  test("exports the notebook as a PDF with text and vector ink", async ({ browser, baseURL }, info) => {
    test.setTimeout(150_000);
    const s = await student(browser, baseURL as string, info, "pdf");
    try {
      const { page, guard, errors } = await s.open("/notes/notebooks");
      const created = await api<Detail>(page, "POST", "/api/notebooks", { title: "Renal export", paper: "cornell" });
      await visit(page, `/notes/notebooks/${created.json.id}/1`);
      await quietNotebook(page);
      await penStroke(page, 0.3);
      await penStroke(page, 0.45);
      await quietNotebook(page);
      const download = page.waitForEvent("download");
      await page.locator("[data-export-pdf]").click();
      const file = await download;
      expect(file.suggestedFilename()).toBe("Renal-export.pdf");
      const bytes = new Uint8Array(readFileSync((await file.path()) as string));
      await expect(page.locator("[data-export-pdf]")).toHaveText("Export PDF");
      await snap(page, info, guard, "page-cornell-exported");

      const pdfjs = await import("pdfjs-dist/legacy/build/pdf.mjs");
      const task = pdfjs.getDocument({ data: bytes, useSystemFonts: false });
      const doc = await task.promise;
      expect(doc.numPages).toBe(1);
      const p1 = await doc.getPage(1);
      const vp = p1.getViewport({ scale: 1 });
      expect([vp.width, vp.height]).toEqual([612, 792]);
      const text = (await p1.getTextContent()).items.map((i) => ("str" in i ? i.str : "")).join("");
      expect(text).toContain("Renal export · page 1 of 1");
      const ops = await p1.getOperatorList();
      const names = new Map(Object.entries(pdfjs.OPS).map(([k, v]) => [v, k]));
      // pdf.js folds the paint operator into constructPath (args[0]); args[1][0] is the path data.
      const paths = ops.fnArray.flatMap((f, i) =>
        names.get(f) === "constructPath"
          ? [
              {
                paint: names.get(ops.argsArray[i][0] as number),
                size: Object.keys(ops.argsArray[i][1]?.[0] ?? {}).length,
              },
            ]
          : [],
      );
      // The paper template's lines are stroked paths; the ink's perfect-freehand outlines are long filled curves.
      expect(paths.some((p) => p.paint === "stroke")).toBe(true);
      expect(paths.some((p) => p.paint === "fill" && p.size > 100)).toBe(true);
      expect(ops.fnArray.some((f) => names.get(f) === "showText")).toBe(true);
      await task.destroy();
      await settle(page);
      expect(errors).toEqual([]);
    } finally {
      await s.done();
    }
  });

  test("offline: edits are kept on the device and sync when back online", async ({ browser, baseURL }, info) => {
    test.setTimeout(120_000);
    const s = await student(browser, baseURL as string, info, "off");
    try {
      const { page, context, guard, errors, allowOffline } = await s.open("/notes/notebooks");
      const created = await api<Detail>(page, "POST", "/api/notebooks", { title: "Cardio", cover: "lime" });
      await visit(page, `/notes/notebooks/${created.json.id}/1`);
      await quietNotebook(page);
      allowOffline();
      await context.setOffline(true);
      await page.locator("[data-add-page]").click();
      await page.waitForURL(/\/2$/);
      await page.locator("[data-paper-choice]").getByRole("button", { name: "Dotted" }).click();
      await expect(page.locator("[data-notebook-save]").first()).toHaveAttribute("data-notebook-save", "local");
      await expect(page.locator("[data-notebook-save]").first()).toContainText("will sync");
      await expect(page.locator("[data-page-position]")).toHaveText("page 2 of 2");
      await settle(page);
      await snap(page, info, guard, "offline-will-sync");
      await context.setOffline(false);
      await page.evaluate(() => window.dispatchEvent(new Event("online")));
      await expect(page.locator("[data-notebook-save]").first()).toHaveAttribute("data-notebook-save", "saved", {
        timeout: 30_000,
      });
      const detail = (await api<Detail>(page, "GET", `/api/notebooks/${created.json.id}`)).json;
      expect(detail.pages.map((p) => p.paper)).toEqual([null, "dotted"]);
      await quietNotebook(page);
      expect(errors).toEqual([]);
    } finally {
      await s.done();
    }
  });

  test("states: loading, error, offline device copy, empty notebook", async ({ browser, baseURL }, info) => {
    test.setTimeout(120_000);
    const s = await student(browser, baseURL as string, info, "st");
    try {
      // serviceWorkers: "block": WebKit hands a reloaded page's requests to the worker, which page.route cannot see, so
      // the held and failed list requests below would never reach the routes.
      const { page, context, guard, errors, allowOffline } = await s.open("/notes/notebooks", baseURL as string, {
        serviceWorkers: "block",
      });
      await expect(page.locator("[data-notebooks-empty]")).toBeVisible();
      const created = await api<Detail>(page, "POST", "/api/notebooks", { title: "Biostats", cover: "surface-2" });
      expect(created.status).toBe(201);

      // Loading: the list request is held until the screenshot is taken.
      let release: () => void = () => undefined;
      const held = new Promise<void>((r) => {
        release = r;
      });
      await page.route("**/api/notebooks", async (route) => {
        await held;
        await route.continue();
      });
      await reload(page);
      await expect(page.locator("[data-notebooks] [aria-busy='true']")).toBeVisible();
      await snap(page, info, guard, "shelf-loading");
      release();
      await expect(page.locator(`[data-notebook="${created.json.id}"]`)).toBeVisible();
      await page.unroute("**/api/notebooks");
      await settle(page);
      await snap(page, info, guard, "shelf-one");

      // Error: the server answers 500 with the error envelope (no device copy is used for a server error).
      await page.route("**/api/notebooks", (route) =>
        route.fulfill({
          status: 500,
          contentType: "application/json",
          body: JSON.stringify({ error: { code: "internal", message: "internal", request_id: "req-e2e" } }),
        }),
      );
      const before = errors.length;
      await reload(page);
      await expect(page.getByRole("alert")).toContainText("Notebooks could not be loaded");
      await settle(page);
      await snap(page, info, guard, "shelf-error");
      await expectNoAxeViolations(page);
      // The deliberate 500 (and its console line) are this step's own.
      errors.splice(before);
      await page.unroute("**/api/notebooks");

      // Offline: the device copy of the list and of the notebook.
      await reload(page);
      await expect(page.locator(`[data-notebook="${created.json.id}"]`)).toBeVisible();
      await visit(page, `/notes/notebooks/${created.json.id}/1`);
      await quietNotebook(page);
      await visit(page, "/notes/notebooks");
      await expect(page.locator(`[data-notebook="${created.json.id}"]`)).toBeVisible();
      await quietNotebook(page);
      allowOffline();
      await context.setOffline(true);
      await page.evaluate(() => window.dispatchEvent(new Event("offline")));
      await expect(page.getByText("Showing the notebooks saved on this device", { exact: false })).toBeVisible();
      await snap(page, info, guard, "shelf-offline");
      await page.locator(`[data-notebook="${created.json.id}"] a`).click();
      await expect(page.locator("[data-page-position]")).toHaveText("page 1 of 1");
      await expect(page.getByText("Showing the copy saved on this device", { exact: false })).toBeVisible();
      await snap(page, info, guard, "page-offline", true);
      await context.setOffline(false);
      await page.evaluate(() => window.dispatchEvent(new Event("online")));
      allowOffline(false);

      // Empty: the only page deleted.
      await quietNotebook(page);
      await page.locator("[data-delete-page]").click();
      await page.getByRole("dialog", { name: "Delete page 1?" }).getByRole("button", { name: "Delete page" }).click();
      await expect(page.locator("[data-notebook-empty]")).toBeVisible();
      await quietNotebook(page);
      await snap(page, info, guard, "notebook-empty");
      await expectNoAxeViolations(page);
      // Add a page from the empty state.
      await page.locator("[data-notebook-empty]").getByRole("button", { name: "Add page" }).click();
      await expect(page.locator("[data-page-position]")).toHaveText("page 1 of 1");
      await quietNotebook(page);
      expect(errors).toEqual([]);
    } finally {
      await s.done();
    }
  });
});

test.describe("production server (strict CSP)", () => {
  const PROD = process.env.E2E_PROD_URL;
  test.skip(!PROD, "needs the local APP_ENV=prod API (not available against the live Space)");

  test("paper page, whiteboard and PDF on APP_ENV=prod with no console errors", async ({ browser, baseURL }, info) => {
    test.setTimeout(150_000);
    const s = await student(browser, baseURL as string, info, "prod");
    try {
      const { context } = await s.open("/notes/notebooks");
      const origin = new URL(PROD as string).origin;
      const state = await context.storageState();
      const prod = await browser.newContext({
        ...info.project.use,
        baseURL: PROD,
        ignoreHTTPSErrors: true,
        acceptDownloads: true,
        storageState: { cookies: [], origins: state.origins.map((o) => ({ ...o, origin })) },
      });
      try {
        const page = await prod.newPage();
        const { errors } = watch(page);
        await visit(page, "/notes/notebooks");
        const created = await api<Detail>(page, "POST", "/api/notebooks", { title: "CSP", paper: "dotted" });
        await visit(page, `/notes/notebooks/${created.json.id}/1`);
        await quietNotebook(page);
        await penStroke(page, 0.4);
        await quietNotebook(page);
        await page.locator("[data-add-whiteboard]").click();
        await expect(page.locator("[data-whiteboard] canvas.interactive")).toBeVisible({ timeout: 30_000 });
        await page.locator("label", { has: page.locator('[data-testid="toolbar-text"]') }).click();
        const box = await page.locator("[data-whiteboard] canvas.interactive").boundingBox();
        if (!box) throw new Error("no canvas");
        await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
        await page.keyboard.type("CSP");
        await page.keyboard.press("Escape");
        await expect(page.locator("[data-whiteboard]")).toHaveAttribute("data-elements", "1");
        // Excalidraw's hand-drawn font came from this origin (no CDN).
        const fontsFrom = await page.evaluate(() =>
          performance
            .getEntriesByType("resource")
            .map((e) => e.name)
            .filter((n) => /\.woff2|\.ttf/.test(n)),
        );
        expect(fontsFrom.length).toBeGreaterThan(0);
        expect(fontsFrom.filter((u) => !u.startsWith(origin))).toEqual([]);
        await expect(page.locator("[data-notebook-save]").first()).toHaveAttribute("data-notebook-save", "saved", {
          timeout: 20_000,
        });
        const download = page.waitForEvent("download");
        await page.locator("[data-export-pdf]").click();
        expect((await download).suggestedFilename()).toBe("CSP.pdf");
        await settle(page);
        expect(errors).toEqual([]);
      } finally {
        await prod.close();
      }
    } finally {
      await s.done();
    }
  });
});
