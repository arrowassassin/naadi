import { mkdirSync } from "node:fs";
import { chromium, type FullConfig } from "@playwright/test";
import { AUTH_STATE_DIR, authStatePath, callbackPath, type E2ERole, magicLink } from "./auth-users";

/**
 * Signs the shared test users in once, through the app's real /auth/callback (Supabase verifies the
 * token), and saves the resulting browser storage. Shell tests reuse it, so each run costs Supabase only
 * a handful of verifications (its verify endpoint is rate-limited per IP).
 */
export default async function globalSetup(config: FullConfig): Promise<void> {
  const baseURL = config.projects[0]?.use.baseURL;
  if (!baseURL) throw new Error("baseURL is not configured");
  mkdirSync(AUTH_STATE_DIR, { recursive: true });
  const browser = await chromium.launch();
  try {
    for (const role of ["student", "admin"] as E2ERole[]) {
      const context = await browser.newContext({
        baseURL,
        extraHTTPHeaders: config.projects[0]?.use.extraHTTPHeaders,
      });
      const page = await context.newPage();
      await page.goto(callbackPath(magicLink(role)));
      await page.waitForURL((u) => u.pathname === "/", { timeout: 30_000 });
      await page.getByRole("heading", { level: 1 }).waitFor({ timeout: 30_000 });
      await context.storageState({ path: authStatePath(role) });
      await context.close();
    }
  } finally {
    await browser.close();
  }
}
