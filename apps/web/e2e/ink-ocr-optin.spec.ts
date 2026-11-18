import { execFileSync } from "node:child_process";
import type { Page } from "@playwright/test";
import { canMintUsers } from "./auth-users";
import {
  expect,
  expectNoAxeViolations,
  expectNoHorizontalOverflow,
  quiet,
  settle,
  snapSegment,
  test,
} from "./fixtures";
import { API_DIR, freshStudent, watchConsole } from "./support/student";

/**
 * Segment 13 rework (review 13, defect 2): handwriting search is opt-in. A student draws on a reader section with
 * the setting never touched: the layer is saved but never queued for OCR (no ink image can be sent). Settings →
 * Pencil & ink shows the switch Off, with the privacy note; turning it on persists across a reload and queues the
 * ink already there; turning it off shows the cleared confirmation and leaves nothing queued or recognised. The OCR
 * queue is backend-only, so it is read from the local database (tests/ink/e2e_ocr_state.py).
 */
test.skip(!canMintUsers(), "signed-in tests need the service role (run against the local stack)");

const SECTION = "/learn/ch05/ch05-s02";
const SETTINGS = "/settings/ink";
const SURFACE = '[data-ink-surface][aria-label="Your ink on this section"]';
const SAVE = "[data-ink-save]";
const LABEL = "Handwriting search (sends ink images to the AI model)";

interface OcrState {
  handwriting_search: unknown;
  layers: {
    surface_type: string;
    surface_id: string;
    item_count: number;
    ocr_status: string | null;
    has_text: boolean;
    ink_rows: number;
  }[];
}

function ocrState(email: string): OcrState {
  const db = process.env.E2E_DATABASE_URL ?? process.env.DATABASE_URL;
  if (!db) throw new Error("needs the local database (E2E_DATABASE_URL or DATABASE_URL)");
  const args = ["--directory", API_DIR, "run", "--frozen", "python", "-m", "tests.ink.e2e_ocr_state"];
  const out = execFileSync("uv", [...args, "--email", email, "--db", db], { encoding: "utf8", timeout: 60_000 });
  return JSON.parse(out.trim().split("\n").pop() ?? "{}") as OcrState;
}

const sectionLayer = (s: OcrState) => s.layers.find((l) => l.surface_type === "section" && l.surface_id === "ch05-s02");

/** A Pencil stroke across the n-th top-level block of the section (synthetic pen pointer events). */
async function penStroke(page: Page, block: number, pointerId: number) {
  await page.evaluate(
    async ({ sel, block, pointerId }) => {
      const surface = document.querySelector(sel) as HTMLElement | null;
      const layer = surface?.querySelector("[data-ink-layer]") as HTMLElement | null;
      const b = surface?.querySelectorAll<HTMLElement>("[data-block-pm]")[block];
      if (!surface || !layer || !b) throw new Error("ink surface missing");
      b.scrollIntoView({ block: "center", behavior: "instant" });
      await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
      const r = layer.getBoundingClientRect();
      const y = b.getBoundingClientRect().top + 8;
      const mk = (kind: string, fx: number, buttons: number) =>
        new PointerEvent(kind, {
          pointerId,
          pointerType: "pen",
          isPrimary: true,
          bubbles: true,
          cancelable: true,
          composed: true,
          clientX: r.left + fx * r.width,
          clientY: y,
          pressure: buttons ? 0.6 : 0,
          buttons,
          button: kind === "pointermove" ? -1 : 0,
          width: 1,
          height: 1,
        });
      layer.dispatchEvent(mk("pointerdown", 0.1, 1));
      for (let i = 1; i < 16; i++) {
        layer.dispatchEvent(mk("pointermove", 0.1 + (0.35 * i) / 15, 1));
        await new Promise((res) => setTimeout(res, 8));
      }
      layer.dispatchEvent(mk("pointerup", 0.45, 0));
      await new Promise((res) => requestAnimationFrame(() => requestAnimationFrame(res)));
    },
    { sel: SURFACE, block, pointerId },
  );
}

test.describe("handwriting search is opt-in", () => {
  test.setTimeout(180_000);

  test("ink drawn while off is never queued; the Settings switch turns it on and off", async ({
    browser,
    baseURL,
  }, info) => {
    const s = await freshStudent(browser, baseURL as string, info, "ocroptin");
    try {
      let con = undefined as ReturnType<typeof watchConsole> | undefined;
      const page = await s.open(SECTION, async (p) => {
        con = watchConsole(p);
      });
      if (!con) throw new Error("console watch missing");
      await expect(page.locator(SAVE)).toHaveAttribute("data-ink-save", "saved", { timeout: 20_000 });
      await expect(page.locator("[data-ink-idle]")).toBeAttached({ timeout: 20_000 });

      // Draw three strokes (ink_ocr.min_items): enough that a default-on build would have queued the layer.
      await page.getByRole("button", { name: "Write" }).click();
      await expect(page.getByRole("toolbar", { name: "Ink tools" })).toBeVisible();
      for (const [i, block] of [1, 2, 3].entries()) {
        const sent = page.waitForResponse(
          (r) => r.url().includes("/api/ink/section/") && r.url().includes("/changes") && r.status() === 200,
        );
        await penStroke(page, block, 10 + i);
        await sent;
      }
      await expect(page.locator(SAVE)).toHaveAttribute("data-ink-save", "saved");
      await expect
        .poll(() => sectionLayer(ocrState(s.email))?.item_count ?? 0, { timeout: 20_000 })
        .toBeGreaterThanOrEqual(3);
      let st = ocrState(s.email);
      expect(st.handwriting_search).toBeNull(); // never touched: the default, off
      expect(sectionLayer(st)).toMatchObject({ ocr_status: null, has_text: false, ink_rows: 0 });

      // Settings → Pencil & ink: Off, in words, with what would be sent.
      await quiet(page);
      await page.goto(SETTINGS);
      await settle(page);
      const sw = page.getByRole("switch", { name: LABEL });
      await expect(sw).toHaveAttribute("aria-checked", "false");
      await expect(sw).toContainText("Off");
      await expect(page.getByText(/Off until you turn it on/)).toBeVisible();
      await expect(page.getByText(/Never your name, your account or your typed notes/)).toBeVisible();
      await expect(page.getByText("Handwriting search is off. No ink images are sent.")).toBeVisible();
      const box = await sw.boundingBox();
      expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);
      await expectNoAxeViolations(page);
      await expectNoHorizontalOverflow(page);
      await snapSegment(page, info, con.guard, "13", "settings-ink-ocr-off");
      expect(sectionLayer(ocrState(s.email))?.ocr_status).toBeNull(); // opening Settings changes nothing

      // On: saved to the account, the ink already there is queued, and it survives a reload.
      const on = page.waitForResponse((r) => r.url().endsWith("/api/me") && r.request().method() === "PATCH");
      await sw.click();
      await expect(sw).toHaveAttribute("aria-checked", "true");
      const onRes = await on;
      expect(onRes.status()).toBe(200);
      expect(onRes.request().postDataJSON()).toEqual({ settings: { handwriting_search: true } });
      await expect(sw).toContainText("On");
      await expect(page.getByText(/Handwriting search is on\./)).toBeVisible();
      st = ocrState(s.email);
      expect(st.handwriting_search).toBe(true);
      expect(sectionLayer(st)?.ocr_status).toBe("pending");
      await quiet(page);
      await page.reload();
      await settle(page);
      await expect(page.getByRole("switch", { name: LABEL })).toHaveAttribute("aria-checked", "true");
      await expect(page.getByRole("switch", { name: LABEL })).toContainText("On");
      // The Drawing settings (saved as settings.ink) and this flag do not overwrite each other.
      const pen = page.getByRole("switch", { name: "Only Pencil draws" });
      const penBefore = await pen.getAttribute("aria-checked");
      const inkSaved = page.waitForResponse((r) => r.url().endsWith("/api/me") && r.request().method() === "PATCH");
      await pen.click();
      expect((await inkSaved).status()).toBe(200);
      await expect(pen).not.toHaveAttribute("aria-checked", penBefore ?? "");
      expect(ocrState(s.email).handwriting_search).toBe(true);
      await pen.click(); // back as it was
      await expect(pen).toHaveAttribute("aria-checked", penBefore ?? "");
      await expect(page.getByText("Saved to your account, on every device")).toBeVisible();
      await expectNoAxeViolations(page);
      await snapSegment(page, info, con.guard, "13", "settings-ink-ocr-on");

      // Off: the cleared confirmation, nothing queued or recognised.
      await quiet(page);
      const off = page.waitForResponse((r) => r.url().endsWith("/api/me") && r.request().method() === "PATCH");
      await page.getByRole("switch", { name: LABEL }).click();
      expect((await off).status()).toBe(200);
      await expect(page.getByText("Handwriting search is off. Recognised handwriting deleted.")).toBeVisible();
      await expect(page.getByRole("switch", { name: LABEL })).toHaveAttribute("aria-checked", "false");
      await expect(page.getByRole("switch", { name: LABEL })).toContainText("Off");
      st = ocrState(s.email);
      expect(st.handwriting_search).toBe(false);
      expect(sectionLayer(st)).toMatchObject({ ocr_status: null, has_text: false, ink_rows: 0 });
      expect(st.layers.every((l) => l.ocr_status === null && !l.has_text)).toBe(true);
      await expect(sw).toBeEnabled();
      await expectNoAxeViolations(page);
      await snapSegment(page, info, con.guard, "13", "settings-ink-ocr-cleared");
      await quiet(page);
      expect(con.errors, "console errors").toEqual([]);
    } finally {
      await s.done();
    }
  });

  test("offline: the switch is disabled with the reason shown", async ({ browser, baseURL }, info) => {
    const s = await freshStudent(browser, baseURL as string, info, "ocroffline");
    try {
      let con = undefined as ReturnType<typeof watchConsole> | undefined;
      const page = await s.open(SETTINGS, async (p) => {
        con = watchConsole(p);
      });
      if (!con) throw new Error("console watch missing");
      const sw = page.getByRole("switch", { name: LABEL });
      await expect(sw).toBeEnabled();
      await quiet(page);
      await page.context().setOffline(true);
      await expect(sw).toBeDisabled();
      const tile = page.getByRole("region", { name: "Search and privacy" });
      await expect(tile.getByRole("status")).toContainText("You're offline.");
      await expect(sw).toHaveAttribute("aria-checked", "false");
      await expectNoAxeViolations(page);
      await page.context().setOffline(false);
      await expect(sw).toBeEnabled();
      await quiet(page);
      expect(ocrState(s.email).handwriting_search).toBeNull();
      expect(con.errors, "console errors").toEqual([]);
    } finally {
      await s.done();
    }
  });
});
