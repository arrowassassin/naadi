import { execFileSync } from "node:child_process";
import path from "node:path";
import type { Browser, BrowserContext, Page, TestInfo } from "@playwright/test";
import { callbackPath, canMintUsers, emailFor, runUsers, toLink } from "./auth-users";
import {
  type ConsoleGuard,
  expect,
  expectNoAxeViolations,
  expectNoHorizontalOverflow,
  quiet,
  settle,
  snapSegment,
  TOOL_CSP_NOISE,
  test,
  trackApi,
} from "./fixtures";

/**
 * Segment 15b: image generation against the real API, database and auth server, with generation stubbed at the API
 * (GENERATION_FAKE: an in-process fake client and an in-memory media store served by the app itself; never a call to
 * the Space). Covers the labelled item in the Library's "Generated" view (chip, label-first alt text, caption), the
 * failed and paused states, delete, the admin approval that publishes a labelled figure in a section, and that the
 * feature is hidden on a server without the generation token (the APP_ENV=prod server).
 */
test.skip(!canMintUsers(), "signed-in tests need the service role (run pnpm e2e:live-auth against the Space)");

const LABEL = "AI-generated illustration — not clinical reference";
const API_DIR = path.resolve(import.meta.dirname, "../../api");

const snap = (page: Page, info: TestInfo, guard: ConsoleGuard, state: string) =>
  guard.duringTool(async () => {
    await snapSegment(page, info, guard, "15b", state, false);
    await page.waitForTimeout(300);
  });

function who(info: TestInfo, kind: string): string {
  const run = String.fromCharCode(97 + (info.repeatEachIndex % 26), 97 + (info.retry % 26));
  return `naadi-e2e-${kind}${info.project.name.replace(/[^a-z]/g, "")}${run}-${process.env.E2E_RUN_ID}@example.com`;
}

function watchConsole(page: Page) {
  const errors: string[] = [];
  let tool = 0;
  page.on("console", (m) => {
    if (m.type() === "error" && !(tool > 0 && m.text() === TOOL_CSP_NOISE)) errors.push(m.text());
  });
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  const guard: ConsoleGuard = {
    allowHttpError: () => {},
    duringTool: async (fn) => {
      tool++;
      try {
        return await fn();
      } finally {
        await page.waitForTimeout(300);
        tool--;
      }
    },
  };
  return { errors, guard };
}

async function user(browser: Browser, baseURL: string, info: TestInfo, email: string) {
  runUsers(["delete", "--email", email]);
  const contexts: BrowserContext[] = [];
  const open = async (to: string) => {
    const context = await browser.newContext({ ...info.project.use, baseURL });
    contexts.push(context);
    const page = await context.newPage();
    trackApi(page);
    const link = toLink(runUsers(["link", "--email", email]));
    await page.goto(callbackPath(link, to));
    await page.waitForURL((u) => u.pathname === to, { timeout: 30_000 });
    await settle(page);
    return { page, ...watchConsole(page) };
  };
  const done = async () => {
    try {
      for (const c of contexts) await c.close().catch(() => undefined);
    } finally {
      runUsers(["delete", "--email", email]);
    }
  };
  return { open, done };
}

async function api<T>(page: Page, method: string, url: string, body?: unknown): Promise<{ status: number; json: T }> {
  return page.evaluate(
    async ({ method, url, body }) => {
      const raw = localStorage.getItem("naadi.auth") ?? "{}";
      const token = (JSON.parse(raw) as { access_token?: string }).access_token ?? "";
      const res = await fetch(url, {
        method,
        headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      return { status: res.status, json: res.status === 204 ? null : ((await res.json()) as unknown) };
    },
    { method, url, body },
  ) as Promise<{ status: number; json: T }>;
}

interface Item {
  id: string;
  status: string;
  url: string | null;
}

function world(cmd: "make" | "drop", tag: string): { chapter: string; section: string; title: string } {
  const out = execFileSync(
    "uv",
    ["--directory", API_DIR, "run", "--frozen", "python", "-m", "tests.generation.e2e_world", cmd, "--tag", tag],
    { encoding: "utf8", env: { ...process.env }, timeout: 60_000 },
  );
  return JSON.parse(out.trim().split("\n").pop() ?? "{}");
}

test.describe("generated media", () => {
  test("a student sees a labelled image, can open the failed state, and deletes with a confirm", async ({
    browser,
    baseURL,
  }, info) => {
    const s = await user(browser, baseURL as string, info, who(info, "gen"));
    try {
      const { page, errors, guard } = await s.open("/");
      const prompt = `Nephron cross-section ${info.project.name}`;
      const made = await api<Item>(page, "POST", "/api/generated/stub", { prompt });
      expect(made.status).toBe(200);
      const bad = await api<{ error: { code: string } }>(page, "POST", "/api/generated/stub", {
        prompt: "FAILME heart",
      });
      expect(bad.status).toBe(502);

      await quiet(page);
      await page.goto("/generated");
      await expect(page.getByRole("heading", { level: 1, name: "Generated by the assistant" })).toBeVisible();
      const cards = page.getByTestId("generated-card");
      await expect(cards).toHaveCount(2);

      const ready = cards.filter({ has: page.locator("[data-status=ready]") });
      await expect(ready.getByText(LABEL)).toBeVisible(); // the AI chip: a word and an icon, always visible
      const img = ready.getByRole("img");
      await expect(img).toHaveAttribute("alt", `${LABEL}: ${prompt}`);
      await expect.poll(() => img.evaluate((el: HTMLImageElement) => el.naturalWidth)).toBeGreaterThan(0);
      await expect(ready.getByText(`Prompt: ${prompt}`)).toBeVisible();

      const failed = cards.filter({ has: page.locator("[data-status=failed]") });
      await expect(failed.getByText("Generation did not finish")).toBeVisible();
      await expect(failed.getByText("The generation failed. Try again in a moment.")).toBeVisible();
      await expect(failed.getByText(LABEL)).toBeVisible();

      await settle(page);
      await expectNoAxeViolations(page);
      await expectNoHorizontalOverflow(page);
      await snap(page, info, guard, "library");

      // Delete asks first and removes the file with the row.
      await ready.getByRole("button", { name: /Delete/ }).click();
      const dialog = page.getByRole("dialog", { name: "Delete this image?" });
      await expect(dialog).toBeVisible();
      await snap(page, info, guard, "delete-confirm");
      await expect(cards).toHaveCount(2); // nothing is deleted before the confirm
      await dialog.getByRole("button", { name: "Delete", exact: true }).click();
      await expect(cards).toHaveCount(1);
      const gone = await page.request.get((made.json.url as string) ?? "/");
      expect(gone.status()).toBe(404);

      await quiet(page);
      // The one expected failure is the stubbed generation that the test itself asked to fail (502).
      expect(errors.filter((e) => !e.includes("502"))).toEqual([]);
    } finally {
      await s.done();
    }
  });

  test("a paused generation says so with the reset time, and keeps the AI label", async ({
    browser,
    baseURL,
  }, info) => {
    const s = await user(browser, baseURL as string, info, who(info, "genp"));
    try {
      const { page, errors, guard } = await s.open("/");
      // The pause is a shared server state (it would stop every other test), so this state is stubbed at the API.
      await page.route("**/api/generated?*", (route) =>
        route.fulfill({
          json: {
            items: [
              {
                id: "6f1e5a2c-0c1b-4c55-9a55-0e8f2b6f7a10",
                kind: "image",
                status: "paused",
                title: null,
                prompt: "Heart valves",
                model: "m",
                width: 1024,
                height: 768,
                duration_s: null,
                url: null,
                ai_generated: true,
                error: "generation_paused",
                paused_until: new Date(Date.now() + 40 * 60_000).toISOString(),
                published: false,
                created_at: new Date().toISOString(),
              },
            ],
            next_before: null,
          },
        }),
      );
      await quiet(page);
      await page.goto("/generated");
      const card = page.getByTestId("generated-card");
      await expect(card.getByText("Generation is paused")).toBeVisible();
      await expect(card.getByText(/Generation resumes at \d/)).toBeVisible();
      await expect(card.getByText(LABEL)).toBeVisible();
      await settle(page);
      await expectNoAxeViolations(page);
      await expectNoHorizontalOverflow(page);
      await snap(page, info, guard, "paused");
      await quiet(page);
      expect(errors).toEqual([]);
    } finally {
      await s.done();
    }
  });

  test("an admin approves an image: a labelled figure lands in the chosen section", async ({
    browser,
    baseURL,
  }, info) => {
    const tag = `g${info.workerIndex}${info.parallelIndex}${Date.now().toString(36)}`.slice(0, 20);
    const target = world("make", tag);
    const student = await user(browser, baseURL as string, info, who(info, "geng"));
    const admin = await user(browser, baseURL as string, info, emailFor("admin"));
    try {
      const sp = (await student.open("/")).page;
      const prompt = `Loop of Henle ${tag}`;
      const made = await api<Item>(sp, "POST", "/api/generated/stub", { prompt });
      expect(made.status).toBe(200);

      const { page, errors, guard } = await admin.open("/admin/generated");
      await expect(page.getByRole("heading", { level: 1, name: "Generated images to review" })).toBeVisible();
      const card = page.getByTestId("admin-generated-card").filter({ hasText: prompt });
      await expect(card.getByText(LABEL)).toBeVisible();
      await expect
        .poll(() => card.getByRole("img").evaluate((el: HTMLImageElement) => el.naturalWidth))
        .toBeGreaterThan(0);
      await settle(page);
      await expectNoAxeViolations(page);
      await snap(page, info, guard, "admin-list");

      await card.getByRole("button", { name: "Publish as a figure" }).click();
      const dialog = page.getByRole("dialog", { name: "Publish as a chapter figure" });
      await expect(dialog).toBeVisible();
      await dialog.getByLabel("Chapter").selectOption({ label: target.title });
      await dialog.getByLabel("Section").selectOption({ label: "Target section 1" });
      await dialog.getByLabel("Caption").fill("The loop of Henle");
      await expect(dialog.getByText("The AI label is added to the end of the caption automatically.")).toBeVisible();
      await snap(page, info, guard, "admin-dialog");
      await dialog.getByRole("button", { name: "Publish", exact: true }).click();
      await expect(dialog).toBeHidden();
      await expect(card).toHaveCount(0); // approved items leave the review list

      const chapter = await api<{
        sections: { id: string; body: { content: { type: string; attrs?: { mediaId: string; caption: string } }[] } }[];
        media: Record<string, { caption: string | null }>;
      }>(page, "GET", `/api/chapters/${target.chapter}`);
      expect(chapter.status).toBe(200);
      const section = chapter.json.sections.find((x) => x.id === target.section);
      const figure = section?.body.content.at(-1);
      expect(figure?.type).toBe("figure");
      const mediaId = figure?.attrs?.mediaId as string;
      expect(mediaId).toMatch(/^gen-/);
      expect(chapter.json.media[mediaId]?.caption).toBe(`The loop of Henle — ${LABEL}`);
      expect(figure?.attrs?.caption.endsWith(LABEL)).toBe(true);

      // The student's own copy is marked published.
      const mine = await api<{ items: { published: boolean }[] }>(sp, "GET", "/api/generated");
      expect(mine.json.items[0]?.published).toBe(true);
      await quiet(page);
      expect(errors).toEqual([]);
    } finally {
      await student.done();
      await admin.done();
      world("drop", tag);
    }
  });
});

test.describe("on a server without the generation token", () => {
  const PROD = process.env.E2E_PROD_URL;
  test.skip(!PROD, "needs the local APP_ENV=prod API");

  test("generation reports not configured, its flag is off and /generated sends a student home", async ({
    browser,
    baseURL,
  }, info) => {
    const origin = new URL(PROD as string).origin;
    // A throwaway student of this test (the shared roles depend on onboarding, which is not under test here).
    const s = await user(browser, baseURL as string, info, who(info, "genq"));
    const main = (await s.open("/")).page;
    const auth = await main.evaluate(() => localStorage.getItem("naadi.auth") ?? "");
    const state = { origins: [{ origin: baseURL as string, localStorage: [{ name: "naadi.auth", value: auth }] }] };
    const ctx = await browser.newContext({
      baseURL: PROD,
      ignoreHTTPSErrors: true,
      storageState: { cookies: [], origins: state.origins.map((o) => ({ ...o, origin })) },
    });
    try {
      const page = await ctx.newPage();
      const me = page.waitForResponse((r) => r.url().endsWith("/api/me"));
      await page.goto("/generated");
      expect((await (await me).json()).features.generation).toBe(false);
      await page.waitForURL((u) => u.pathname !== "/generated");
      const status = await api<{ state: string }>(page, "GET", "/api/generation/status");
      expect(status.json.state).toBe("not_configured");
      const health = await page.request.get("/api/health");
      const generation = (await health.json()).components.generation;
      expect(generation.configured).toBe(false);
    } finally {
      await ctx.close();
      await s.done();
    }
  });
});
