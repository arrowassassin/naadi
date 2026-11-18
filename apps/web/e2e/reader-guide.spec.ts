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

test("whole tour launches once, visits enabled screens, and replays from Help", async ({ page }) => {
  await page.goto("/");
  const step = page.locator(".guide-step");
  await expect(step.locator(".driver-popover-title")).toHaveText("Follow today's plan");
  for (const path of [
    "/learn",
    "/practice",
    "/cards",
    "/notes",
    "/plan",
    "/pathway",
    "/learn/library",
    "/search",
    "/settings/account",
    "/settings/account",
  ]) {
    const progress = await step.locator(".driver-popover-progress-text").textContent();
    await step.getByRole("button", { name: "Next", exact: true }).click();
    await expect.poll(() => new URL(page.url()).pathname).toBe(path);
    await expect(step.locator(".driver-popover-progress-text")).not.toHaveText(progress ?? "");
  }
  await step.getByRole("button", { name: "Finish tour" }).click();
  await expect(page).toHaveURL(/\/$/);
  await page.reload();
  await expect(page.locator("[data-state=populated]")).toBeVisible();
  await expect(step).toHaveCount(0);
  await page.goto("/settings/account");
  await page.getByRole("button", { name: "How Naadi works", exact: true }).click();
  await page.getByRole("button", { name: "Tour the whole app" }).click();
  await expect(step.locator(".driver-popover-title")).toHaveText("Follow today's plan");
  await page.keyboard.press("Escape");
  await expect(page).toHaveURL(/\/settings\/account$/);
});

test("writing in split page notes keeps lesson navigation and chat on the left", async ({ page }) => {
  test.skip((page.viewportSize()?.width ?? 0) < 1100, "Side-by-side reader");
  await page.goto("/learn/ch01/ch01-s01");
  await page.locator("[data-page-notes-toggle]:visible").click();
  const notes = page.locator("[data-reader-notes-side]");
  await expect(notes.locator("[data-note-editor]")).toBeVisible();
  const mode = notes.getByRole("combobox");
  if (await mode.isVisible()) await mode.selectOption("write");
  else await notes.getByRole("button", { name: "Write", exact: true }).click();
  const toolbar = notes.getByRole("toolbar", { name: "Ink tools" });
  await expect(toolbar).toBeVisible();
  const toolsBox = (await toolbar.boundingBox())!;
  const paperBox = (await notes.locator("[data-page-ink] [data-paper]").boundingBox())!;
  expect(paperBox.y - (toolsBox.y + toolsBox.height)).toBeGreaterThanOrEqual(11);
  expect(toolsBox.x - paperBox.x).toBeGreaterThanOrEqual(7);
  const toggle = toolbar.getByRole("button", { name: "Ink tools" });
  for (const expanded of [false, true]) {
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-expanded", String(expanded));
    await notes.evaluate((el) => {
      el.scrollTop = el.scrollHeight;
    });
    await expect
      .poll(() =>
        notes.evaluate((el) => {
          const paper = el.querySelector("[data-paper]")!.getBoundingClientRect();
          const tools = el.querySelector('[role="toolbar"]')!.getBoundingClientRect();
          const bottom = document.elementFromPoint(paper.left + paper.width / 2, paper.bottom - 4);
          return tools.bottom <= paper.top - 10 && !!bottom?.closest("[data-page-ink]");
        }),
      )
      .toBe(true);
  }
  const dock = page.locator("[data-shell-bottom]");
  const next = dock.locator("[data-reader-slot] a").last();
  const chat = dock.getByRole("button", { name: "Open the assistant" });
  for (const control of [next, chat]) {
    await expect(control).toBeInViewport();
    const rect = (await control.boundingBox())!;
    expect(rect.x + rect.width).toBeLessThanOrEqual((await notes.boundingBox())!.x);
  }
  await chat.click();
  await expect(page.locator('[data-assistant="panel"]')).toBeVisible();
  await next.click();
  await expect(page).not.toHaveURL(/ch01-s01$/);
  await expect(notes).toBeVisible();
});
