import { readFileSync } from "node:fs";
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
  await page.goto("/search?q=thyroid+nodule+heat+intolerance+TSH&scope=questions");
  const group = page.getByRole("region", { name: "Questions" });
  await expect(group.getByText(/palpable nodule in the right thyroid lobe/i).first()).toBeVisible();
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

test("nothing found: a clear empty state, and the web panel says what it found", async ({
  page,
  consoleErrors,
}, info) => {
  await page.goto("/search?q=qzxv+wplk+jjrt");
  await expect(page.getByText("Nothing found for “qzxv wplk jjrt”")).toBeVisible();
  // Nothing local, so the web is asked for automatically. Whatever the network allows in this environment,
  // the panel says what happened: labelled results, an empty note, or that the web is unavailable. It
  // never stays blank or spinning.
  const web = page.getByRole("region", { name: "Web" });
  await expect(web).toBeVisible();
  await expect(
    web
      .getByText(/^Web results from /)
      .or(web.getByText("The web search found nothing for this."))
      .or(web.getByText("Web search is not available right now."))
      .or(web.getByText(/allowance is used up/)),
  ).toBeVisible({ timeout: 45_000 });
  await expectNoAxeViolations(page);
  await expectNoHorizontalOverflow(page);
  await snap(page, info, consoleErrors, "search-empty-result");
});

test("the web is one click away when the library has answers, and its results are labelled", async ({
  page,
  consoleErrors,
}, info) => {
  await page.goto("/search?q=thyroid");
  await expect(page.getByRole("region", { name: "Chapters" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Web" })).toHaveCount(0); // not asked for, not shown
  await page.getByRole("button", { name: "Search the web too" }).click();
  await expect(page).toHaveURL(/web=1/);
  const web = page.getByRole("region", { name: "Web" });
  await expect(web).toBeVisible();
  const labelled = web.getByText(/^Web results from /);
  await expect(
    labelled
      .or(web.getByText("The web search found nothing for this."))
      .or(web.getByText("Web search is not available right now."))
      .or(web.getByText(/allowance is used up/)),
  ).toBeVisible({ timeout: 45_000 });
  if (await labelled.isVisible()) {
    // each web row names its source and opens in a new tab
    const row = web.getByRole("link").first();
    await expect(row).toHaveAttribute("target", "_blank");
    await expect(row).toHaveAttribute("rel", /noopener/);
  }
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

test("phone tab bar: icon above label, no label truncated or overflowing", async ({ page }) => {
  await page.goto("/search");
  const phone = (page.viewportSize()?.width ?? 1000) < 768;
  test.skip(!phone, "the tab bar is the phone and small-tablet layout");
  const tabs = page.getByRole("navigation", { name: "Tabs" });
  await expect(tabs.getByRole("link", { name: "Search" })).toBeVisible();
  const fits = await tabs.locator("a").evaluateAll((links) =>
    links.map((a) => {
      const label = a.querySelector("span");
      const box = a.getBoundingClientRect();
      const l = label?.getBoundingClientRect();
      return {
        text: label?.textContent ?? "",
        fits:
          !!label &&
          label.scrollWidth <= label.clientWidth &&
          !!l &&
          l.left >= box.left - 0.5 &&
          l.right <= box.right + 0.5,
        stacked:
          !!l && !!a.querySelector("svg") && (a.querySelector("svg")?.getBoundingClientRect().bottom ?? 0) <= l.top + 1,
      };
    }),
  );
  expect(fits.length).toBeGreaterThanOrEqual(3);
  for (const t of fits) {
    expect(t.fits, `label "${t.text}" fits its tab`).toBe(true);
    expect(t.stacked, `icon sits above "${t.text}"`).toBe(true);
  }
});

test("on the production-mode server (strict CSP) search runs with zero console errors", async ({ browser }, info) => {
  const prod = process.env.E2E_PROD_URL;
  test.skip(!prod, "needs the APP_ENV=prod server of the local/CI webServer set");
  // The student's session moved to the https prod origin (self-signed loopback certificate): signing in again
  // there would send the Supabase calls through upgrade-insecure-requests, which breaks the http local auth stack.
  const file = await signedInState(browser, info.project.use.baseURL as string, "student");
  const saved = JSON.parse(readFileSync(file, "utf8")) as {
    origins: { origin: string; localStorage: { name: string; value: string }[] }[];
  };
  const origin = new URL(prod as string).origin;
  const context = await browser.newContext({
    ...info.project.use,
    baseURL: prod,
    ignoreHTTPSErrors: true,
    storageState: { cookies: [], origins: saved.origins.map((o) => ({ ...o, origin })) },
  });
  const page = await context.newPage();
  const errors: string[] = [];
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(m.text());
  });
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  try {
    await page.goto("/search?q=thyroid");
    await expect(page.getByRole("region", { name: "Chapters" })).toBeVisible();
    await expect(page.getByRole("region", { name: "Questions" })).toBeVisible();
    await page.evaluate(() => document.fonts.ready.then(() => undefined));
    expect(errors, "console errors on the prod-mode server").toEqual([]);
  } finally {
    await context.close();
  }
});
