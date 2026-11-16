import type { Browser, Page, TestInfo } from "@playwright/test";
import { canMintUsers, type E2ERole } from "./auth-users";
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
 * Segment 04: the admin agent diagnostics panel against the real Aegra runtime (naadi_api.agent.host:app,
 * started by playwright.config.ts when a database is configured), real Supabase sessions and the real
 * `ping` graph. With provider keys the router model streams its answer; without them (CI) the run still
 * completes with "unavailable", so the runtime path (block, interrupt → resume, record) is always tested.
 * The second server has its LLM budget forced to zero: the degradation path.
 */
const hasRuntime = Boolean(process.env.E2E_DATABASE_URL ?? process.env.DATABASE_URL) && !process.env.E2E_BASE_URL;
test.skip(!canMintUsers() || !hasRuntime, "the agent runtime needs a local database and throwaway users");

const BUDGET_ZERO_URL = `http://127.0.0.1:${Number(process.env.E2E_PORT ?? 7860) + 1}`;

const signedInAs = (role: E2ERole, origin?: string) => ({
  storageState: async (
    { browser, baseURL }: { browser: Browser; baseURL: string | undefined },
    use: (path: string) => Promise<void>,
  ) => use(await signedInState(browser, origin ?? (baseURL as string), role)),
  ...(origin ? { baseURL: origin } : {}),
});
const snap = (page: Page, info: TestInfo, guard: ConsoleGuard, state: string) =>
  snapSegment(page, info, guard, "04", state, true);

const panel = (page: Page) => page.getByTestId("ping-panel");

async function openDiagnostics(page: Page): Promise<void> {
  await page.goto("/admin/agent");
  await expect(page.getByRole("heading", { name: "Agent diagnostics", level: 1 })).toBeVisible();
  await expect(page.getByTestId("agent-runtime")).toBeVisible();
}

test.describe("admin", () => {
  test.use(signedInAs("admin"));

  test("runtime status, ping streams, interrupts for confirm, records on Record", async ({
    page,
    consoleErrors,
  }, info) => {
    await openDiagnostics(page);
    await expect(page.getByTestId("agent-runtime").getByText(/OK|Degraded|Down/)).toBeVisible();
    await snap(page, info, consoleErrors, "populated");

    await page.getByRole("button", { name: "Run ping" }).click();
    await expect(panel(page)).toHaveAttribute("data-phase", /streaming|interrupted/);
    await snap(page, info, consoleErrors, "streaming");

    await expect(page.getByTestId("ping-confirm")).toBeVisible({ timeout: 45_000 });
    await expect(panel(page)).toHaveAttribute("data-phase", "interrupted");
    await expect(page.getByTestId("agent-block-table")).toBeVisible();
    await expect(page.getByRole("button", { name: "Run ping" })).toBeDisabled();
    await expectNoAxeViolations(page);
    await expectNoHorizontalOverflow(page);
    await snap(page, info, consoleErrors, "interrupted");

    await page.getByRole("button", { name: "Record" }).click();
    await expect(page.getByTestId("ping-done")).toHaveText(/Recorded as health log entry \d+\./, { timeout: 30_000 });
    await expect(panel(page)).toHaveAttribute("data-phase", "idle");
    await expectNoAxeViolations(page);
  });

  test("discard does not record", async ({ page }) => {
    await openDiagnostics(page);
    await page.getByRole("button", { name: "Run ping" }).click();
    await expect(page.getByTestId("ping-confirm")).toBeVisible({ timeout: 45_000 });
    await page.getByRole("button", { name: "Discard" }).click();
    await expect(page.getByTestId("ping-done")).toHaveText("Not recorded.", { timeout: 30_000 });
  });

  test("offline: the self-test is disabled and says why", async ({ page, context, consoleErrors }, info) => {
    await openDiagnostics(page);
    // Fetch every declared font face first: a face first used by the offline banner would otherwise fail to
    // load once the network is gone (a test artefact, not an app error).
    await page.evaluate(() => Promise.all([...document.fonts].map((f) => f.load().catch(() => undefined))));
    await context.setOffline(true);
    await expect(panel(page).getByRole("status").filter({ hasText: "Offline" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Run ping" })).toBeDisabled();
    await expect(panel(page)).toHaveAttribute("data-phase", "offline");
    await expectNoAxeViolations(page);
    await snap(page, info, consoleErrors, "offline");
    await context.setOffline(false);
  });

  test("an agent run with an expired session is an error state, not a hang", async ({ page, consoleErrors }, info) => {
    await openDiagnostics(page);
    // The runtime refuses a bad token (401); the panel shows the failure with a retry.
    consoleErrors.allowHttpError({ urlPart: "/threads", status: 401, agentSdk: true });
    await page.route("**/threads**", (route) =>
      route.continue({ headers: { ...route.request().headers(), authorization: "Bearer expired.token.value" } }),
    );
    await page.getByRole("button", { name: "Run ping" }).click();
    await expect(panel(page)).toHaveAttribute("data-phase", "error", { timeout: 30_000 });
    await expect(panel(page).getByText("The self-test failed")).toBeVisible();
    await expectNoAxeViolations(page);
    await snap(page, info, consoleErrors, "error");
  });
});

test.describe("admin, budget forced to zero", () => {
  test.use(signedInAs("admin", BUDGET_ZERO_URL));

  test("the ping degrades and shows when the AI quota resets", async ({ page, consoleErrors }, info) => {
    await openDiagnostics(page);
    await page.getByRole("button", { name: "Run ping" }).click();
    await expect(page.getByTestId("budget-exhausted")).toBeVisible({ timeout: 45_000 });
    await expect(page.getByTestId("budget-exhausted")).toContainText(/00:00/);
    await expect(panel(page)).toHaveAttribute("data-phase", "budget_exhausted");
    await expectNoAxeViolations(page);
    await expectNoHorizontalOverflow(page);
    await snap(page, info, consoleErrors, "budget-exhausted");
  });
});

test.describe("student", () => {
  test.use(signedInAs("student"));

  test("the diagnostics page does not exist for a student, and the runtime refuses the ping", async ({ page }) => {
    await page.goto("/admin/agent");
    await expect(page.getByRole("heading", { name: "Page not found", level: 1 })).toBeVisible();
    const token = await page.evaluate(() => {
      const raw = localStorage.getItem("naadi.auth");
      return raw ? (JSON.parse(raw) as { access_token: string }).access_token : "";
    });
    const res = await page.request.post("/runs/crons", {
      headers: { authorization: `Bearer ${token}` },
      data: { assistant_id: "ping", schedule: "* * * * *", input: { trigger: "background" } },
    });
    expect(res.status()).toBe(403);
  });
});
