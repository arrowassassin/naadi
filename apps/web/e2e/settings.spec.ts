import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import type { Page } from "@playwright/test";
import { canMintUsers } from "./auth-users";
import { expect, expectNoAxeViolations, expectNoHorizontalOverflow, quiet, snapSegment, test } from "./fixtures";
import { API_DIR, watchConsole as baseWatch, freshStudent } from "./support/student";

/**
 * Segment 21 (W2): the Settings shell and its groups against the real API, database, auth and the local
 * storage-api. Export everything runs for real: the server builds the ZIP into storage, the signed URL downloads,
 * and this device makes the notes PDFs. A separate test stubs the export endpoints only to show states a real job
 * passes through too quickly to look at (running, failed, expired, 409, 503, list error).
 */
test.skip(!canMintUsers(), "signed-in tests need the service role (run pnpm e2e:live-auth against the Space)");

/**
 * The shared console watcher plus a filter: WebKit reports the failed POST of the CSP violation report that the
 * screenshot tool's own injected stylesheet causes (see TOOL_CSP_NOISE) as "WebKit encountered an internal error"
 * on /api/csp-report, after the tool window has closed. That request is the browser's, not the app's.
 * `errors` is a function here: call it at the end of the test.
 */
function watchConsole(page: Page) {
  const w = baseWatch(page);
  const report: string[] = [];
  page.on("console", (m) => {
    if (m.type() === "error" && m.location().url.endsWith("/api/csp-report")) report.push(m.text());
  });
  const errors = () => {
    const skip = [...report];
    return w.errors.filter((e) => {
      const i = skip.indexOf(e);
      if (i < 0) return true;
      skip.splice(i, 1);
      return false;
    });
  };
  return { guard: w.guard, errors };
}

const GROUPS = [
  { id: "account", path: "/settings/account", name: "Account" },
  { id: "ink", path: "/settings/ink", name: "Pencil & ink" },
  { id: "plan", path: "/settings/plan", name: "Study plan" },
  { id: "appearance", path: "/settings/appearance", name: "Appearance" },
  // S18: Download this week, storage and persistence (every student, no flag).
  { id: "offline", path: "/settings/offline", name: "Offline" },
  // S14: shown only while the assistant feature is on (the test filters it by /api/me's features).
  { id: "assistant", path: "/settings/assistant", name: "Assistant" },
  { id: "data", path: "/settings/data", name: "Data & export" },
  { id: "about", path: "/settings/about", name: "About & privacy" },
] as const;

async function api<T = unknown>(
  page: Page,
  method: string,
  url: string,
  body?: unknown,
): Promise<{ status: number; json: T }> {
  return page.evaluate(
    async ({ method, url, body }) => {
      const raw = localStorage.getItem("naadi.auth") ?? "{}";
      const token = (JSON.parse(raw) as { access_token?: string }).access_token ?? "";
      const res = await fetch(url, {
        method,
        headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      return { status: res.status, json: res.status === 204 ? null : await res.json() };
    },
    { method, url, body },
  ) as Promise<{ status: number; json: T }>;
}

const inDays = (n: number): string => new Date(Date.now() + n * 86_400_000).toISOString().slice(0, 10);

async function onboard(page: Page): Promise<void> {
  const r = await api(page, "PUT", "/api/onboarding", {
    exam: "step1",
    exam_date: inDays(90),
    daily_hours: 6,
    study_days: [1, 2, 3, 4, 5, 6],
    materials: ["qbank"],
    medical_school: null,
  });
  expect(r.status, "onboarding through the API").toBe(200);
}

function dbUrl(): string {
  const db = process.env.E2E_DATABASE_URL ?? process.env.DATABASE_URL;
  if (!db) throw new Error("needs the local database (E2E_DATABASE_URL or DATABASE_URL)");
  return db;
}

/** A stored upload, a vault object and its vault_documents row for the throwaway student (local stack only). */
function exportFiles(action: "create" | "delete", email: string): { upload: string; vault: string } {
  const out = execFileSync(
    "uv",
    ["--directory", API_DIR, "run", "--frozen", "python", "-m", "tests.auth.e2e_export_files", action].concat([
      "--email",
      email,
      "--db",
      dbUrl(),
    ]),
    { encoding: "utf8", timeout: 60_000, stdio: ["ignore", "pipe", "pipe"] },
  );
  return JSON.parse(out.trim().split("\n").pop() || "{}") as { upload: string; vault: string };
}

const PY_LIST = "import sys,zipfile,json;print(json.dumps(zipfile.ZipFile(sys.argv[1]).namelist()))";
const PY_READ = "import sys,zipfile;sys.stdout.write(zipfile.ZipFile(sys.argv[1]).read(sys.argv[2]).decode())";

function zipNames(file: string): string[] {
  return JSON.parse(execFileSync("python3", ["-I", "-c", PY_LIST, file], { encoding: "utf8" })) as string[];
}
function zipText(file: string, name: string): string {
  return execFileSync("python3", ["-I", "-c", PY_READ, file, name], { encoding: "utf8", maxBuffer: 64 * 1024 * 1024 });
}

const shot = (
  page: Page,
  info: Parameters<typeof snapSegment>[1],
  guard: Parameters<typeof snapSegment>[2],
  n: string,
) => snapSegment(page, info, guard, "21", n, true);

/** The group page is up: heading, fonts, nothing in flight. */
async function ready(page: Page, heading: string): Promise<void> {
  await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible();
  await quiet(page);
}

test.describe("Settings shell and groups", () => {
  test("every group renders, links only to pages that exist, is axe-clean and saves the study plan", async ({
    browser,
    baseURL,
  }, info) => {
    test.setTimeout(240_000);
    const student = await freshStudent(browser, baseURL as string, info, "settings");
    try {
      const page = await student.open("/settings/account");
      const { errors, guard } = watchConsole(page);
      await onboard(page);

      // /settings lands on the profile
      await page.goto("/settings");
      await page.waitForURL((u) => u.pathname === "/settings/account");
      await ready(page, "Account");

      const nav = page.getByRole("navigation", { name: "Settings sections" });
      // Assistant follows the server's `assistant` flag (on by default since S14; a database whose feature_flags an
      // admin saved before then keeps it off through reseeds). Whichever it is, the nav must match it exactly.
      const features = (await api<{ features: Record<string, boolean> }>(page, "GET", "/api/me")).json.features;
      const groups = GROUPS.filter((g) => g.id !== "assistant" || features.assistant === true);
      const links = await nav.getByRole("link").evaluateAll((as) => as.map((a) => (a as HTMLAnchorElement).pathname));
      expect(links).toEqual(groups.map((g) => g.path));
      await expect(nav.getByRole("link", { name: "Offline" })).toHaveCount(1);
      if (features.assistant !== true) await expect(nav.getByRole("link", { name: "Assistant" })).toHaveCount(0);

      for (const g of groups) {
        await page.goto(g.path);
        await ready(page, g.name === "Account" ? "Account" : g.name);
        await expect(page.getByText("Page not found")).toHaveCount(0);
        await expect(nav.getByRole("link", { name: g.name })).toHaveAttribute("aria-current", "page");
        await expectNoHorizontalOverflow(page);
        await expectNoAxeViolations(page);
        await shot(page, info, guard, `settings-${g.id}`);
      }

      // phone: the pill row scrolls sideways; no pill is cut in half by wrapping or truncation
      if ((info.project.use.viewport?.width ?? 1000) < 500) {
        const row = nav.getByRole("list");
        const { sw, cw } = await row.evaluate((el) => ({ sw: el.scrollWidth, cw: el.clientWidth }));
        expect(sw).toBeGreaterThanOrEqual(cw);
        const tops = await nav
          .getByRole("link")
          .evaluateAll((as) => new Set(as.map((a) => a.getBoundingClientRect().top)).size);
        expect(tops, "one row of pills").toBe(1);
      }

      // Appearance: facts, no control
      await page.goto("/settings/appearance");
      await ready(page, "Appearance");
      const main = page.locator("[data-settings-group=appearance]");
      await expect(main.getByText("Dark", { exact: true })).toBeVisible();
      await expect(main.getByText(/light theme will come later/i)).toBeVisible();
      for (const role of ["switch", "checkbox", "radio", "combobox", "slider", "button"] as const) {
        await expect(main.getByRole(role)).toHaveCount(0);
      }

      // About: version, statements, YouTube links open in a new tab
      await page.goto("/settings/about");
      await ready(page, "About & privacy");
      await expect(page.locator("[data-app-version]")).toContainText("e2e");
      await expect(page.getByText(/may log the prompts/)).toBeVisible();
      await expect(page.getByText(/YouTube API Services/).first()).toBeVisible();
      const terms = page.getByRole("link", { name: /YouTube Terms of Service/ });
      await expect(terms).toHaveAttribute("href", "https://www.youtube.com/t/terms");
      await expect(terms).toHaveAttribute("target", "_blank");
      await expect(terms).toHaveAttribute("rel", "noopener noreferrer");
      await expect(page.getByRole("link", { name: /Google Privacy Policy/ })).toHaveAttribute(
        "href",
        "https://policies.google.com/privacy",
      );

      // Study plan: Save is off until something changes, then the plan is rebuilt and the change survives a reload
      await page.goto("/settings/plan");
      await ready(page, "Study plan");
      const save = page.getByRole("button", { name: "Save changes" });
      await expect(save).toBeDisabled();
      const sun = page.getByRole("button", { name: "Sun", exact: true });
      await expect(sun).toHaveAttribute("aria-pressed", "false");
      await sun.click();
      await expect(sun).toHaveAttribute("aria-pressed", "true");
      await expect(page.locator("[data-plan-summary]")).toContainText("study days");
      await expect(save).toBeEnabled();
      await shot(page, info, guard, "settings-plan-dirty");
      await save.click();
      await expect(
        page.getByRole("status").filter({ hasText: "Saved. Your plan was rebuilt from today." }),
      ).toBeVisible();
      await expect(save).toBeDisabled();
      await quiet(page);
      await shot(page, info, guard, "settings-plan-saved");
      await page.reload();
      await ready(page, "Study plan");
      await expect(page.getByRole("button", { name: "Sun", exact: true })).toHaveAttribute("aria-pressed", "true");
      await expectNoAxeViolations(page);
      const me = await api<{ profile: { study_days: number[] } }>(page, "GET", "/api/me");
      expect(me.json.profile.study_days).toEqual([1, 2, 3, 4, 5, 6, 7]);

      expect(errors(), "console errors").toEqual([]);
    } finally {
      await student.done();
    }
  });

  test("a student with no plan is sent to set one up; the plan loading and offline states", async ({
    browser,
    baseURL,
  }, info) => {
    test.setTimeout(120_000);
    const student = await freshStudent(browser, baseURL as string, info, "noplan");
    try {
      const page = await student.open("/settings/plan");
      const { errors, guard } = watchConsole(page);
      await ready(page, "Study plan");
      const setup = page.getByRole("link", { name: "Set up my plan" });
      await expect(setup).toHaveAttribute("href", "/welcome");
      await expectNoAxeViolations(page);
      await shot(page, info, guard, "settings-plan-empty");

      await onboard(page);
      await page.reload();
      await ready(page, "Study plan");
      await page.context().setOffline(true);
      await page.getByRole("button", { name: "Sun", exact: true }).click();
      await expect(page.getByText("Connect to save changes.")).toBeVisible();
      await expect(page.getByRole("button", { name: "Save changes" })).toBeDisabled();
      await shot(page, info, guard, "settings-plan-offline");
      await page.context().setOffline(false);
      expect(errors().filter((e) => !/Failed to load resource|Load failed|net::|NetworkError/.test(e))).toEqual([]);
    } finally {
      await student.done();
    }
  });
});

test.describe("Data & export", () => {
  test("Export everything: the ZIP downloads with the student's own data, the vault only when ticked, and the notes PDFs are made on the device", async ({
    browser,
    baseURL,
  }, info) => {
    test.setTimeout(300_000);
    const student = await freshStudent(browser, baseURL as string, info, "export");
    try {
      const page = await student.open("/settings/data");
      const { errors, guard } = watchConsole(page);
      await ready(page, "Data & export");

      // Empty state
      await expect(page.getByText("No exports yet.")).toBeVisible();
      const cta = page.getByRole("button", { name: "Export everything" });
      await expect(cta).toBeEnabled();
      const vault = page.getByRole("checkbox", { name: "Include Vault documents" });
      await expect(vault).not.toBeChecked();
      await expectNoAxeViolations(page);
      await expectNoHorizontalOverflow(page);
      await shot(page, info, guard, "export-empty");

      // The student's own content: a typed note, a notebook, a stored upload and a vault document
      const note = await api<{ id: string }>(page, "POST", "/api/notes", {
        kind: "note",
        title: "E2E renal note",
        body: {
          type: "doc",
          content: [{ type: "paragraph", content: [{ type: "text", text: "Loop diuretics block NKCC2." }] }],
        },
      });
      expect(note.status).toBe(201);
      const book = await api<{ id: string }>(page, "POST", "/api/notebooks", {
        title: "E2E cardio book",
        paper: "ruled",
      });
      expect(book.status).toBe(201);
      const stored = exportFiles("create", student.email);

      // Offline: the CTA is disabled and says why
      await page.context().setOffline(true);
      await expect(cta).toBeDisabled();
      await expect(page.getByText("You are offline. Connect to export.")).toBeVisible();
      await shot(page, info, guard, "export-offline");
      await page.context().setOffline(false);
      await expect(cta).toBeEnabled();

      // Run it (Vault off). The job runs against the real API and storage.
      await quiet(page);
      await cta.click();
      const row = page.locator("[data-export-job]").first();
      await expect(row).toHaveAttribute("data-export-job", "ready", { timeout: 90_000 });
      await expect(row.getByText("Ready", { exact: true })).toBeVisible();
      await expect(row.getByText(/Total size/)).toBeVisible();
      await expect(row.getByText(/Available until .*20\d\d/)).toBeVisible();
      await expect(row.getByText("Without Vault documents")).toBeVisible();
      const part = row.getByRole("link", { name: /Data and files/ });
      await expect(part).toHaveCount(1);

      // The notes PDFs are made in the same flow, on this device
      const pdfs = page.locator("[data-pdf-phase]");
      await expect(pdfs).toHaveAttribute("data-pdf-phase", "done", { timeout: 120_000 });
      await expect(page.getByRole("button", { name: /^Notes PDF/ })).toBeVisible();
      await expect(page.getByRole("button", { name: /^Notebooks PDF/ })).toBeVisible();
      await expect(page.getByText("No highlights to export.")).toBeVisible();
      await quiet(page);
      await expectNoAxeViolations(page);
      await expectNoHorizontalOverflow(page);
      await shot(page, info, guard, "export-ready");

      const zipDownload = page.waitForEvent("download");
      await part.click();
      const zipFile = await zipDownload;
      expect(zipFile.suggestedFilename()).toMatch(/\.zip$/);
      const zipPath = (await zipFile.path()) as string;
      const names = zipNames(zipPath);
      expect(names).toContain("manifest.json");
      expect(names.filter((n) => n.startsWith("data/") && n.endsWith(".json")).length).toBeGreaterThan(5);
      expect(names).toContain("data/notes.json");
      expect(names).toContain("files/uploads/e2e-upload.pdf");
      expect(
        names.some((n) => n.includes("vault")),
        "no vault file or table by default",
      ).toBe(false);
      expect(zipText(zipPath, "files/uploads/e2e-upload.pdf")).toBe("%PDF-1.4 e2e uploaded file");
      const manifest = JSON.parse(zipText(zipPath, "manifest.json")) as { include_vault: boolean };
      expect(manifest.include_vault).toBe(false);
      expect(zipText(zipPath, "data/notes.json")).toContain("E2E renal note");
      expect(zipText(zipPath, "data/notebooks.json")).toContain("E2E cardio book");

      // The notes PDF (a user gesture per file) holds the note's words
      const pdfDownload = page.waitForEvent("download");
      await page.getByRole("button", { name: /^Notes PDF/ }).click();
      const pdfFile = await pdfDownload;
      expect(pdfFile.suggestedFilename()).toBe("naadi-notes.pdf");
      const pdfjs = await import("pdfjs-dist/legacy/build/pdf.mjs");
      const doc = await pdfjs.getDocument({ data: new Uint8Array(readFileSync((await pdfFile.path()) as string)) })
        .promise;
      let text = "";
      for (let i = 1; i <= doc.numPages; i++) {
        text += (await (await doc.getPage(i)).getTextContent()).items
          .map((it) => ("str" in it ? it.str : ""))
          .join(" ");
      }
      expect(text).toContain("Loop diuretics block NKCC2");
      const bookDownload = page.waitForEvent("download");
      await page.getByRole("button", { name: /^Notebooks PDF/ }).click();
      expect((await bookDownload).suggestedFilename()).toBe("naadi-notebooks.pdf");

      // A reload keeps the finished export (and nothing was uploaded for the PDFs)
      await page.reload();
      await ready(page, "Data & export");
      await expect(page.locator("[data-export-job=ready]")).toHaveCount(1);
      await expect(page.locator("[data-pdf-phase]")).toHaveAttribute("data-pdf-phase", "idle");
      await expect(page.getByRole("button", { name: "Make notes PDFs" })).toBeVisible();

      // Second run with the Vault ticked: the vault document and its table are in the ZIP
      await page.getByRole("checkbox", { name: "Include Vault documents" }).check();
      await page.getByRole("button", { name: "Export everything" }).click();
      const second = page.locator("[data-export-job]").first();
      await expect(second).toHaveAttribute("data-export-job", "ready", { timeout: 90_000 });
      await expect(second.getByText("With Vault documents")).toBeVisible();
      const vaultDownload = page.waitForEvent("download");
      await second.getByRole("link", { name: /Data and files/ }).click();
      const vaultZip = (await (await vaultDownload).path()) as string;
      const vaultNames = zipNames(vaultZip);
      expect(vaultNames).toContain(`files/vault/${stored.vault}`);
      expect(vaultNames).toContain("data/vault_documents.json");
      expect(zipText(vaultZip, `files/vault/${stored.vault}`)).toBe("%PDF-1.4 e2e vault document");
      expect(zipText(vaultZip, "files/uploads/e2e-upload.pdf")).toBe("%PDF-1.4 e2e uploaded file");
      expect((JSON.parse(zipText(vaultZip, "manifest.json")) as { include_vault: boolean }).include_vault).toBe(true);
      await quiet(page);
      await shot(page, info, guard, "export-two-ready");

      // Delete a finished export
      await page.locator("[data-export-job=ready]").first().getByRole("button", { name: "Delete export" }).click();
      await shot(page, info, guard, "export-delete-confirm");
      await page.getByRole("button", { name: "Delete", exact: true }).click();
      await expect(page.locator("[data-export-job=ready]")).toHaveCount(1);
      await quiet(page);

      expect(errors(), "console errors").toEqual([]);
    } finally {
      try {
        exportFiles("delete", student.email);
      } finally {
        await student.done();
      }
    }
  });

  test("the states of a job and of a refused start, worded and axe-clean", async ({ browser, baseURL }, info) => {
    test.setTimeout(240_000);
    const student = await freshStudent(browser, baseURL as string, info, "states");
    try {
      const base = {
        id: "00000000-0000-4000-8000-000000000001",
        include_vault: false,
        parts: 1,
        bytes: null,
        created_at: new Date().toISOString(),
        expires_at: null,
        error: null,
        downloads: [],
      };
      let items: unknown[] = [];
      let startAnswer: { status: number; code: string } | null = null;
      let listFails = true;
      const page = await student.open("/settings/data", async (p) => {
        await p.route("**/api/export", async (route) => {
          const req = route.request();
          if (req.method() === "GET") {
            if (listFails) {
              return route.fulfill({
                status: 500,
                contentType: "application/json",
                body: JSON.stringify({ error: { code: "internal", message: "internal", request_id: "req-e2e" } }),
              });
            }
            return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items }) });
          }
          if (startAnswer) {
            return route.fulfill({
              status: startAnswer.status,
              contentType: "application/json",
              body: JSON.stringify({
                error: { code: startAnswer.code, message: startAnswer.code, request_id: "req-e2e" },
              }),
            });
          }
          return route.continue();
        });
        await p.route("**/api/export/00000000-0000-4000-8000-000000000001", (route) =>
          route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify(items[0] ?? base),
          }),
        );
      });
      const { errors, guard } = watchConsole(page);
      await ready(page, "Data & export");
      // The list could not be loaded: the error, its request id and a retry (the stub answers 500 until told otherwise).
      // Pages are not reloaded below: once the service worker controls the page, the stubs no longer see its requests.
      await expect(page.getByRole("alert").filter({ hasText: "Your exports could not be loaded" })).toContainText(
        "req-e2e",
      );
      await expectNoAxeViolations(page);
      await shot(page, info, guard, "export-list-error");
      listFails = false;
      await page.getByRole("button", { name: "Try again" }).click();
      await expect(page.locator("[data-export-empty]")).toBeVisible();
      // Not page.reload(): a reload skips the stubs above (the service worker controls the page), so leave the page and come back to refetch
      const revisit = async () => {
        await page.getByRole("link", { name: "Appearance" }).first().click();
        await ready(page, "Appearance");
        await page.getByRole("link", { name: "Data & export" }).first().click();
        await ready(page, "Data & export");
      };
      const state = async (name: string, list: unknown[]) => {
        items = list;
        await revisit();
        await expectNoAxeViolations(page);
        await expectNoHorizontalOverflow(page);
        await shot(page, info, guard, `export-${name}`);
      };

      await state("running", [{ ...base, status: "running" }]);
      await expect(page.getByText("Building", { exact: true })).toBeVisible();
      await expect(page.getByText(/Building your archive/)).toBeVisible();
      await expect(page.getByRole("button", { name: "Export in progress" })).toBeDisabled();

      await state("queued", [{ ...base, status: "queued" }]);
      await expect(page.getByText("Queued", { exact: true })).toBeVisible();

      await state("failed", [{ ...base, status: "failed", error: "export_failed" }]);
      await expect(page.getByRole("alert").filter({ hasText: "The export failed" })).toBeVisible();
      await expect(page.getByRole("button", { name: "Export everything" })).toBeEnabled();

      await state("expired", [{ ...base, status: "expired" }]);
      await expect(page.getByText(/This export has expired/)).toBeVisible();

      await state("multipart", [
        {
          ...base,
          status: "ready",
          parts: 2,
          bytes: 73_400_320,
          expires_at: new Date(Date.now() + 86_400_000).toISOString(),
          downloads: [
            { part: 1, url: "http://127.0.0.1:1/p1.zip" },
            { part: 2, url: "http://127.0.0.1:1/p2.zip" },
          ],
        },
      ]);
      await expect(page.getByRole("link", { name: "Data and files, part 1 of 2" })).toBeVisible();
      await expect(page.getByRole("link", { name: "Data and files, part 2 of 2" })).toBeVisible();
      await expect(page.getByText(/Total size 70\.0 MB/)).toBeVisible();

      // Refused starts
      items = [];
      for (const [name, status, code, words] of [
        ["409", 409, "export_active", "An export is already running"],
        ["503", 503, "export_unavailable", "Export is not available right now"],
      ] as const) {
        startAnswer = { status, code };
        await revisit();
        await page.getByRole("button", { name: "Export everything" }).click();
        await expect(page.getByText(words)).toBeVisible();
        await expectNoAxeViolations(page);
        await shot(page, info, guard, `export-start-${name}`);
        // The PDFs are only made when a job runs (or is already running): wait for the 409 run to settle
        await expect(page.locator("[data-pdf-phase]")).not.toHaveAttribute("data-pdf-phase", "preparing", {
          timeout: 60_000,
        });
      }
      startAnswer = null;

      // The intended refusals log "Failed to load resource"; anything else is a defect
      expect(errors().filter((e) => !/Failed to load resource|status of (409|500|503)/.test(e))).toEqual([]);
    } finally {
      await student.done();
    }
  });
});
