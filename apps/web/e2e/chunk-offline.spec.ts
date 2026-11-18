/**
 * Segment 22 (lead decision 20261117T124555): a lazy page whose code cannot be fetched never crashes the app.
 * For every top route: the app starts online on Today, then the network is cut for every script
 * not loaded yet (as if the device went offline before that part was ever cached; the service worker is blocked
 * so nothing is served from its cache), and the route is opened in the app. The page shows the "needs a
 * connection" notice (words, icon, Try again), the shell and its navigation keep working, nothing is logged as an
 * error, and the page appears by itself once the device is back online.
 */
import type { Browser, Page, Route } from "@playwright/test";
import { expect, expectNoAxeViolations, test } from "./fixtures";
import { signedInState } from "./session";

test.use({
  serviceWorkers: "block",
  storageState: async (
    { browser, baseURL }: { browser: Browser; baseURL: string | undefined },
    use: (p: string) => Promise<void>,
  ) => use(await signedInState(browser, baseURL as string, "student")),
});

const ROUTES = [
  "/learn",
  "/learn/library",
  "/practice",
  "/cards",
  "/notes",
  "/notes/notebooks",
  "/search",
  "/plan",
  "/pathway",
  "/settings/account",
];

// Scripts only: a missing script already fails the page's import (a missing stylesheet is covered by the unit test).
const CHUNK = /\/assets\/[^/?]+\.js(?:\?.*)?$/;

/** Open a route the way the app does (history + popstate), without a document load. */
async function openInApp(page: Page, path: string): Promise<void> {
  await page.evaluate((p) => {
    window.history.pushState({}, "", p);
    window.dispatchEvent(new PopStateEvent("popstate", { state: window.history.state }));
  }, path);
  await expect.poll(() => new URL(page.url()).pathname).toBe(path);
}

for (const path of ROUTES) {
  test(`${path}: offline before its chunks load, the page says so and comes back online`, async ({
    page,
    consoleErrors,
  }) => {
    test.setTimeout(90_000);
    await page.goto("/");
    await expect(page.locator("[data-state=populated]")).toBeVisible();

    // From here on, every chunk not loaded yet fails like a dropped connection.
    consoleErrors.allowHttpError({ urlPart: "/assets/", status: 0 });
    const cut = (route: Route) => route.abort("internetdisconnected");
    await page.route(CHUNK, cut);
    await openInApp(page, path);

    const notice = page.locator("[data-chunk-unavailable]").first();
    await expect(notice).toBeVisible();
    await expect(notice).toContainText("needs a connection");
    await expect(notice.getByRole("button", { name: "Try again" })).toBeVisible();
    await expect(page.getByText("Something went wrong")).toHaveCount(0);
    await expectNoAxeViolations(page);

    // The rest of the app still works: Today is in the first load.
    await openInApp(page, "/");
    await expect(page.locator("[data-state=populated]")).toBeVisible();

    // Back online: the page loads by itself, no reload.
    await openInApp(page, path);
    await expect(page.locator("[data-chunk-unavailable]").first()).toBeVisible();
    await page.unroute(CHUNK, cut);
    await page.evaluate(() => window.dispatchEvent(new Event("online")));
    await expect(page.locator("[data-chunk-unavailable]")).toHaveCount(0, { timeout: 30_000 });
    await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
  });
}
