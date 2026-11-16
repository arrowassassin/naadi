import AxeBuilder from "@axe-core/playwright";
import type { Page } from "@playwright/test";
import { canMintUsers, type E2ERole } from "./auth-users";
import { type AxeResults, axeFrozen, expect, summarizeViolations, test, WCAG_TAGS } from "./fixtures";
import { signedInState } from "./session";

/**
 * The frozen-copy axe run (fixtures.ts `axeFrozen`, used everywhere because live-page axe crashes
 * Firefox) must judge exactly like axe on the live page. Chromium, where live axe is stable, is the
 * reference: same violations and the same set of rules that pass, for every S03 page.
 */
test.skip(({ browserName }) => browserName !== "chromium", "live-page reference runs on Chromium");

function verdict(r: AxeResults) {
  return {
    violations: summarizeViolations(r),
    passes: r.passes.map((p) => p.id).sort(),
    incomplete: r.incomplete.map((p) => p.id).sort(),
  };
}

async function expectParity(page: Page): Promise<void> {
  const live = await new AxeBuilder({ page }).withTags(WCAG_TAGS).analyze();
  const frozen = await axeFrozen(page);
  expect(verdict(frozen)).toEqual(verdict(live));
  expect(verdict(live).passes.length).toBeGreaterThan(10); // a real page was judged, not an empty one
}

test.describe("signed out", () => {
  for (const path of ["/system", "/sign-in", "/sign-in?error=expired", "/auth/callback"]) {
    test(`parity on ${path}`, async ({ page }) => {
      await page.goto(path);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      if (path === "/system") {
        await expect(page.locator("section[aria-labelledby='health-h']")).toHaveAttribute("aria-busy", "false");
      }
      await expectParity(page);
    });
  }

  test("parity with typed form state", async ({ page }) => {
    await page.goto("/sign-in");
    await page.getByRole("textbox", { name: "Email" }).fill("not-an-email");
    await page.getByRole("button", { name: "Email me a sign-in link" }).click();
    await expect(page.getByRole("textbox", { name: "Email" })).toHaveAttribute("aria-invalid", "true");
    await expectParity(page);
  });
});

test.describe("signed in", () => {
  test.skip(!canMintUsers(), "needs the service role (local auth stack)");
  const role: E2ERole = "student";
  test.use({
    storageState: async ({ browser, baseURL }, use) => use(await signedInState(browser, baseURL as string, role)),
  });

  for (const path of ["/", "/settings/account"]) {
    test(`parity on ${path}`, async ({ page }) => {
      await page.goto(path);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await expect(page.locator("[aria-busy=true]")).toHaveCount(0);
      await expectParity(page);
    });
  }
});
