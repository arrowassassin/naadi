import { execFileSync } from "node:child_process";
import path from "node:path";
import type { Browser, BrowserContext, Page, Response, TestInfo } from "@playwright/test";
import { callbackPath, canMintUsers, runUsers, toLink } from "./auth-users";
import {
  type ConsoleGuard,
  expect,
  expectNoAxeViolations,
  expectNoHorizontalOverflow,
  settle,
  snapSegment,
  TOOL_CSP_NOISE,
  test,
} from "./fixtures";
import { signedInState } from "./session";

/**
 * Segment 06: course map and reader against the real API, database (seeded by segment 05's importer) and
 * Storage (figures load from SUPABASE_URL/storage/v1, the same path as the hosted project).
 *
 * Read-only flows share the run's "student" user. Flows that write reading positions or reports use their
 * own throwaway user per browser project (deleted afterwards), so parallel projects never race on a position.
 */
test.skip(!canMintUsers(), "signed-in tests need the service role (run pnpm e2e:live-auth against the Space)");

// WebKit reports the screenshot tool's refused <style> asynchronously; keep the tool window open a little
// longer than the fixture's own 300 ms so a late report is still attributed to the tool.
const snap = (page: Page, info: TestInfo, guard: ConsoleGuard, state: string, full = false) =>
  guard.duringTool(async () => {
    await snapSegment(page, info, guard, "06", state, full);
    await page.waitForTimeout(500);
  });

/**
 * Nothing in flight on the current page: every font face loaded (shared `settle`) and, on a /learn page, no
 * reading-position save or offline cache write pending (`data-learn-idle`). Called before every navigation
 * and before going offline, so neither a font subset nor a Cache Storage put is cut off by the page leaving.
 */
async function quiet(page: Page): Promise<void> {
  if (page.url() === "about:blank") return;
  await settle(page);
  await page.waitForFunction(() => document.querySelector("[data-learn-page]:not([data-learn-idle])") === null);
  // On a reader section, its ink layer has finished loading too (WebKit logs a request cut off by the next
  // navigation as an error).
  if (await page.locator("[data-section-ink]").count()) await expect(page.locator("[data-ink-idle]")).toBeAttached();
}

/**
 * Navigate after the current page is quiet, then wait until the new page and its fonts are settled.
 * `heading: false` for a page held in its loading state by a test route.
 */
async function go(page: Page, url: string, opts: { heading?: boolean } = {}): Promise<void> {
  await quiet(page);
  await page.goto(url);
  if (opts.heading !== false) await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
  await settle(page);
}

/** Reload the same way: quiet first, settled after. */
async function reload(page: Page): Promise<void> {
  await quiet(page);
  await page.reload();
  await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
  await settle(page);
}

const asStudent = {
  storageState: async (
    { browser, baseURL }: { browser: Browser; baseURL: string | undefined },
    use: (p: string) => Promise<void>,
  ) => use(await signedInState(browser, baseURL as string, "student")),
};

function readerEmail(info: TestInfo, kind = "reader"): string {
  // letters only (the e2e user pattern); repeat and retry get their own user so they never share positions
  const run = String.fromCharCode(97 + (info.repeatEachIndex % 26), 97 + (info.retry % 26));
  const role = `${kind}${info.project.name.replace(/[^a-z]/g, "")}${run}`;
  return `naadi-e2e-${role}-${process.env.E2E_RUN_ID}@example.com`;
}

/**
 * Console errors of a page opened outside the fixture's own page (fresh contexts), with the same rule as the
 * fixture: WebKit's screenshot <style> refused by our CSP is the tool's noise, ignored only while snapping.
 */
function watchConsole(page: Page) {
  const errors: string[] = [];
  let tool = 0;
  page.on("console", (m) => {
    if (m.type() === "error" && !(tool > 0 && m.text() === TOOL_CSP_NOISE)) {
      errors.push(m.text());
    }
  });
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message} ${e.stack ?? ""}`.trim()));
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

/** A fresh, signed-in context for a throwaway user of this project (deleted in `finally`). */
async function freshReader(browser: Browser, baseURL: string, info: TestInfo, kind = "reader") {
  const email = readerEmail(info, kind);
  // A run that was killed may have left this user (and its positions) behind: always start from nothing.
  runUsers(["delete", "--email", email]);
  const contexts: BrowserContext[] = [];
  const open = async (): Promise<Page> => {
    const context = await browser.newContext({ ...info.project.use, baseURL });
    contexts.push(context);
    const page = await context.newPage();
    const link = toLink(runUsers(["link", "--email", email]));
    await page.goto(callbackPath(link, "/learn"));
    await page.waitForURL((u) => u.pathname === "/learn", { timeout: 30_000 });
    await settle(page);
    return page;
  };
  const done = async () => {
    try {
      for (const c of contexts) await c.close().catch(() => undefined);
    } finally {
      runUsers(["delete", "--email", email]);
    }
  };
  return { open, done, email };
}

const API_DIR = path.resolve(import.meta.dirname, "../../api");

/** A private chapter with every rich node for this user (apps/api/tests/courses/e2e_rich.py; local DB only). */
function richChapter(action: "create" | "delete", email: string): { chapter_id: string; section_id?: string } {
  const db = process.env.E2E_DATABASE_URL ?? process.env.DATABASE_URL;
  if (!db) throw new Error("rich content needs the local database (E2E_DATABASE_URL or DATABASE_URL)");
  const args = ["--directory", API_DIR, "run", "--frozen", "python", "-m", "tests.courses.e2e_rich", action];
  const out = execFileSync("uv", [...args, "--email", email, "--db", db], { encoding: "utf8", timeout: 60_000 });
  return JSON.parse(out.trim().split("\n").pop() ?? "{}") as { chapter_id: string; section_id?: string };
}

const isPhone = (page: Page) => (page.viewportSize()?.width ?? 1000) < 640;

async function limeCount(page: Page): Promise<number> {
  return page.evaluate(() => {
    const lime = "rgb(198, 255, 61)";
    // the whole page: on phone the reader's page bar lives in the shell's bottom bar, outside <main>
    return Array.from(document.querySelectorAll<HTMLElement>("a, button")).filter((el) => {
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0 && getComputedStyle(el).backgroundColor === lime;
    }).length;
  });
}

function isPositionSave(r: Response): boolean {
  return r.url().endsWith("/api/reading-position") && r.request().method() === "PUT" && r.status() === 200;
}

test.describe("course map", () => {
  test.use(asStudent);

  test("is populated from the database, filters by exam and system, opens a chapter", async ({
    page,
    consoleErrors,
  }, info) => {
    await go(page, "/learn");
    await expect(page.getByRole("heading", { level: 1, name: /Everything/ })).toBeVisible();
    const list = page
      .getByRole("region", { name: "All chapters" })
      .or(page.locator("section[aria-labelledby=chapters-h]"));
    await expect(list.getByRole("listitem")).toHaveCount(7); // Step 1 chapters only
    // Mock exams are Practice content: never listed in /learn (their sections are not reading material).
    await expect(page.getByText(/Mock Exam/)).toHaveCount(0);
    await expect(page.getByRole("link", { name: /^(Start|Continue|Next): / })).toHaveCount(1);
    expect(await limeCount(page)).toBe(1);
    // coverage and progress are words, not colour alone
    await expect(list.getByText("Not started").first()).toBeVisible();
    await expect(list.getByText(/\d+ sections · \d+ figures · \d+ questions/).first()).toBeVisible();
    await expectNoAxeViolations(page);
    await expectNoHorizontalOverflow(page);
    await snap(page, info, consoleErrors, "courses-populated");

    await page.getByRole("link", { name: "USMLE Step 2 CK" }).click();
    await expect(page).toHaveURL(/exam=step2ck/);
    await expect(page.getByRole("link", { name: "USMLE Step 2 CK" })).toHaveAttribute("aria-current", "page");
    await expect(list.getByRole("listitem")).toHaveCount(5);
    await expect(list.getByRole("listitem").first()).toContainText("Step 2 CK");

    const tile = page
      .getByRole("list")
      .filter({ has: page.getByRole("link", { name: /Cardiovascular/ }) })
      .getByRole("link", { name: /Cardiovascular/ });
    await tile.click();
    await expect(page).toHaveURL(/system=step2ck\.sys\.cardiovascular/);
    await expect(page.getByRole("heading", { level: 2, name: /Cardiovascular/ })).toBeVisible();
    const rows = list.getByRole("listitem");
    expect(await rows.count()).toBeGreaterThan(0);
    expect(await rows.count()).toBeLessThan(5);
    await expectNoAxeViolations(page);
    await snap(page, info, consoleErrors, "courses-filtered");

    await rows.first().getByRole("link").click();
    await expect(page).toHaveURL(/\/learn\/ch\d+\/ch\d+-s\d+$/);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  });

  test("loading shows skeletons", async ({ page, consoleErrors }, info) => {
    let release: () => void = () => {};
    const gate = new Promise<void>((r) => {
      release = r;
    });
    await page.route("**/api/courses**", async (route) => {
      await gate;
      await route.continue();
    });
    await go(page, "/learn", { heading: false });
    await expect(page.getByRole("status").filter({ hasText: "Loading your courses" })).toBeAttached();
    await expect(page.locator("[data-skeleton]").first()).toBeVisible();
    await snap(page, info, consoleErrors, "courses-loading");
    release();
    await expect(page.getByRole("heading", { level: 1, name: /Everything/ })).toBeVisible();
  });

  test("an API failure shows the error with its request id and retries", async ({ page, consoleErrors }, info) => {
    consoleErrors.allowHttpError({ urlPart: "/api/courses", status: 503 });
    let fail = true;
    await page.route("**/api/courses**", async (route) => {
      if (!fail) return route.continue();
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        headers: { "x-request-id": "e2e-courses-503" },
        body: JSON.stringify({
          error: {
            code: "service_unavailable",
            message: "The library cannot be reached right now. Try again in a moment.",
            request_id: "e2e-courses-503",
          },
        }),
      });
    });
    await go(page, "/learn");
    const alert = page.getByRole("alert");
    await expect(alert).toContainText("Your courses could not be loaded");
    await expect(alert).toContainText("Request ID e2e-courses-503");
    await expectNoAxeViolations(page);
    await snap(page, info, consoleErrors, "courses-error");
    fail = false;
    await alert.getByRole("button", { name: "Try again" }).click();
    await expect(page.getByRole("heading", { level: 2, name: "All chapters" })).toBeVisible();
  });

  test("a filter with no chapters shows the empty state and a way back", async ({ page, consoleErrors }, info) => {
    await go(page, "/learn?exam=step1&system=step1.sys.no_such_system");
    await expect(page.getByText("No chapters cover this system yet.")).toBeVisible();
    await expectNoAxeViolations(page);
    await snap(page, info, consoleErrors, "courses-empty");
    await page.getByRole("button", { name: "Show all systems" }).click();
    await expect(page.getByRole("heading", { level: 2, name: "All chapters" })).toBeVisible();
  });

  test("offline: the loaded chapter stays readable and the shell says so", async ({
    page,
    context,
    consoleErrors,
  }, info) => {
    // Visit both sections online first, so the font subsets they need are in the HTTP cache, as they would
    // be for a returning reader.
    await go(page, "/learn/ch01/ch01-s02");
    await go(page, "/learn/ch01/ch01-s01");
    // Nothing in flight when the connection drops: every font loaded, the opened-section save landed and its
    // offline cache write finished.
    await expect(page.locator("[data-learn-idle]")).toBeAttached();
    await quiet(page);
    await context.setOffline(true);
    try {
      await expect(page.getByRole("status").filter({ hasText: "Offline" })).toBeVisible();
      await page.getByRole("navigation", { name: "Sections" }).getByRole("link").last().click();
      await expect(page).toHaveURL(/ch01-s02$/);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await expectNoAxeViolations(page);
      await snap(page, info, consoleErrors, "reader-offline");
    } finally {
      await context.setOffline(false);
    }
  });
});

test.describe("reader", () => {
  test.use(asStudent);

  test("renders a chapter with figures from Storage, sections, contents and keyboard", async ({
    page,
    consoleErrors,
  }, info) => {
    test.setTimeout(120_000); // walks up to six sections on the slowest projects
    await go(page, "/learn/ch01/ch01-s01");
    await expect(page.getByText(isPhone(page) ? /Ch 1 · page 1 \/ 16/ : /Chapter 1 of 7/)).toBeVisible();
    // The lime Next pill is named for where it goes and never wraps onto a second line.
    const nextPill = page.getByRole("link", { name: /^Next section: / });
    await expect(nextPill).toBeVisible();
    expect((await nextPill.boundingBox())?.height ?? 0).toBeLessThanOrEqual(56);
    expect(await limeCount(page)).toBe(1);
    await expectNoAxeViolations(page);
    await expectNoHorizontalOverflow(page);
    await snap(page, info, consoleErrors, "reader-populated");

    // Find the first section with a figure by walking forward with the keyboard / next button.
    const next = page.getByRole("navigation", { name: "Sections" }).getByRole("link").last();
    let figure = page.locator("[data-node=figure] img").first();
    for (let i = 0; i < 16 && (await figure.count()) === 0; i++) {
      if (isPhone(page)) await next.click();
      else await page.keyboard.press("ArrowRight");
      await expect(page).toHaveURL(new RegExp(`ch01-s${String(i + 2).padStart(2, "0")}$`));
      figure = page.locator("[data-node=figure] img").first();
    }
    await figure.scrollIntoViewIfNeeded();
    await expect(figure).toHaveAttribute(
      "src",
      /\/storage\/v1\/object\/public\/content-public\/figures\/ch01-fig\d+\.svg$/,
    );
    await expect
      .poll(() => figure.evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0))
      .toBe(true);
    await expect(figure).toHaveAttribute("alt", /^Figure \d+\./);
    await expect(page.locator("[data-node=figure] figcaption").first()).toContainText("Source:");
    await snap(page, info, consoleErrors, "reader-figure");

    await page.locator("[data-node=figure] button").first().click();
    const zoom = page.getByRole("dialog");
    await expect(zoom).toBeVisible();
    await expectNoAxeViolations(page);
    await snap(page, info, consoleErrors, "reader-figure-zoom");
    await zoom.getByRole("button", { name: "Close figure" }).click();
    await expect(zoom).toBeHidden();

    if (!isPhone(page)) {
      await page.keyboard.press("ArrowLeft");
      await expect(page).toHaveURL(/ch01-s\d+$/);
    }
    await page.getByRole("button", { name: "Contents" }).click();
    const sheet = page.getByRole("dialog", { name: "Contents" });
    await expect(sheet.getByRole("listitem")).toHaveCount(16);
    // contrast is measured on the settled sheet, not mid fade-in
    await sheet.evaluate((el) => Promise.all(el.getAnimations().map((a) => a.finished)).then(() => undefined));
    await expectNoAxeViolations(page);
    await snap(page, info, consoleErrors, "reader-contents");
    await sheet.getByRole("button", { name: /^03/ }).click();
    await expect(page).toHaveURL(/ch01-s03$/);
    await expect(sheet).toBeHidden();
  });

  test("loading and error states", async ({ page, consoleErrors }, info) => {
    consoleErrors.allowHttpError({ urlPart: "/api/chapters/", status: 404 });
    let release: () => void = () => {};
    const gate = new Promise<void>((r) => {
      release = r;
    });
    await page.route("**/api/chapters/ch02", async (route) => {
      await gate;
      await route.continue();
    });
    await go(page, "/learn/ch02/ch02-s01", { heading: false });
    await expect(page.getByRole("status").filter({ hasText: "Loading the chapter" })).toBeAttached();
    await snap(page, info, consoleErrors, "reader-loading");
    release();
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();

    await go(page, "/learn/no-such-chapter/x-s01");
    const alert = page.getByRole("alert");
    await expect(alert).toContainText("This chapter is not available");
    await expect(alert).toContainText(/Request ID [0-9a-f]{32}/);
    await expectNoAxeViolations(page);
    await snap(page, info, consoleErrors, "reader-not-found");

    // A mock exam is not readable here either (the API answers 404 for it).
    await go(page, "/learn/mock1/mock1-s01");
    await expect(page.getByRole("alert")).toContainText("This chapter is not available");
  });
});

test.describe("reading position and reports", () => {
  test("syncs across devices and restores on reload; an issue can be reported", async ({ browser, baseURL }, info) => {
    test.setTimeout(240_000);
    const reader = await freshReader(browser, baseURL as string, info);
    try {
      const page = await reader.open();
      const watch = watchConsole(page);
      await page.getByRole("link", { name: /^Start: / }).click();
      await expect(page).toHaveURL(/\/learn\/ch01\/ch01-s01$/);
      // ch01-s02 is long (a big table): scroll part-way and let the position save.
      // The first visit records the section as opened (offset 0) once the reader has laid it out: wait for
      // that, so the scroll below is not undone by the reader's own restore.
      const opened = page.waitForResponse(isPositionSave);
      await go(page, "/learn/ch01/ch01-s02");
      await opened;
      await expect(page.locator("[data-content-root] > [data-block-index]").nth(2)).toBeAttached();
      const target = await page.evaluate(() => {
        const blocks = document.querySelectorAll<HTMLElement>("[data-content-root] > [data-block-index]");
        const el = blocks[Math.min(2, blocks.length - 1)] as HTMLElement;
        return window.scrollY + el.getBoundingClientRect().top + el.getBoundingClientRect().height * 0.4 - 200;
      });
      const saved = page.waitForResponse(
        (r) => isPositionSave(r) && (JSON.parse(r.request().postData() ?? "{}") as { offset: number }).offset > 1,
      );
      await page.evaluate((y) => window.scrollTo(0, y), target);
      await page.evaluate(() => window.dispatchEvent(new Event("scroll")));
      const body = (await (await saved).request().postDataJSON()) as { offset: number; section_id: string };
      expect(body.section_id).toBe("ch01-s02");
      expect(body.offset).toBeGreaterThan(1);
      const anchor = async (p: Page) =>
        p.evaluate(() => {
          const top =
            Number.parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--shell-top")) || 0;
          const y = top + 24;
          const blocks = Array.from(document.querySelectorAll<HTMLElement>("[data-content-root] > [data-block-index]"));
          return blocks.findIndex((b) => b.getBoundingClientRect().bottom > y);
        });
      const before = await anchor(page);
      await snap(page, info, watch.guard, "reader-scrolled");

      // Reload: the same block is at the top again.
      await reload(page);
      await expect.poll(() => anchor(page)).toBe(before);

      // A second device (new browser context, same user) opening the chapter lands on the same spot.
      // A second device (new browser context, same user) opens the app: the course map offers to continue
      // there, and following it lands on the same spot. (In-app navigation, as a student would.)
      const other = await reader.open();
      const otherWatch = watchConsole(other);
      const resume = other.getByRole("link", { name: /^Continue: Step 1 · Biochemistry/ });
      await expect(resume).toBeVisible();
      await resume.click();
      await expect(other).toHaveURL(/\/learn\/ch01\/ch01-s02$/);
      await expect.poll(() => anchor(other)).toBe(before);
      await snap(other, info, otherWatch.guard, "reader-second-device");

      // The chapter link (/learn/ch01) also resumes at the saved section.
      await go(page, "/learn/ch01");
      await expect(page).toHaveURL(/\/learn\/ch01\/ch01-s02$/);
      await expect.poll(() => anchor(page)).toBe(before);

      // Report an issue on this section.
      await page.getByRole("button", { name: "Report an issue" }).click();
      const dialog = page.getByRole("dialog", { name: "Report an issue" });
      await dialog.getByRole("textbox").fill("The second table's header is cut off on my phone.");
      await expectNoAxeViolations(page);
      await snap(page, info, watch.guard, "reader-report");
      const posted = page.waitForResponse(
        (r) => r.url().endsWith("/api/report-issue") && r.request().method() === "POST",
      );
      await dialog.getByRole("button", { name: "Send report" }).click();
      expect((await posted).status()).toBe(201);
      await expect(dialog.getByRole("status")).toContainText("Your report about");
      await snap(page, info, watch.guard, "reader-report-sent");
      expect(watch.errors, "console errors (first device)").toEqual([]);
      expect(otherWatch.errors, "console errors (second device)").toEqual([]);
    } finally {
      await reader.done();
    }
  });
});

test.describe("reader layout and keys", () => {
  test.use(asStudent);

  test("arrow keys scroll a focused table instead of turning the page", async ({ page }) => {
    await go(page, "/learn/ch01/ch01-s02");
    const table = page.locator("[data-node=table] section[tabindex='0']").first();
    await table.focus();
    await page.keyboard.press("ArrowRight");
    await page.keyboard.press("ArrowLeft");
    await expect(page).toHaveURL(/ch01-s02$/);
    await expect(table).toBeFocused();
    // Back on the article itself, the same key turns the page.
    await page.locator("article[aria-labelledby=section-h]").focus();
    await page.keyboard.press("ArrowRight");
    await expect(page).toHaveURL(/ch01-s03$/);
  });

  test("scrolled-to content clears the nav pill and the bottom bars", async ({ page, consoleErrors }, info) => {
    await go(page, "/learn/ch01/ch01-s02");
    const chrome = () =>
      page.evaluate(() => {
        const pill = document.querySelector("nav[aria-label=Main]")?.getBoundingClientRect();
        const slot = document.querySelector("[data-shell-bottom-slot]")?.parentElement?.getBoundingClientRect();
        const bar = document.querySelector("nav[aria-label=Sections]")?.getBoundingClientRect();
        const visible = (r: DOMRect | undefined) => (r && r.height > 0 ? r : undefined);
        const tops = [visible(slot)?.top, visible(bar)?.top].filter((v): v is number => typeof v === "number");
        return { top: visible(pill)?.bottom ?? 0, bottom: Math.min(window.innerHeight, ...tops) };
      });
    // Re-measured until layout has settled (late font or bar measurement), never with a looser bound.
    const clears = async (locator: ReturnType<Page["locator"]>, block: "start" | "end") => {
      await locator.evaluate((el, b) => el.scrollIntoView({ block: b, behavior: "instant" }), block);
      const r = await locator.boundingBox();
      const c = await chrome();
      if (!r) return false;
      return block === "end" ? r.y + r.height <= c.bottom + 0.5 : r.y >= c.top - 0.5;
    };
    const report = page.getByRole("button", { name: "Report an issue" });
    await expect.poll(() => clears(report, "end"), { message: "the button clears the bottom bars" }).toBe(true);
    const heading = page.locator("[data-content-root] h3, [data-content-root] h2").first();
    if ((await heading.count()) > 0) {
      await expect.poll(() => clears(heading, "start"), { message: "the heading clears the nav pill" }).toBe(true);
    }
    await snap(page, info, consoleErrors, "reader-scroll-padding");
  });
});

test.describe("rich content under the production CSP", () => {
  test("math, Mermaid, video, PDF, question and an unknown node render with zero console errors", async ({
    browser,
    baseURL,
  }, info) => {
    test.setTimeout(180_000);
    const reader = await freshReader(browser, baseURL as string, info, "rich");
    try {
      const page = await reader.open();
      const watch = watchConsole(page);
      const rich = richChapter("create", reader.email);
      await go(page, `/learn/${rich.chapter_id}/${rich.section_id}`);
      const diagram = page.locator("[data-node=mermaid] [role=img] svg");
      await expect(diagram).toBeVisible({ timeout: 30_000 });
      // the label keeps its ">" (escaped for Mermaid, not stripped)
      await expect(diagram).toContainText("Gap > 12?");
      // no <style> element entered the page: the diagram's rules live in the shared constructed stylesheet
      expect(await diagram.evaluate((el) => el.querySelectorAll("style").length)).toBe(0);
      await expect(page.locator("[data-node=math] math")).toBeAttached();
      await expect(page.getByRole("button", { name: /^Play video: Anion gap/ })).toBeVisible();
      await expect(page.getByRole("link", { name: /Lab values/ })).toHaveAttribute("href", /#page=2$/);
      await expect(page.getByRole("region", { name: "Practice question" })).toBeVisible();
      await expect(page.getByText("Text inside a node type from the future.")).toBeVisible();
      await expect(page.getByText(/Unknown content/)).toHaveCount(0);
      await expect(page.getByRole("complementary", { name: "Trap" })).toBeVisible();
      await expectNoAxeViolations(page);
      await snap(page, info, watch.guard, "reader-rich", true);
      // one hidden renderer frame, sandboxed, whatever the number of diagrams
      const frames = page.locator("iframe.naadi-mermaid-sandbox");
      await expect(frames).toHaveCount(1);
      await expect(frames).toHaveAttribute("sandbox", "allow-scripts");
      expect(watch.errors, "console errors").toEqual([]);
    } finally {
      try {
        richChapter("delete", reader.email);
      } finally {
        await reader.done();
      }
    }
  });
});
