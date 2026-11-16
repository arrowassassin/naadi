import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
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
const API_DIR = path.resolve(import.meta.dirname, "../../api");
const RECORDED_STREAM = readFileSync(path.join(import.meta.dirname, "fixtures/ping-stream.sse"), "utf8");

/** Recorded search results for the self-test's media block (Commons image, PMC figure, YouTube video) go
 * into search_cache, so every run renders the same media without a search call. */
test.beforeAll(() => {
  const db = process.env.E2E_DATABASE_URL ?? process.env.DATABASE_URL;
  if (!db || !hasRuntime) return;
  execFileSync(
    "uv",
    ["--directory", API_DIR, "run", "--frozen", "python", "-m", "tests.agent.seed_media_cache", "--database-url", db],
    { stdio: ["ignore", "pipe", "pipe"], timeout: 60_000 },
  );
});

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
    if (!process.env.E2E_LIVE_LLM) {
      // No provider keys: the result says so in words, not only with a colour.
      await expect(page.getByTestId("agent-block-result").getByText("Unavailable").first()).toBeVisible();
    }
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
    await expect(panel(page).getByText("Your session has ended. Sign in again, then retry.")).toBeVisible();
    await expect(panel(page)).not.toContainText("{"); // never raw response JSON
    await expectNoAxeViolations(page);
    await snap(page, info, consoleErrors, "error");
  });
});

test.describe("admin, answer blocks", () => {
  test.use(signedInAs("admin"));

  test("media: Commons image, PMC figure and YouTube embed with credits, licences and links", async ({
    page,
    consoleErrors,
  }, info) => {
    await openDiagnostics(page);
    await page.getByRole("button", { name: "Run ping" }).click();
    const media = page.getByTestId("agent-block-media");
    await expect(media).toBeVisible({ timeout: 45_000 });
    for (const id of ["media-commons", "media-pmc_figure"]) {
      const fig = media.getByTestId(id);
      await expect(fig.locator("img")).toBeVisible();
      await expect
        .poll(() => fig.locator("img").evaluate((i: HTMLImageElement) => i.complete && i.naturalWidth > 0))
        .toBe(true);
      await expect(fig).toContainText(/CC BY/);
      await expect(fig.getByRole("link", { name: "Source" })).toHaveAttribute("href", /^https:\/\//);
    }
    const yt = media.getByTestId("media-youtube").locator("iframe");
    await expect(yt).toHaveAttribute("src", /^https:\/\/www\.youtube-nocookie\.com\/embed\/czY5nyvZ7cU\?origin=/);
    await expect(yt).toHaveAttribute("referrerpolicy", "strict-origin-when-cross-origin");
    await expect(yt).toHaveAttribute("title", /^Video: /);
    await expect(page.getByTestId("ping-confirm")).toBeVisible({ timeout: 45_000 });
    await expectNoAxeViolations(page);
    await expectNoHorizontalOverflow(page);
    await snap(page, info, consoleErrors, "media");
  });

  test("streamed reply (recorded stream): the assistant's answer renders in violet", async ({
    page,
    consoleErrors,
  }, info) => {
    await openDiagnostics(page);
    // One real ping stream, recorded from the router model (e2e/fixtures/ping-stream.sse; model ids redacted).
    await page.route("**/threads/*/runs/stream", (route) =>
      route.fulfill({ status: 200, contentType: "text/event-stream", body: RECORDED_STREAM }),
    );
    await page.getByRole("button", { name: "Run ping" }).click();
    const reply = page.getByTestId("ping-reply");
    await expect(reply).toBeVisible({ timeout: 30_000 });
    await expect(reply).not.toBeEmpty();
    await expect(page.getByTestId("agent-block-result")).toContainText("Answered");
    await expectNoAxeViolations(page);
    await snap(page, info, consoleErrors, "streamed-reply");
  });
});

test.describe("admin, budget forced to zero", () => {
  test.use(signedInAs("admin", BUDGET_ZERO_URL));

  test("the ping degrades and shows when the AI quota resets", async ({ page, consoleErrors }, info) => {
    await openDiagnostics(page);
    await page.getByRole("button", { name: "Run ping" }).click();
    await expect(page.getByTestId("budget-exhausted")).toBeVisible({ timeout: 45_000 });
    // Copy from app_config.ui_copy (agent.quota_reset), reset at the next 00:00 UTC.
    await expect(page.getByTestId("budget-exhausted")).toContainText("AI quota resets at 00:00 UTC");
    await expect(panel(page)).toHaveAttribute("data-phase", "budget_exhausted");
    // Nothing pending: a spent budget ends the run without a confirm.
    await expect(page.getByTestId("ping-confirm")).toHaveCount(0);
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
