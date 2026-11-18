import { canMintUsers } from "./auth-users";
import { expect, expectNoHorizontalOverflow, test } from "./fixtures";
import { signedInState } from "./session";

test.skip(!canMintUsers(), "requires the signed-in student fixture");

test.use({
  storageState: async ({ browser, baseURL }, use) => {
    await use(await signedInState(browser, baseURL as string, "student"));
  },
});

test("reader guide highlights controls and chat keeps a draft across layouts", async ({ page }) => {
  await page.goto("/learn/ch01/ch01-s01");
  await expect(page.locator("#section-h")).toBeVisible();
  await expect(page.locator("[data-shell-bottom] nav a[href*='/learn/']").last()).toBeVisible();
  await page.getByRole("button", { name: "How Naadi works", exact: true }).click();
  await page.getByRole("button", { name: "Tour this screen" }).click();
  const popover = page.locator(".guide-step");
  await expect(popover).toBeVisible();
  await expect(popover.locator(".driver-popover-title")).toHaveText("Read and annotate a section");
  await popover.getByRole("button", { name: "Next", exact: true }).click();
  await expect(popover.locator(".driver-popover-title")).toHaveText("Keep notes beside a lesson");
  await page.keyboard.press("Escape");
  await expect(popover).toHaveCount(0);
  await expectNoHorizontalOverflow(page);
  if ((page.viewportSize()?.width ?? 0) >= 1100) {
    await page.locator("[data-assistant='dock'] button").first().click();
    const panel = page.locator("[data-assistant='panel']");
    await panel.locator("textarea").fill("Explain this passage");
    await panel.getByRole("button", { name: "Open chat full screen" }).click();
    const full = page.locator("[data-assistant='fullscreen']");
    await expect(full.locator("textarea")).toHaveValue("Explain this passage");
    await full.getByRole("button", { name: "Return to side panel" }).click();
    await expect(panel.locator("textarea")).toHaveValue("Explain this passage");
  }
});
