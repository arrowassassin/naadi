import { execFileSync } from "node:child_process";
import path from "node:path";
import type { Browser, BrowserContext, Page, TestInfo } from "@playwright/test";
import { canMintUsers, type E2ERole } from "./auth-users";
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
import { signedInState } from "./session";

/**
 * Segment 21: the question review UI (/admin/review) against the real API, database and auth server, with the
 * authoring model calls answered by the API's scripted fake (AUTHORING_FAKE). Covers drafting from the UI, the
 * blind two-admin review, the disagreement and adjudication path, the seed spot-check, the student's no-access,
 * and every state of the queue and the item view with frozen-DOM axe.
 */
test.skip(!canMintUsers(), "signed-in tests need the service role (run pnpm e2e:live-auth against the Space)");

const guarded = test.extend<{ consoleErrors: ConsoleGuard }>({
  // Replaces the shared fixture of the same name with this spec's guard (see watchConsole).
  consoleErrors: async ({ page }, use) => {
    const w = watchConsole(page);
    await use(w.guard);
    w.verify();
  },
});

const ADMIN = "/api/admin/review";
const created: string[] = [];
const API_DIR = path.resolve(import.meta.dirname, "../../api");

/**
 * The scripted author writes one template for every topic, so the linter's originality rule (L16) drops a draft once
 * a fake item is embedded. This forgets the fake items' vectors before a run (and retires a test's own items after
 * it), through a test-only helper on the local database.
 */
function fakeItems(action: "embeddings" | "retire" | "seed-ids", ids: string[] = []): string {
  const db = process.env.E2E_DATABASE_URL ?? process.env.DATABASE_URL;
  if (!db) return "{}";
  return execFileSync(
    "uv",
    [
      "--directory",
      API_DIR,
      "run",
      "--frozen",
      "python",
      "-m",
      "tests.authoring.e2e_fake_items",
      action,
      "--db",
      db,
      ...(ids.length ? ["--ids", ids.join(",")] : []),
    ],
    { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"], timeout: 60_000 },
  );
}

const signedInAs = (role: E2ERole) => ({
  // The stubs below (slow item, refused checks) only see a page's requests when no service worker controls it
  serviceWorkers: "block" as const,
  storageState: async (
    { browser, baseURL }: { browser: Browser; baseURL: string | undefined },
    use: (path: string) => Promise<void>,
  ) => use(await signedInState(browser, baseURL as string, role)),
});

const PROJECTS = ["webkit-ipad-portrait", "webkit-ipad-landscape", "webkit-phone-390", "chromium-laptop-1440"];
// Topics with seeded study material (the fake author grounds its draft on it), one per project and test.
const TOPICS = [
  "Growth & puberty",
  "Adolescent health",
  "Bone disorders & bone tumors",
  "Normal aging & geriatrics",
  "Orthopedics, fractures & sports injuries",
  "Care of the well patient & preventive care",
  "Burns & wound healing",
  "Inflammatory dermatoses",
  "Aorta, PAD & vascular disease",
  "Acute abdomen, bowel obstruction & hernias",
  "Child psychiatry & eating disorders",
  "Law & health systems (EMTALA, malpractice, insurance)",
  "Developmental milestones",
  "Normal newborn & neonatal care",
  "Prevention levels & screening programs",
  "Blood products & transfusion medicine",
  "GI bleeding",
  "Pleural disease & pneumothorax",
  "Vaginitis, PID & sexually transmitted infections",
  "Professionalism, gifts, conflicts of interest & impaired colleagues",
];
const projectIndex = (info: TestInfo) => Math.max(0, PROJECTS.indexOf(info.project.name));
// A topic no other project or test drafts at the same time (the run is found by its topic).
const topicFor = (info: TestInfo, slot: number) => TOPICS[(projectIndex(info) + 4 * slot) % TOPICS.length] as string;

const snap = (page: Page, info: TestInfo, guard: ConsoleGuard, state: string) =>
  guard.duringTool(async () => {
    await snapSegment(page, info, guard, "21", `review-${state}`, false);
    await page.waitForTimeout(200);
  });

/**
 * The console guard of this spec. Same rules as the shared `consoleErrors` fixture, with two lines it does not know
 * yet (they come from the hardening segment's headers, not from this screen): the Trusted Types report-only notice
 * (the shared SafeHtml renderer parses with DOMParser; csp.spec.ts collects those from the violation events) and the
 * CSP report POST that a navigation cancels.
 */
const REPORT_ONLY = /\[Report Only\]|report-only|Report-Only/i;
function watchConsole(page: Page) {
  const errors: string[] = [];
  const allowed: { urlPart: string; status: number }[] = [];
  const network: { url: string; status: number }[] = [];
  let tool = 0;
  page.on("console", (m) => {
    if (m.type() !== "error") return;
    const text = m.text();
    if (REPORT_ONLY.test(text) || m.location().url.includes("/api/csp-report")) return;
    if (tool > 0 && text === TOOL_CSP_NOISE) return;
    const status = /Failed to load resource: the server responded with a status of (\d{3})/.exec(text);
    if (status && allowed.some((a) => a.status === Number(status[1]))) return;
    errors.push(text);
  });
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  page.on("response", (res) => {
    if (res.status() === 429) errors.push(`HTTP 429 ${new URL(res.url()).pathname}`);
    if (res.status() >= 400 && !res.url().includes("/api/csp-report")) {
      network.push({ url: res.url(), status: res.status() });
    }
  });
  const guard: ConsoleGuard = {
    allowHttpError: (e) => allowed.push({ urlPart: e.urlPart, status: e.status }),
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
  const verify = () => {
    expect(errors, "console errors").toEqual([]);
    const unexpected = network.filter((n) => !allowed.some((a) => a.status === n.status && n.url.includes(a.urlPart)));
    expect(unexpected, "unexpected failed responses").toEqual([]);
  };
  return { errors, guard, verify };
}

/** Another signed-in user in its own context (the second and third reviewer, the student). */
async function openAs(browser: Browser, baseURL: string, info: TestInfo, role: E2ERole, path: string) {
  const context: BrowserContext = await browser.newContext({
    ...info.project.use,
    baseURL,
    storageState: await signedInState(browser, baseURL, role),
  });
  const page = await context.newPage();
  trackApi(page);
  const watch = watchConsole(page);
  await page.goto(path);
  await settle(page);
  return { page, context, ...watch };
}

async function token(page: Page): Promise<string> {
  const t = await page.evaluate(() => {
    const raw = localStorage.getItem("naadi.auth");
    return raw ? (JSON.parse(raw) as { access_token: string }).access_token : null;
  });
  expect(t).toBeTruthy();
  return `Bearer ${t}`;
}

async function api<T = unknown>(page: Page, method: "GET" | "POST", path: string, data?: unknown) {
  const res = await page.request.fetch(path, {
    method,
    headers: { authorization: await token(page) },
    ...(data === undefined ? {} : { data }),
  });
  const text = await res.text();
  return { status: res.status(), json: (text ? JSON.parse(text) : null) as T };
}

interface Detail {
  id: string;
  status: string;
  answer: string;
  origin: string;
  can_review: { allowed: boolean; reason: string | null };
  gates: { lint_ok: boolean; source_ok: boolean; cross_check_ok: boolean; independent: boolean };
  lint: { severity: string }[];
}

const group = (page: Page, name: RegExp | string) => page.getByRole("group", { name });

/** Rate every part of the form (Part C). */
async function rate(page: Page, over: { accuracy?: string; single?: string } = {}) {
  await group(page, "Accuracy")
    .getByText(over.accuracy ?? "Correct", { exact: true })
    .click();
  await group(page, "Is there exactly one best answer?")
    .getByText(over.single ?? "Yes", { exact: true })
    .click();
  await group(page, "Cognitive level").getByText("Application", { exact: true }).click();
  await group(page, /^Difficulty/)
    .getByText("Medium", { exact: true })
    .click();
  await group(page, /^Are the taxonomy tags right/)
    .getByText("Yes", { exact: true })
    .click();
}

/**
 * Draft one item from the UI and return its id. The scripted author grounds its draft on the topic's study
 * excerpts and the linter may drop a draft for one topic and not another, so the next topic is tried when a run
 * ends without an item (the run's outcome is shown in the panel either way).
 */
async function draft(page: Page, consoleGuard: ConsoleGuard, info: TestInfo, n: number): Promise<string> {
  // Runs are served one at a time for the whole server, so a test may wait for the other projects' runs.
  test.setTimeout(360_000);
  consoleGuard.allowHttpError({ urlPart: `${ADMIN}/runs`, status: 409 });
  await page.goto("/admin/review");
  await settle(page);
  const panel = page.getByTestId("draft-panel");
  await panel.getByRole("button", { name: /Draft new items/ }).click();
  for (let attempt = 0; attempt < 2; attempt++) {
    const topic = topicFor(info, n + 2 * attempt);
    await panel.getByLabel("Search topics").fill(topic.slice(0, 12));
    const select = page.getByTestId("draft-topic");
    await expect(select).toBeVisible();
    const value = await select.evaluate((el, label) => {
      const o = [...(el as HTMLSelectElement).options].find((x) => x.text.startsWith(label));
      return o?.value ?? null;
    }, topic);
    expect(value, `topic "${topic}" is offered`).toBeTruthy();
    await select.selectOption(value as string);
    const rows = panel.getByTestId("run-row");
    const known = new Set(await rows.evaluateAll((els) => els.map((e) => e.getAttribute("data-run-id"))));
    // One run at a time server-wide: another project's run makes the button wait, or the server answer 409.
    for (let i = 0; i < 90; i++) {
      const start = panel.getByRole("button", { name: "Start drafting" });
      await expect(start).toBeEnabled({ timeout: 180_000 });
      fakeItems("embeddings");
      const [res] = await Promise.all([
        page.waitForResponse((r) => r.url().endsWith(`${ADMIN}/runs`) && r.request().method() === "POST"),
        start.click(),
      ]);
      if (res.status() === 202) break;
      expect(res.status()).toBe(409);
      await page.waitForTimeout(2000);
    }
    // The panel polls while the run is going and then lists it: a new row for this topic (newest first).
    const label = topic.slice(0, 30);
    let found: { id: string | null; href: string | null } | null = null;
    await expect
      .poll(
        async () => {
          const all = await rows.evaluateAll((els) =>
            els.map((e) => ({
              id: e.getAttribute("data-run-id"),
              text: e.textContent ?? "",
              href: e.querySelector("a")?.getAttribute("href") ?? null,
            })),
          );
          const mine = all.find((r) => r.id && !known.has(r.id) && r.text.includes(label));
          found = mine ? { id: mine.id, href: mine.href } : null;
          return found !== null;
        },
        { timeout: 180_000, intervals: [1000], message: "the drafting run finished" },
      )
      .toBe(true);
    const href = (found as { href: string | null } | null)?.href;
    const itemId = href?.split("/").pop();
    if (itemId) {
      created.push(itemId);
      return itemId;
    }
  }
  throw new Error("no topic produced a draft");
}

test.describe("admin: queue and drafting", () => {
  guarded.use(signedInAs("admin"));

  // Items a test drafted leave the student pool and the originality index afterwards (tests run in order per worker).
  guarded.afterEach(() => {
    fakeItems("retire", created.splice(0));
  });

  guarded("the queue, its filters and the draft panel", async ({ page, consoleErrors }, info) => {
    await page.goto("/admin/review");
    await settle(page);
    await expect(page.getByRole("heading", { name: "Question review", level: 1 })).toBeVisible();
    await expect(page.getByTestId("count-seed_spot_check")).not.toHaveText("–");
    expect(Number(await page.getByTestId("count-seed_spot_check").textContent())).toBeGreaterThan(0);
    await expect(page.getByRole("button", { name: "Review next" })).toHaveAttribute("data-variant", "primary");
    expect(await page.locator('[data-variant="primary"]').count()).toBe(1);
    await expectNoHorizontalOverflow(page);
    await expectNoAxeViolations(page);
    await quiet(page);
    await snap(page, info, consoleErrors, "queue-ai-drafts");

    await page.getByRole("button", { name: /Seed spot-check/ }).click();
    await expect(page.getByTestId("queue-list")).toBeVisible();
    await expect(page.getByTestId("queue-row").first()).toContainText("Seed question");
    await expect(page.getByTestId("queue-row").first()).toContainText("Lint errors");
    await expect(page).toHaveURL(/filter=seed_spot_check/);
    await expectNoAxeViolations(page);
    await quiet(page);
    await snap(page, info, consoleErrors, "queue-seed");

    await page.getByRole("button", { name: /Psychometric flags/ }).click();
    await expect(page.getByRole("button", { name: /Psychometric flags/ })).toHaveAttribute("aria-current", "true");
    await quiet(page);
    await snap(page, info, consoleErrors, "queue-psychometric");

    await page.goto("/admin/review");
    await settle(page);
    const panel = page.getByTestId("draft-panel");
    await panel.getByRole("button", { name: /Draft new items/ }).click();
    await expect(panel.getByLabel("Topic", { exact: true })).toBeVisible();
    await expect(panel.getByRole("button", { name: "Start drafting" })).toBeDisabled();
    await expect(panel).toContainText("Choose a topic to start drafting");
    await expect(panel.getByRole("button", { name: "Start drafting" })).toHaveAttribute("data-variant", "assistant");
    await expectNoHorizontalOverflow(page);
    await expectNoAxeViolations(page);
    await quiet(page);
    await snap(page, info, consoleErrors, "draft-panel");
  });

  guarded("loading, empty, error and offline states of the queue", async ({ page, context, consoleErrors }, info) => {
    consoleErrors.allowHttpError({ urlPart: `${ADMIN}/items`, status: 500 });
    await page.goto("/admin/review");
    await settle(page);
    await quiet(page);

    // Loading: the list request is held.
    let release: () => void = () => {};
    const gate = new Promise<void>((r) => {
      release = r;
    });
    await page.route(`**${ADMIN}/items?filter=adjudication*`, async (route) => {
      await gate;
      await route.continue();
    });
    await page.getByRole("button", { name: /^Adjudication/ }).click();
    await expect(page.getByTestId("queue-loading")).toBeVisible();
    await expectNoAxeViolations(page);
    await snap(page, info, consoleErrors, "queue-loading");
    release();
    await expect(page.getByTestId("queue-loading")).toBeHidden();
    await page.unroute(`**${ADMIN}/items?filter=adjudication*`);

    // Empty: a filter with nothing in it (answered with the server's own empty shape).
    await page.route(`**${ADMIN}/items?filter=needs_check*`, (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [] }) }),
    );
    await page.getByRole("button", { name: /^Needs independent check/ }).click();
    await expect(page.getByTestId("queue-empty")).toBeVisible();
    await expect(page.getByRole("button", { name: "Review next" })).toBeDisabled();
    await expectNoAxeViolations(page);
    await snap(page, info, consoleErrors, "queue-empty");
    await page.unroute(`**${ADMIN}/items?filter=needs_check*`);

    // Error: a 500 with the server's envelope, retry and request id.
    await page.route(`**${ADMIN}/items?filter=psychometric_flags*`, (route) =>
      route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ error: { code: "internal_error", message: "boom", request_id: "req-e2e-1" } }),
      }),
    );
    await page.getByRole("button", { name: /^Psychometric flags/ }).click();
    const alert = page.getByRole("alert").filter({ hasText: "The review queue could not be loaded" });
    await expect(alert).toBeVisible();
    await expect(alert).toContainText("req-e2e-1");
    await expectNoAxeViolations(page);
    await snap(page, info, consoleErrors, "queue-error");
    await page.unroute(`**${ADMIN}/items?filter=psychometric_flags*`);
    await alert.getByRole("button", { name: "Try again" }).click();
    await expect(alert).toBeHidden();

    // Offline.
    await quiet(page);
    await context.setOffline(true);
    await expect(page.getByText(/You're offline\. The queue can't be refreshed/)).toBeVisible();
    await snap(page, info, consoleErrors, "queue-offline");
    await context.setOffline(false);
  });

  guarded(
    "draft, approve with a second review, the second admin sees nothing until submitting, publish, student pool",
    async ({ page, browser, baseURL, consoleErrors }, info) => {
      const id = await draft(page, consoleErrors, info, 0);
      // The run shows in the panel with a link to the item and a status word.
      await expect(page.getByTestId("run-row").first()).toBeVisible();

      await page.goto(`/admin/review/${id}`);
      await settle(page);
      await expect(page.getByRole("heading", { name: "Review this question", level: 1 })).toBeVisible();
      await expect(page.getByTestId("review-preview")).toBeVisible();
      await expect(
        page.getByTestId("review-preview").getByRole("list", { name: "Answer options" }).getByRole("listitem"),
      ).toHaveCount(5);
      await expect(page.getByTestId("review-preview").getByText("Key", { exact: true })).toBeVisible();
      await expect(page.getByTestId("review-preview").getByText("Admin view: key shown")).toBeVisible();
      // Provenance (violet), lint without errors, the solver agrees, every gate passed.
      await expect(page.getByText("Written by the assistant")).toBeVisible();
      await expect(page.locator('[data-severity="error"]')).toHaveCount(0);
      await expect(page.getByTestId("review-solver")).toContainText("which matches the key");
      await expect(page.getByTestId("review-solver")).toContainText("Different model family from the author");
      await expect(page.getByTestId("review-gates").locator('[data-passed="true"]')).toHaveCount(4);
      await expect(page.getByTestId("review-all-clear")).toBeVisible();
      // Approve is the one lime button and stays off until every rating is set, with the reason in words.
      const approve = page.getByRole("button", { name: "Approve" });
      await expect(approve).toBeDisabled();
      await expect(page.getByTestId("review-why")).toContainText("rate every part of the form first");
      expect(await page.locator('[data-variant="primary"]:not([disabled])').count()).toBe(0);
      await expectNoHorizontalOverflow(page);
      await expectNoAxeViolations(page);
      await quiet(page);
      await snap(page, info, consoleErrors, "item-gates-green");

      // Ratings that contradict an approval switch Approve off but allow Reject.
      await rate(page, { accuracy: "Incorrect" });
      await expect(approve).toBeDisabled();
      await expect(page.getByTestId("review-why")).toContainText("say the item is incorrect");
      await expect(page.getByRole("button", { name: "Reject" })).toBeEnabled();
      await snap(page, info, consoleErrors, "item-contradiction");
      await rate(page);
      await expect(approve).toBeEnabled();
      await expect(approve).toHaveAttribute("data-variant", "primary");
      await page.getByLabel("Note (optional)").fill("Grounded and clear.");
      await page.getByRole("checkbox", { name: /Ask for a second review/ }).check();
      await expectNoAxeViolations(page);
      await quiet(page);
      await snap(page, info, consoleErrors, "item-form-ready");
      await approve.click();
      const outcome = page.getByTestId("review-outcome");
      await expect(outcome).toHaveAttribute("data-outcome", "recorded");
      await expect(outcome).toContainText("waits for a second reviewer");
      await expect(outcome.getByRole("button", { name: "Review the next question" })).toHaveAttribute(
        "data-variant",
        "primary",
      );
      await expectNoAxeViolations(page);
      await quiet(page);
      await snap(page, info, consoleErrors, "item-outcome-waiting");

      // Reload: the same admin cannot fill a second slot; their own review is shown.
      await page.reload();
      await settle(page);
      await expect(page.getByTestId("review-entry")).toHaveCount(1);
      await expect(page.getByTestId("review-resolution")).toContainText("1 of 2 reviews");
      await expect(page.getByRole("button", { name: "Reject" })).toBeDisabled();
      await expect(page.getByTestId("review-why")).toContainText("You have already reviewed this question");
      await quiet(page);
      await snap(page, info, consoleErrors, "item-reviewed-by-me");

      // The second admin: blind. Nothing of the first review is visible.
      const b = await openAs(browser, baseURL as string, info, "adminb", `/admin/review/${id}`);
      try {
        await expect(b.page.getByTestId("review-hidden")).toContainText(
          "A second review has been submitted. You'll see it once you have submitted yours.",
        );
        await expect(b.page.getByTestId("review-entry")).toHaveCount(0);
        await expect(b.page.getByText("Grounded and clear.")).toHaveCount(0);
        await expectNoAxeViolations(b.page);
        await quiet(b.page);
        await snap(b.page, info, b.guard, "item-blind");
        await rate(b.page);
        await b.page.getByRole("button", { name: "Approve" }).click();
        await expect(b.page.getByTestId("review-outcome")).toHaveAttribute("data-outcome", "published");
        await expect(b.page.getByTestId("review-outcome")).toContainText("published");
        await quiet(b.page);
        await snap(b.page, info, b.guard, "item-outcome-published");
        // Both reviews are visible now.
        await b.page.reload();
        await settle(b.page);
        await expect(b.page.getByTestId("review-entry")).toHaveCount(2);
        await expect(b.page.getByTestId("review-psych")).toBeVisible();
        await expectNoAxeViolations(b.page);
        await quiet(b.page);
        await snap(b.page, info, b.guard, "item-published-psych-empty");
        b.verify();
      } finally {
        await b.context.close();
      }

      // Published: the student can build a block on it.
      const s = await openAs(browser, baseURL as string, info, "student", "/");
      try {
        const res = await api(s.page, "POST", "/api/blocks/single", { question_id: id });
        expect(res.status).toBe(201);
      } finally {
        await s.context.close();
      }
    },
  );

  guarded(
    "two admins disagree, a third adjudicates and retires the item",
    async ({ page, browser, baseURL, consoleErrors }, info) => {
      const id = await draft(page, consoleErrors, info, 1);
      await page.goto(`/admin/review/${id}`);
      await settle(page);
      await rate(page);
      await page.getByRole("checkbox", { name: /Ask for a second review/ }).check();
      await page.getByRole("button", { name: "Approve" }).click();
      await expect(page.getByTestId("review-outcome")).toHaveAttribute("data-outcome", "recorded");

      const b = await openAs(browser, baseURL as string, info, "adminb", `/admin/review/${id}`);
      try {
        await rate(b.page, { accuracy: "Incorrect" });
        await b.page.getByLabel("Note (optional)").fill("The key is wrong.");
        await b.page.getByRole("button", { name: "Reject" }).click();
        await expect(b.page.getByTestId("review-outcome")).toHaveAttribute("data-outcome", "adjudication");
        await expect(b.page.getByTestId("review-outcome")).toContainText("A third admin will decide");
        await quiet(b.page);
        await snap(b.page, info, b.guard, "item-outcome-adjudication");
        // A reviewer cannot adjudicate their own item.
        await b.page.reload();
        await settle(b.page);
        await expect(b.page.getByTestId("review-adjudication")).toContainText("third admin has to decide");
        b.verify();
      } finally {
        await b.context.close();
      }

      const c = await openAs(browser, baseURL as string, info, "adminc", "/admin/review?filter=adjudication");
      try {
        await expect(c.page.locator(`[data-item-id="${id}"]`)).toBeVisible();
        await expect(c.page.locator(`[data-item-id="${id}"]`)).toContainText("Needs a deciding review");
        await c.page.goto(`/admin/review/${id}`);
        await settle(c.page);
        await expect(c.page.getByTestId("review-adjudication")).toContainText("Your decision is final");
        await expect(c.page.getByTestId("review-entry")).toHaveCount(2);
        await expect(c.page.getByTestId("review-note").first()).toBeVisible();
        await expectNoAxeViolations(c.page);
        await quiet(c.page);
        await snap(c.page, info, c.guard, "item-adjudication");
        await rate(c.page, { accuracy: "Incorrect" });
        await c.page.getByRole("button", { name: "Reject" }).click();
        await expect(c.page.getByTestId("review-outcome")).toHaveAttribute("data-outcome", "rejected");
        await quiet(c.page);
        await snap(c.page, info, c.guard, "item-outcome-rejected");
        const detail = await api<Detail>(c.page, "GET", `${ADMIN}/items/${id}`);
        expect(detail.json.status).toBe("retired");
        c.verify();
      } finally {
        await c.context.close();
      }
    },
  );

  guarded(
    "seed spot-check: run the blind solver, approve, the item is marked human-reviewed",
    async ({ page, consoleErrors }, info) => {
      await page.goto("/admin/review?filter=seed_spot_check");
      await settle(page);
      // Unreviewed seed items the scripted solver can confirm (it always answers A), from the database in one query
      // (the review API is rate limited), split between the projects so two never take the same item. The solver
      // test only mocks routes, so it may share this project's item.
      const keyedA =
        (JSON.parse(fakeItems("seed-ids").trim().split("\n").pop() ?? "{}") as { ids?: string[] }).ids ?? [];
      const mine = keyedA.filter(
        (q) => [...q].reduce((n, c) => n + c.charCodeAt(0), 0) % PROJECTS.length === projectIndex(info),
      );
      expect(mine.length, "seed items this project can review").toBeGreaterThan(1);
      let id = "";
      for (const candidate of mine.slice(1, 7)) {
        const d = await api<Detail>(page, "GET", `${ADMIN}/items/${candidate}`);
        if (d.json.gates.lint_ok && d.json.gates.source_ok && d.json.can_review.allowed) {
          id = candidate;
          break;
        }
      }
      expect(id, "a seed item whose own checks pass").not.toBe("");

      await page.goto(`/admin/review/${id}`);
      await settle(page);
      await expect(page.getByText("Seed question").first()).toBeVisible();
      await expect(page.getByTestId("review-solver-none")).toBeVisible();
      await expect(page.getByTestId("review-blocked")).toBeVisible();
      await expect(page.getByTestId("review-gates").locator('[data-gate="cross"]')).toContainText("Failed");
      await expect(page.getByText("Run the blind solver").first()).toBeVisible();
      await rate(page);
      await expect(page.getByRole("button", { name: "Approve" })).toBeDisabled();
      await expect(page.getByTestId("review-why")).toContainText("a publish gate has not passed");
      await expectNoAxeViolations(page);
      await quiet(page);
      await snap(page, info, consoleErrors, "item-gates-blocked");

      await page.getByRole("button", { name: "Re-run blind solver" }).click();
      await expect(page.getByTestId("review-gates").locator('[data-gate="cross"]')).toContainText("Passed", {
        timeout: 60_000,
      });
      await expect(page.getByTestId("review-solver")).toContainText("which matches the key");
      await expect(page.getByTestId("review-all-clear")).toBeVisible();
      await quiet(page);
      await snap(page, info, consoleErrors, "item-solver-done");
      const approve = page.getByRole("button", { name: "Approve" });
      await expect(approve).toBeEnabled();
      await approve.click();
      const outcome = page.getByTestId("review-outcome");
      await expect(outcome).toHaveAttribute("data-outcome", "published");
      await expect(outcome).toContainText("marked as reviewed by a person");
      const after = await api<Detail>(page, "GET", `${ADMIN}/items/${id}`);
      expect(after.json.status).toBe("published");
      expect(after.json.can_review).toEqual({ allowed: false, reason: "already_reviewed" });
      await quiet(page);
      await snap(page, info, consoleErrors, "item-outcome-seed");
    },
  );

  guarded("solver unavailable, resubmit and the psychometric panel states", async ({ page, consoleErrors }, info) => {
    consoleErrors.allowHttpError({ urlPart: "/checks", status: 503 });
    consoleErrors.allowHttpError({ urlPart: "/checks", status: 502 });
    await page.goto("/admin/review?filter=seed_spot_check");
    await settle(page);
    // Unreviewed seed items the scripted solver can confirm (it always answers A), from the database in one query
    // (the review API is rate limited), split between the projects so two never take the same item.
    const keyedA = (JSON.parse(fakeItems("seed-ids").trim().split("\n").pop() ?? "{}") as { ids?: string[] }).ids ?? [];
    const mine = keyedA.filter(
      (q) => [...q].reduce((n, c) => n + c.charCodeAt(0), 0) % PROJECTS.length === projectIndex(info),
    );
    expect(mine.length, "seed items this project can review").toBeGreaterThan(0);
    let id = "";
    for (const candidate of mine.slice(0, 1)) {
      const d = await api<Detail>(page, "GET", `${ADMIN}/items/${candidate}`);
      if (d.json.gates.lint_ok && d.json.gates.source_ok && d.json.can_review.allowed) {
        id = candidate;
        break;
      }
    }
    expect(id, "a seed item whose own checks pass").not.toBe("");

    await page.goto(`/admin/review/${id}`);
    await settle(page);
    // The checks endpoint answers 503 (budget) and then 502 (provider): both are worded, never a raw code.
    await page.route(`**${ADMIN}/items/${id}/checks`, (route) =>
      route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ error: { code: "solver_unavailable", message: "x", request_id: "req-solver" } }),
      }),
    );
    await page.getByRole("button", { name: "Re-run blind solver" }).click();
    await expect(page.getByRole("alert").filter({ hasText: "model budget is used up" })).toBeVisible();
    await expectNoAxeViolations(page);
    await snap(page, info, consoleErrors, "item-solver-budget");
    await page.unroute(`**${ADMIN}/items/${id}/checks`);
    await page.route(`**${ADMIN}/items/${id}/checks`, (route) =>
      route.fulfill({
        status: 502,
        contentType: "application/json",
        body: JSON.stringify({ error: { code: "solver_unavailable", message: "x", request_id: "req-solver2" } }),
      }),
    );
    await page.getByRole("button", { name: "Re-run blind solver" }).click();
    await expect(page.getByRole("alert").filter({ hasText: "could not be reached" })).toBeVisible();
    await page.unroute(`**${ADMIN}/items/${id}/checks`);

    // The psychometric panel with statistics: the real detail with a statistics block injected (the seed has no
    // student responses), so the table, bars, flags in words and the disabled Retire are exercised and audited.
    const real = (await api<Record<string, unknown>>(page, "GET", `${ADMIN}/items/${id}`)).json;
    const stats = {
      n: 42,
      p_correct: 0.12,
      point_biserial: 0.02,
      option_counts: { A: 5, B: 21, C: 6, D: 6, E: 4 },
      median_seconds: 96,
      report_count: 2,
      computed_at: null,
      flags: ["p_too_low", "point_biserial_low", "reports"],
      can_act: false,
    };
    await page.route(`**${ADMIN}/items/${id}`, (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ...real, stats }) }),
    );
    await page.goto(`/admin/review/${id}`);
    await settle(page);
    const psych = page.getByTestId("review-psych");
    await psych.scrollIntoViewIfNeeded();
    await expect(psych.getByTestId("psych-n")).toHaveText("42");
    await expect(psych.getByTestId("psych-options")).toContainText("21 · 50%");
    await expect(psych.getByTestId("psych-flags")).toContainText("Too hard (few answer correctly)");
    await expect(psych.getByTestId("psych-needed")).toContainText("Responses needed before this item can be retired");
    await expect(psych.getByRole("button", { name: "Retire" })).toBeDisabled();
    await expect(psych.getByTestId("psych-retire-reason")).toContainText("Retire is switched off until there are");
    await expectNoHorizontalOverflow(page);
    await expectNoAxeViolations(page);
    await quiet(page);
    await snap(page, info, consoleErrors, "psychometrics");
    await page.unroute(`**${ADMIN}/items/${id}`);

    // A draft shows Resubmit as the lime action and surfaces a lint refusal in words.
    await page.route(`**${ADMIN}/items/${id}`, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ...real,
          status: "draft",
          can_review: { allowed: false, reason: "status_not_reviewable" },
        }),
      }),
    );
    await page.route(`**${ADMIN}/items/${id}/resubmit`, (route) =>
      route.fulfill({
        status: 409,
        contentType: "application/json",
        body: JSON.stringify({ error: { code: "publish_blocked", message: "x", request_id: "req-rs" } }),
      }),
    );
    consoleErrors.allowHttpError({ urlPart: "/resubmit", status: 409 });
    await page.goto(`/admin/review/${id}`);
    await settle(page);
    const resubmit = page.getByRole("button", { name: "Resubmit for review" });
    await expect(resubmit).toHaveAttribute("data-variant", "primary");
    await expect(page.getByTestId("review-form")).toHaveCount(0);
    await resubmit.click();
    await expect(page.getByRole("alert").filter({ hasText: "still has lint errors" })).toBeVisible();
    await expectNoAxeViolations(page);
    await quiet(page);
    await snap(page, info, consoleErrors, "item-draft-resubmit");
  });

  guarded("item states: loading and not found", async ({ page, consoleErrors }, info) => {
    consoleErrors.allowHttpError({ urlPart: `${ADMIN}/items/`, status: 404 });
    await page.goto("/admin/review");
    await settle(page);
    await quiet(page);
    await page.route(`**${ADMIN}/items/slow-item`, async (route) => {
      await new Promise((r) => setTimeout(r, 1500));
      await route.continue();
    });
    await page.goto("/admin/review/slow-item");
    await expect(page.getByTestId("review-item-loading")).toBeVisible();
    await snap(page, info, consoleErrors, "item-loading");
    await expect(page.getByText("This question no longer exists")).toBeVisible();
    await expectNoAxeViolations(page);
    await quiet(page);
    await snap(page, info, consoleErrors, "item-not-found");
    await page.unroute(`**${ADMIN}/items/slow-item`);
  });
});

test.describe("student", () => {
  guarded.use(signedInAs("student"));

  guarded("cannot open the review UI and the API refuses", async ({ page, consoleErrors }, info) => {
    consoleErrors.allowHttpError({ urlPart: ADMIN, status: 403 });
    await page.goto("/admin/review");
    await settle(page);
    await expect(page.getByTestId("review-forbidden")).toBeVisible();
    await expect(page.getByRole("navigation").getByRole("link", { name: "Review" })).toHaveCount(0);
    await expect(page.getByTestId("queue-list")).toHaveCount(0);
    await expectNoAxeViolations(page);
    await quiet(page);
    await snap(page, info, consoleErrors, "student-forbidden");
    expect((await api(page, "GET", `${ADMIN}/summary`)).status).toBe(403);
    expect((await api(page, "GET", `${ADMIN}/items`)).status).toBe(403);
    expect((await api(page, "POST", `${ADMIN}/runs`, { exam: "step1", topic_id: "x" })).status).toBe(403);
  });
});
