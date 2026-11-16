import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import type { Page, TestInfo } from "@playwright/test";
import { type ConsoleGuard, expect, expectNoAxeViolations, expectNoHorizontalOverflow, test } from "./fixtures";

const SCREENS = path.resolve(import.meta.dirname, "../../..", "test-results/screens/12a");
const PAGE = '[data-ink-surface][aria-label="Ink page"]';
const LAYER = `${PAGE} [data-ink-layer]`;

type Pt = [number, number];
interface StrokeOpts {
  pointerType?: "pen" | "touch" | "mouse";
  pointerId?: number;
  pressure?: number;
  stepMs?: number;
  /** Leave the pointer down (no pointerup). */
  hold?: boolean;
  /** Skip the pointerdown (continue a held pointer). */
  continue?: boolean;
  surface?: string;
}

async function snap(page: Page, info: TestInfo, guard: ConsoleGuard, state: string, fullPage = false) {
  mkdirSync(SCREENS, { recursive: true });
  await page.evaluate(() => document.fonts.ready);
  await guard.duringTool(() =>
    page.screenshot({
      path: path.join(SCREENS, `${state}--${info.project.name}.png`),
      // Phones show only a sliver of the lab per screen: capture the whole page so every state is visible.
      fullPage: fullPage || info.project.name.includes("phone"),
      animations: "disabled",
    }),
  );
}

function record(info: TestInfo, name: string, data: unknown) {
  mkdirSync(SCREENS, { recursive: true });
  writeFileSync(path.join(SCREENS, `${name}--${info.project.name}.json`), `${JSON.stringify(data, null, 2)}\n`);
}

/** Dispatch a pointer stroke at fractions of the surface (synthetic PointerEvents, like a Pencil would). */
async function stroke(page: Page, pts: Pt[], opts: StrokeOpts = {}) {
  await page.evaluate(
    async ({ pts, opts, layerSel }) => {
      const layer = document.querySelector(opts.surface ? `${opts.surface} [data-ink-layer]` : layerSel) as HTMLElement;
      if (!layer) throw new Error("ink layer missing");
      const r = layer.getBoundingClientRect();
      const type = opts.pointerType ?? "pen";
      const id = opts.pointerId ?? (type === "pen" ? 2 : type === "touch" ? 11 : 1);
      const mk = (kind: string, [fx, fy]: [number, number], buttons: number) =>
        new PointerEvent(kind, {
          pointerId: id,
          pointerType: type,
          isPrimary: true,
          bubbles: true,
          cancelable: true,
          composed: true,
          clientX: r.left + fx * r.width,
          clientY: r.top + fy * r.height,
          pressure: type === "pen" ? (opts.pressure ?? 0.6) : buttons ? 0.5 : 0,
          buttons,
          button: kind === "pointermove" ? -1 : 0,
          width: 1,
          height: 1,
        });
      const wait = (ms: number) => new Promise((res) => setTimeout(res, ms));
      const step = opts.stepMs ?? 8;
      const [first, ...rest] = pts as [[number, number], ...[number, number][]];
      if (!opts.continue) layer.dispatchEvent(mk("pointerdown", first, 1));
      for (const p of rest) {
        layer.dispatchEvent(mk("pointermove", p, 1));
        if (step > 0) await wait(step);
      }
      if (!opts.hold) layer.dispatchEvent(mk("pointerup", pts[pts.length - 1] as [number, number], 0));
      await new Promise((res) => requestAnimationFrame(() => requestAnimationFrame(res)));
    },
    { pts, opts, layerSel: LAYER },
  );
}

const line = (x0: number, y0: number, x1: number, y1: number, n = 24): Pt[] =>
  Array.from({ length: n }, (_, i) => [x0 + ((x1 - x0) * i) / (n - 1), y0 + ((y1 - y0) * i) / (n - 1)]);

const wave = (x0: number, y: number, w: number, n = 40): Pt[] =>
  Array.from({ length: n }, (_, i) => [x0 + (w * i) / (n - 1), y + 0.015 * Math.sin((i / (n - 1)) * Math.PI * 4)]);

const count = (page: Page) => page.evaluate(() => window.naadiInkLab?.page()?.getItems().length ?? -1);
const items = (page: Page) => page.evaluate(() => window.naadiInkLab?.page()?.getItems() ?? []);

async function openLab(page: Page) {
  await page.goto("/system/ink-lab");
  await expect(page.getByRole("heading", { level: 1, name: "Ink lab" })).toBeVisible();
  await expect(page.locator(LAYER)).toBeAttached();
  await expect(page.getByRole("toolbar", { name: "Ink tools" })).toBeVisible();
}

/** Mean RGB of a PNG screenshot, decoded in the page (no image library on the test side). */
async function meanColour(page: Page, png: Buffer): Promise<number[]> {
  return page.evaluate(async (b64) => {
    const img = new Image();
    img.src = `data:image/png;base64,${b64}`;
    await img.decode();
    const c = document.createElement("canvas");
    c.width = img.naturalWidth;
    c.height = img.naturalHeight;
    const ctx = c.getContext("2d") as CanvasRenderingContext2D;
    ctx.drawImage(img, 0, 0);
    const d = ctx.getImageData(0, 0, c.width, c.height).data;
    const sum = [0, 0, 0];
    for (let i = 0; i < d.length; i += 4) for (let k = 0; k < 3; k++) sum[k] = (sum[k] ?? 0) + (d[i + k] as number);
    const n = d.length / 4;
    return sum.map((v) => v / n);
  }, png.toString("base64"));
}

/**
 * Is there committed ink at this fraction of the surface? Ink is on a slab canvas while it is being
 * edited and on a settled image otherwise; read whichever is shown (or the canvas, if it still holds pixels).
 */
async function inkedAt(page: Page, fx: number, fy: number, radius = 3, sel = PAGE): Promise<boolean> {
  return page.evaluate(
    ({ fx, fy, radius, sel }) => {
      const host = document.querySelector(`${sel} [data-ink-host]`) as HTMLElement;
      const py = fy * host.clientHeight;
      const covers = (el: HTMLElement) => {
        const top = Number.parseFloat(el.style.top || "0");
        return py >= top && py < top + Number.parseFloat(el.style.height);
      };
      const canvas = [...document.querySelectorAll<HTMLCanvasElement>(`${sel} canvas[data-ink-canvas="ink"]`)].find(
        (c) => covers(c) && c.width > 0,
      );
      const img = [...document.querySelectorAll<HTMLImageElement>(`${sel} img[data-ink-image="ink"]`)].find(
        (i) => covers(i) && i.style.display !== "none" && i.naturalWidth > 0,
      );
      const source: HTMLCanvasElement | HTMLImageElement | undefined =
        canvas && canvas.style.display !== "none" ? canvas : (img ?? canvas);
      if (!source) return false;
      const w = source instanceof HTMLCanvasElement ? source.width : source.naturalWidth;
      const h = source instanceof HTMLCanvasElement ? source.height : source.naturalHeight;
      const scratch = document.createElement("canvas");
      scratch.width = w;
      scratch.height = h;
      const ctx = scratch.getContext("2d") as CanvasRenderingContext2D;
      ctx.drawImage(source, 0, 0);
      const scale = w / host.clientWidth;
      const top = Number.parseFloat(source.style.top || "0");
      const x = Math.round(fx * host.clientWidth * scale);
      const y = Math.round((py - top) * scale);
      const r = Math.max(1, Math.round(radius * scale));
      const data = ctx.getImageData(x - r, y - r, r * 2 + 1, r * 2 + 1).data;
      for (let i = 3; i < data.length; i += 4) if ((data[i] as number) > 40) return true;
      return false;
    },
    { fx, fy, radius, sel },
  );
}

test.describe("ink lab", () => {
  // The live Space runs APP_ENV=prod, where the lab exists for admins only; ink-gate.spec covers that.
  test.skip(Boolean(process.env.E2E_BASE_URL), "the lab is admin-only on the production Space (see ink-gate.spec)");

  test("renders with no axe violations or overflow", async ({ page, consoleErrors }, info) => {
    await openLab(page);
    for (const name of ["Pen", "Marker", "Eraser", "Lasso", "Shapes", "Text"]) {
      const b = page.getByRole("toolbar", { name: "Ink tools" }).getByRole("button", { name, exact: true });
      await expect(b).toBeVisible();
      const box = await b.boundingBox();
      expect(box?.height ?? 0, `${name} target`).toBeGreaterThanOrEqual(44);
    }
    await expect(page.getByRole("button", { name: "Pen", exact: true })).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByTestId("pen-status")).toContainText("Not detected");
    await expectNoHorizontalOverflow(page);
    await expectNoAxeViolations(page);
    await snap(page, info, consoleErrors, "lab-empty", true);
  });

  test("pen draws pressure strokes in surface-relative [x, y, p, t]", async ({ page, consoleErrors }, info) => {
    const requests: string[] = [];
    await openLab(page);
    // Network = http(s). Settled ink is shown from local blob: URLs, which never leave the device.
    page.on("request", (r) => {
      // Same-origin static assets (the PNG-encode worker script) are code, not data leaving the device.
      const url = new URL(r.url());
      if (/^https?:$/.test(url.protocol) && !url.pathname.startsWith("/assets/")) requests.push(r.url());
    });
    await stroke(page, wave(0.1, 0.15, 0.6), { pressure: 0.8 });
    await stroke(page, wave(0.1, 0.22, 0.5), { pressure: 0.3 });
    await expect.poll(() => count(page)).toBe(2);
    await expect(page.getByTestId("pen-status")).toContainText("Detected");
    await expect(page.getByTestId("finger-status")).toContainText("Scrolls");
    const [a, b] = (await items(page)) as { tool: string; points: number[][]; size: number }[];
    for (const s of [a, b]) {
      expect(s?.tool).toBe("pen");
      for (const [x, y, , t] of s?.points ?? []) {
        expect(x).toBeGreaterThanOrEqual(0);
        expect(x).toBeLessThanOrEqual(1);
        expect(y).toBeGreaterThanOrEqual(0);
        expect(y).toBeLessThanOrEqual(1180 / 820 + 0.01);
        expect(Number.isInteger(t)).toBe(true);
      }
      const ts = (s?.points ?? []).map((p) => p[3] as number);
      expect([...ts].sort((x, y) => x - y)).toEqual(ts);
    }
    expect(a?.points[1]?.[2]).toBeCloseTo(0.8, 2);
    expect(b?.points[1]?.[2]).toBeCloseTo(0.3, 2);
    expect(await inkedAt(page, 0.1, 0.15)).toBe(true);
    expect(requests, "the ink engine makes no network calls").toEqual([]);
    await expect(page.locator('[data-testid="events"] li').first()).toContainText("1 added");
    await expect.poll(() => page.locator('[data-metric="frames"]').textContent()).not.toBe("0");
    await snap(page, info, consoleErrors, "pen-strokes", true);
  });

  test("palm rejection: touches are ignored while the pen is down, and fingers scroll once a pen exists", async ({
    page,
  }) => {
    await openLab(page);
    await stroke(page, line(0.1, 0.1, 0.3, 0.1, 6), { hold: true });
    // A palm lands and drags while the pen is still down.
    await stroke(page, line(0.5, 0.5, 0.7, 0.7), { pointerType: "touch", pointerId: 21 });
    await stroke(page, line(0.3, 0.1, 0.5, 0.12, 6), { continue: true });
    await expect.poll(() => count(page)).toBe(1);
    // Pen present + pen-only: one finger never draws, and the browser keeps native scrolling.
    await stroke(page, line(0.2, 0.6, 0.6, 0.6), { pointerType: "touch", pointerId: 22 });
    expect(await count(page)).toBe(1);
    expect(await page.locator(LAYER).evaluate((el) => getComputedStyle(el).touchAction)).toBe("auto");
    // Pen-only off: fingers draw again (one-finger pan blocked, two-finger pan/zoom kept).
    await page.getByRole("switch", { name: /Pen-only drawing/ }).click();
    await expect(page.getByTestId("finger-status")).toContainText("Draws");
    expect(await page.locator(LAYER).evaluate((el) => getComputedStyle(el).touchAction)).toBe("pinch-zoom");
    await stroke(page, line(0.2, 0.6, 0.6, 0.6), { pointerType: "touch", pointerId: 23 });
    await expect.poll(() => count(page)).toBe(2);
  });

  test("with no pen, a finger draws; a second finger cancels the stroke", async ({ page }) => {
    await openLab(page);
    await expect(page.getByTestId("finger-status")).toContainText("Draws");
    expect(await page.locator(LAYER).evaluate((el) => getComputedStyle(el).touchAction)).toBe("pinch-zoom");
    await stroke(page, wave(0.1, 0.3, 0.5), { pointerType: "touch", pointerId: 31 });
    await expect.poll(() => count(page)).toBe(1);
    await stroke(page, line(0.1, 0.5, 0.3, 0.5, 6), { pointerType: "touch", pointerId: 32, hold: true });
    await stroke(page, line(0.6, 0.5, 0.7, 0.6, 4), { pointerType: "touch", pointerId: 33 });
    await stroke(page, line(0.3, 0.5, 0.5, 0.5, 4), { pointerType: "touch", pointerId: 32, continue: true });
    expect(await count(page)).toBe(1);
    const [s] = (await items(page)) as { points: number[][] }[];
    expect(s?.points.every((p) => p[2] === 0.5)).toBe(true);
  });

  test("a real mouse draws (trusted input, coalesced where supported)", async ({ page }) => {
    await openLab(page);
    // Real input only lands inside the viewport: bring the top of the page into view below the toolbar.
    await page
      .locator(LAYER)
      .evaluate((el) => window.scrollTo(0, el.getBoundingClientRect().top + window.scrollY - 160));
    const box = await page.locator(LAYER).boundingBox();
    if (!box) throw new Error("no layer box");
    const y = box.y + Math.min(box.height * 0.3, 240);
    await page.mouse.move(box.x + box.width * 0.2, y);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width * 0.6, y + 30, { steps: 20 });
    await page.mouse.up();
    await expect.poll(() => count(page)).toBe(1);
    await expect
      .poll(async () => Number(await page.locator('[data-metric="samples"]').textContent()))
      .toBeGreaterThan(5);
  });

  test("tools, colours and keyboard shortcuts; undo/redo; clear with confirm", async ({
    page,
    browserName,
    consoleErrors,
  }, info) => {
    await openLab(page);
    const tools = page.getByRole("toolbar", { name: "Ink tools" });
    const mod = browserName === "webkit" ? "Meta" : "Control";
    await page.locator("h1").click();
    await page.keyboard.press("m");
    await expect(tools.getByRole("button", { name: "Marker", exact: true })).toHaveAttribute("aria-pressed", "true");
    await expect(tools.getByRole("button", { name: "Marker lime, key fact" })).toHaveAttribute("aria-pressed", "true");
    await stroke(page, line(0.1, 0.2, 0.7, 0.2));
    await page.keyboard.press("p");
    await expect(tools.getByRole("button", { name: "Pen", exact: true })).toHaveAttribute("aria-pressed", "true");
    await tools.getByRole("button", { name: "Ink violet" }).click();
    await stroke(page, wave(0.1, 0.35, 0.6));
    const [m, p] = (await items(page)) as { tool: string; color: string }[];
    expect(m).toMatchObject({ tool: "marker", color: "lime" });
    expect(p).toMatchObject({ tool: "pen", color: "violet" });
    // The marker layer sits under the page content (multiply-equivalent on paper), ink above it.
    const z = await page.evaluate((sel) => {
      const q = (n: string) =>
        getComputedStyle(document.querySelector(`${sel} canvas[data-ink-canvas="${n}"]`) as Element).zIndex;
      return { marker: q("marker"), ink: q("ink") };
    }, PAGE);
    expect(z).toEqual({ marker: "-1", ink: "1" });
    await snap(page, info, consoleErrors, "marker-and-violet");

    await page.keyboard.press("e");
    await expect(tools.getByRole("button", { name: "Eraser", exact: true })).toHaveAttribute("aria-pressed", "true");
    await stroke(page, line(0.4, 0.15, 0.4, 0.25, 8));
    await expect.poll(() => count(page)).toBe(1);
    await page.keyboard.press(`${mod}+z`);
    await expect.poll(() => count(page)).toBe(2);
    await page.keyboard.press(`Shift+${mod}+z`);
    await expect.poll(() => count(page)).toBe(1);
    await tools.getByRole("button", { name: "Undo" }).click();
    await expect.poll(() => count(page)).toBe(2);
    await tools.getByRole("button", { name: "Redo" }).click();
    await expect.poll(() => count(page)).toBe(1);

    await tools.getByRole("button", { name: "Clear page" }).click();
    const dialog = page.getByRole("dialog", { name: "Clear all ink on this page?" });
    await expect(dialog).toBeVisible();
    await expectNoAxeViolations(page);
    await snap(page, info, consoleErrors, "clear-confirm");
    await dialog.getByRole("button", { name: "Cancel" }).click();
    expect(await count(page)).toBe(1);
    await tools.getByRole("button", { name: "Clear page" }).click();
    await dialog.getByRole("button", { name: "Clear page" }).click();
    await expect.poll(() => count(page)).toBe(0);
    await tools.getByRole("button", { name: "Undo" }).click();
    await expect.poll(() => count(page)).toBe(1);
  });

  test("pixel eraser splits a stroke; whole-stroke eraser removes it", async ({ page }) => {
    await openLab(page);
    await stroke(page, line(0.1, 0.3, 0.8, 0.3, 30));
    await page.getByRole("button", { name: "Eraser", exact: true }).click();
    await page.getByRole("button", { name: "Partial", exact: true }).click();
    await stroke(page, line(0.45, 0.25, 0.45, 0.35, 8));
    await expect.poll(() => count(page)).toBe(2);
    expect(await inkedAt(page, 0.45, 0.3, 1)).toBe(false);
    expect(await inkedAt(page, 0.2, 0.3)).toBe(true);
    await page.getByRole("button", { name: "Whole stroke", exact: true }).click();
    await stroke(page, line(0.2, 0.25, 0.2, 0.35, 8));
    await expect.poll(() => count(page)).toBe(1);
  });

  test("lasso selects, moves, copies, converts, asks and deletes", async ({ page, consoleErrors }, info) => {
    await openLab(page);
    await stroke(page, wave(0.2, 0.3, 0.3));
    await stroke(page, wave(0.2, 0.36, 0.3));
    await stroke(page, wave(0.2, 0.7, 0.3));
    await page.keyboard.press("l");
    const loop: Pt[] = Array.from({ length: 40 }, (_, i) => {
      const a = (i / 39) * Math.PI * 2;
      return [0.35 + 0.25 * Math.cos(a), 0.33 + 0.07 * Math.sin(a)];
    });
    await stroke(page, loop);
    const sel = page.getByRole("toolbar", { name: "Selection" });
    await expect(sel).toBeVisible();
    await expect(page.locator('[data-testid="events"] li[data-kind="lasso"]').first()).toContainText("2 items");
    await expect(page.getByRole("img", { name: "Lasso selection as sent to later features" })).toBeVisible();
    await expectNoAxeViolations(page);
    await snap(page, info, consoleErrors, "lasso-selection");

    const before = (await items(page)) as { id: string; points: number[][] }[];
    await stroke(page, line(0.35, 0.33, 0.45, 0.43, 10));
    const after = (await items(page)) as { id: string; points: number[][] }[];
    const moved = after.find((s) => s.id === before[0]?.id);
    expect((moved?.points[0]?.[0] ?? 0) - (before[0]?.points[0]?.[0] ?? 0)).toBeCloseTo(0.1, 2);

    await sel.getByRole("button", { name: "Copy" }).click();
    await expect.poll(() => count(page)).toBe(5);
    await sel.getByRole("button", { name: "Convert to text" }).click();
    await expect(page.locator('[data-testid="events"] li[data-kind="convert"]').first()).toContainText("2 items");
    await sel.getByRole("button", { name: "Ask Naadi" }).click();
    await expect(page.locator('[data-testid="events"] li[data-kind="ask"]').first()).toContainText("2 items");
    await sel.getByRole("button", { name: "Delete" }).click();
    await expect.poll(() => count(page)).toBe(3);
    await expect(sel).toHaveCount(0);
  });

  test("shapes straighten (Shapes tool, and pen hold) and text boxes commit", async ({ page, consoleErrors }, info) => {
    await openLab(page);
    await page.getByRole("button", { name: "Shapes", exact: true }).click();
    await stroke(
      page,
      line(0.1, 0.2, 0.7, 0.24, 30).map(([x, y], i) => [x, y + (i % 2 ? 0.002 : -0.002)] as Pt),
    );
    const circle: Pt[] = Array.from({ length: 60 }, (_, i) => {
      const a = (i / 59) * Math.PI * 2;
      return [0.4 + 0.15 * Math.cos(a), 0.45 + ((0.15 * 820) / 1180) * Math.sin(a)];
    });
    await stroke(page, circle);
    const shapes = ((await items(page)) as { shape?: string }[]).map((s) => s.shape);
    expect(shapes).toEqual(["line", "circle"]);

    await page.getByRole("button", { name: "Pen", exact: true }).click();
    await stroke(page, line(0.1, 0.7, 0.6, 0.72, 20), { hold: true, stepMs: 6 });
    await page.waitForTimeout(800);
    await stroke(page, [[0.6, 0.72]], { continue: true });
    expect(((await items(page)) as { shape?: string }[])[2]?.shape).toBe("line");

    await page.getByRole("button", { name: "Text", exact: true }).click();
    await stroke(page, [[0.2, 0.85]], { pointerType: "mouse" });
    const box = page.getByRole("textbox", { name: "New text box" });
    await expect(box).toBeFocused();
    await box.fill("Type 4 = aldo down");
    await snap(page, info, consoleErrors, "text-editing");
    await page.locator("h1").click();
    await expect.poll(() => count(page)).toBe(4);
    expect(((await items(page)) as { tool: string; text?: string }[])[3]).toMatchObject({
      tool: "text",
      text: "Type 4 = aldo down",
    });
    await snap(page, info, consoleErrors, "shapes-and-text", true);
  });

  test("ink survives resize and rotation without drifting more than 1 px", async ({ page }, info) => {
    test.skip(info.project.name.includes("desktop"), "rotation is exercised on the tablet/phone/laptop profiles");
    await openLab(page);
    await stroke(page, wave(0.2, 0.3, 0.4));
    await expect.poll(() => count(page)).toBe(1);
    const measure = () =>
      page.evaluate(() => {
        const lab = window.naadiInkLab?.page();
        const id = lab?.getItems()[0]?.id as string;
        const host = document.querySelector('[data-ink-surface][aria-label="Ink page"] [data-ink-host]') as HTMLElement;
        return { rect: lab?.itemRect(id), width: host.clientWidth };
      });
    const a = await measure();
    const vp = page.viewportSize() ?? { width: 820, height: 1180 };
    await page.setViewportSize({ width: vp.height, height: vp.width });
    await expect.poll(async () => (await measure()).width).not.toBe(a.width);
    // The engine re-measures on its ResizeObserver; once it has, every edge must be within 1 px of
    // where uniform scaling puts it.
    const drift = async () => {
      const b = await measure();
      const k = b.width / a.width;
      return Math.max(
        ...(["left", "top", "width", "height"] as const).map((key) =>
          Math.abs((b.rect?.[key] ?? 0) - (a.rect?.[key] ?? 0) * k),
        ),
      );
    };
    await expect.poll(drift).toBeLessThanOrEqual(1);
    record(info, "resize-drift", { before: a, after: await measure(), maxDriftPx: await drift() });
    // The pixels follow the data: the re-rendered canvas has ink where the stroke now is.
    await expect.poll(() => inkedAt(page, 0.2, 0.3)).toBe(true);
  });

  test("one lime control (Ink on), and every colour swatch has a visible ring", async ({ page }) => {
    await openLab(page);
    const limes = await page.evaluate(() =>
      [...document.querySelectorAll<HTMLElement>("main *")]
        .filter((el) => getComputedStyle(el).backgroundColor === "rgb(198, 255, 61)" && el.offsetParent !== null)
        .map((el) => el.textContent?.trim()),
    );
    expect(limes).toEqual(["Ink on"]);
    const tools = page.getByRole("toolbar", { name: "Ink tools" });
    await tools.getByRole("button", { name: "Ink violet" }).click();
    // WCAG 1.4.11: the unselected black swatch on the black bar needs a 3:1 boundary.
    const contrast = await tools.getByRole("button", { name: "Ink black" }).evaluate((el) => {
      const lum = (c: string) => {
        const [r, g, b] = (c.match(/\d+(\.\d+)?/g) ?? []).slice(0, 3).map(Number) as [number, number, number];
        const f = (v: number) => {
          const s = v / 255;
          return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
        };
        return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
      };
      const ring = lum(getComputedStyle(el).borderTopColor);
      const bar = lum(getComputedStyle(el.closest("[role=toolbar]") as Element).backgroundColor);
      const [hi, lo] = ring > bar ? [ring, bar] : [bar, ring];
      return (hi + 0.05) / (lo + 0.05);
    });
    expect(contrast).toBeGreaterThanOrEqual(3);
  });

  test("lasso around a region with no ink still selects it for Ask Naadi", async ({ page, consoleErrors }, info) => {
    await openLab(page);
    await stroke(page, wave(0.2, 0.75, 0.3));
    await page.keyboard.press("l");
    const loop: Pt[] = Array.from({ length: 40 }, (_, i) => {
      const a = (i / 39) * Math.PI * 2;
      return [0.45 + 0.2 * Math.cos(a), 0.2 + 0.06 * Math.sin(a)];
    });
    await stroke(page, loop);
    await expect(page.locator('[data-testid="events"] li[data-kind="lasso"]').first()).toContainText("region only");
    const sel = page.getByRole("toolbar", { name: "Selection" });
    await expect(sel.getByRole("button", { name: "Copy" })).toBeDisabled();
    await expect(sel.getByRole("button", { name: "Delete" })).toBeDisabled();
    await expect(sel.getByRole("button", { name: "Convert to text" })).toBeDisabled();
    await sel.getByRole("button", { name: "Ask Naadi" }).click();
    await expect(page.locator('[data-testid="events"] li[data-kind="ask"]').first()).toContainText("0 items");
    await expect(page.locator('[data-testid="events"] li[data-kind="ask"]').first()).toContainText("no ink picture");
    await snap(page, info, consoleErrors, "lasso-region-only", true);
  });

  test("marker: under plain text by default, multiplied over an opaque row on PDF/image surfaces", async ({
    page,
    consoleErrors,
  }) => {
    await openLab(page);
    const rowBox = async (testId: string) => {
      const host = await page.locator(`${PAGE} [data-ink-host]`).boundingBox();
      const row = await page.getByTestId(testId).boundingBox();
      if (!host || !row) throw new Error("no box");
      return { host, row, fy: (row.y + row.height / 2 - host.y) / host.height };
    };
    const shot = async (testId: string) => {
      await page.getByTestId(testId).evaluate((el) => el.scrollIntoView({ block: "center" }));
      const { row } = await rowBox(testId);
      const clip = {
        x: row.x + row.width * 0.3,
        y: row.y + row.height * 0.3,
        width: 24,
        height: Math.max(6, row.height * 0.4),
      };
      const png = await consoleErrors.duringTool(() => page.screenshot({ clip, animations: "disabled" }));
      return meanColour(page, png);
    };
    // Largest per-channel change of the mean colour (screenshots differ by a few levels run to run).
    const moved = (a: number[], b: number[]) => Math.max(...a.map((v, i) => Math.abs(v - (b[i] ?? 0))));
    const markRow = async (testId: string) => {
      const { host, row, fy } = await rowBox(testId);
      const fx0 = (row.x - host.x + row.width * 0.1) / host.width;
      const fx1 = (row.x - host.x + row.width * 0.7) / host.width;
      await stroke(page, line(fx0, fy, fx1, fy, 20));
    };
    await page.keyboard.press("m");
    // Text surface: the opaque row hides an under-layer marker; the plain row shows it.
    const plainBefore = await shot("plain-row");
    const opaqueBefore = await shot("opaque-row");
    await markRow("plain-row");
    await markRow("opaque-row");
    await page.waitForTimeout(300);
    expect(moved(await shot("plain-row"), plainBefore), "marker shows on a plain row").toBeGreaterThan(20);
    expect(
      moved(await shot("opaque-row"), opaqueBefore),
      "under-layer marker is hidden by an opaque fill",
    ).toBeLessThan(6);

    // PDF/image surface: the marker multiplies over the content, so the opaque row is highlighted too.
    await page.getByRole("button", { name: "Page content: text" }).click();
    const marker = page.locator(`${PAGE} canvas[data-ink-canvas="marker"]`).first();
    await expect.poll(() => marker.evaluate((c) => getComputedStyle(c).mixBlendMode)).toBe("multiply");
    expect(await marker.evaluate((c) => getComputedStyle(c).zIndex)).toBe("1");
    await expect.poll(async () => moved(await shot("opaque-row"), opaqueBefore)).toBeGreaterThan(20);
  });

  test("12 surfaces share one canvas memory budget", async ({ page }, info) => {
    test.setTimeout(120_000);
    await openLab(page);
    const mount = page.getByRole("button", { name: "Mount 12 pages" });
    // Centre it first: on a phone the sticky toolbar would cover a button scrolled to the top edge.
    await mount.evaluate((el) => el.scrollIntoView({ block: "center" }));
    await mount.focus();
    await page.keyboard.press("Enter");
    const pages = page.locator('[data-testid="many-pages"] [data-ink-surface]');
    await expect(pages).toHaveCount(12);
    let maxTotal = 0;
    const limit = await page.evaluate(() => window.naadiInkLab?.budget.stats().limit ?? 0);
    for (let i = 0; i < 12; i++) {
      await pages.nth(i).scrollIntoViewIfNeeded();
      await expect
        .poll(() => page.evaluate((n) => window.naadiInkLab?.pages()[n]?.renderPending() ?? true, i), {
          timeout: 20_000,
        })
        .toBe(false);
      const s = await page.evaluate(() => window.naadiInkLab?.budget.stats());
      maxTotal = Math.max(maxTotal, s?.total ?? 0);
      const entries = await page.evaluate(() => window.naadiInkLab?.budget.entriesSnapshot());
      expect(s?.overPinned, `only on-screen canvases may exceed the budget: ${JSON.stringify(entries)}`).toBe(false);
    }
    // Every page painted, and the last one shows its ink (from its canvas or its settled image).
    expect(await inkedAt(page, 0.5, 0.5, 40, '[aria-label="Ink page 12 of 12"]')).toBe(true);
    record(info, "canvas-budget", {
      limit,
      maxTotal,
      final: await page.evaluate(() => window.naadiInkLab?.budget.stats()),
    });
    expect(maxTotal).toBeLessThanOrEqual(limit);
  });

  test("latency harness records input-to-paint percentiles", async ({ page, consoleErrors }, info) => {
    await openLab(page);
    for (let i = 0; i < 4; i++) await stroke(page, wave(0.1, 0.15 + i * 0.08, 0.7, 120), { stepMs: 4 });
    await expect
      .poll(async () => Number(await page.locator('[data-metric="frames"]').textContent()))
      .toBeGreaterThan(30);
    await page.waitForTimeout(300);
    const summary = await page.evaluate(() => window.naadiInkLab?.latency.summary());
    const caps = await page.evaluate(() => ({
      coalesced: "getCoalescedEvents" in PointerEvent.prototype,
      predicted: "getPredictedEvents" in PointerEvent.prototype,
    }));
    // Let the strokes settle into images, then record the main-thread cost of starting those encodes.
    await expect
      .poll(() => page.evaluate(() => window.naadiInkLab?.page()?.renderPending() ?? true), { timeout: 20_000 })
      .toBe(false);
    const render = await page.evaluate(() => window.naadiInkLab?.page()?.renderStats);
    record(info, "latency", {
      ...summary,
      caps,
      settleMainMs: render?.settleMainMs,
      note: "synthetic PointerEvents, headless; see DEVICE_CHECKLIST for the iPad number",
    });
    // A sanity bound on shared, software-rendered runners (the median, which load disturbs least); the
    // 20 ms target is the device number from the 240 fps protocol in docs/DEVICE_CHECKLIST.md.
    expect(summary?.paint.p50 ?? 999).toBeLessThan(60);
    await snap(page, info, consoleErrors, "latency-panel", true);
  });

  test("5,000-stroke page scrolls without the ink costing frames (measured against this runner)", async ({
    page,
    consoleErrors,
  }, info) => {
    test.setTimeout(180_000);
    await openLab(page);
    await page.getByRole("button", { name: "Build 5,000-stroke page" }).click();
    await expect(page.getByTestId("stress-count")).toHaveText("5,000 items");
    // Measure scrolling, not the first paint: wait until every tile and stroke path is built.
    await expect
      .poll(() => page.evaluate(() => window.naadiInkLab?.stress()?.renderPending() ?? true), { timeout: 30_000 })
      .toBe(false);
    const measure = async () => {
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.waitForTimeout(300);
      await page.getByRole("button", { name: "Measure scroll", exact: true }).click();
      await expect(page.getByRole("button", { name: "Measuring scroll…" })).toBeVisible();
      await expect(page.getByRole("button", { name: "Measure scroll", exact: true })).toBeVisible({ timeout: 20_000 });
      return Number(await page.getByTestId("scroll-result").getAttribute("data-fps"));
    };
    const setInkVisible = (visible: boolean) =>
      page.evaluate((v) => {
        for (const el of document.querySelectorAll<HTMLElement>("[data-ink-layer]"))
          el.style.visibility = v ? "" : "hidden";
      }, visible);
    // Paired runs: the same page with the ink layers shown, then hidden. The hidden run is what this
    // runner can scroll at all (software rendering on shared CI runners is often below 60 fps even
    // without ink), so the ink is judged by what it costs on top of that. Four pairs, best against best,
    // so a noisy neighbour on a shared runner doesn't decide the result.
    const ink: number[] = [];
    const base: number[] = [];
    for (let i = 0; i < 4; i++) {
      await setInkVisible(true);
      ink.push(await measure());
      await setInkVisible(false);
      base.push(await measure());
      if (Math.max(...ink) >= 58) break;
    }
    await setInkVisible(true);
    const bestInk = Math.max(...ink);
    const bestBase = Math.max(...base);
    // Settled ink is shown as images (canvases only while editing); record what the page actually had.
    const layers = await page.evaluate(() => {
      const shown = (sel: string) =>
        [...document.querySelectorAll<HTMLElement>(sel)].filter((el) => getComputedStyle(el).display !== "none").length;
      return { canvases: shown("[data-ink-layer] canvas"), images: shown("[data-ink-layer] img") };
    });
    record(info, "scroll-fps", { withInk: ink, inkHidden: base, bestInk, bestBase, layers });
    expect(layers.canvases, "settled ink is not left on canvases").toBe(0);
    // The ink may cost at most 30 % of what this runner scrolls at with the ink hidden, and never drop below
    // 25 fps. (Software rendering on shared CI runners is below 60 fps on large viewports even without ink;
    // the device number is measured on the iPad, docs/DEVICE_CHECKLIST.md.)
    expect(bestInk, `with ink ${ink.join("/")} fps, ink hidden ${base.join("/")} fps`).toBeGreaterThanOrEqual(
      bestBase * 0.7,
    );
    // The 25 fps floor needs a runner that can show it: when this runner scrolls the page below 30 fps
    // with no ink at all (WebKit's software compositor at desktop size or dpr 2 under load), only the
    // relative bound above can judge the ink, and the run is annotated.
    if (bestBase >= 30) expect(bestInk, "absolute floor").toBeGreaterThanOrEqual(25);
    else
      info.annotations.push({
        type: "fps-floor",
        description: `runner baseline ${bestBase} fps < 30: floor not applicable`,
      });
    await expectNoHorizontalOverflow(page);
    await snap(page, info, consoleErrors, "stress-page");
  });
});
