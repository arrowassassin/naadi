import type { Browser, BrowserContext } from "@playwright/test";
import { canMintUsers, type E2ERole } from "./auth-users";
import { expect, test } from "./fixtures";
import { signedInState } from "./session";

/**
 * The ink lab on a production server (APP_ENV=prod, the second API from playwright.config.ts): it does not
 * exist for the public or for students, and opens for admins. The flag comes from the server (/api/me when
 * signed in, /api/public-config when signed out), so this is the whole check that nothing in the bundle
 * decides it.
 */
const PROD = process.env.E2E_PROD_URL;

test.skip(!PROD, "needs the local APP_ENV=prod API (not available against the live Space)");

/** A signed-in context on the prod origin (its own session: auth states are per origin). */
async function prodContext(browser: Browser, role: E2ERole): Promise<BrowserContext> {
  const storageState = await signedInState(browser, PROD as string, role);
  return browser.newContext({ baseURL: PROD, storageState });
}

test.describe("ink lab on a production server", () => {
  // Production's CSP has upgrade-insecure-requests. Chromium and Firefox exempt loopback, WebKit upgrades
  // http://127.0.0.1 asset requests to https, so a local http prod server cannot load its own assets there.
  // The gate is the same code in every browser; the deployed (https) Space covers WebKit.
  test.skip(({ browserName }) => browserName === "webkit", "prod CSP upgrades loopback http in WebKit");

  test("signed out: Page not found", async ({ page }) => {
    await page.goto(`${PROD}/system/ink-lab`);
    await expect(page.getByRole("heading", { level: 1, name: "Page not found" })).toBeVisible();
    await expect(page.locator("[data-ink-layer]")).toHaveCount(0);
  });

  test.describe("signed in", () => {
    test.skip(!canMintUsers(), "signed-in tests need the service role (CI auth container)");

    test("a student gets Page not found", async ({ browser }) => {
      const ctx = await prodContext(browser, "student");
      try {
        const page = await ctx.newPage();
        const me = page.waitForResponse((r) => r.url().endsWith("/api/me"));
        await page.goto("/system/ink-lab");
        expect((await (await me).json()).features.ink_lab).toBe(false);
        await expect(page.getByRole("heading", { level: 1, name: "Page not found" })).toBeVisible();
      } finally {
        await ctx.close();
      }
    });

    test("an admin gets the lab", async ({ browser }) => {
      const ctx = await prodContext(browser, "admin");
      try {
        const page = await ctx.newPage();
        await page.goto("/system/ink-lab");
        await expect(page.getByRole("heading", { level: 1, name: "Ink lab" })).toBeVisible();
        await expect(page.locator("[data-ink-layer]").first()).toBeAttached();
      } finally {
        await ctx.close();
      }
    });
  });
});
