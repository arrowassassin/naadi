import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import type { Browser, Page, TestInfo } from "@playwright/test";
import { canMintUsers, type E2ERole } from "./auth-users";
import { isLoopbackDatabaseUrl } from "./databaseUrl";
import {
  type ConsoleGuard,
  expect,
  expectNoAxeViolations,
  expectNoHorizontalOverflow,
  ignorableConsoleLine,
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
// Every ping's media block loads images from Wikimedia and NCBI's PMC bucket (YouTube only after a click). Behind
// a TLS-intercepting proxy (an agent sandbox) Firefox rejects the proxy's certificate (it keeps its own trust
// store), and the console guard rightly reports those failed requests; such a run opts in with
// E2E_IGNORE_HTTPS_ERRORS=1. CI and real devices have no such proxy, so there certificates are checked.
test.use({ ignoreHTTPSErrors: process.env.E2E_IGNORE_HTTPS_ERRORS === "1" });

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
/** The runtime status as the page words it from the server's structured facts (never the server's `detail`). */
const RUNTIME_SUMMARY =
  /^(\d+ graphs? loaded; crons are firing \(last hourly ping \d\d:\d\d UTC\)\.|Running, but .+\.)$/;
const API_DIR = path.resolve(import.meta.dirname, "../../api");
const RECORDED_STREAM = readFileSync(path.join(import.meta.dirname, "fixtures/ping-stream.sse"), "utf8");

/** Recorded search results for the self-test's media block (Commons image, PMC figure, YouTube video) go
 * into search_cache, so every run renders the same media without a search call. */
const DB_URL = process.env.E2E_DATABASE_URL ?? process.env.DATABASE_URL;
/** The recorded results are seeded only into a local test database (E2E_ALLOW_HOSTED=1 runs skip the media test). */
const canSeedMedia = isLoopbackDatabaseUrl(DB_URL ?? "");
test.beforeAll(() => {
  const db = DB_URL;
  if (!db || !hasRuntime || !canSeedMedia) return;
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
    // Worded from the server's structured facts with ui_copy, never the server's English `detail`.
    await expect(page.getByTestId("agent-runtime-summary")).toHaveText(RUNTIME_SUMMARY);
    await expect(page.getByTestId("agent-runtime-summary")).not.toContainText(/Runtime up|Not ok:/);
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
    test.skip(!canSeedMedia, "the recorded media results are seeded only into a local test database");
    const youtubeRequests: string[] = [];
    page.on("request", (r) => {
      if (
        /(^|\.)(youtube(-nocookie)?\.com|ytimg\.com|googlevideo\.com|arrowassassin-naadi-player\.static\.hf\.space)$/.test(
          new URL(r.url()).hostname,
        )
      ) {
        youtubeRequests.push(r.url());
      }
    });
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
    // Click-to-load: no contact with YouTube until the admin asks for the video.
    const video = media.getByTestId("media-youtube");
    await expect(video.locator("iframe")).toHaveCount(0);
    expect(youtubeRequests, "no request to YouTube before the click").toEqual([]);
    await expect(page.getByTestId("ping-confirm")).toBeVisible({ timeout: 45_000 });
    await expectNoAxeViolations(page);
    await expectNoHorizontalOverflow(page);
    await snap(page, info, consoleErrors, "media");
    // Only the player page's frame is asserted here (the bridge itself is covered by video.spec.ts): serve a blank page.
    await page.route("https://arrowassassin-naadi-player.static.hf.space/**", (route) =>
      route.fulfill({ status: 200, contentType: "text/html", body: "<!doctype html><title>player</title>" }),
    );
    await video.getByRole("button", { name: /^Play video: / }).click();
    const yt = video.locator("iframe");
    // The strict app CSP frames only the player page (its own origin); the YouTube embed lives inside that page.
    await expect(yt).toHaveAttribute(
      "src",
      /^https:\/\/arrowassassin-naadi-player\.static\.hf\.space\/index\.html\?parent=/,
    );
    await expect(yt).toHaveAttribute("sandbox", "allow-scripts allow-same-origin");
    await expect(yt).toHaveAttribute("referrerpolicy", "strict-origin-when-cross-origin");
    await expect(yt).toHaveAttribute("title", /^Video: /);
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

test.describe("admin, production server (APP_ENV=prod, production CSP)", () => {
  const PROD = process.env.E2E_PROD_URL;

  test("the diagnostics page renders with zero console errors and shows the media block", async ({
    browser,
    baseURL,
  }) => {
    test.skip(!PROD, "needs the local APP_ENV=prod API");
    test.skip(!canSeedMedia, "the recorded media results are seeded only into a local test database");
    // The admin session from the main origin, moved to the https prod origin (as in ink-gate.spec): signing in
    // again there would send the Supabase calls through production's upgrade-insecure-requests.
    const file = await signedInState(browser, baseURL as string, "admin");
    const state = JSON.parse(readFileSync(file, "utf8")) as {
      origins: { origin: string; localStorage: { name: string; value: string }[] }[];
    };
    const origin = new URL(PROD as string).origin;
    const ctx = await browser.newContext({
      baseURL: PROD,
      ignoreHTTPSErrors: true,
      storageState: { cookies: [], origins: state.origins.map((o) => ({ ...o, origin })) },
    });
    try {
      const page = await ctx.newPage();
      const errors: string[] = [];
      page.on("console", (m) => {
        if (m.type() === "error" && !ignorableConsoleLine(m.text(), m.location().url)) {
          errors.push(`${m.text()} @ ${m.location().url}`);
        }
      });
      page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
      await page.goto("/admin/agent");
      await expect(page.getByRole("heading", { level: 1, name: "Agent diagnostics" })).toBeVisible();
      await expect(page.getByTestId("agent-runtime-summary")).toHaveText(RUNTIME_SUMMARY);
      // The media answer block under production's CSP: img-src and frame-src must admit exactly these hosts.
      await page.getByRole("button", { name: "Run ping" }).click();
      const media = page.getByTestId("agent-block-media");
      await expect(media).toBeVisible({ timeout: 45_000 });
      for (const id of ["media-commons", "media-pmc_figure"]) {
        const img = media.getByTestId(id).locator("img");
        await img.scrollIntoViewIfNeeded();
        await expect
          .poll(() => img.evaluate((i: HTMLImageElement) => i.complete && i.naturalWidth > 0), { timeout: 20_000 })
          .toBe(true);
      }
      // The video stays a click-to-load facade: the page makes no contact with YouTube.
      await expect(media.getByTestId("media-youtube").getByRole("button", { name: /^Play video: / })).toBeVisible();
      await expect(media.getByTestId("media-youtube").locator("iframe")).toHaveCount(0);
      await expect(page.getByTestId("ping-confirm")).toBeVisible({ timeout: 45_000 });
      await page.getByRole("button", { name: "Discard" }).click();
      await expect(page.getByTestId("ping-done")).toHaveText("Not recorded.", { timeout: 30_000 });
      await page.evaluate(() => document.fonts.ready);
      expect(errors, "console errors under the production CSP").toEqual([]);
    } finally {
      await ctx.close();
    }
  });
});
