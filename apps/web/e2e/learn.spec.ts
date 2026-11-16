import type { Browser, BrowserContext, Page, Response, TestInfo } from "@playwright/test";
import { callbackPath, canMintUsers, runUsers, toLink } from "./auth-users";
import {
  type ConsoleGuard,
  expect,
  expectNoAxeViolations,
  expectNoHorizontalOverflow,
  snapSegment,
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

/** Let pending font loads finish, so a navigation does not abort them (Firefox logs aborted font loads). */
const settle = (page: Page) => page.evaluate(() => document.fonts.ready.then(() => undefined));

/** Load every declared font face (all unicode-range subsets), as a returning reader's cache would hold. */
const loadAllFonts = (page: Page) =>
  page.evaluate(() => Promise.all([...document.fonts].map((f) => f.load().catch(() => f))).then(() => undefined));

const asStudent = {
  storageState: async (
    { browser, baseURL }: { browser: Browser; baseURL: string | undefined },
    use: (p: string) => Promise<void>,
  ) => use(await signedInState(browser, baseURL as string, "student")),
};

function readerEmail(info: TestInfo): string {
  // letters only (the e2e user pattern); repeat and retry get their own user so they never share positions
  const run = String.fromCharCode(97 + (info.repeatEachIndex % 26), 97 + (info.retry % 26));
  const role = `reader${info.project.name.replace(/[^a-z]/g, "")}${run}`;
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
    if (m.type() === "error" && !(tool > 0 && m.text().startsWith("Refused to apply a stylesheet"))) {
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
async function freshReader(browser: Browser, baseURL: string, info: TestInfo) {
  const email = readerEmail(info);
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
    return page;
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

const isPhone = (page: Page) => (page.viewportSize()?.width ?? 1000) < 640;

async function limeCount(page: Page): Promise<number> {
  return page.evaluate(() => {
    const lime = "rgb(198, 255, 61)";
    return Array.from(document.querySelectorAll<HTMLElement>("main a, main button")).filter((el) => {
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
    await page.goto("/learn");
    await expect(page.getByRole("heading", { level: 1, name: /Everything/ })).toBeVisible();
    const list = page
      .getByRole("region", { name: "All chapters" })
      .or(page.locator("section[aria-labelledby=chapters-h]"));
    await expect(list.getByRole("listitem")).toHaveCount(7); // Step 1 chapters (mock exam listed separately)
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
    await page.goto("/learn");
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
    await page.goto("/learn");
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
    await page.goto("/learn?exam=step1&system=step1.sys.no_such_system");
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
    await page.goto("/learn/ch01/ch01-s02");
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    await page.goto("/learn/ch01/ch01-s01");
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await loadAllFonts(page);
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
    await page.goto("/learn/ch01/ch01-s01");
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await expect(page.getByText(/Chapter 1 of 7/)).toBeVisible();
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
    await page.goto("/learn/ch02/ch02-s01");
    await expect(page.getByRole("status").filter({ hasText: "Loading the chapter" })).toBeAttached();
    await snap(page, info, consoleErrors, "reader-loading");
    release();
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();

    await page.goto("/learn/no-such-chapter/x-s01");
    const alert = page.getByRole("alert");
    await expect(alert).toContainText("This chapter is not available");
    await expect(alert).toContainText(/Request ID [0-9a-f]{32}/);
    await expectNoAxeViolations(page);
    await snap(page, info, consoleErrors, "reader-not-found");
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
      await settle(page);
      // The first visit records the section as opened (offset 0) once the reader has laid it out: wait for
      // that, so the scroll below is not undone by the reader's own restore.
      const opened = page.waitForResponse(isPositionSave);
      await page.goto("/learn/ch01/ch01-s02");
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
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
      await settle(page);
      await page.reload();
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
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
      await settle(page);
      await page.goto("/learn/ch01");
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
