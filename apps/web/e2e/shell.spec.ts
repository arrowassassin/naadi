import type { Page, TestInfo } from "@playwright/test";
import { canMintUsers, type E2ERole, emailFor } from "./auth-users";
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
 * The signed-in shell: real Supabase sessions (throwaway users, signed in through /auth/callback), real
 * /api/me and /api/nav against the real database. Against the live Space these run in `pnpm e2e:live-auth`.
 */
test.skip(!canMintUsers(), "signed-in tests need the service role (run pnpm e2e:live-auth against the Space)");

const signedInAs = (role: E2ERole) => ({
  storageState: async (
    { browser, baseURL }: { browser: import("@playwright/test").Browser; baseURL: string | undefined },
    use: (path: string) => Promise<void>,
  ) => use(await signedInState(browser, baseURL as string, role)),
});
const snap = (page: Page, info: TestInfo, guard: ConsoleGuard, state: string, full = false) =>
  snapSegment(page, info, guard, "03", state, full);

const pill = (page: Page) => page.getByRole("navigation", { name: "Main" });
const tabs = (page: Page) => page.getByRole("navigation", { name: "Tabs" });

/** Forget the last good /api/me and /api/nav so loading and error states are reachable. */
async function dropUserCache(page: Page): Promise<void> {
  await page.addInitScript(() => {
    for (let i = localStorage.length - 1; i >= 0; i--) {
      const k = localStorage.key(i);
      if (k?.startsWith("naadi.user-cache.v1:")) localStorage.removeItem(k);
    }
  });
}

async function expectChromeFor(page: Page, width: number): Promise<void> {
  if (width < 640) {
    await expect(tabs(page)).toBeVisible();
    await expect(pill(page)).toBeHidden();
    await expect(tabs(page).getByRole("link", { name: "Today" })).toHaveAttribute("aria-current", "page");
  } else {
    await expect(pill(page)).toBeVisible();
    await expect(tabs(page)).toBeHidden();
    await expect(pill(page).getByRole("link", { name: "Today", exact: true })).toHaveAttribute("aria-current", "page");
    await expect(pill(page).getByRole("link", { name: "Account and settings" })).toBeVisible();
  }
}

async function bearer(page: Page): Promise<string> {
  const token = await page.evaluate(() => {
    const raw = localStorage.getItem("naadi.auth");
    return raw ? (JSON.parse(raw) as { access_token: string }).access_token : null;
  });
  expect(token).toBeTruthy();
  return `Bearer ${token}`;
}

test.describe("signed in as a student", () => {
  test.use(signedInAs("student"));

  test("Today is populated from the profile", async ({ page, consoleErrors }, info) => {
    await page.goto("/");
    const h1 = page.getByRole("heading", { level: 1 });
    await expect(h1).toHaveText(/^Good (morning|afternoon|evening)\.$/);
    await expect(page.locator("[data-state=populated]")).toBeVisible();
    await expect(page.getByRole("heading", { name: "USMLE Step 1" })).toBeVisible();
    await expect(page.getByText("No exam date yet")).toBeVisible();
    // no display name yet: the one lime action is to add it
    await expect(page.getByRole("link", { name: "Add your name" })).toHaveAttribute("href", "/settings/account");
    await expectChromeFor(page, page.viewportSize()?.width ?? 0);
    await expectNoHorizontalOverflow(page);
    await expectNoAxeViolations(page);
    await snap(page, info, consoleErrors, "today-populated", true);
  });

  test("navigation has no dead links", async ({ page }) => {
    await page.goto("/");
    await expect(page.locator("[data-state=populated]")).toBeVisible();
    const hrefs = await page
      .locator("nav[aria-label=Main] a[href], nav[aria-label=Tabs] a[href]")
      .evaluateAll((els) => [...new Set(els.map((e) => (e as HTMLAnchorElement).getAttribute("href")))]);
    expect(hrefs.length).toBeGreaterThanOrEqual(2);
    for (const href of hrefs) {
      await page.goto(href as string);
      await expect(page.getByRole("heading", { name: "Page not found" })).toHaveCount(0);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    }
    // the server lists only enabled items; the search and assistant buttons are absent, not disabled
    await expect(page.getByRole("button", { name: /search/i })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Ask Naadi" })).toHaveCount(0);
  });

  test("loading shows skeletons", async ({ page, consoleErrors }, info) => {
    await dropUserCache(page);
    let release: () => void = () => {};
    const gate = new Promise<void>((r) => {
      release = r;
    });
    await page.route("**/api/me", async (route) => {
      await gate;
      await route.continue();
    });
    await page.goto("/");
    const busy = page.locator("section[aria-busy=true]");
    await expect(busy).toBeVisible();
    await expect(busy.getByRole("status")).toHaveText("Loading your day…");
    expect(await busy.locator("[data-skeleton]").count()).toBeGreaterThanOrEqual(3);
    await expectNoAxeViolations(page);
    await snap(page, info, consoleErrors, "today-loading");
    release();
    await expect(page.locator("[data-state=populated]")).toBeVisible();
  });

  test("an API error shows retry and the request id", async ({ page, consoleErrors }, info) => {
    await dropUserCache(page);
    consoleErrors.allowHttpError({ urlPart: "/api/me", status: 500 });
    await page.route("**/api/me", (route) =>
      route.fulfill({
        status: 500,
        headers: { "x-request-id": "e2e-me-500" },
        json: {
          error: {
            code: "internal_error",
            message: "Something went wrong on our side. Try again in a moment.",
            request_id: "e2e-me-500",
          },
        },
      }),
    );
    await page.goto("/");
    const alert = page.getByRole("alert");
    await expect(alert).toContainText("Your profile could not be loaded");
    await expect(alert).toContainText("Request ID e2e-me-500");
    await expectNoAxeViolations(page);
    await snap(page, info, consoleErrors, "today-error");
    await page.unroute("**/api/me");
    await alert.getByRole("button", { name: "Try again" }).click();
    await expect(page.locator("[data-state=populated]")).toBeVisible();
  });

  test("offline keeps the shell and the last profile", async ({ page, context, consoleErrors }, info) => {
    await page.goto("/");
    await expect(page.locator("[data-state=populated]")).toBeVisible();
    // The banner uses a font face the page has not needed yet; fetch it while online, as a returning
    // visitor's HTTP cache would already have it.
    await page.evaluate(() =>
      Promise.all([...document.fonts].filter((f) => f.family.includes("JetBrains")).map((f) => f.load())),
    );
    await context.setOffline(true);
    const banner = page.getByRole("status").filter({ hasText: "Offline" });
    await expect(banner).toContainText("Showing what was loaded last");
    await expect(page.locator("[data-state=populated]")).toBeVisible();
    await expectNoAxeViolations(page);
    await snap(page, info, consoleErrors, "today-offline");
    await context.setOffline(false);
    await expect(banner).toHaveCount(0);
  });

  test("the session survives a reload", async ({ page }) => {
    await page.goto("/settings/account");
    await expect(page.getByRole("heading", { name: "Account", level: 1 })).toBeVisible();
    await page.reload();
    await expect(page).toHaveURL(/\/settings\/account$/);
    await expect(page.getByRole("heading", { name: emailFor("student") })).toBeVisible();
  });

  test("Account settings", async ({ page, consoleErrors }, info) => {
    await page.goto("/settings");
    await expect(page).toHaveURL(/\/settings\/account$/);
    await expect(page.getByText("Email verified")).toBeVisible();
    await expect(page.getByText("Admin", { exact: true })).toHaveCount(0);
    const save = page.getByRole("button", { name: "Save changes" });
    await expect(save).toBeDisabled();
    await expect(page.getByRole("combobox", { name: "Time zone" })).toHaveValue("Asia/Kolkata");
    await expectNoHorizontalOverflow(page);
    await expectNoAxeViolations(page);
    await snap(page, info, consoleErrors, "account", true);
  });

  test("admin routes are refused server-side for a student", async ({ page }) => {
    await page.goto("/");
    await expect(page.locator("[data-state=populated]")).toBeVisible();
    const res = await page.request.get("/api/admin/overview", { headers: { authorization: await bearer(page) } });
    expect(res.status()).toBe(403);
    const me = await page.request.get("/api/me", { headers: { authorization: await bearer(page) } });
    expect((await me.json()).is_admin).toBe(false);
  });

  for (const width of [320, 507, 694]) {
    test(`Split View ${width} px`, async ({ page, consoleErrors }, info) => {
      await page.setViewportSize({ width, height: page.viewportSize()?.height ?? 900 });
      await page.goto("/");
      await expect(page.locator("[data-state=populated]")).toBeVisible();
      await expectChromeFor(page, width);
      await expectNoHorizontalOverflow(page);
      const insets = await page.evaluate(() => {
        const s = getComputedStyle(document.documentElement);
        return {
          top: Number.parseFloat(s.getPropertyValue("--shell-top")),
          bottom: Number.parseFloat(s.getPropertyValue("--shell-bottom")),
        };
      });
      if (width < 640) {
        expect(insets.top).toBe(0);
        expect(insets.bottom).toBeGreaterThan(48);
      } else {
        expect(insets.top).toBeGreaterThan(44);
        expect(insets.bottom).toBe(0);
      }
      await expectNoAxeViolations(page);
      await snap(page, info, consoleErrors, `split-${width}`);
    });
  }

  test("skip link moves focus to the content", async ({ page, browserName }) => {
    await page.goto("/");
    await expect(page.locator("[data-state=populated]")).toBeVisible();
    await page.keyboard.press(browserName === "webkit" ? "Alt+Tab" : "Tab");
    const skip = page.getByRole("link", { name: "Skip to content" });
    await expect(skip).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page.locator("main#main")).toBeFocused();
  });
});

test.describe("signed in as an editor", () => {
  test.use(signedInAs("editor"));

  test("saving a display name updates the greeting", async ({ page, consoleErrors }, info) => {
    const name = `Editor ${info.project.name}`.slice(0, 60);
    await page.goto("/settings/account");
    const field = page.getByRole("textbox", { name: "Display name" });
    await field.fill(name);
    await page.getByRole("button", { name: "Save changes" }).click();
    await expect(page.getByRole("status").filter({ hasText: "Saved" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Save changes" })).toBeDisabled();
    await snap(page, info, consoleErrors, "account-saved");
    await (info.project.name.includes("phone")
      ? page.getByRole("navigation", { name: "Tabs" }).getByRole("link", { name: "Today" })
      : page.getByRole("link", { name: "Naadi, Today" })
    ).click();
    await expect(page.getByRole("heading", { level: 1 })).toContainText(name);
  });
});

/** Admin: the API gate and the UI marker, for a throwaway admin (ADMIN_EMAILS of the API under test). */
test.describe("admin", () => {
  test.use(signedInAs("admin"));

  test("is an admin and passes the server-side admin gate", async ({ page, request }) => {
    await page.goto("/settings/account");
    await expect(page.getByText("Admin", { exact: true })).toBeVisible();
    const authorization = await bearer(page);
    const me = await request.get("/api/me", { headers: { authorization } });
    expect(me.status()).toBe(200);
    expect((await me.json()).is_admin).toBe(true);
    const res = await request.get("/api/admin/overview", { headers: { authorization } });
    expect(res.status()).toBe(200);
    const body = await res.json();
    expect(body.admins_configured).toBeGreaterThanOrEqual(1);
    expect(body.profiles).toBeGreaterThanOrEqual(1);
  });
});
