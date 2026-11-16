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
 * Segment 12b: ink persistence and cross-device sync against the real API and database. Each test signs in its own
 * throwaway user per browser project (deleted afterwards), so parallel projects never share a layer. A "device" is a
 * browser context: separate IndexedDB, same account.
 */
test.skip(!canMintUsers(), "signed-in tests need the service role (run pnpm e2e:live-auth against the Space)");

// Paragraph, table, paragraph, paragraph, ...: plain text blocks with nothing violet in them (the ink colour we detect).
const SECTION = "/learn/ch05/ch05-s02";
const SURFACE = '[data-ink-surface][aria-label="Your ink on this section"]';
const SAVE = "[data-ink-save]";

const snap = (page: Page, info: TestInfo, guard: ConsoleGuard, state: string, full = false) =>
  guard.duringTool(async () => {
    await snapSegment(page, info, guard, "12b", state, full);
    await page.waitForTimeout(500);
  });

function emailFor(info: TestInfo, tag: string): string {
  const run = String.fromCharCode(97 + (info.repeatEachIndex % 26), 97 + (info.retry % 26));
  const role = `ink${tag}${info.project.name.replace(/[^a-z]/g, "")}${run}`;
  return `naadi-e2e-${role}-${process.env.E2E_RUN_ID}@example.com`;
}

const guards = new WeakMap<Page, ConsoleGuard>();

function consoleOf(page: Page) {
  const errors: string[] = [];
  let tool = 0;
  let allowed: string[] = [];
  page.on("console", (m) => {
    if (m.type() !== "error") return;
    const text = m.text();
    if (tool > 0 && text.startsWith("Refused to apply a stylesheet")) return;
    const where = m.location().url ?? "";
    if (allowed.some((a) => text.includes(a) || where.includes(a))) return;
    errors.push(text);
  });
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  const guard: ConsoleGuard = {
    allowHttpError: ({ urlPart }) => {
      allowed = [...allowed, urlPart];
    },
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
  guards.set(page, guard);
  // Offline requests (route.abort) are reported by the browser as console errors.
  const allowOffline = () => {
    allowed = [...allowed, "/api/ink/", "net::ERR_FAILED", "Failed to load resource", "Load failed", "NetworkError"];
  };
  return { errors, guard, allowOffline };
}

/** One student, several "devices" (contexts). */
async function student(browser: Browser, baseURL: string, info: TestInfo, tag: string) {
  const email = emailFor(info, tag);
  runUsers(["delete", "--email", email]);
  const contexts: BrowserContext[] = [];
  const device = async (viewport?: { width: number; height: number }) => {
    const context = await browser.newContext({ ...info.project.use, ...(viewport ? { viewport } : {}), baseURL });
    contexts.push(context);
    const page = await context.newPage();
    const link = toLink(runUsers(["link", "--email", email]));
    await page.goto(callbackPath(link, SECTION));
    await page.waitForURL((u) => u.pathname === SECTION, { timeout: 30_000 });
    await page.evaluate(() => document.fonts.ready);
    await expect(page.locator(SAVE)).toHaveAttribute("data-ink-save", "saved", { timeout: 20_000 });
    return page;
  };
  const done = async () => {
    try {
      for (const c of contexts) await c.close().catch(() => undefined);
    } finally {
      runUsers(["delete", "--email", email]);
    }
  };
  return { device, done };
}

/** A pen stroke at fractions (x) of the ink surface and an absolute y (CSS px from the surface top). */
async function penStroke(page: Page, x0: number, x1: number, yPx: number, pointerId = 2) {
  await showBand(page, yPx);
  await page.evaluate(
    async ({ sel, x0, x1, yPx, pointerId }) => {
      const layer = document.querySelector(`${sel} [data-ink-layer]`) as HTMLElement | null;
      if (!layer) throw new Error("ink layer missing");
      const r = layer.getBoundingClientRect();
      const mk = (kind: string, fx: number, buttons: number) =>
        new PointerEvent(kind, {
          pointerId,
          pointerType: "pen",
          isPrimary: true,
          bubbles: true,
          cancelable: true,
          composed: true,
          clientX: r.left + fx * r.width,
          clientY: r.top + yPx,
          pressure: buttons ? 0.6 : 0,
          buttons,
          button: kind === "pointermove" ? -1 : 0,
          width: 1,
          height: 1,
        });
      const n = 24;
      layer.dispatchEvent(mk("pointerdown", x0, 1));
      for (let i = 1; i < n; i++) {
        layer.dispatchEvent(mk("pointermove", x0 + ((x1 - x0) * i) / (n - 1), 1));
        await new Promise((res) => setTimeout(res, 8));
      }
      layer.dispatchEvent(mk("pointerup", x1, 0));
      await new Promise((res) => requestAnimationFrame(() => requestAnimationFrame(res)));
    },
    { sel: SURFACE, x0, x1, yPx, pointerId },
  );
}

/** Scroll so that `yPx` (CSS px from the ink surface's top) is in the middle of the viewport. */
async function showBand(page: Page, yPx: number) {
  await page.evaluate(
    ({ sel, yPx }) => {
      const s = document.querySelector(sel) as HTMLElement;
      const top = s.getBoundingClientRect().top + window.scrollY;
      window.scrollTo({ top: Math.max(0, top + yPx - window.innerHeight / 2), behavior: "instant" });
    },
    { sel: SURFACE, yPx },
  );
  await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
}

/** Top of the n-th top-level block, CSS px from the ink surface's top. */
async function blockTop(page: Page, n: number): Promise<number> {
  return page.evaluate(
    ({ sel, n }) => {
      const s = document.querySelector(sel) as HTMLElement;
      const b = s.querySelectorAll<HTMLElement>("[data-block-pm]")[n];
      if (!b) throw new Error(`no block ${n}`);
      return b.getBoundingClientRect().top - s.getBoundingClientRect().top;
    },
    { sel: SURFACE, n },
  );
}

/**
 * Vertical centre (CSS px from the surface top) of the violet ink in a band of the surface, or null when there is
 * none. Read from a screenshot, so it is what the student sees.
 */
async function violetCentre(page: Page, fromPx: number, toPx: number): Promise<number | null> {
  await showBand(page, (fromPx + toPx) / 2);
  const box = await page.locator(SURFACE).boundingBox();
  if (!box) throw new Error("no surface");
  const top = Math.max(0, fromPx);
  const height = Math.max(1, toPx - top);
  await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
  const shoot = () =>
    page.screenshot({ clip: { x: box.x, y: box.y + top, width: box.width, height }, animations: "disabled" });
  // WebKit reports the screenshot tool's <style> as refused by our CSP: the tool's noise, not the app's.
  const g = guards.get(page);
  const png = g ? await g.duringTool(shoot) : await shoot();
  const centre = await page.evaluate(async (b64) => {
    const img = new Image();
    img.src = `data:image/png;base64,${b64}`;
    await img.decode();
    const c = document.createElement("canvas");
    c.width = img.width;
    c.height = img.height;
    const ctx = c.getContext("2d") as CanvasRenderingContext2D;
    ctx.drawImage(img, 0, 0);
    const d = ctx.getImageData(0, 0, c.width, c.height).data;
    let sum = 0;
    let weight = 0;
    for (let y = 0; y < c.height; y++) {
      for (let x = 0; x < c.width; x++) {
        const i = (y * c.width + x) * 4;
        const r = d[i] ?? 0;
        const g = d[i + 1] ?? 0;
        const bl = d[i + 2] ?? 0;
        const v = bl - Math.max(r * 0.6, g); // violet: blue well above green
        if (v > 40 && bl > 120) {
          sum += (y + 0.5) * v;
          weight += v;
        }
      }
    }
    return weight > 0 ? { y: sum / weight, scale: img.height } : null;
  }, png.toString("base64"));
  if (!centre) return null;
  return top + (centre.y * height) / centre.scale;
}

async function turnInkOn(page: Page) {
  await page.getByRole("button", { name: "Write" }).click();
  await expect(page.getByRole("toolbar", { name: "Ink tools" })).toBeVisible();
}

async function pickViolet(page: Page) {
  await page.getByRole("button", { name: "Ink violet" }).click();
}

async function refetch(page: Page) {
  // TanStack Query refetches on visibilitychange (the page coming back), like switching back to the tab.
  await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange", { bubbles: true })));
}

/** One refetch (a refetch while another is in flight is merged into it, so polls ask again), then read. */
async function refetchThen<T>(page: Page, read: () => Promise<T>): Promise<T> {
  await refetch(page);
  await page.waitForTimeout(300);
  return read();
}

const POLL = { timeout: 20_000, intervals: [500, 1000, 2000] };

test.describe("ink on a reader section", () => {
  test.setTimeout(180_000);

  test("draw → saved → reload → still there; a second device sees it in one refetch", async ({
    browser,
    baseURL,
  }, info) => {
    const s = await student(browser, baseURL as string, info, "sync");
    try {
      const a = await s.device();
      const ca = consoleOf(a);
      await expect(a.getByRole("button", { name: "Write" })).toHaveAttribute("aria-pressed", "false");
      await expectNoAxeViolations(a);
      await snap(a, info, ca.guard, "reader-ink-off");
      await turnInkOn(a);
      // One lime action while writing: the toolbar's "Ink on", the page bar's Next is out of the way.
      await expect(a.getByRole("navigation", { name: "Sections" })).toHaveCount(0);
      await pickViolet(a);
      const y = (await blockTop(a, 2)) + 10;
      const saved = a.waitForResponse((r) => r.url().includes("/api/ink/section/") && r.request().method() === "POST");
      await penStroke(a, 0.1, 0.45, y);
      await expect(a.locator(SAVE)).toHaveAttribute("data-ink-save", /saving|saved/);
      expect((await saved).status()).toBe(200);
      await expect(a.locator(SAVE)).toHaveAttribute("data-ink-save", "saved");
      await expect(a.locator(SAVE)).toContainText("Saved");
      expect(await violetCentre(a, y - 12, y + 12)).not.toBeNull();
      await expectNoAxeViolations(a);
      await expectNoHorizontalOverflow(a);
      await snap(a, info, ca.guard, "reader-ink-saved");

      await a.reload();
      await expect(a.locator(SAVE)).toHaveAttribute("data-ink-save", "saved", { timeout: 20_000 });
      await expect.poll(() => violetCentre(a, y - 12, y + 12)).not.toBeNull();

      const b = await s.device();
      const cb = consoleOf(b);
      await expect.poll(() => violetCentre(b, y - 12, y + 12)).not.toBeNull();
      // A second stroke on A reaches B within one refetch, without a reload.
      await turnInkOn(a);
      await pickViolet(a);
      const y2 = (await blockTop(a, 3)) + 10;
      const sent = a.waitForResponse((r) => r.url().includes("/changes") && r.status() === 200);
      await penStroke(a, 0.2, 0.6, y2, 3);
      await sent;
      expect(await violetCentre(b, y2 - 12, y2 + 12)).toBeNull();
      const got = b.waitForResponse((r) => r.url().includes("/api/ink/section/") && r.url().includes("since="));
      await refetch(b);
      await got;
      await expect.poll(() => violetCentre(b, y2 - 12, y2 + 12)).not.toBeNull();
      expect(ca.errors).toEqual([]);
      expect(cb.errors).toEqual([]);
    } finally {
      await s.done();
    }
  });

  test("delete on one device, edit on the other: both end on the last write", async ({ browser, baseURL }, info) => {
    const s = await student(browser, baseURL as string, info, "lww");
    try {
      const a = await s.device();
      await turnInkOn(a);
      await pickViolet(a);
      const y = (await blockTop(a, 2)) + 10;
      const first = a.waitForResponse((r) => r.url().includes("/changes") && r.status() === 200);
      await penStroke(a, 0.1, 0.45, y);
      await first;
      const b = await s.device();
      const cb = consoleOf(b);
      cb.allowOffline();
      await expect.poll(() => violetCentre(b, y - 12, y + 12)).not.toBeNull();
      // B does not hear about A's deletion before its own edit (its refetches fail for a moment).
      await b.route(/\/api\/ink\/section\/[^/]+\?since=/, (route) => route.abort("internetdisconnected"));
      // A erases it (whole-stroke eraser) ...
      await a.getByRole("button", { name: "Eraser", exact: true }).click();
      const erased = a.waitForResponse((r) => r.url().includes("/changes") && r.status() === 200);
      await penStroke(a, 0.25, 0.3, y, 4);
      await erased;
      expect(await violetCentre(a, y - 12, y + 12)).toBeNull();
      // ... then B, which has not seen the deletion, edits the stroke later: it erases and undoes, and the undo
      // re-stamps the stroke with B's (later) time, so B's version is the last write.
      await turnInkOn(b);
      await b.getByRole("button", { name: "Eraser", exact: true }).click();
      await penStroke(b, 0.25, 0.3, y, 5);
      const bSent = b.waitForResponse((r) => r.url().includes("/changes") && r.status() === 200);
      await b.getByRole("button", { name: "Undo" }).click();
      await bSent;
      await expect(b.locator(SAVE)).toHaveAttribute("data-ink-save", "saved");
      await expect.poll(() => violetCentre(b, y - 12, y + 12)).not.toBeNull();
      await b.unroute(/\/api\/ink\/section\/[^/]+\?since=/);
      // Last writer wins on both devices: A gets the stroke back on its next refetch.
      await expect.poll(() => refetchThen(a, () => violetCentre(a, y - 12, y + 12)), POLL).not.toBeNull();
      // And the reverse: A deletes again (now the latest write); B converges on "gone".
      await penStroke(a, 0.25, 0.3, y, 6);
      await a.waitForResponse((r) => r.url().includes("/changes") && r.status() === 200);
      await expect.poll(() => refetchThen(b, () => violetCentre(b, y - 12, y + 12)), POLL).toBeNull();
    } finally {
      await s.done();
    }
  });

  test("offline: draw → saved on this device → reload → reconnect → synced", async ({ browser, baseURL }, info) => {
    const s = await student(browser, baseURL as string, info, "off");
    try {
      const a = await s.device();
      const ca = consoleOf(a);
      ca.allowOffline();
      await a.route("**/api/ink/**", (route) => route.abort("internetdisconnected"));
      await turnInkOn(a);
      await pickViolet(a);
      const y = (await blockTop(a, 2)) + 10;
      await penStroke(a, 0.1, 0.45, y);
      await expect(a.locator(SAVE)).toHaveAttribute("data-ink-save", "local", { timeout: 15_000 });
      await expect(a.locator(SAVE)).toContainText("Saved on this device · will sync");
      await expectNoAxeViolations(a);
      await snap(a, info, ca.guard, "reader-ink-local");
      await a.reload();
      await expect(a.locator(SURFACE)).toBeVisible({ timeout: 20_000 });
      await expect.poll(() => violetCentre(a, y - 12, y + 12), { timeout: 15_000 }).not.toBeNull();
      await expect(a.locator(SAVE)).toHaveAttribute("data-ink-save", "local", { timeout: 15_000 });
      await a.unroute("**/api/ink/**");
      // Back online: the next refetch (or the retry timer) syncs it. The pagehide keepalive POST of the reload may
      // already have delivered it (keepalive requests outlive the page and bypass the test's route), in which case
      // the refetch finds the same version on the server and the device adopts it: saved either way.
      await expect.poll(() => refetchThen(a, () => a.locator(SAVE).getAttribute("data-ink-save")), POLL).toBe("saved");
      const b = await s.device();
      await expect.poll(() => violetCentre(b, y - 12, y + 12)).not.toBeNull();
    } finally {
      await s.done();
    }
  });

  test("a stroke stays beside its paragraph from 1180 to 390 px wide (within 1 px)", async ({
    browser,
    baseURL,
  }, info) => {
    const s = await student(browser, baseURL as string, info, "flow");
    try {
      const a = await s.device({ width: 1180, height: 820 });
      await turnInkOn(a);
      await pickViolet(a);
      const top1 = await blockTop(a, 2);
      const y = top1 + 14;
      const sent = a.waitForResponse((r) => r.url().includes("/changes") && r.status() === 200);
      await penStroke(a, 0.05, 0.35, y);
      await sent;
      const c1 = await violetCentre(a, y - 14, y + 14);
      expect(c1).not.toBeNull();
      const w1 = (await a.locator(SURFACE).boundingBox())?.width ?? 0;
      await a.setViewportSize({ width: 390, height: 844 });
      await a.waitForTimeout(400); // reflow, re-measure, re-anchor
      const top2 = await blockTop(a, 2);
      const w2 = (await a.locator(SURFACE).boundingBox())?.width ?? 0;
      expect(top2 / w2).toBeGreaterThan(top1 / w1 + 0.05); // the text really reflowed (block moved down in su)
      const expected = top2 + ((c1 as number) - top1) * (w2 / w1);
      const c2 = await violetCentre(a, expected - 20, expected + 20);
      expect(c2).not.toBeNull();
      expect(Math.abs((c2 as number) - expected)).toBeLessThanOrEqual(1);
      await snap(a, info, consoleOf(a).guard, "reader-ink-reflow-390");
    } finally {
      await s.done();
    }
  });

  test("states: saving error with retry, section updated notice, toolbar on phone", async ({
    browser,
    baseURL,
  }, info) => {
    const s = await student(browser, baseURL as string, info, "states");
    try {
      const a = await s.device();
      const ca = consoleOf(a);
      ca.guard.allowHttpError({ urlPart: "/api/ink/", status: 500 });
      await a.route("**/api/ink/**/changes", (route) =>
        route.fulfill({
          status: 500,
          contentType: "application/json",
          body: JSON.stringify({ error: { code: "internal_error", message: "x", request_id: "e2e-ink-500" } }),
        }),
      );
      await turnInkOn(a);
      await snap(a, info, ca.guard, "reader-ink-on");
      const y = (await blockTop(a, 2)) + 10;
      await penStroke(a, 0.1, 0.45, y);
      await expect(a.locator(SAVE)).toHaveAttribute("data-ink-save", "error", { timeout: 15_000 });
      await expect(a.getByRole("alert")).toContainText("Couldn't save");
      await expectNoAxeViolations(a);
      await snap(a, info, ca.guard, "reader-ink-error");
      await a.unroute("**/api/ink/**/changes");
      await a.getByRole("button", { name: "Retry" }).click();
      await expect(a.locator(SAVE)).toHaveAttribute("data-ink-save", "saved", { timeout: 15_000 });
      // The section's text changed after this ink was drawn (served as such): the notice, ink kept.
      await a.route("**/api/ink/section/**", async (route) => {
        if (route.request().method() !== "GET") return route.continue();
        const res = await route.fetch();
        const body = (await res.json()) as Record<string, unknown>;
        await route.fulfill({ response: res, json: { ...body, outdated: 1 } });
      });
      await a.reload();
      await expect(a.getByRole("note").filter({ hasText: "This section was updated" })).toBeVisible({
        timeout: 20_000,
      });
      await expectNoAxeViolations(a);
      await snap(a, info, ca.guard, "reader-ink-outdated");
      expect(ca.errors).toEqual([]);
    } finally {
      await s.done();
    }
  });
});

test.describe("Settings → Pencil & ink", () => {
  test("pen-only and predicted ink save to the account", async ({ browser, baseURL }, info) => {
    const s = await student(browser, baseURL as string, info, "set");
    try {
      const a = await s.device();
      const ca = consoleOf(a);
      await a.goto("/settings/ink");
      const penOnly = a.getByRole("switch", { name: "Only Pencil draws" });
      await expect(penOnly).toHaveAttribute("aria-checked", "true");
      await expectNoAxeViolations(a);
      await expectNoHorizontalOverflow(a);
      await snap(a, info, ca.guard, "settings-ink");
      const saved = a.waitForResponse((r) => r.url().endsWith("/api/me") && r.request().method() === "PATCH");
      await penOnly.click();
      expect((await saved).status()).toBe(200);
      await expect(penOnly).toHaveAttribute("aria-checked", "false");
      await a.reload();
      await expect(a.getByRole("switch", { name: "Only Pencil draws" })).toHaveAttribute("aria-checked", "false");
      await expect(a.getByRole("switch", { name: "Predicted ink" })).toHaveAttribute("aria-checked", "true");
      await snap(a, info, ca.guard, "settings-ink-changed");
      await a.getByRole("navigation", { name: "Settings sections" }).getByRole("link", { name: "Account" }).click();
      await expect(a).toHaveURL(/\/settings\/account$/);
      expect(ca.errors).toEqual([]);
    } finally {
      await s.done();
    }
  });
});
