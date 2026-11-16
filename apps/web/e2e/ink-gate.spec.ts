import { readFileSync } from "node:fs";
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

/**
 * A context on the https prod origin (self-signed loopback certificate, see playwright.config.ts). Signed in,
 * it carries the role's session from the main origin moved to this origin: signing in again here would send
 * the Supabase calls through production's upgrade-insecure-requests, which breaks the http local auth stack.
 */
async function prodContext(browser: Browser, baseURL: string, role?: E2ERole): Promise<BrowserContext> {
  const origin = new URL(PROD as string).origin;
  let storageState:
    | { cookies: never[]; origins: { origin: string; localStorage: { name: string; value: string }[] }[] }
    | undefined;
  if (role) {
    const file = await signedInState(browser, baseURL, role);
    const state = JSON.parse(readFileSync(file, "utf8")) as {
      origins: { origin: string; localStorage: { name: string; value: string }[] }[];
    };
    storageState = { cookies: [], origins: state.origins.map((o) => ({ ...o, origin })) };
  }
  return browser.newContext({ baseURL: PROD, ignoreHTTPSErrors: true, storageState });
}

test.describe("ink lab on a production server", () => {
  test("signed out: Page not found", async ({ browser, baseURL }) => {
    const ctx = await prodContext(browser, baseURL as string);
    try {
      const page = await ctx.newPage();
      await page.goto("/system/ink-lab");
      await expect(page.getByRole("heading", { level: 1, name: "Page not found" })).toBeVisible();
      await expect(page.locator("[data-ink-layer]")).toHaveCount(0);
    } finally {
      await ctx.close();
    }
  });

  test.describe("signed in", () => {
    test.skip(!canMintUsers(), "signed-in tests need the service role (CI auth container)");

    test("a student gets Page not found", async ({ browser, baseURL }) => {
      const ctx = await prodContext(browser, baseURL as string, "student");
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

    test("an admin gets the lab", async ({ browser, baseURL }) => {
      const ctx = await prodContext(browser, baseURL as string, "admin");
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
