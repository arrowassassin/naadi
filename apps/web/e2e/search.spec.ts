import type { Browser, Page, TestInfo } from "@playwright/test";
import { canMintUsers } from "./auth-users";
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
 * Segment 07: the Search screen against the real API, database (seeded content, indexed with the local
 * embedding model by `python -m naadi_api.embeddings reindex`) and the real web layer. Read-only: every test
 * shares the run's "student" user.
 */
test.skip(!canMintUsers(), "signed-in tests need the service role (run pnpm e2e:live-auth against the Space)");

const snap = (page: Page, info: TestInfo, guard: ConsoleGuard, state: string, full = false) =>
  guard.duringTool(async () => {
    await snapSegment(page, info, guard, "07", state, full);
    await page.waitForTimeout(500);
  });

const settle = (page: Page) => page.evaluate(() => document.fonts.ready.then(() => undefined));
const loadAllFonts = (page: Page) =>
  page.evaluate(() => Promise.all([...document.fonts].map((f) => f.load().catch(() => f))).then(() => undefined));

test.use({
  storageState: async (
    { browser, baseURL }: { browser: Browser; baseURL: string | undefined },
    use: (p: string) => Promise<void>,
  ) => use(await signedInState(browser, baseURL as string, "student")),
});

const searchBox = (page: Page) => page.getByRole("searchbox", { name: "Search everything" });

test("Ctrl+K opens Search from anywhere; results are grouped; a chapter hit opens at the right section", async ({
  page,
  consoleErrors,
}, info) => {
  await page.goto("/learn");
  await expect(page.getByRole("heading", { level: 1, name: /Everything/ })).toBeVisible();
  await page.keyboard.press("Control+K");
  await expect(page).toHaveURL(/\/search$/);
  await expect(searchBox(page)).toBeFocused();
  await expect(page.getByText("Search everything you study")).toBeVisible();
  await expectNoAxeViolations(page);
  await snap(page, info, consoleErrors, "search-empty");

  await searchBox(page).fill("thyroid");
  const chapters = page.getByRole("region", { name: "Chapters" });
  const hit = chapters.getByRole("link", { name: /12\. Thyroid/ }).first();
  await expect(hit).toBeVisible();
  await expect(page).toHaveURL(/q=thyroid/);
  await expect(page.getByRole("region", { name: "Questions" })).toBeVisible();
  // the snippet marks the matched words, in text (bold + underline), not by colour alone
  await expect(chapters.locator("mark").first()).toBeVisible();
  await expectNoAxeViolations(page);
  await expectNoHorizontalOverflow(page);
  await snap(page, info, consoleErrors, "search-results");

  await hit.click();
  await expect(page).toHaveURL(/\/learn\/ch09\/ch09-s13$/);
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Thyroid");
});

test("the navigation's search button opens Search, and the shortcut refocuses the field", async ({ page }) => {
  await page.goto("/");
  const phone = (page.viewportSize()?.width ?? 1000) < 768;
  const button = phone
    ? page.getByRole("navigation", { name: "Tabs" }).getByRole("link", { name: "Search" })
    : page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Search everything" });
  await button.click();
  await expect(page).toHaveURL(/\/search$/);
  await expect(searchBox(page)).toBeFocused();
  await searchBox(page).fill("thyroid");
  await searchBox(page).evaluate((el) => el.blur()); // focus elsewhere
  await expect(searchBox(page)).not.toBeFocused();
  await page.keyboard.press("Control+K");
  await expect(searchBox(page)).toBeFocused();
});

test("filters narrow the results; the URL carries the query for back and share", async ({ page }) => {
  await page.goto("/search?q=thyroid");
  await expect(page.getByRole("region", { name: "Chapters" })).toBeVisible();
  await page.getByRole("button", { name: "Questions", exact: true }).click();
  await expect(page).toHaveURL(/scope=questions/);
  await expect(page.getByRole("button", { name: "Questions", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("region", { name: "Questions" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Chapters" })).toHaveCount(0);
  await page.getByRole("button", { name: "Everything" }).click();
  await expect(page.getByRole("region", { name: "Chapters" })).toBeVisible();
});

test("question results show the stem and never an answer or explanation", async ({ page }) => {
  const responses: string[] = [];
  page.on("response", async (r) => {
    if (r.url().includes("/api/search?")) responses.push(await r.text());
  });
  await page.goto("/search?q=kussmaul+respirations+type+1+diabetes+abdominal+pain&scope=questions");
  const group = page.getByRole("region", { name: "Questions" });
  await expect(group.getByText(/type 1 diabetes has abdominal pain/i).first()).toBeVisible();
  expect(responses.length).toBeGreaterThan(0);
  for (const body of responses) {
    expect(body).not.toMatch(/explanation|why_not|educational_objective|"answer"/i);
  }
});

test("keyboard: arrows move through results and Enter opens one", async ({ page }) => {
  await page.goto("/search?q=thyroid");
  const first = page.getByRole("region", { name: "Chapters" }).getByRole("link").first();
  await expect(first).toBeVisible();
  await searchBox(page).focus();
  await page.keyboard.press("ArrowDown");
  await expect(first).toBeFocused();
  await page.keyboard.press("ArrowUp");
  await expect(searchBox(page)).toBeFocused();
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/learn\/ch\d+\//);
});

test("nothing found: a clear empty state, and the web is one click away and labelled", async ({
  page,
  consoleErrors,
}, info) => {
  await page.goto("/search?q=qzxv+wplk+jjrt");
  await expect(page.getByText("Nothing found for “qzxv wplk jjrt”")).toBeVisible();
  await expectNoAxeViolations(page);
  await snap(page, info, consoleErrors, "search-empty-result");
  await page.getByRole("button", { name: "Search the web too" }).click();
  await expect(page).toHaveURL(/web=1/);
  const web = page.getByRole("region", { name: "Web" });
  await expect(web).toBeVisible();
  // Whatever the network allows in this environment, the panel says what happened: labelled results, an
  // empty note, or that the web is unavailable. It never stays blank or spinning.
  await expect(
    web
      .getByText(/^Web results from /)
      .or(web.getByText("The web search found nothing for this."))
      .or(web.getByText("Web search is not available right now."))
      .or(web.getByText(/allowance is used up/)),
  ).toBeVisible({ timeout: 45_000 });
  await expectNoAxeViolations(page);
  await snap(page, info, consoleErrors, "search-web");
});

test("offline: a clear state instead of results", async ({ page, context, consoleErrors }, info) => {
  await page.goto("/search?q=renal");
  await expect(page.getByRole("region", { name: "Chapters" })).toBeVisible();
  await settle(page);
  await loadAllFonts(page);
  await context.setOffline(true);
  try {
    await expect(page.getByText("You are offline", { exact: true })).toBeVisible();
    await expectNoAxeViolations(page);
    await snap(page, info, consoleErrors, "search-offline");
  } finally {
    await context.setOffline(false);
  }
});

test("only one lime thing per view: the search icon", async ({ page }) => {
  await page.goto("/search?q=thyroid");
  await expect(page.getByRole("region", { name: "Chapters" })).toBeVisible();
  const lime = await page.evaluate(() => {
    const target = "rgb(198, 255, 61)";
    return Array.from(document.querySelectorAll<HTMLElement | SVGElement>("main *")).filter((el) => {
      const cs = getComputedStyle(el);
      const r = el.getBoundingClientRect();
      return (
        r.width > 0 && r.height > 0 && (cs.backgroundColor === target || (el.tagName === "svg" && cs.color === target))
      );
    }).length;
  });
  expect(lime).toBe(1);
});
