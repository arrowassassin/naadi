import { execFileSync } from "node:child_process";
import path from "node:path";
import type { Browser, BrowserContext, Page, TestInfo } from "@playwright/test";
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
import { ensureOnboarded, signedInState } from "./session";

/**
 * Segment 17: the Pathway screen, the document vault and the cost tracker against the real API, database, Storage and
 * auth server. Each test is its own throwaway student (deleted afterwards); the admin is the run's admin account.
 *
 * Covers: the seeded 16 stages with their checked dates and official links; a task set to Done survives a reload;
 * the "Re-verify" badge (word + icon) on a task last checked 91 days ago or flagged, and not at 90 days; the vault
 * (upload through a signed URL, open through a signed URL, delete; private from other students; disabled offline
 * with a reason); costs in two stages and two currencies with totals that equal the server's; a status and a cost made
 * offline wait on the device and sync; an admin's "Mark verified"; the Today strip; the production CSP.
 * Frozen-DOM axe on every screen, zero console errors, no horizontal scroll, one lime action.
 */
test.skip(!canMintUsers(), "signed-in tests need the service role (run pnpm e2e:live-auth against the Space)");

const API_DIR = path.resolve(import.meta.dirname, "../../api");

const snap = (page: Page, info: TestInfo, guard: ConsoleGuard, state: string, full = false) =>
  snapSegment(page, info, guard, "17", state, full);

function email(info: TestInfo, kind: string): string {
  const run = String.fromCharCode(97 + (info.repeatEachIndex % 26), 97 + (info.retry % 26));
  const role = `${kind}${info.project.name.replace(/[^a-z]/g, "")}${run}`;
  return `naadi-e2e-${role}-${process.env.E2E_RUN_ID}@example.com`;
}

/** A short lowercase tag unique to this test, project and run (the throwaway stage's name). */
function tagFor(info: TestInfo, kind: string): string {
  const project = info.project.name.replace(/[^a-z]/g, "").slice(0, 8);
  const run = String.fromCharCode(97 + (info.repeatEachIndex % 26), 97 + (info.retry % 26));
  const id = (process.env.E2E_RUN_ID ?? "x").replace(/[^a-z0-9]/g, "").slice(-5);
  return `${kind}${project}${run}${id}`.slice(0, 24);
}

function db(): string {
  const url = process.env.E2E_DATABASE_URL ?? process.env.DATABASE_URL;
  if (!url) throw new Error("pathway.spec needs the local database (E2E_DATABASE_URL or DATABASE_URL)");
  return url;
}

function helper(action: "make" | "drop" | "state", extra: string[]): Record<string, string | boolean | number | null> {
  const args = ["--directory", API_DIR, "run", "--frozen", "python", "-m", "tests.pathway.e2e_pathway", action];
  const out = execFileSync("uv", [...args, ...extra, "--db", db()], { encoding: "utf8", timeout: 60_000 });
  return JSON.parse(out.trim().split("\n").pop() ?? "{}") as Record<string, string | boolean | number | null>;
}

/** Console errors of pages opened outside the fixture's page, with the fixture's rule for the screenshot tool. */
function watchConsole(page: Page) {
  const errors: string[] = [];
  let tool = 0;
  page.on("console", (m) => {
    // Trusted Types ship report-only: WebKit and Firefox log those reports as console errors (csp.spec collects them).
    if (/trusted[ -]types?/i.test(m.text()) && /report[ -]only/i.test(m.text())) return;
    if (m.type() === "error" && !(tool > 0 && m.text() === TOOL_CSP_NOISE)) errors.push(m.text());
  });
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  page.on("crash", () => errors.push("pageerror: the page crashed"));
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
  return { open, done, who };
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
      const text = res.status === 204 ? "" : await res.text();
      return { status: res.status, json: text ? (JSON.parse(text) as unknown) : null };
    },
    { method, url, body },
  ) as Promise<{ status: number; json: T }>;
}

/** Visible elements painted lime (the next-action colour): one per view. */
async function limeCount(page: Page): Promise<number> {
  await page.mouse.move(1, 1);
  return page.evaluate(() => {
    const lime = "rgb(198, 255, 61)";
    return [...document.querySelectorAll<HTMLElement>("body *:not([data-brand-mark])")].filter((el) => {
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0 && getComputedStyle(el).backgroundColor === lime;
    }).length;
  });
}

const isTaskWrite = (r: import("@playwright/test").Response) =>
  /\/api\/pathway\/tasks\//.test(r.url()) && r.request().method() === "PATCH" && r.status() === 200;
const isCostWrite = (method: string, status: number) => (r: import("@playwright/test").Response) =>
  /\/api\/costs(\/|$)/.test(new URL(r.url()).pathname) && r.request().method() === method && r.status() === status;

const PDF = Buffer.from("%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n");

async function revealStage(page: Page, stage: string): Promise<void> {
  const card = page.locator(`[data-stage='${stage}']`);
  await page
    .getByRole("navigation", { name: "Route: stages from eligibility to residency" })
    .getByRole("button", { name: new RegExp(`E2E stage ${stage.slice(3)}`) })
    .click();
  await expect(card.locator("[data-task]").first()).toBeVisible();
}

test.describe("pathway", () => {
  test("the seeded route: 16 stages, every task checked and linked, one lime action, no overflow", async ({
    browser,
    baseURL,
  }, info) => {
    test.setTimeout(150_000);
    const student = await freshStudent(browser, baseURL as string, info, "route");
    try {
      const { page } = await student.open("/pathway");
      const { errors, guard } = watchConsole(page);
      const pathway = (
        await api<{
          stages: { key: string; tasks: { id: string; last_verified: string | null; official_url: string | null }[] }[];
        }>(page, "GET", "/api/pathway")
      ).json;
      await expect(page.getByRole("heading", { level: 1 })).toContainText("MBBS");
      const stages = pathway.stages.filter((s) => !s.key.startsWith("e2e"));
      expect(stages).toHaveLength(16);
      expect(stages.flatMap((s) => s.tasks)).toHaveLength(186);
      // Every task carries when it was last checked and an official link (acceptance 1).
      for (const t of stages.flatMap((s) => s.tasks)) {
        expect(t.last_verified, t.id).toBeTruthy();
        expect(t.official_url, t.id).toMatch(/^https:\/\//);
      }
      await expect(page.locator("[data-stage]")).toHaveCount(pathway.stages.length);
      await expect(
        page.getByRole("navigation", { name: "Route: stages from eligibility to residency" }).getByRole("button"),
      ).toHaveCount(pathway.stages.length);
      // The student's own stage is open: its tasks show when they were checked and where the official page is.
      const first = page.locator("[data-task]").first();
      await expect(first).toBeVisible();
      await expect(first.getByText(/^Checked \d{1,2} \w{3} \d{4}$/)).toBeVisible();
      await expect(first.getByRole("link", { name: /Official page|Source/ })).toHaveAttribute("href", /^https:\/\//);
      expect(await limeCount(page), "one lime action").toBe(1);
      await expect(page.getByRole("button", { name: "Open this task" })).toBeVisible();
      await expectNoHorizontalOverflow(page);
      await quiet(page);
      await expectNoAxeViolations(page);
      await snap(page, info, guard, "route", true);
      expect(errors).toEqual([]);
    } finally {
      await student.done();
    }
  });

  test("a task set to Done survives a reload and moves the stage's progress", async ({ browser, baseURL }, info) => {
    test.setTimeout(150_000);
    const student = await freshStudent(browser, baseURL as string, info, "done");
    try {
      const { page } = await student.open("/pathway");
      const { errors, guard } = watchConsole(page);
      const row = page.locator("[data-task]").first();
      const id = (await row.getAttribute("data-task")) as string;
      await expect(row).toHaveAttribute("data-status", "todo");
      const saved = page.waitForResponse(isTaskWrite);
      await row.getByRole("button", { name: "Mark done" }).click();
      await expect(row).toHaveAttribute("data-status", "done");
      await expect(row.getByText("Done", { exact: true })).toBeVisible();
      await saved;
      await quiet(page);
      await page.reload();
      await settle(page);
      const again = page.locator(`[data-task='${id}']`);
      await expect(again).toHaveAttribute("data-status", "done");
      await expect(again.getByRole("button", { name: "Mark to do" })).toBeVisible();
      await expect(again.getByText("Done", { exact: true })).toBeVisible();
      // the status the server holds is the one shown (and it is this student's alone)
      const mine = await api<{ stages: { tasks: { id: string; status: string }[] }[] }>(page, "GET", "/api/pathway");
      expect(mine.json.stages.flatMap((s) => s.tasks).find((t) => t.id === id)?.status).toBe("done");
      // the next task moved on: the lime action opens it
      await page.getByRole("button", { name: "Open this task" }).click();
      const sheet = page.getByRole("dialog");
      await expect(sheet).toBeVisible();
      await expect(sheet.getByText(/Checked against .+ on \d{1,2} \w{3} \d{4}\./)).toBeVisible();
      const doing = page.waitForResponse(isTaskWrite);
      await sheet
        .locator("label")
        .filter({ hasText: /^Doing$/ })
        .click();
      await doing;
      await expectNoAxeViolations(page);
      await snap(page, info, guard, "task-sheet");
      await sheet.getByRole("button", { name: "Close" }).click();
      expect(errors).toEqual([]);
    } finally {
      await student.done();
    }
  });

  test("Re-verify shows as a word and icon at 91 days and when flagged, never at 90 days", async ({
    browser,
    baseURL,
  }, info) => {
    test.setTimeout(150_000);
    const tag = tagFor(info, "rv");
    const world = helper("make", ["--tag", tag]);
    const student = await freshStudent(browser, baseURL as string, info, "reverify");
    try {
      const { page } = await student.open("/pathway");
      const { errors, guard } = watchConsole(page);
      await revealStage(page, world.stage as string);
      const row = (k: string) => page.locator(`[data-task='${world[k]}']`);
      for (const k of ["d91", "flagged"]) {
        await expect(row(k).getByText(/Re-verify$/)).toBeVisible();
      }
      for (const k of ["fresh", "d90"]) {
        await expect(row(k).getByText(/Re-verify$/)).toHaveCount(0);
      }
      await expect(row("d91").getByText(/Last checked a long time ago/)).toBeAttached();
      await expect(row("flagged").getByText(/The official page changed/)).toBeAttached();
      // the check tile counts them and filters the screen down to them
      const tile = page.locator("[data-panel='check']");
      await expect(tile).toContainText(/\d+ tasks? to re-verify/);
      await tile.getByRole("button", { name: "Show tasks to re-verify" }).click();
      await expect(page.locator(`[data-stage='${world.stage}'] [data-task]`)).toHaveCount(2);
      await expect(tile.getByRole("button", { name: "Show all tasks" })).toHaveAttribute("aria-pressed", "true");
      await expectNoAxeViolations(page);
      await snap(page, info, guard, "reverify");
      // the task sheet: the official-source strip with a link on an official domain, and the due rule in words
      await row("d91")
        .getByRole("button", { name: /E2E task d91/ })
        .click();
      const sheet = page.getByRole("dialog");
      await expect(sheet.getByText(/Checked against nrmp\.org on/)).toBeVisible();
      await expect(sheet.getByRole("link", { name: /Open the official page/ })).toHaveAttribute(
        "href",
        "https://www.nrmp.org/e2e-91",
      );
      await expect(sheet.getByText(/7 days after you start/)).toBeVisible();
      await expect(sheet.getByRole("button", { name: "Mark verified" })).toHaveCount(0); // students cannot confirm
      await snap(page, info, guard, "reverify-sheet");
      expect(errors).toEqual([]);
    } finally {
      await student.done();
      helper("drop", ["--tag", tag]);
    }
  });

  test("an admin sees what the check found and confirms the task: the badge goes and last_verified moves", async ({
    browser,
    baseURL,
  }, info) => {
    test.setTimeout(150_000);
    const tag = tagFor(info, "ad");
    const world = helper("make", ["--tag", tag]);
    const state = await signedInState(browser, baseURL as string, "admin");
    const context = await browser.newContext({ ...info.project.use, baseURL, storageState: state });
    try {
      const page = await context.newPage();
      const { errors, guard } = watchConsole(page);
      trackApi(page);
      await page.goto("/pathway");
      await revealStage(page, world.stage as string);
      const flagged = page.locator(`[data-task='${world.flagged}']`);
      await expect(flagged.getByText(/Re-verify$/)).toBeVisible();
      await flagged.getByRole("button", { name: /E2E task flagged/ }).click();
      const sheet = page.getByRole("dialog");
      const finding = sheet.locator("[data-finding='changed']");
      await expect(finding).toContainText("The page changed and may disagree with this task.");
      await expect(finding).toContainText("The fee is now $950.");
      await expect(finding.getByText("“The fee is $950.”")).toBeVisible();
      await expect(finding.getByRole("link", { name: "ecfmg.org" })).toHaveAttribute(
        "href",
        "https://www.ecfmg.org/e2e-flagged",
      );
      await snap(page, info, guard, "admin-finding");
      const verified = page.waitForResponse(
        (r) => /\/api\/admin\/pathway\/tasks\/.+\/verify$/.test(r.url()) && r.status() === 200,
      );
      await sheet.getByRole("button", { name: "Mark verified" }).click();
      await verified;
      await expect(sheet.getByText("Marked verified today.")).toBeVisible();
      await sheet.getByRole("button", { name: "Close" }).click();
      await expect(flagged.getByText(/Re-verify$/)).toHaveCount(0);
      const row = helper("state", ["--task", world.flagged as string]);
      expect(row).toMatchObject({ found: true, needs_review: false, open_checks: 0 });
      expect(row.last_verified).toBe(new Date().toISOString().slice(0, 10));
      expect(errors).toEqual([]);
    } finally {
      await context.close();
      helper("drop", ["--tag", tag]);
    }
  });

  test("a student cannot reach the admin routes", async ({ browser, baseURL }, info) => {
    const student = await freshStudent(browser, baseURL as string, info, "noadmin");
    try {
      const { page } = await student.open("/pathway");
      expect((await api(page, "GET", "/api/admin/pathway/checks")).status).toBe(403);
      expect((await api(page, "POST", "/api/admin/pathway/tasks/elig-01/verify")).status).toBe(403);
      expect((await api(page, "PATCH", "/api/admin/pathway/tasks/elig-01", { cost_usd: 1 })).status).toBe(403);
    } finally {
      await student.done();
    }
  });
});

test.describe("vault", () => {
  test("upload → open through a signed URL → delete, private to the student and never kept", async ({
    browser,
    baseURL,
  }, info) => {
    test.setTimeout(180_000);
    const student = await freshStudent(browser, baseURL as string, info, "vault");
    const other = await freshStudent(browser, baseURL as string, info, "vaultother");
    try {
      const { page, context } = await student.open("/pathway");
      const { errors, guard } = watchConsole(page);
      const panel = page.locator("[data-panel='vault']");
      await expect(panel.getByText("Vault files are never sent to AI.")).toBeVisible();
      await expect(panel.locator("[data-empty='vault']")).toBeVisible();
      await snap(page, info, guard, "vault-empty");

      // upload: the bytes go from the browser to Storage on the signed URL, then the API checks them
      await panel
        .locator("[data-vault-input='file']")
        .setInputFiles({ name: "passport.pdf", mimeType: "application/pdf", buffer: PDF });
      const sheet = page.getByRole("dialog", { name: "Add to the vault" });
      await expect(sheet).toBeVisible();
      await sheet.getByLabel("Type").selectOption("passport");
      const soon = new Date(Date.now() + 30 * 86_400_000).toISOString().slice(0, 10);
      await sheet.getByLabel("Expiry date (optional)").fill(soon);
      await expectNoAxeViolations(page);
      const completed = page.waitForResponse(
        (r) => /\/api\/vault\/[^/]+\/complete$/.test(r.url()) && r.status() === 200,
      );
      await sheet.getByRole("button", { name: "Save to vault" }).click();
      const doc = (await (await completed).json()) as { id: string; mime: string; bytes: number };
      expect(doc.mime).toBe("application/pdf");
      expect(doc.bytes).toBe(PDF.length);
      const item = panel.locator(`[data-doc='${doc.id}']`);
      await expect(item).toBeVisible();
      await expect(item.getByText("Passport · 1 KB")).toBeVisible();
      await expect(item.getByText(/Expires in (29|30|31) days/)).toBeVisible(); // amber + word under 60 days
      await expect(panel.locator("[data-empty='vault']")).toHaveCount(0);
      expect(await limeCount(page), "one lime action").toBe(1);
      await snap(page, info, guard, "vault-list");

      // open: a short-lived signed URL in a new tab (a PDF may be handed to a download by the browser, so the
      // request to Storage is what is checked, not the page that follows)
      const signedResponse = context.waitForEvent("response", {
        predicate: (r) => /\/storage\/v1\/object\/sign\/vault\//.test(r.url()),
        timeout: 30_000,
      });
      await item.getByRole("button", { name: "Open Passport" }).click();
      const opened = await signedResponse;
      expect(opened.status()).toBe(200);
      const signed = opened.url();
      expect(new URL(signed).searchParams.get("token")).toBeTruthy();
      const fetched = await page.request.get(signed);
      expect(fetched.status()).toBe(200);
      expect((await fetched.body()).subarray(0, 5).toString()).toBe("%PDF-");
      for (const extra of context.pages()) if (extra !== page) await extra.close().catch(() => undefined);
      // the API never lets a signed URL be cached, and another student can neither list nor open the document
      const listed = await page.evaluate(async () => {
        const token =
          (JSON.parse(localStorage.getItem("naadi.auth") ?? "{}") as { access_token?: string }).access_token ?? "";
        const r = await fetch("/api/vault", { headers: { authorization: `Bearer ${token}` } });
        return r.headers.get("cache-control");
      });
      expect(listed).toBe("no-store");
      const otherSession = await other.open("/pathway");
      const theirs = await api<{ items: unknown[] }>(otherSession.page, "GET", "/api/vault");
      expect(theirs.json.items).toEqual([]);
      expect((await api(otherSession.page, "GET", `/api/vault/${doc.id}/url`)).status).toBe(404);
      expect((await api(otherSession.page, "DELETE", `/api/vault/${doc.id}`)).status).toBe(404);

      // nothing of the vault is on this device
      const stored = await page.evaluate(async () => {
        const keys: string[] = [];
        for (const name of await caches.keys()) {
          for (const req of await (await caches.open(name)).keys()) keys.push(req.url);
        }
        const dbs = (await indexedDB.databases?.()) ?? [];
        return { keys, dbs: dbs.map((d) => d.name ?? "") };
      });
      expect(stored.keys.filter((k) => /vault|passport/i.test(k))).toEqual([]);
      expect(stored.dbs.filter((n) => /vault/i.test(n))).toEqual([]);

      // delete, with a confirmation
      await item.getByRole("button", { name: "Delete Passport" }).click();
      const confirm = page.getByRole("dialog", { name: "Delete this document?" });
      await confirm.getByRole("button", { name: "Delete document" }).click();
      await expect(item).toHaveCount(0);
      await expect(panel.locator("[data-empty='vault']")).toBeVisible();
      await expect.poll(async () => (await page.request.get(signed)).status()).toBeGreaterThanOrEqual(400); // the object is gone
      expect(errors).toEqual([]);
    } finally {
      await student.done();
      await other.done();
    }
  });

  test("a file that is not a PDF or an image is refused and nothing is kept", async ({ browser, baseURL }, info) => {
    test.setTimeout(150_000);
    const student = await freshStudent(browser, baseURL as string, info, "vaultbad");
    try {
      const { page } = await student.open("/pathway");
      const { guard } = watchConsole(page);
      const panel = page.locator("[data-panel='vault']");
      // a script renamed to .pdf: the browser says application/pdf, the bytes say otherwise
      await panel.locator("[data-vault-input='file']").setInputFiles({
        name: "scan.pdf",
        mimeType: "application/pdf",
        buffer: Buffer.from("<html><script>alert(1)</script></html>"),
      });
      const sheet = page.getByRole("dialog", { name: "Add to the vault" });
      const refused = page.waitForResponse((r) => /\/api\/vault\/[^/]+\/complete$/.test(r.url()) && r.status() === 415);
      await sheet.getByRole("button", { name: "Save to vault" }).click();
      await refused;
      await expect(sheet.getByRole("alert")).toContainText("Only PDF, JPEG, PNG or HEIC");
      await sheet.getByRole("button", { name: "Cancel" }).click();
      await expect(panel.locator("[data-doc]")).toHaveCount(0);
      expect((await api<{ items: unknown[] }>(page, "GET", "/api/vault")).json.items).toEqual([]);
      // a file type the browser can name is refused before anything is sent
      await panel
        .locator("[data-vault-input='file']")
        .setInputFiles({ name: "notes.txt", mimeType: "text/plain", buffer: Buffer.from("hello") });
      await page
        .getByRole("dialog", { name: "Add to the vault" })
        .getByRole("button", { name: "Save to vault" })
        .click();
      await expect(page.getByRole("dialog", { name: "Add to the vault" }).getByRole("alert")).toContainText(
        "Only PDF, JPEG, PNG or HEIC",
      );
      await snap(page, info, guard, "vault-refused");
    } finally {
      await student.done();
    }
  });
});

test.describe("costs", () => {
  test("entries in two stages and two currencies: totals per stage and currency equal the server's", async ({
    browser,
    baseURL,
  }, info) => {
    test.setTimeout(180_000);
    const student = await freshStudent(browser, baseURL as string, info, "costs");
    try {
      const { page } = await student.open("/pathway");
      const { errors, guard } = watchConsole(page);
      const panel = page.locator("[data-panel='costs']");
      await expect(panel.locator("[data-empty='costs']")).toBeVisible();
      await snap(page, info, guard, "costs-empty");
      const stageKeys = await page.evaluate(() =>
        [...document.querySelectorAll<HTMLElement>("[data-stage]")]
          .map((s) => s.dataset.stage ?? "")
          .filter((k) => !k.startsWith("e2e")),
      );
      const [one, two] = [stageKeys[0] as string, stageKeys[1] as string];
      const stageTitle = async (key: string) =>
        (await page.locator(`[data-stage='${key}'] h3`).first().textContent()) as string;

      const add = async (label: string, amount: string, currency: string, stage: string, paid: string) => {
        await panel.getByRole("button", { name: "Add a cost" }).click();
        const sheet = page.getByRole("dialog", { name: "Add a cost" });
        await sheet.getByLabel("What it is for").fill(label);
        await sheet.getByLabel("Amount").fill(amount);
        await sheet.getByLabel("Currency").selectOption(currency);
        await sheet.getByLabel("Stage").selectOption({ label: await stageTitle(stage) });
        if (paid) await sheet.getByLabel("Paid on (optional)").fill(paid);
        const saved = page.waitForResponse(isCostWrite("POST", 201));
        await sheet.getByRole("button", { name: "Save cost" }).click();
        await saved;
      };
      await add("Application fee", "580", "USD", one, "2026-11-01");
      await add("Verification courier", "1200.50", "INR", one, "2026-11-02");
      await add("Prometric seat", "695", "USD", two, "");
      await expect(panel.locator("[data-cost]")).toHaveCount(3);
      // totals on screen: paid per currency (never added together), still to pay, by stage
      await expect(panel.locator("[data-total='paid']")).toHaveText(["₹1,200.50", "$580"]);
      await expect(panel).toContainText("still to pay: $695");
      await expect(panel.locator(`[data-cost-stage='${one}']`)).toContainText("paid ₹1,200.50 · paid $580");
      await expect(panel.locator(`[data-cost-stage='${two}']`)).toContainText("to pay $695");
      await expect(panel.locator(`[data-cost-stage='${two}']`).getByText("Not paid yet")).toBeVisible();

      // the server computes the same totals (by stage and by currency, nothing converted)
      const summary = await api<{
        paid: { currency: string; amount: string }[];
        upcoming: { currency: string; amount: string }[];
        by_stage: {
          stage_key: string;
          paid: { currency: string; amount: string }[];
          upcoming: { currency: string; amount: string }[];
        }[];
      }>(page, "GET", "/api/costs/summary");
      expect(summary.json.paid).toEqual([
        { currency: "INR", amount: "1200.50" },
        { currency: "USD", amount: "580.00" },
      ]);
      expect(summary.json.upcoming).toEqual([{ currency: "USD", amount: "695.00" }]);
      const byStage = Object.fromEntries(summary.json.by_stage.map((s) => [s.stage_key, s]));
      expect(byStage[one]?.paid).toEqual(summary.json.paid);
      expect(byStage[two]?.upcoming).toEqual([{ currency: "USD", amount: "695.00" }]);

      // edit and delete
      const seat = panel.locator("[data-cost]").filter({ hasText: "Prometric seat" });
      await seat.getByRole("button", { name: "Edit Prometric seat" }).click();
      const edit = page.getByRole("dialog", { name: "Edit cost" });
      await edit.getByLabel("Paid on (optional)").fill("2026-11-12");
      const patched = page.waitForResponse(isCostWrite("PATCH", 200));
      await edit.getByRole("button", { name: "Save cost" }).click();
      await patched;
      await expect(panel.locator("[data-total='paid']")).toHaveText(["₹1,200.50", "$1,275"]);
      await panel
        .locator("[data-cost]")
        .filter({ hasText: "Verification courier" })
        .getByRole("button", { name: /Delete/ })
        .click();
      const deleted = page.waitForResponse(isCostWrite("DELETE", 204));
      await page
        .getByRole("dialog", { name: "Delete this cost?" })
        .getByRole("button", { name: "Delete cost" })
        .click();
      await deleted;
      await expect(panel.locator("[data-total='paid']")).toHaveText(["$1,275"]);
      expect(await limeCount(page), "one lime action").toBe(1);
      await expectNoHorizontalOverflow(page);
      await quiet(page);
      await expectNoAxeViolations(page);
      await snap(page, info, guard, "costs", true);
      expect(errors).toEqual([]);
    } finally {
      await student.done();
    }
  });

  test("a cost from a task sheet carries its task and stage", async ({ browser, baseURL }, info) => {
    test.setTimeout(150_000);
    const tag = tagFor(info, "ct");
    const world = helper("make", ["--tag", tag]);
    const student = await freshStudent(browser, baseURL as string, info, "costtask");
    try {
      const { page } = await student.open("/pathway");
      const { errors } = watchConsole(page);
      await revealStage(page, world.stage as string);
      await page
        .locator(`[data-task='${world.d91}']`)
        .getByRole("button", { name: /E2E task d91/ })
        .click();
      const sheet = page.getByRole("dialog");
      await expect(sheet.getByText(/About \$50/)).toBeVisible();
      await sheet.getByRole("button", { name: "Add a cost for this task" }).click();
      const form = page.getByRole("dialog", { name: "Add a cost" });
      await expect(form.getByLabel("Amount")).toHaveValue("50");
      await expect(form.getByText(/For the task: E2E task d91/)).toBeVisible();
      await expect(form.getByLabel("Stage")).toHaveCount(0); // the task's stage is used
      const saved = page.waitForResponse(isCostWrite("POST", 201));
      await form.getByLabel("Paid on (optional)").fill("2026-11-05");
      await form.getByRole("button", { name: "Save cost" }).click();
      const body = (await (await saved).json()) as { cost: { task_id: string; stage_key: string } };
      expect(body.cost.task_id).toBe(world.d91);
      expect(body.cost.stage_key).toBe(world.stage);
      expect(errors).toEqual([]);
    } finally {
      await student.done();
      helper("drop", ["--tag", tag]);
    }
  });
});

test.describe("offline", () => {
  test("a status and a cost made offline wait on the device and sync; the vault says it needs a connection", async ({
    browser,
    baseURL,
  }, info) => {
    test.setTimeout(180_000);
    const student = await freshStudent(browser, baseURL as string, info, "offline");
    try {
      const { page, context } = await student.open("/pathway");
      const { errors, guard } = watchConsole(page);
      const row = page.locator("[data-task]").first();
      const id = (await row.getAttribute("data-task")) as string;
      await quiet(page);
      await context.setOffline(true);
      await expect(page.getByText(/Your progress and costs are saved on this device/)).toBeVisible();
      await row.getByRole("button", { name: "Mark done" }).click();
      await expect(row).toHaveAttribute("data-status", "done");
      await expect(row.getByText("Saved on this device · will sync")).toBeVisible();
      const costs = page.locator("[data-panel='costs']");
      await costs.getByRole("button", { name: "Add a cost" }).click();
      const form = page.getByRole("dialog", { name: "Add a cost" });
      await form.getByLabel("What it is for").fill("Offline fee");
      await form.getByLabel("Amount").fill("42");
      await form.getByRole("button", { name: "Save cost" }).click();
      await expect(
        costs.locator("[data-cost]").filter({ hasText: "Offline fee" }).getByText("Saved on this device · will sync"),
      ).toBeVisible();
      await expect(costs.locator("[data-total='paid']").first()).toBeVisible();
      // the vault: upload and open need a connection, said with the reason
      const vault = page.locator("[data-panel='vault']");
      await expect(vault.getByRole("button", { name: "Upload a file" })).toBeDisabled();
      await expect(vault.getByRole("button", { name: "Scan with camera" })).toBeDisabled();
      await expect(vault.getByText(/Offline: the vault needs a connection/)).toBeVisible();
      await snap(page, info, guard, "offline");
      // back online: both reach the server, and the marks go
      const taskSaved = page.waitForResponse(isTaskWrite);
      const costSaved = page.waitForResponse(isCostWrite("POST", 201));
      await context.setOffline(false);
      await Promise.all([taskSaved, costSaved]);
      await expect(row.getByText("Saved on this device · will sync")).toHaveCount(0);
      await expect(costs.getByText("Saved on this device · will sync")).toHaveCount(0);
      const mine = await api<{ stages: { tasks: { id: string; status: string }[] }[] }>(page, "GET", "/api/pathway");
      expect(mine.json.stages.flatMap((s) => s.tasks).find((t) => t.id === id)?.status).toBe("done");
      const list = await api<{ items: { label: string; amount: string }[] }>(page, "GET", "/api/costs");
      expect(list.json.items.map((c) => [c.label, c.amount])).toEqual([["Offline fee", "42.00"]]);
      await quiet(page);
      expect(errors.filter((e) => !/ERR_INTERNET_DISCONNECTED|offline|Failed to load resource/i.test(e))).toEqual([]);
    } finally {
      await student.done();
    }
  });
});

test.describe("phone tab bar", () => {
  test("five tabs fit in the bar, the rest are in a More sheet, labels never overflow", async ({
    browser,
    baseURL,
  }, info) => {
    test.skip((info.project.use.viewport?.width ?? 1000) >= 640, "the tab bar is the phone layout");
    test.setTimeout(150_000);
    const student = await freshStudent(browser, baseURL as string, info, "tabs");
    try {
      const { page } = await student.open("/pathway");
      const { errors, guard } = watchConsole(page);
      const bar = page.getByRole("navigation", { name: "Tabs" });
      const items = bar.locator("li > a, li > button");
      const count = await items.count();
      expect(count).toBeLessThanOrEqual(5);
      // every label sits inside its own tab, and no two tabs overlap
      const boxes = await items.evaluateAll((els) =>
        els.map((el) => {
          const label = el.querySelector("span") as HTMLElement;
          const r = el.getBoundingClientRect();
          const l = label.getBoundingClientRect();
          return {
            left: r.left,
            right: r.right,
            labelLeft: l.left,
            labelRight: l.right,
            scroll: label.scrollWidth,
            client: label.clientWidth,
          };
        }),
      );
      for (const [i, b] of boxes.entries()) {
        expect(b.labelLeft, `tab ${i} label starts inside`).toBeGreaterThanOrEqual(b.left - 0.5);
        expect(b.labelRight, `tab ${i} label ends inside`).toBeLessThanOrEqual(b.right + 0.5);
        expect(b.scroll).toBeLessThanOrEqual(b.client + 1);
        if (i > 0) expect(b.left).toBeGreaterThanOrEqual((boxes[i - 1] as { right: number }).right - 0.5);
      }
      await snap(page, info, guard, "tabs");
      // More holds the rest: Pathway is there, and it opens
      await bar.getByRole("button", { name: "More" }).click();
      const sheet = page.getByRole("dialog", { name: "More" });
      await expect(sheet.getByRole("link", { name: "Pathway" })).toHaveAttribute("aria-current", "page");
      await expect(sheet.getByRole("link", { name: "Account" })).toBeVisible();
      await expectNoAxeViolations(page);
      await snap(page, info, guard, "tabs-more");
      await sheet.getByRole("link", { name: "Account" }).click();
      await page.waitForURL((u) => u.pathname === "/settings/account");
      expect(errors).toEqual([]);
    } finally {
      await student.done();
    }
  });
});

test.describe("today strip and production", () => {
  test("the Today pathway strip opens the pathway", async ({ browser, baseURL }, info) => {
    test.setTimeout(150_000);
    const student = await freshStudent(browser, baseURL as string, info, "strip");
    try {
      const { page } = await student.open("/pathway");
      await ensureOnboarded(page);
      await quiet(page);
      await page.goto("/");
      await settle(page);
      const strip = page.locator("section[aria-labelledby='today-pathway']");
      await expect(strip).toBeVisible();
      await strip.getByRole("link", { name: "Open the pathway" }).click();
      await page.waitForURL((u) => u.pathname === "/pathway");
      await expect(page.getByRole("heading", { level: 1 })).toContainText("MBBS");
    } finally {
      await student.done();
    }
  });

  test("the pathway loads on the production server (strict CSP) with no console errors", async ({
    browser,
    baseURL,
  }, info) => {
    const prod = process.env.E2E_PROD_URL;
    test.skip(!prod, "needs the local APP_ENV=prod API (not available against the live Space)");
    test.setTimeout(150_000);
    const origin = new URL(prod as string).origin;
    // Its own throwaway student: a shared session dies as soon as another test refreshes it (tokens rotate).
    const student = await freshStudent(browser, baseURL as string, info, "prod");
    const own = await student.open("/pathway");
    const saved = await own.context.storageState();
    await own.context.close();
    const context = await browser.newContext({
      baseURL: prod,
      ignoreHTTPSErrors: true,
      storageState: { cookies: [], origins: saved.origins.map((o) => ({ ...o, origin })) },
    });
    try {
      const page = await context.newPage();
      const { errors } = watchConsole(page);
      const response = await page.goto("/pathway");
      const csp = response?.headers()["content-security-policy"] ?? "";
      expect(csp).toContain("script-src 'self'");
      expect(csp).not.toMatch(/script-src[^;]*unsafe-inline/);
      await expect(page.getByRole("heading", { level: 1 })).toContainText("MBBS");
      await expect(page.locator("[data-stage]").first()).toBeVisible();
      await expect(page.locator("[data-panel='vault']").getByText("Vault files are never sent to AI.")).toBeVisible();
      await expect(page.locator("[data-panel='costs']")).toBeVisible();
      await settle(page);
      expect(errors).toEqual([]);
    } finally {
      await context.close();
      await student.done();
    }
  });
});
