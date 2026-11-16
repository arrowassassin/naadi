import type { Route } from "@playwright/test";
import { expect, expectNoAxeViolations, expectNoHorizontalOverflow, healthPanel, snap, test } from "./fixtures";

const COMPONENTS = ["database", "storage", "tavily", "openrouter", "nvidia"] as const;
// The chip reads "<Component> status: <Word>" to assistive tech; the visible word is the last token.
const STATUS_WORDS = /status: (OK|Degraded|Down)$/;

test.describe("token sheet", () => {
  for (const path of ["/", "/system"]) {
    test(`renders at ${path}`, async ({ page, consoleErrors }) => {
      const health = page.waitForResponse("**/api/health");
      await page.goto(path);
      const status = (await health).status();
      if (status === 503) consoleErrors.allowHttpError({ urlPart: "/api/health", status });

      await expect(page.getByRole("heading", { level: 1 })).toHaveText(/Naadi\.\s*system/);
      for (const name of ["Color tokens", "Type", "Radius · spacing · targets", "Components", "System health"]) {
        await expect(page.getByRole("heading", { name, exact: true })).toBeVisible();
      }
      await expect(page.getByRole("list", { name: "Role colours" }).getByRole("listitem")).toHaveCount(16);
      await expect(page.getByRole("list", { name: "Neutral colours" }).getByRole("listitem")).toHaveCount(12);
      // Swatch values are read from the live CSS variables, so this proves tokens.css is applied.
      await expect(page.getByText("#C6FF3D · action")).toBeVisible();
      await expect(page.getByText("#9D8CFF · assistant")).toBeVisible();
      await expect(page.getByText("#050506 · page")).toBeVisible();

      const fonts = await page.evaluate(async () => {
        await document.fonts.ready;
        return {
          display: document.fonts.check('800 40px "Bricolage Grotesque Variable"'),
          sans: document.fonts.check('400 16px "Inter Variable"'),
          mono: document.fonts.check('500 12px "JetBrains Mono"'),
          ink: document.fonts.check('700 34px "Caveat"'),
          bg: getComputedStyle(document.body).backgroundColor,
        };
      });
      expect(fonts).toEqual({ display: true, sans: true, mono: true, ink: true, bg: "rgb(5, 5, 6)" });
      await expectNoHorizontalOverflow(page);
    });
  }

  test("fonts and assets are same-origin only", async ({ page, baseURL, consoleErrors }) => {
    const foreign: string[] = [];
    page.on("request", (req) => {
      const url = new URL(req.url());
      if (url.origin !== new URL(baseURL as string).origin && !url.protocol.startsWith("data")) foreign.push(req.url());
    });
    const health = page.waitForResponse("**/api/health");
    await page.goto("/");
    const status = (await health).status();
    if (status === 503) consoleErrors.allowHttpError({ urlPart: "/api/health", status });
    await page.evaluate(() => document.fonts.ready);
    expect(foreign).toEqual([]);
  });

  test("keyboard focus ring is 3 px violet", async ({ page, consoleErrors, browserName }) => {
    const health = page.waitForResponse("**/api/health");
    await page.goto("/");
    const status = (await health).status();
    if (status === 503) consoleErrors.allowHttpError({ urlPart: "/api/health", status });
    // WebKit only tabs to links with Alt+Tab (Safari's default); buttons are reached with Tab in all three.
    const key = browserName === "webkit" ? "Alt+Tab" : "Tab";
    let found = false;
    for (let i = 0; i < 25 && !found; i++) {
      await page.keyboard.press(key);
      found = await page.evaluate(() => document.activeElement?.tagName === "BUTTON");
    }
    expect(found, "a button receives keyboard focus").toBe(true);
    const ring = () =>
      page.evaluate(() => {
        const el = document.activeElement as HTMLElement;
        const s = getComputedStyle(el);
        return {
          matches: el.matches(":focus-visible"),
          style: s.outlineStyle,
          width: s.outlineWidth,
          color: s.outlineColor,
          offset: s.outlineOffset,
        };
      });
    await expect
      .poll(ring)
      .toEqual({ matches: true, style: "solid", width: "3px", color: "rgb(157, 140, 255)", offset: "2px" });
  });
});

test.describe("health panel", () => {
  test("populated from the running API", async ({ page, consoleErrors }, testInfo) => {
    const healthResponse = page.waitForResponse("**/api/health");
    await page.goto("/");
    const res = await healthResponse;
    const status = res.status();
    expect([200, 503]).toContain(status);
    if (status === 503) consoleErrors.allowHttpError({ urlPart: "/api/health", status });
    const body = await res.json();

    const panel = healthPanel(page);
    for (const name of COMPONENTS) {
      const card = panel.locator(`[data-component="${name}"]`);
      await expect(card).toBeVisible();
      await expect(card).toHaveAttribute("data-status", body.components[name].status);
      await expect(card.locator("[data-tone]")).toHaveText(STATUS_WORDS);
      await expect(card).toContainText(body.components[name].detail);
    }
    const probes = body.components.nvidia.models ?? [];
    const modelList = panel.locator('[data-component="nvidia"]').getByRole("list", { name: "Configured models" });
    if (probes.length > 0) {
      await expect(modelList.getByRole("listitem")).toHaveCount(probes.length);
      for (const p of probes) {
        await expect(modelList.locator(`[data-model="${p.model}"]`)).toContainText(p.ok ? "Answers" : "Fails:");
      }
    } else {
      await expect(modelList).toHaveCount(0);
    }
    if (process.env.E2E_EXPECT_DB_OK) {
      await expect(panel.locator('[data-component="database"]')).toHaveAttribute("data-status", "ok");
    }
    if (status === 503) {
      await expect(panel.getByRole("alert")).toContainText("The database is down");
    } else {
      await expect(panel.getByRole("alert")).toHaveCount(0);
    }
    await expect(panel).toHaveAttribute("aria-busy", "false");
    await expectNoHorizontalOverflow(page);
    await expectNoAxeViolations(page);
    await snap(page, testInfo, consoleErrors, "populated", true);
  });

  test("loading shows skeletons", async ({ page, consoleErrors }, testInfo) => {
    let release: () => void = () => {};
    const gate = new Promise<void>((r) => {
      release = r;
    });
    await page.route("**/api/health", async (route: Route) => {
      await gate;
      await route.continue();
    });
    await page.goto("/");
    const panel = healthPanel(page);
    await expect(panel).toHaveAttribute("aria-busy", "true");
    await expect(panel.getByRole("status")).toHaveText("Checking system health…");
    expect(await panel.locator("[data-skeleton]").count()).toBeGreaterThanOrEqual(16);
    await expectNoHorizontalOverflow(page);
    await expectNoAxeViolations(page);
    await snap(page, testInfo, consoleErrors, "loading");
    const done = page.waitForResponse("**/api/health");
    release();
    const status = (await done).status();
    if (status === 503) consoleErrors.allowHttpError({ urlPart: "/api/health", status });
    await expect(panel.locator("[data-component]")).toHaveCount(COMPONENTS.length);
  });

  test("error shows a message and retry recovers", async ({ page, consoleErrors }, testInfo) => {
    consoleErrors.allowHttpError({ urlPart: "/api/health", status: 500 });
    await page.route("**/api/health", (route) =>
      route.fulfill({
        status: 500,
        contentType: "application/json",
        headers: { "x-request-id": "e2e-injected-500" },
        body: JSON.stringify({
          error: {
            code: "internal_error",
            message: "Something went wrong on our side. Try again in a moment.",
            request_id: "e2e-injected-500",
          },
        }),
      }),
    );
    await page.goto("/");
    const alert = healthPanel(page).getByRole("alert");
    await expect(alert).toContainText("System health could not be loaded");
    await expect(alert).toContainText("Something went wrong on our side. Try again in a moment. (HTTP 500)");
    await expect(alert).toContainText("Request ID e2e-injected-500");
    await expectNoHorizontalOverflow(page);
    await expectNoAxeViolations(page);
    await snap(page, testInfo, consoleErrors, "error");

    await page.unroute("**/api/health");
    const retried = page.waitForResponse("**/api/health");
    await alert.getByRole("button", { name: "Try again" }).click();
    const status = (await retried).status();
    if (status === 503) consoleErrors.allowHttpError({ urlPart: "/api/health", status });
    await expect(healthPanel(page).locator("[data-component]")).toHaveCount(COMPONENTS.length);
  });

  test("offline shows the banner and keeps the last result", async ({ page, context, consoleErrors }, testInfo) => {
    const healthResponse = page.waitForResponse("**/api/health");
    await page.goto("/");
    const status = (await healthResponse).status();
    if (status === 503) consoleErrors.allowHttpError({ urlPart: "/api/health", status });
    const panel = healthPanel(page);
    await expect(panel.locator("[data-component]")).toHaveCount(COMPONENTS.length);

    await context.setOffline(true);
    const banner = panel.getByRole("status").filter({ hasText: "Offline" });
    await expect(banner).toBeVisible();
    await expect(banner).toContainText("Showing the last result from");
    await expect(panel.locator("[data-component]")).toHaveCount(COMPONENTS.length);
    await expect(panel.getByRole("button", { name: /Check again/ })).toBeDisabled();
    await expectNoHorizontalOverflow(page);
    await expectNoAxeViolations(page);
    await snap(page, testInfo, consoleErrors, "offline");

    await context.setOffline(false);
    await expect(banner).toHaveCount(0);
  });
});
