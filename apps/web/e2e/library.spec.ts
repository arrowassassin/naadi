import { execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync } from "node:fs";
import path from "node:path";
import type { Page, TestInfo } from "@playwright/test";
import { canMintUsers } from "./auth-users";
import { expect, expectNoAxeViolations, expectNoHorizontalOverflow, quiet, settle, test } from "./fixtures";
import { API_DIR, freshStudent, watchConsole } from "./support/student";

/**
 * Segment 15: the Library against the real API, database, Storage and auth server. Uploads go straight to Storage
 * with the signed upload token; the ingest run executes in the API process (no model keys in e2e, so the pipeline
 * uses its local rules). Covers upload → confirm → private chapter in Library, Courses and Search; a synthetic
 * personal document → Vault prompt → Store in Vault; the PDF viewer with ink on page 2 that survives a reload; and
 * lasso → "Ask about this", which is absent while the assistant is off. Every file is generated in code
 * (apps/api/tests/ingest/files.py); the passport is synthetic (made-up MRZ with valid check digits).
 */
test.skip(!canMintUsers(), "signed-in tests need the service role (run pnpm e2e:live-auth against the Space)");

const TMP = path.resolve(import.meta.dirname, "../../../.tmp");

function tag(info: TestInfo): string {
  return `${info.project.name.replace(/[^a-z0-9]/g, "")}${info.repeatEachIndex}${info.retry}`;
}

/** A generated PDF: `notes` (3 text pages with a unique word) or `passport` (one page with a synthetic MRZ). */
function makePdf(file: string, kind: "notes" | "passport", title: string, word: string): string {
  const code = [
    "import sys",
    "from pathlib import Path",
    "from tests.ingest.files import LOREM, passport_text, pdf",
    "kind, title, word, out = sys.argv[1:5]",
    "if kind == 'passport':",
    "    data = pdf(1, title=title, image=False, extra=passport_text())",
    "else:",
    "    data = pdf(3, title=title, text=f'{word} ' + LOREM, image=True)",
    "Path(out).write_bytes(data)",
  ].join("\n");
  execFileSync("uv", ["--directory", API_DIR, "run", "--frozen", "python", "-c", code, kind, title, word, file], {
    timeout: 60_000,
    stdio: ["ignore", "pipe", "pipe"],
  });
  return file;
}

function workDir(): string {
  mkdirSync(TMP, { recursive: true });
  return mkdtempSync(path.join(TMP, "library-"));
}

async function upload(page: Page, file: string): Promise<void> {
  await page.getByTestId("library-file-input").setInputFiles(file);
}

/**
 * Console errors that count. Trusted Types ship report-only (segment 22): their lines are reports for the
 * enforcement step and are judged by csp.spec.ts, not here (the PDF viewer's own worker is created through a
 * Trusted Types policy; the ink engine's PNG encoder worker still reports).
 */
function appErrors(errors: string[]): string[] {
  return errors.filter((e) => !/\[Report Only\]/.test(e));
}

const card = (page: Page, name: string) => page.locator("[data-file-card]").filter({ hasText: name });

test.describe("Library", () => {
  test("upload → confirm → a private chapter in Library, Courses and Search", async ({ browser, baseURL }, info) => {
    test.setTimeout(180_000);
    const student = await freshStudent(browser, baseURL as string, info, "lib");
    const work = workDir();
    try {
      const word = `zentrix${tag(info)}`;
      const title = `Tubule notes ${tag(info)}`;
      const file = makePdf(path.join(work, "tubule-notes.pdf"), "notes", title, word);
      const page = await student.open("/learn/library");
      const { errors } = watchConsole(page);
      await expect(page.getByRole("heading", { level: 1, name: /Bring your\s*own shelf\./ })).toBeVisible();
      await expect(page.getByText("Nothing here yet")).toBeVisible();
      await expect(page.getByRole("meter")).toHaveAttribute("aria-valuenow", "0");
      await settle(page);
      await expectNoAxeViolations(page);
      await expectNoHorizontalOverflow(page);

      await upload(page, file);
      // The assistant's proposal waits for the student: nothing is placed before Confirm.
      const confirm = page.locator("[data-confirm-card]");
      await expect(confirm).toBeVisible({ timeout: 90_000 });
      const option = confirm.getByRole("radio", { name: new RegExp(`New chapter: ${title}`) });
      await expect(option).toBeChecked();
      await expect(card(page, "tubule-notes.pdf")).toHaveAttribute("data-status", "needs_you");
      await settle(page);
      await expectNoAxeViolations(page);
      await expectNoHorizontalOverflow(page);
      // The one lime action of the view is the card's Confirm (Choose files steps back while something waits).
      await expect(page.locator("main [data-variant=primary]")).toHaveCount(1);
      await confirm.getByRole("button", { name: "Confirm" }).click();

      const ready = card(page, "tubule-notes.pdf");
      await expect(ready).toHaveAttribute("data-status", "ready", { timeout: 60_000 });
      await expect(ready.getByText("Status: Ready")).toBeVisible();
      await expect(ready.getByText("Your file")).toBeVisible();
      await expect(ready.getByRole("button", { name: "Undo placement" })).toBeVisible();
      await expect(page.getByRole("meter")).not.toHaveAttribute("aria-valuenow", "0");
      await expectNoAxeViolations(page);

      // Courses: the private chapter is in "Your files" with its chip, and the group links to the Library.
      await quiet(page);
      await page.goto("/learn");
      const own = page.locator("section[aria-labelledby=own-h]");
      await expect(own.getByRole("link", { name: new RegExp(title) })).toBeVisible();
      await expect(own.getByText("Your file").first()).toBeVisible();
      await expect(own.getByRole("link", { name: "Open your Library" })).toBeVisible();

      // Search: the private chapter's text is found, marked as the student's own file.
      await quiet(page);
      await expect
        .poll(
          async () => {
            await quiet(page);
            await page.goto(`/search?q=${word}`);
            const hit = page.locator("[data-result]").filter({ hasText: "Your file" });
            await hit
              .first()
              .waitFor({ timeout: 5_000 })
              .catch(() => undefined);
            return hit.count();
          },
          { timeout: 60_000, intervals: [1_000] },
        )
        .toBeGreaterThan(0);

      // Reading it opens the private chapter.
      await quiet(page);
      await page.goto("/learn/library");
      await card(page, "tubule-notes.pdf").getByRole("link", { name: "Open chapter" }).click();
      await expect(page).toHaveURL(/\/learn\/[^/]+\/[^/]+$/);
      await quiet(page);
      expect(appErrors(errors)).toEqual([]);
    } finally {
      await student.done();
      rmSync(work, { recursive: true, force: true });
    }
  });

  test("a personal document stops at the Vault prompt and is stored in the Vault", async ({
    browser,
    baseURL,
  }, info) => {
    test.setTimeout(180_000);
    const student = await freshStudent(browser, baseURL as string, info, "libv");
    const work = workDir();
    try {
      const file = makePdf(path.join(work, "travel-scan.pdf"), "passport", "Scan", "x");
      const page = await student.open("/learn/library");
      const { errors } = watchConsole(page);
      await upload(page, file);
      const prompt = page.locator("[data-vault-card]");
      await expect(prompt).toBeVisible({ timeout: 90_000 });
      await expect(prompt.getByText("This looks like a personal document.")).toBeVisible();
      await expect(prompt.getByText("Vault files are never sent to AI.", { exact: false })).toBeVisible();
      await expect(prompt.getByText("Passport machine-readable lines")).toBeVisible();
      await expect(card(page, "travel-scan.pdf")).toHaveAttribute("data-status", "personal");
      // A personal document is never offered as a chapter or opened in the viewer.
      await expect(page.locator("[data-confirm-card]")).toHaveCount(0);
      await expect(card(page, "travel-scan.pdf").getByRole("link", { name: "Open PDF" })).toHaveCount(0);
      await settle(page);
      await expectNoAxeViolations(page);
      await expectNoHorizontalOverflow(page);

      await prompt.getByRole("button", { name: "Store in Vault" }).click();
      const stored = card(page, "travel-scan.pdf");
      await expect(stored).toHaveAttribute("data-status", "vaulted", { timeout: 60_000 });
      await expect(stored.getByText("Status: In your Vault")).toBeVisible();
      await expect(stored.getByText("Stored in your Vault. Vault files are never sent to AI.")).toBeVisible();
      await expect(page.locator("[data-vault-card]")).toHaveCount(0);
      await expectNoAxeViolations(page);
      await quiet(page);
      expect(appErrors(errors)).toEqual([]);
    } finally {
      await student.done();
      rmSync(work, { recursive: true, force: true });
    }
  });

  test("the PDF viewer keeps ink on page 2 across a reload; Ask is absent while the assistant is off", async ({
    browser,
    baseURL,
  }, info) => {
    test.setTimeout(180_000);
    const student = await freshStudent(browser, baseURL as string, info, "libp");
    const work = workDir();
    try {
      const file = makePdf(path.join(work, "cardio.pdf"), "notes", `Cardio ${tag(info)}`, "cardio");
      const page = await student.open("/learn/library");
      const { errors } = watchConsole(page);
      await upload(page, file);
      const open = card(page, "cardio.pdf").getByRole("link", { name: "Open PDF" });
      await expect(open).toBeVisible({ timeout: 90_000 });
      await quiet(page);
      await open.click();
      await expect(page).toHaveURL(/\/learn\/library\/pdf\/[0-9a-f-]{36}/);
      await expect(page.locator("[data-pdf-ready]")).toBeAttached({ timeout: 30_000 });
      await expect(page.getByRole("list", { name: "Page thumbnails" }).getByRole("button")).toHaveCount(3);

      // Go to page 2 with the thumbnail strip, then write on it.
      await page.getByRole("button", { name: "Go to page 2" }).click();
      const page2 = page.locator("li[data-page='2']");
      await expect(page2.locator("canvas[data-pdf-canvas=rendered]")).toBeAttached({ timeout: 30_000 });
      await expect(page.getByLabel("Page number")).toHaveValue("2");
      await page.getByRole("button", { name: "Write" }).click();
      await penStroke(page, "2", 0.2, 0.7, 0.3);
      await penStroke(page, "2", 0.25, 0.65, 0.4);
      await expect(page2.locator("[data-ink-save=saved]")).toBeAttached({ timeout: 20_000 });
      await expect(page2.locator("[data-ink-idle]")).toBeAttached({ timeout: 20_000 });
      await settle(page);
      await expectNoAxeViolations(page);
      await expectNoHorizontalOverflow(page);

      // Lasso around the ink: the selection is made, but "Ask about this" only exists while the assistant can
      // answer, and the assistant is off on this build.
      await page.getByRole("button", { name: /^Lasso/ }).click();
      await lasso(page, "2", 0.15, 0.25, 0.75, 0.45);
      await expect(page.locator("[data-pdf-ask]")).toHaveCount(0);

      // Reload at ?page=2: the viewer opens there and page 2's ink comes back from the server.
      await quiet(page);
      const url = new URL(page.url());
      expect(url.searchParams.get("page")).toBe("2");
      await page.reload();
      await expect(page.locator("[data-pdf-ready]")).toBeAttached({ timeout: 30_000 });
      await expect(page.getByLabel("Page number")).toHaveValue("2");
      const ink = page.locator("li[data-page='2'] [data-pdf-ink]");
      await expect(ink).toHaveAttribute("data-ink-idle", "", { timeout: 20_000 });
      await expect.poll(() => strokeCount(page, "2"), { timeout: 20_000 }).toBe(2);
      await quiet(page);
      expect(appErrors(errors)).toEqual([]);
    } finally {
      await student.done();
      rmSync(work, { recursive: true, force: true });
    }
  });
});

/** Scroll a PDF page into the middle of the viewport. */
async function showPage(page: Page, n: string, fy: number) {
  await page.evaluate(
    ({ n, fy }) => {
      const s = document.querySelector(`li[data-page='${n}'] [data-ink-surface]`) as HTMLElement;
      const r = s.getBoundingClientRect();
      window.scrollTo({
        top: Math.max(0, r.top + window.scrollY + r.height * fy - window.innerHeight / 2),
        behavior: "instant",
      });
    },
    { n, fy },
  );
}

/** A pen stroke across page `n` at height `fy` from `x0` to `x1` (fractions of the page). */
async function penStroke(page: Page, n: string, x0: number, x1: number, fy: number) {
  await showPage(page, n, fy);
  await page.evaluate(
    async ({ n, x0, x1, fy }) => {
      const layer = document.querySelector(`li[data-page='${n}'] [data-ink-layer]`) as HTMLElement | null;
      if (!layer) throw new Error("ink layer missing");
      const r = layer.getBoundingClientRect();
      const mk = (kind: string, fx: number, buttons: number) =>
        new PointerEvent(kind, {
          pointerId: 7,
          pointerType: "pen",
          isPrimary: true,
          bubbles: true,
          cancelable: true,
          composed: true,
          clientX: r.left + fx * r.width,
          clientY: r.top + fy * r.height,
          pressure: buttons ? 0.6 : 0,
          buttons,
          button: kind === "pointermove" ? -1 : 0,
          width: 1,
          height: 1,
        });
      layer.dispatchEvent(mk("pointerdown", x0, 1));
      for (let i = 1; i < 20; i++) {
        layer.dispatchEvent(mk("pointermove", x0 + ((x1 - x0) * i) / 19, 1));
        await new Promise((res) => setTimeout(res, 8));
      }
      layer.dispatchEvent(mk("pointerup", x1, 0));
      await new Promise((res) => requestAnimationFrame(() => requestAnimationFrame(res)));
    },
    { n, x0, x1, fy },
  );
}

/** A closed lasso loop (a rectangle) on page `n`, in fractions of the page. */
async function lasso(page: Page, n: string, x0: number, y0: number, x1: number, y1: number) {
  await showPage(page, n, (y0 + y1) / 2);
  await page.evaluate(
    async ({ n, x0, y0, x1, y1 }) => {
      const layer = document.querySelector(`li[data-page='${n}'] [data-ink-layer]`) as HTMLElement | null;
      if (!layer) throw new Error("ink layer missing");
      const r = layer.getBoundingClientRect();
      const pts: [number, number][] = [];
      const edge = (ax: number, ay: number, bx: number, by: number) => {
        for (let i = 0; i < 8; i++) pts.push([ax + ((bx - ax) * i) / 8, ay + ((by - ay) * i) / 8]);
      };
      edge(x0, y0, x1, y0);
      edge(x1, y0, x1, y1);
      edge(x1, y1, x0, y1);
      edge(x0, y1, x0, y0);
      pts.push([x0, y0]);
      const mk = (kind: string, [fx, fy]: [number, number], buttons: number) =>
        new PointerEvent(kind, {
          pointerId: 8,
          pointerType: "pen",
          isPrimary: true,
          bubbles: true,
          cancelable: true,
          composed: true,
          clientX: r.left + fx * r.width,
          clientY: r.top + fy * r.height,
          pressure: buttons ? 0.5 : 0,
          buttons,
          button: kind === "pointermove" ? -1 : 0,
        });
      layer.dispatchEvent(mk("pointerdown", pts[0] as [number, number], 1));
      for (const p of pts.slice(1)) {
        layer.dispatchEvent(mk("pointermove", p, 1));
        await new Promise((res) => setTimeout(res, 8));
      }
      layer.dispatchEvent(mk("pointerup", pts.at(-1) as [number, number], 0));
      await new Promise((res) => requestAnimationFrame(() => requestAnimationFrame(res)));
    },
    { n, x0, y0, x1, y1 },
  );
}

/** Strokes the server holds for page `n`'s ink surface (read through the ink API as the student). */
async function strokeCount(page: Page, n: string): Promise<number> {
  return page.evaluate(async (n) => {
    const id = document.querySelector(`li[data-page='${n}'] [data-pdf-ink]`)?.getAttribute("data-pdf-ink") ?? "";
    const raw = localStorage.getItem("naadi.auth") ?? "{}";
    const token = (JSON.parse(raw) as { access_token?: string }).access_token ?? "";
    const res = await fetch(`/api/ink/pdf_page/${encodeURIComponent(id)}`, {
      headers: { authorization: `Bearer ${token}` },
    });
    if (!res.ok) return -1;
    return ((await res.json()) as { item_count: number }).item_count;
  }, n);
}
