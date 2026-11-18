import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
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
// The production build registers a service worker (Web Push). In WebKit, page.route no longer sees the requests of
// a page that worker controls (every load after the first), and these tests route the ink API. They test sync,
// not push, so the worker is blocked.
const NO_SW = "block" as const;

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
    // Trusted Types ship report-only (security.py): csp.spec.ts collects those reports; they are not errors.
    if (/\[Report Only\]|report-only|Report-Only/i.test(text)) return;
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
  const device = async (viewport?: { width: number; height: number }, opts: { clockAheadMs?: number } = {}) => {
    const context = await browser.newContext({
      ...info.project.use,
      ...(viewport ? { viewport } : {}),
      baseURL,
      serviceWorkers: NO_SW,
    });
    contexts.push(context);
    if (opts.clockAheadMs) {
      // This device's clock runs fast (Date.now only: timers and animation frames run as usual).
      await context.addInitScript((ms) => {
        const real = Date.now.bind(Date);
        Date.now = () => real() + ms;
      }, opts.clockAheadMs);
    }
    const page = await context.newPage();
    if (process.env.INK_CPU_THROTTLE && info.project.use.browserName === "chromium") {
      const cdp = await context.newCDPSession(page);
      await cdp.send("Emulation.setCPUThrottlingRate", { rate: Number(process.env.INK_CPU_THROTTLE) });
    }
    const link = toLink(runUsers(["link", "--email", email]));
    await page.goto(callbackPath(link, SECTION));
    await page.waitForURL((u) => u.pathname === SECTION, { timeout: 30_000 });
    await page.evaluate(() => document.fonts.ready);
    await expect(page.locator(SAVE)).toHaveAttribute("data-ink-save", "saved", { timeout: 20_000 });
    await expect(page.locator("[data-ink-idle]")).toBeAttached({ timeout: 20_000 }); // loaded, nothing in flight
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
  await page.evaluate(() => window.getSelection()?.removeAllRanges());
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
  if (process.env.CI) {
    // What the detector saw, kept with the run's screenshots (the CI artifact) so a red run can be read.
    const dir = path.resolve(import.meta.dirname, "../../..", "test-results/screens/12b/probe");
    mkdirSync(dir, { recursive: true });
    writeFileSync(
      path.join(
        dir,
        `${test.info().title.slice(0, 24).replace(/\W+/g, "-")}-${test.info().project.name}-${Date.now()}.png`,
      ),
      png,
    );
  }
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
    // A stroke is a long run of violet in a row; anti-aliased text (sub-pixel colour fringes on some renderers)
    // only scatters a few bluish pixels per row. Rows count when at least 10 % of the band is violet.
    const minRun = Math.max(12, Math.floor(c.width * 0.1));
    let sum = 0;
    let weight = 0;
    for (let y = 0; y < c.height; y++) {
      let n = 0;
      for (let x = 0; x < c.width; x++) {
        const i = (y * c.width + x) * 4;
        const r = d[i] ?? 0;
        const g = d[i + 1] ?? 0;
        const bl = d[i + 2] ?? 0;
        // Ink violet (#c4baff on dark): blue well above green, and red above green.
        if (bl - g > 40 && bl > 120 && r > g + 4) n++;
      }
      if (n >= minRun) {
        sum += (y + 0.5) * n;
        weight += n;
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
      await expect(a.getByRole("button", { name: "Eraser", exact: true })).toHaveAttribute("aria-pressed", "true");
      const erased = a.waitForResponse((r) => r.url().includes("/changes") && r.status() === 200);
      await penStroke(a, 0.25, 0.3, (await violetCentre(a, y - 12, y + 12)) ?? y, 4);
      await erased;
      expect(await violetCentre(a, y - 12, y + 12)).toBeNull();
      // ... then B, which has not seen the deletion, edits the stroke later: it erases and undoes, and the undo
      // re-stamps the stroke with B's (later) time, so B's version is the last write.
      await turnInkOn(b);
      await b.getByRole("button", { name: "Eraser", exact: true }).click();
      await expect(b.getByRole("button", { name: "Eraser", exact: true })).toHaveAttribute("aria-pressed", "true");
      await penStroke(b, 0.25, 0.3, (await violetCentre(b, y - 12, y + 12)) ?? y, 5);
      const bSent = b.waitForResponse((r) => r.url().includes("/changes") && r.status() === 200);
      await b.getByRole("button", { name: "Undo" }).click();
      await bSent;
      await expect(b.locator(SAVE)).toHaveAttribute("data-ink-save", "saved");
      await expect.poll(() => violetCentre(b, y - 12, y + 12)).not.toBeNull();
      await b.unroute(/\/api\/ink\/section\/[^/]+\?since=/);
      // Last writer wins on both devices: A gets the stroke back on its next refetch.
      await expect.poll(() => refetchThen(a, () => violetCentre(a, y - 12, y + 12)), POLL).not.toBeNull();
      // And the reverse: A deletes again (now the latest write); B converges on "gone".
      await penStroke(a, 0.25, 0.3, (await violetCentre(a, y - 12, y + 12)) ?? y, 6);
      await a.waitForResponse((r) => r.url().includes("/changes") && r.status() === 200);
      await expect.poll(() => refetchThen(b, () => violetCentre(b, y - 12, y + 12)), POLL).toBeNull();
    } finally {
      await s.done();
    }
  });

  test("a device whose clock runs 10 minutes fast stamps server time; another device's delete sticks", async ({
    browser,
    baseURL,
  }, info) => {
    const s = await student(browser, baseURL as string, info, "clock");
    try {
      const a = await s.device(undefined, { clockAheadMs: 600_000 });
      expect(await a.evaluate(() => Date.now() - performance.timeOrigin - performance.now())).toBeGreaterThan(590_000);
      await turnInkOn(a);
      await pickViolet(a);
      const y = (await blockTop(a, 2)) + 10;
      const sent = a.waitForResponse((r) => r.url().includes("/changes") && r.status() === 200);
      await penStroke(a, 0.1, 0.45, y);
      const res = await sent;
      const out = (await res.json()) as { server_now: number; applied: string[] };
      const body = res.request().postDataJSON() as { items: { updatedAt: number }[] };
      // Stamped with the server's clock (learned from the load), not 10 minutes ahead.
      for (const item of body.items) expect(Math.abs(item.updatedAt - out.server_now)).toBeLessThan(30_000);
      expect(out.applied.length).toBeGreaterThan(0);
      // B (correct clock) erases it a moment later ...
      const b = await s.device();
      await expect.poll(() => violetCentre(b, y - 12, y + 12)).not.toBeNull();
      await turnInkOn(b);
      await b.getByRole("button", { name: "Eraser", exact: true }).click();
      const erased = b.waitForResponse((r) => r.url().includes("/changes") && r.status() === 200);
      await penStroke(b, 0.25, 0.3, (await violetCentre(b, y - 12, y + 12)) ?? y, 4);
      await erased;
      expect(await violetCentre(b, y - 12, y + 12)).toBeNull();
      // ... and A takes the deletion on its next refetch: the fast clock does not keep the stroke alive.
      await expect.poll(() => refetchThen(a, () => violetCentre(a, y - 12, y + 12)), POLL).toBeNull();
      // A keeps writing elsewhere; after a reload on both, the erased stroke is still gone.
      const y2 = (await blockTop(a, 4)) + 10;
      const more = a.waitForResponse((r) => r.url().includes("/changes") && r.status() === 200);
      await penStroke(a, 0.1, 0.45, y2, 6);
      await more;
      await a.reload();
      await expect(a.locator(SAVE)).toHaveAttribute("data-ink-save", "saved", { timeout: 20_000 });
      await expect(a.locator("[data-ink-idle]")).toBeAttached({ timeout: 20_000 });
      expect(await violetCentre(a, y - 12, y + 12)).toBeNull();
      await expect.poll(() => violetCentre(a, y2 - 12, y2 + 12)).not.toBeNull();
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

  test("colour wheel ink crosses both margins and syncs in the original text coordinates", async ({
    browser,
    baseURL,
  }, info) => {
    const s = await student(browser, baseURL as string, info, "margins");
    try {
      const page = await s.device({ width: 1440, height: 900 });
      await turnInkOn(page);
      await page.getByRole("button", { name: "Colour wheel" }).click();
      const dialog = page.getByRole("dialog", { name: "Colour wheel" });
      await dialog.getByLabel("Exact colour").fill("#1199dd");
      await dialog.getByRole("button", { name: "Done" }).click();
      const saving = page.waitForRequest((r) => r.url().includes("/changes") && r.method() === "POST");
      await penStroke(page, 0.02, 0.98, (await blockTop(page, 2)) + 10);
      const payload = (await saving).postDataJSON();
      const stroke = payload.items.find((item: { color: string }) => item.color === "#1199dd");
      expect(stroke.points[0][0]).toBeLessThan(0);
      expect(stroke.points.at(-1)[0]).toBeGreaterThan(1);
      await expect(page.locator(SAVE)).toHaveAttribute("data-ink-save", "saved");
      await page.reload();
      await expect(page.locator(SAVE)).toHaveAttribute("data-ink-save", "saved");
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
      const w1 = (await a.locator("[data-ink-column]").boundingBox())?.width ?? 0;
      await a.setViewportSize({ width: 390, height: 844 });
      await a.waitForTimeout(400); // reflow, re-measure, re-anchor
      const top2 = await blockTop(a, 2);
      const w2 = (await a.locator("[data-ink-column]").boundingBox())?.width ?? 0;
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

  test("an edited section: ink beside a paragraph that changed stays where it was drawn, with the notice", async ({
    browser,
    baseURL,
  }, info) => {
    const s = await student(browser, baseURL as string, info, "edit");
    try {
      const a = await s.device();
      const ca = consoleOf(a);
      await turnInkOn(a);
      await pickViolet(a);
      const y = (await blockTop(a, 2)) + 10;
      const sent = a.waitForResponse((r) => r.url().includes("/changes") && r.status() === 200);
      await penStroke(a, 0.1, 0.45, y);
      await sent;
      const drawn = await violetCentre(a, y - 14, y + 14);
      expect(drawn).not.toBeNull();
      // The author inserts a paragraph above it: every later block starts further down, and the stroke's block
      // position now belongs to another block (served as such; the API counts the stroke as outdated).
      const sectionId = SECTION.split("/").pop() as string;
      await a.route(/\/api\/chapters\/[^/?]+(\?|$)/, async (route) => {
        const res = await route.fetch();
        const body = (await res.json()) as { sections: { id: string; body: { content: unknown[] } }[] };
        for (const sec of body.sections) {
          if (sec.id !== sectionId) continue;
          const words = "An inserted paragraph that pushes the rest of the section down by a few lines. ".repeat(6);
          sec.body.content = [{ type: "paragraph", content: [{ type: "text", text: words }] }, ...sec.body.content];
        }
        await route.fulfill({ response: res, json: body });
      });
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
      await expect(a.locator("[data-ink-idle]")).toBeAttached({ timeout: 20_000 });
      const moved = (await blockTop(a, 3)) - (await blockTop(a, 2));
      expect(moved).toBeGreaterThan(0);
      // Same place as drawn (its anchor no longer matches a block), not beside whatever block took its position.
      const now = await violetCentre(a, (drawn as number) - 20, (drawn as number) + 20);
      expect(now).not.toBeNull();
      expect(Math.abs((now as number) - (drawn as number))).toBeLessThanOrEqual(1);
      await snap(a, info, ca.guard, "reader-ink-edited-section");
      expect(ca.errors).toEqual([]);
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
      // The server refuses an item as invalid: it is kept on the device (quarantine) and the student decides.
      await a.route("**/api/ink/**/changes", async (route) => {
        const sent = route.request().postDataJSON() as { base_rev: number; items: { id: string }[] };
        const res = await route.fetch({ postData: JSON.stringify({ base_rev: sent.base_rev, items: [] }) });
        const body = (await res.json()) as Record<string, unknown>;
        const rejected = sent.items.map((i) => ({ id: i.id, reason: "invalid" }));
        await route.fulfill({ response: res, json: { ...body, applied: [], rejected } });
      });
      const y3 = (await blockTop(a, 3)) + 10;
      await pickViolet(a);
      await penStroke(a, 0.1, 0.45, y3, 7);
      await expect(a.locator(SAVE)).toHaveAttribute("data-ink-save", "error", { timeout: 15_000 });
      await expect(a.getByRole("alert")).toContainText("Some ink couldn't be saved");
      await expect.poll(() => violetCentre(a, y3 - 12, y3 + 12)).not.toBeNull(); // still on the page
      await expectNoAxeViolations(a);
      await snap(a, info, ca.guard, "reader-ink-rejected");
      await a.getByRole("button", { name: "Discard" }).click();
      await expect(a.locator(SAVE)).toHaveAttribute("data-ink-save", "saved", { timeout: 15_000 });
      await expect.poll(() => violetCentre(a, y3 - 12, y3 + 12)).toBeNull();
      await a.unroute("**/api/ink/**/changes");
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

test.describe("ink code not loaded yet, then offline", () => {
  test.setTimeout(120_000);

  test("the section stays readable, Write says it needs a connection, and ink loads when back online", async ({
    browser,
    baseURL,
  }, info) => {
    const email = emailFor(info, "lazyoff");
    runUsers(["delete", "--email", email]);
    const context = await browser.newContext({ ...info.project.use, baseURL, serviceWorkers: NO_SW });
    try {
      const page = await context.newPage();
      const c = consoleOf(page);
      c.allowOffline();
      // The ink chunk cannot be fetched (as if the device went offline before it ever loaded).
      let blocked = true;
      await page.route(/\/assets\/SectionInkLive-[^/]+\.js$/, (route) =>
        blocked ? route.abort("internetdisconnected") : route.continue(),
      );
      await page.goto(callbackPath(toLink(runUsers(["link", "--email", email])), SECTION));
      await page.waitForURL((u) => u.pathname === SECTION, { timeout: 30_000 });
      await expect(page.locator(SURFACE).locator("[data-block-pm]").first()).toBeVisible({ timeout: 20_000 });
      await page.waitForTimeout(1500); // the idle-time load has been tried (and failed) by now
      await context.setOffline(true);
      // Readable: the text is there; nothing about ink shows until the student asks to write.
      await expect(page.locator("[data-chunk-unavailable]")).toBeHidden();
      await page.getByRole("button", { name: "Write" }).click();
      const notice = page.locator("[data-chunk-unavailable='inline']");
      await expect(notice).toBeVisible({ timeout: 15_000 });
      await expect(notice).toContainText("This part needs a connection.");
      await expect(notice.getByRole("button", { name: "Try again" })).toBeVisible();
      await expect(page.locator(SURFACE).locator("[data-block-pm]").first()).toBeVisible();
      await expectNoAxeViolations(page);
      await snap(page, info, c.guard, "reader-ink-needs-connection");
      // Back online: it retries by itself, the engine attaches, and ink works.
      blocked = false;
      await context.setOffline(false);
      await expect(page.getByRole("toolbar", { name: "Ink tools" })).toBeVisible({ timeout: 20_000 });
      await expect(notice).toBeHidden();
      await pickViolet(page);
      const y = (await blockTop(page, 2)) + 10;
      const sent = page.waitForResponse((r) => r.url().includes("/changes") && r.status() === 200);
      await penStroke(page, 0.1, 0.45, y);
      await sent;
      await expect(page.locator(SAVE)).toHaveAttribute("data-ink-save", "saved", { timeout: 15_000 });
      expect(c.errors.filter((e) => !e.startsWith("pageerror"))).toEqual([]);
      expect(c.errors).toEqual([]);
    } finally {
      await context.close();
      runUsers(["delete", "--email", email]);
    }
  });
});

test.describe("ink toolbar on a phone", () => {
  test.setTimeout(120_000);

  for (const width of [390, 412]) {
    test(`every control fits at ${width} px: on screen, at least 44 px, nothing scrolled out of sight`, async ({
      browser,
      baseURL,
    }, info) => {
      const s = await student(browser, baseURL as string, info, width === 390 ? "barsmall" : "barwide");
      try {
        const a = await s.device({ width, height: 844 });
        await turnInkOn(a);
        const bar = a.getByRole("toolbar", { name: "Ink tools" });
        const check = async (state: string) => {
          await bar.scrollIntoViewIfNeeded();
          const r = await bar.evaluate((el) => {
            const vw = document.documentElement.clientWidth;
            const controls = [...el.querySelectorAll<HTMLElement>("button")].filter(
              (b) => !b.closest("dialog") && b.getClientRects().length > 0,
            );
            return {
              scrolls: el.scrollWidth > el.clientWidth + 1,
              bad: controls
                .map((b) => {
                  const box = b.getBoundingClientRect();
                  const name = b.getAttribute("aria-label") ?? b.textContent ?? "?";
                  if (box.left < -0.5 || box.right > vw + 0.5) return `${name}: off screen (${box.left}-${box.right})`;
                  if (box.width < 43.5 || box.height < 43.5) return `${name}: ${box.width}x${box.height}`;
                  return null;
                })
                .filter(Boolean),
              count: controls.length,
            };
          });
          expect(r.scrolls, `${state}: the toolbar scrolls sideways`).toBe(false);
          expect(r.bad, state).toEqual([]);
          expect(r.count).toBeGreaterThanOrEqual(11);
        };
        await check("pen");
        await expectNoHorizontalOverflow(a);
        await snap(a, info, consoleOf(a).guard, `toolbar-phone-${width}`);
        await a.getByRole("button", { name: "Eraser", exact: true }).click();
        await check("eraser");
        await a.getByRole("button", { name: "Marker", exact: true }).click();
        await check("marker");
      } finally {
        await s.done();
      }
    });
  }
});

test.describe("ink on a tutor-mode question", () => {
  test.setTimeout(120_000);

  test("saved per question (stem and options), back after a reload", async ({ browser, baseURL }, info) => {
    const s = await student(browser, baseURL as string, info, "q");
    try {
      const a = await s.device();
      const ca = consoleOf(a);
      test.skip((a.viewportSize()?.width ?? 0) < 640, "question ink is offered from tablet width up");
      const made = await a.evaluate(async () => {
        const raw = localStorage.getItem("naadi.auth") ?? "{}";
        const token = (JSON.parse(raw) as { access_token?: string }).access_token ?? "";
        const res = await fetch("/api/blocks", {
          method: "POST",
          headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
          body: JSON.stringify({ exam: "step1", count: 3, mode: "tutor", status: "all", seed: 4 }),
        });
        return (await res.json()) as { id: string };
      });
      await a.goto(`/practice/blocks/${made.id}/1`);
      await expect(a.getByRole("radio")).toHaveCount(5);
      await a.getByRole("button", { name: "Scratch ink" }).click();
      const Q = '[data-ink-surface][aria-label="Scratch ink"]';
      const box = await a.locator(Q).boundingBox();
      if (!box) throw new Error("no question surface");
      const sent = a.waitForResponse((r) => r.url().includes("/api/ink/question/") && r.url().endsWith("/changes"));
      await a.evaluate(async (sel) => {
        const layer = document.querySelector(`${sel} [data-ink-layer]`) as HTMLElement;
        const r = layer.getBoundingClientRect();
        const ev = (k: string, f: number, b: number) =>
          new PointerEvent(k, {
            pointerId: 7,
            pointerType: "pen",
            isPrimary: true,
            bubbles: true,
            cancelable: true,
            clientX: r.left + r.width * f,
            clientY: r.top + 30,
            pressure: b ? 0.6 : 0,
            buttons: b,
          });
        layer.dispatchEvent(ev("pointerdown", 0.1, 1));
        for (let i = 1; i <= 20; i++) {
          layer.dispatchEvent(ev("pointermove", 0.1 + i * 0.02, 1));
          await new Promise((res) => setTimeout(res, 8));
        }
        layer.dispatchEvent(ev("pointerup", 0.5, 0));
      }, Q);
      expect((await sent).status()).toBe(200);
      await expect(a.locator(SAVE)).toHaveAttribute("data-ink-save", "saved");
      await expectNoAxeViolations(a);
      await snap(a, info, ca.guard, "question-ink-saved");
      // The explanation is outside the ink surface (it never carries ink).
      const q = await a.evaluate(() => location.pathname);
      await a.reload();
      await expect(a).toHaveURL(new RegExp(`${q}$`));
      const layer = await a.evaluate(async (blockId) => {
        const raw = localStorage.getItem("naadi.auth") ?? "{}";
        const token = (JSON.parse(raw) as { access_token?: string }).access_token ?? "";
        const h = { authorization: `Bearer ${token}` };
        const qv = (await (await fetch(`/api/blocks/${blockId}/questions/1`, { headers: h })).json()) as {
          question: { id: string };
        };
        return (await (await fetch(`/api/ink/question/${qv.question.id}`, { headers: h })).json()) as {
          item_count: number;
        };
      }, made.id);
      expect(layer.item_count).toBe(1);
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

test.describe("on the production server (strict CSP)", () => {
  const PROD = process.env.E2E_PROD_URL;
  test.skip(!PROD, "needs the local APP_ENV=prod API");

  test("section ink loads, draws and saves with zero console errors", async ({ browser, baseURL }, info) => {
    // Its own throwaway student (a copy of the shared student's session dies as soon as another test refreshes
    // that session: refresh tokens rotate). Signed in on the test origin, then the session moves to the https
    // prod origin; the first context is closed first, so nothing else ever refreshes it.
    const email = emailFor(info, "prod");
    runUsers(["delete", "--email", email]);
    const signIn = await browser.newContext({ ...info.project.use, baseURL, serviceWorkers: NO_SW });
    let origins: { origin: string; localStorage: { name: string; value: string }[] }[];
    try {
      const p = await signIn.newPage();
      await p.goto(callbackPath(toLink(runUsers(["link", "--email", email])), "/"));
      await p.waitForURL((u) => !u.pathname.startsWith("/auth/"), { timeout: 30_000 });
      origins = (await signIn.storageState()).origins;
    } finally {
      await signIn.close();
    }
    const origin = new URL(PROD as string).origin;
    const ctx = await browser.newContext({
      ...info.project.use,
      baseURL: PROD,
      ignoreHTTPSErrors: true,
      serviceWorkers: NO_SW,
      storageState: { cookies: [], origins: origins.map((o) => ({ ...o, origin })) },
    });
    try {
      const page = await ctx.newPage();
      const c = consoleOf(page);
      await page.goto(SECTION);
      await expect(page.locator(SAVE)).toHaveAttribute("data-ink-save", "saved", { timeout: 20_000 });
      await turnInkOn(page);
      const y = (await blockTop(page, 2)) + 10;
      const sent = page.waitForResponse((r) => r.url().includes("/changes"));
      await penStroke(page, 0.1, 0.4, y);
      expect((await sent).status()).toBe(200);
      await expect(page.locator(SAVE)).toHaveAttribute("data-ink-save", "saved");
      await page.goto("/settings/ink");
      await expect(page.getByRole("switch", { name: "Only Pencil draws" })).toBeVisible();
      expect(c.errors).toEqual([]);
    } finally {
      await ctx.close();
      runUsers(["delete", "--email", email]);
    }
  });
});
