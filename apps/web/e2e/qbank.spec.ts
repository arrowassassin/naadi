import type { Browser, BrowserContext, Page, TestInfo } from "@playwright/test";
import { callbackPath, canMintUsers, runUsers, toLink } from "./auth-users";
import {
  type ConsoleGuard,
  expect,
  expectNoAxeViolations,
  expectNoHorizontalOverflow,
  quiet,
  snapSegment,
  test,
  trackApi,
} from "./fixtures";

/**
 * Segment 08: the Builder and the tutor-mode question view against the real API, the database seeded by
 * segment 05 plus the S23 content packs (Step 1: 1,835 published questions) and the real auth server. Every project signs in as its own throwaway
 * student (deleted afterwards), because blocks and attempts are the thing under test.
 */
test.skip(!canMintUsers(), "signed-in tests need the service role (run pnpm e2e:live-auth against the Space)");

// 1,835 published Step 1 questions (380 from segment 05, 1,455 from the S23 content packs), 80 of them mock-exam
// items (exam-only: never in a tutor pool). In review pack items are not published, so never in a pool either.
const STEP1_QUESTIONS = 1755;

const snap = (page: Page, info: TestInfo, guard: ConsoleGuard, state: string, full = false) =>
  guard.duringTool(async () => {
    await snapSegment(page, info, guard, "08", state, full);
    await page.waitForTimeout(500);
  });

function practiceEmail(info: TestInfo): string {
  const run = String.fromCharCode(97 + (info.repeatEachIndex % 26), 97 + (info.retry % 26));
  const role = `practice${info.project.name.replace(/[^a-z]/g, "")}${run}${info.title
    .replace(/[^a-z]/gi, "")
    .slice(0, 8)
    .toLowerCase()}`;
  return `naadi-e2e-${role}-${process.env.E2E_RUN_ID}@example.com`;
}

async function freshStudent(browser: Browser, baseURL: string, info: TestInfo) {
  const email = practiceEmail(info);
  runUsers(["delete", "--email", email]);
  const contexts: BrowserContext[] = [];
  const open = async (path = "/practice"): Promise<{ page: Page; context: BrowserContext }> => {
    const context = await browser.newContext({ ...info.project.use, baseURL });
    contexts.push(context);
    const page = await context.newPage();
    trackApi(page); // before the sign-in callback, so quiet() sees every /api request this page makes
    const link = toLink(runUsers(["link", "--email", email]));
    await page.goto(callbackPath(link, path));
    await page.waitForURL((u) => u.pathname === path, { timeout: 30_000 });
    return { page, context };
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

const isPhone = (page: Page) => (page.viewportSize()?.width ?? 1000) < 640;

/** Console errors of pages opened outside the fixture's own page, with the fixture's rule for the screenshot tool. */
function watchConsole(page: Page) {
  const errors: string[] = [];
  let tool = 0;
  page.on("console", (m) => {
    if (m.type() === "error" && !(tool > 0 && m.text().startsWith("Refused to apply a stylesheet"))) {
      errors.push(m.text());
    }
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

/** Call the API from the page with the signed-in student's own token (what the app itself sends). */
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
      return { status: res.status, json: (await res.json()) as unknown };
    },
    { method, url, body },
  ) as Promise<{ status: number; json: T }>;
}

/** Elements inside <main> painted with the lime role colour. */
async function limeCount(page: Page): Promise<number> {
  return page.evaluate(() => {
    const lime = "rgb(198, 255, 61)";
    return Array.from(document.querySelectorAll<HTMLElement>("main *")).filter((el) => {
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0 && getComputedStyle(el).backgroundColor === lime;
    }).length;
  });
}

const LEAK = /explanation_html|why_not_html|answer_html|educational_objective|"answer"/;

interface BlockOut {
  id: string;
  total: number;
  next_n: number;
}
interface QuestionOut {
  question: { id: string; options: { label: string }[] };
  explanation: unknown;
  attempt: unknown;
}

test.describe("builder", () => {
  test("shows live pool counts from the database, one lime CTA, timed and exam available", async ({
    browser,
    baseURL,
  }, info) => {
    const student = await freshStudent(browser, baseURL as string, info);
    try {
      const { page } = await student.open();
      const { errors, guard } = watchConsole(page);
      await expect(page.getByRole("heading", { level: 1, name: /Build/ })).toBeVisible();
      // default status is "unused": every Step 1 question for a brand-new student
      await expect(page.getByText(`${STEP1_QUESTIONS} questions match.`)).toBeVisible();
      const systems = page.getByRole("group", { name: "Systems" });
      await expect(systems.getByRole("button", { name: /^All systems/ })).toHaveAttribute("aria-pressed", "true");
      expect(await systems.getByRole("button").count()).toBeGreaterThan(10);
      const status = page.getByRole("group", { name: "Which questions" });
      await expect(status.getByRole("button", { name: /^Unused/ })).toHaveAttribute("aria-pressed", "true");
      await expect(status.getByRole("button", { name: /^Unused/ })).toContainText(String(STEP1_QUESTIONS));
      await expect(status.getByRole("button", { name: /^Incorrect/ })).toContainText("0");

      const mode = page.getByRole("group", { name: "Mode" });
      await expect(mode.getByRole("button", { name: "Tutor: explain after each" })).toHaveAttribute(
        "aria-pressed",
        "true",
      );
      // qbank_timed is on (segment 09): timed and exam can be chosen
      for (const name of ["Timed", "Exam simulation"]) {
        await expect(mode.getByRole("button", { name })).not.toHaveAttribute("aria-disabled", "true");
      }

      expect(await limeCount(page)).toBe(1);
      await expect(page.getByRole("button", { name: "Start block" })).toBeVisible();
      await expectNoAxeViolations(page);
      await expectNoHorizontalOverflow(page);
      await snap(page, info, guard, "builder");

      // a filter moves the counts: one system, and the matching total
      const first = systems.getByRole("button").nth(1);
      const label = (await first.textContent()) ?? "";
      const n = Number(/(\d+)$/.exec(label)?.[1] ?? "0");
      expect(n).toBeGreaterThan(0);
      await first.click();
      await expect(first).toHaveAttribute("aria-pressed", "true");
      await expect(page.getByText(`${n} questions match.`)).toBeVisible();

      // the empty pool is explained and cannot be started
      await status.getByRole("button", { name: /^Flagged/ }).click();
      await expect(page.getByText(/No questions match these filters/)).toBeVisible();
      await expect(page.getByRole("button", { name: "Start block" })).toBeDisabled();
      await snap(page, info, guard, "builder-empty-pool");
      expect(errors).toEqual([]);
    } finally {
      await student.done();
    }
  });

  test("a pool failure shows the error with its request id and retries", async ({ browser, baseURL }, info) => {
    const student = await freshStudent(browser, baseURL as string, info);
    try {
      const { page } = await student.open();
      const { errors, guard } = watchConsole(page);
      let fail = true;
      await page.route("**/api/qbank/pool**", async (route) => {
        if (!fail) return route.continue();
        await route.fulfill({
          status: 503,
          contentType: "application/json",
          headers: { "x-request-id": "e2e-pool-503" },
          body: JSON.stringify({
            error: { code: "service_unavailable", message: "Practice is down.", request_id: "e2e-pool-503" },
          }),
        });
      });
      await quiet(page); // Firefox logs aborted font loads
      await page.reload();
      await expect(page.getByText("Practice could not be loaded")).toBeVisible();
      await expect(page.getByText("Request ID e2e-pool-503")).toBeVisible();
      await snap(page, info, guard, "builder-error");
      fail = false;
      await page.getByRole("button", { name: "Try again" }).click();
      await expect(page.getByRole("button", { name: "Start block" })).toBeVisible();
      expect(errors.filter((e) => !/503/.test(e))).toEqual([]);
    } finally {
      await student.done();
    }
  });
});

test.describe("tutor block", () => {
  test("build 10 questions, answer with the keyboard, explanation only after submit, flag, next, past blocks", async ({
    browser,
    baseURL,
  }, info) => {
    test.setTimeout(150_000); // three axe audits, a screenshot per state and a busy CI host
    const student = await freshStudent(browser, baseURL as string, info);
    try {
      const { page } = await student.open();
      const { errors, guard } = watchConsole(page);
      await expect(page.getByText(`${STEP1_QUESTIONS} questions match.`)).toBeVisible();

      // 10 questions: the lower bound is 1, the default 40, the slider follows the pool
      const slider = page.getByRole("slider", { name: "Questions in the block" });
      await slider.fill("10");
      await expect(page.locator("output")).toHaveText("10");
      await page.getByRole("button", { name: "Fewer questions" }).click();
      await page.getByRole("button", { name: "More questions" }).click();
      await expect(page.locator("output")).toHaveText("10");
      await page.getByRole("button", { name: "Start block" }).click();
      await page.waitForURL(/\/practice\/blocks\/[0-9a-f-]{36}\/1$/);
      const blockId = /blocks\/([0-9a-f-]{36})/.exec(page.url())?.[1] ?? "";

      // question 1: stem and five options, no explanation anywhere in the DOM
      await expect(page.getByRole("radio")).toHaveCount(5);
      await expect(page.getByText(/^Why it's/)).toHaveCount(0);
      await expect(page.getByRole("group", { name: "Answer choices" })).toBeVisible();
      const html = await page.content();
      expect(html).not.toMatch(/Why the others are wrong/);
      // exactly one lime control, and it is the next action: Submit (disabled until an option is chosen)
      await expect(page.getByRole("button", { name: "Submit" })).toBeDisabled();
      await expectNoAxeViolations(page);
      await expectNoHorizontalOverflow(page);
      await snap(page, info, guard, "question-unanswered");

      // leak probe: the next question's payload, fetched with the student's own token, before answering
      const probe = await api<QuestionOut>(page, "GET", `/api/blocks/${blockId}/questions/2`);
      expect(probe.status).toBe(200);
      expect(JSON.stringify(probe.json)).not.toMatch(LEAK);
      expect(probe.json.explanation).toBeNull();
      const attemptsBefore = await api<{ questions: { answered: boolean }[] }>(page, "GET", `/api/blocks/${blockId}`);
      expect(attemptsBefore.json.questions.every((q) => !q.answered)).toBe(true);
      expect(JSON.stringify(attemptsBefore.json)).not.toMatch(LEAK);

      // keyboard: C picks option C, Control+Enter submits
      await page.keyboard.press("c");
      await expect(page.getByRole("radio", { name: /^C\./ })).toBeChecked();
      await expect(page.getByRole("button", { name: "Submit" })).toBeEnabled();
      await page.keyboard.press("Control+Enter");
      const explanation = page.getByRole("region", { name: "Explanation" });
      await expect(explanation).toBeVisible();
      await expect(explanation.getByRole("heading", { level: 2, name: /^Why it's [A-E]$/ })).toBeVisible();
      // the result is a word and an icon, never colour alone
      await expect(page.getByRole("status").filter({ hasText: /^(Correct|Incorrect)/ })).toBeVisible();
      await expect(page.getByRole("radio", { name: /^C\./ })).toBeDisabled();
      await expect(page.getByText(/Your answer/).first()).toBeVisible();
      await expect(
        page.getByText("Correct answer", { exact: true }).or(page.getByText(/^Correct · Your answer/)),
      ).toBeVisible();
      await expectNoAxeViolations(page);
      await snap(page, info, guard, "question-answered");
      // one lime control in the bar: Next
      await expect(page.getByRole("button", { name: "Next", exact: true })).toBeVisible();

      // answered once is final: the same request again returns the same graded attempt
      const again = await api<{ chosen: string }>(page, "POST", `/api/blocks/${blockId}/attempts`, {
        question_id: await questionId(page, blockId, 1),
        chosen: "A",
        seconds: 1,
      });
      expect(again.status).toBe(200);
      expect(again.json.chosen).toBe("C");

      // F flags (a word change, aria-pressed), N goes to question 2
      await page.keyboard.press("f");
      await expect(page.getByRole("button", { name: "Flagged" })).toHaveAttribute("aria-pressed", "true");
      await page.keyboard.press("n");
      await page.waitForURL(/\/practice\/blocks\/[0-9a-f-]{36}\/2$/);
      await expect(page.getByRole("radio")).toHaveCount(5);
      await expect(page.getByText(/^Why it's/)).toHaveCount(0); // question 2 shows nothing until answered
      await page.keyboard.press("2");
      await expect(page.getByRole("radio", { name: /^B\./ })).toBeChecked();
      await page.keyboard.press("Control+Enter");
      await expect(page.getByRole("region", { name: "Explanation" })).toBeVisible();

      // back to question 1: it still shows its explanation and its flag
      await page.getByRole("button", { name: "Previous" }).click();
      await page.waitForURL(/\/1$/);
      await expect(page.getByRole("region", { name: "Explanation" })).toBeVisible();
      await expect(page.getByRole("button", { name: "Flagged" })).toBeVisible();

      // past blocks list the block with its progress, and Continue resumes at question 3
      await quiet(page);
      await page.goto("/practice");
      const past = page.getByRole("region", { name: "Past blocks" }).or(page.locator("section#past-blocks"));
      await expect(past.getByText("2 of 10 answered")).toBeVisible();
      await expect(past.getByText("Continue")).toBeVisible();
      await expect(past.getByText("Tutor: explain after each")).toBeVisible();
      await snap(page, info, guard, "builder-past-blocks");
      await past.getByRole("link").first().click();
      await page.waitForURL(/\/3$/);
      expect(errors).toEqual([]);
    } finally {
      await student.done();
    }
  });

  test("finishing a block, the Lab values panel and the lesson link", async ({ browser, baseURL }, info) => {
    test.setTimeout(150_000); // three axe audits, a screenshot per state and a busy CI host
    const student = await freshStudent(browser, baseURL as string, info);
    try {
      const { page } = await student.open();
      const { errors, guard } = watchConsole(page);
      const made = await api<BlockOut>(page, "POST", "/api/blocks", {
        exam: "step1",
        count: 2,
        mode: "tutor",
        status: "all",
        seed: 4,
      });
      expect(made.status).toBe(201);
      await quiet(page);
      await page.goto(`/practice/blocks/${made.json.id}/1`);
      await expect(page.getByRole("radio")).toHaveCount(5);

      // lab values: our own table from app_config, the official one is linked, not copied
      await page.getByRole("button", { name: "Lab values" }).click();
      const sheet = page.getByRole("dialog", { name: "Lab values" });
      await expect(sheet.getByRole("row", { name: /Sodium/ })).toContainText("136-145 mEq/L");
      await expect(sheet.getByRole("link", { name: /Official lab values/ })).toHaveAttribute(
        "href",
        /^https:\/\/www\.nbme\.org\//,
      );
      // the provenance note is whatever the server's ui_copy says (editable without a redeploy)
      const served = (await (await page.request.get("/api/public-config")).json()) as {
        ui_copy: Record<string, string>;
      };
      expect(served.ui_copy["practice.labs_source"]).toBeTruthy();
      await expect(sheet.getByText(served.ui_copy["practice.labs_source"] as string)).toBeVisible();
      // the sheet slides in: audit it once the animation has finished, not mid-flight
      await sheet.evaluate((d) => Promise.all(d.getAnimations().map((a) => a.finished)).then(() => undefined));
      // The audit runs on a frozen copy where an open <dialog> is not in the top layer, so the page behind it can
      // overlap the sheet's targets there. Audit the sheet on its own: hide everything else for the audit only.
      const isolate = (on: boolean) =>
        sheet.evaluate((d, hide) => {
          document.body.style.visibility = hide ? "hidden" : "";
          d.style.visibility = hide ? "visible" : "";
        }, on);
      await isolate(true);
      try {
        await expectNoAxeViolations(page);
      } finally {
        await isolate(false);
      }
      await snap(page, info, guard, "lab-values");
      await page.keyboard.press("Escape");
      await expect(sheet).toBeHidden();

      // answer both questions; the last one finishes the block
      await page.keyboard.press("1");
      await page.keyboard.press("Control+Enter");
      await expect(page.getByRole("region", { name: "Explanation" })).toBeVisible();
      await page.getByRole("button", { name: "Next", exact: true }).click();
      await page.waitForURL(/\/2$/);
      await page.keyboard.press("a");
      await page.keyboard.press("Control+Enter");
      await expect(page.getByRole("region", { name: "Explanation" })).toBeVisible();
      await expect(page.getByRole("heading", { name: "Block complete" })).toBeVisible();
      await expect(page.getByText(/You answered 2 questions and got \d right/)).toBeVisible();
      await expect(page.getByRole("link", { name: "Finish block" })).toBeVisible();
      await snap(page, info, guard, "block-complete");
      const detail = await api<{ finished_at: string | null; score: number | null }>(
        page,
        "GET",
        `/api/blocks/${made.json.id}`,
      );
      expect(detail.json.finished_at).not.toBeNull();
      await page.getByRole("link", { name: "Finish block" }).click();
      await page.waitForURL((u) => u.pathname === "/practice");
      await expect(page.getByText("2 of 2 answered")).toBeVisible();
      await expect(page.getByText("Review")).toBeVisible();
      expect(errors).toEqual([]);
    } finally {
      await student.done();
    }
  });

  test("weak-spot block: needs history, then targets the missed topics", async ({ browser, baseURL }, info) => {
    const student = await freshStudent(browser, baseURL as string, info);
    try {
      const { page } = await student.open();
      const { errors, guard } = watchConsole(page);
      await expect(page.getByText(`${STEP1_QUESTIONS} questions match.`)).toBeVisible();
      await page.getByRole("button", { name: "Build my weak-spot block" }).click();
      await expect(page.getByRole("alert").filter({ hasText: /practice history/ })).toBeVisible();
      await snap(page, info, guard, "weak-spot-needs-history");

      // 15 answers, all "A": most are misses, so some topic has three attempts and a miss
      const made = await api<BlockOut>(page, "POST", "/api/blocks", {
        exam: "step1",
        count: 15,
        mode: "tutor",
        status: "all",
        seed: 9,
      });
      expect(made.status).toBe(201);
      for (let n = 1; n <= 15; n++) {
        const q = await api<QuestionOut>(page, "GET", `/api/blocks/${made.json.id}/questions/${n}`);
        const out = await api<{ correct: boolean }>(page, "POST", `/api/blocks/${made.json.id}/attempts`, {
          question_id: q.json.question.id,
          chosen: "A",
          seconds: 3,
        });
        expect(out.status).toBe(200);
      }
      await quiet(page);
      await page.goto("/practice");
      await page.getByRole("button", { name: "Build my weak-spot block" }).click();
      await page.waitForURL(/\/practice\/blocks\/[0-9a-f-]{36}\/\d+$/);
      await expect(page.getByText("Weak-spot block")).toBeVisible();
      await expect(page.getByRole("radio")).toHaveCount(5);
      await snap(page, info, guard, "weak-spot-block");
      expect(errors.filter((e) => !/status of 422/.test(e))).toEqual([]); // the refusal before any history
    } finally {
      await student.done();
    }
  });

  test("a search hit opens the single question without showing its answer", async ({ browser, baseURL }, info) => {
    const student = await freshStudent(browser, baseURL as string, info);
    try {
      const { page } = await student.open();
      const { errors } = watchConsole(page);
      await quiet(page);
      await page.goto("/practice/question/ch01-q01");
      await page.waitForURL(/\/practice\/blocks\/[0-9a-f-]{36}\/1$/);
      await expect(page.getByRole("radio")).toHaveCount(5);
      await expect(page.getByText(/Why it's/)).toHaveCount(0);
      await expect(page.getByText(/xeroderma|Endonuclease excision/)).toHaveCount(1); // the option text only
      // let pending font loads finish, so the navigation does not abort them (Firefox logs aborted font loads)
      await quiet(page);
      await page.goto("/practice/question/does-not-exist");
      await expect(page.getByText("This question is not available")).toBeVisible();
      expect(errors.filter((e) => !/404/.test(e))).toEqual([]);
    } finally {
      await student.done();
    }
  });

  test("offline: an answer is kept on the device and syncs when the connection returns", async ({
    browser,
    baseURL,
  }, info) => {
    const student = await freshStudent(browser, baseURL as string, info);
    try {
      const { page, context } = await student.open();
      const { errors, guard } = watchConsole(page);
      const made = await api<BlockOut>(page, "POST", "/api/blocks", {
        exam: "step1",
        count: 3,
        mode: "tutor",
        status: "all",
        seed: 2,
      });
      await quiet(page);
      await page.goto(`/practice/blocks/${made.json.id}/1`);
      await expect(page.getByRole("radio")).toHaveCount(5);
      await page.waitForTimeout(800); // the next question is read ahead and saved on this device
      await quiet(page); // no font download may be cut off by going offline
      await context.setOffline(true);
      await page.keyboard.press("b");
      await page.keyboard.press("Control+Enter");
      await expect(page.getByRole("status").filter({ hasText: /will sync when you are back online/ })).toBeVisible();
      await expect(page.getByText(/^Why it's/)).toHaveCount(0); // grading needs the server
      await snap(page, info, guard, "offline-saved");
      await context.setOffline(false);
      await page.evaluate(() => window.dispatchEvent(new Event("online")));
      await expect(page.getByRole("region", { name: "Explanation" })).toBeVisible({ timeout: 30_000 });
      const detail = await api<{ questions: { answered: boolean }[] }>(page, "GET", `/api/blocks/${made.json.id}`);
      expect(detail.json.questions[0]?.answered).toBe(true);
      expect(errors.filter((e) => !/Failed to load resource|net::ERR|NetworkError|Load failed/.test(e))).toEqual([]);
    } finally {
      await student.done();
    }
  });
});

test.describe("phone layout", () => {
  test("Flag, Labs and Submit are in reach and nothing overflows", async ({ browser, baseURL }, info) => {
    const student = await freshStudent(browser, baseURL as string, info);
    try {
      const { page } = await student.open();
      const { errors, guard } = watchConsole(page);
      const made = await api<BlockOut>(page, "POST", "/api/blocks", {
        exam: "step1",
        count: 2,
        mode: "tutor",
        status: "all",
        seed: 6,
      });
      await quiet(page);
      await page.goto(`/practice/blocks/${made.json.id}/1`);
      await expect(page.getByRole("radio")).toHaveCount(5);
      for (const name of ["Flag", "Lab values", "Submit"]) {
        const b = page.getByRole("button", { name });
        await expect(b).toBeVisible();
        const box = await b.boundingBox();
        expect(box?.height ?? 0).toBeGreaterThanOrEqual(44); // touch target
        const vp = page.viewportSize();
        expect((box?.x ?? 0) + (box?.width ?? 0)).toBeLessThanOrEqual(vp?.width ?? 0);
      }
      await expectNoHorizontalOverflow(page);
      if (isPhone(page)) await snap(page, info, guard, "question-phone");
      else await snap(page, info, guard, "question-wide");
      await quiet(page);
      expect(errors).toEqual([]);
    } finally {
      await student.done();
    }
  });
});

async function questionId(page: Page, blockId: string, n: number): Promise<string> {
  const q = await api<QuestionOut>(page, "GET", `/api/blocks/${blockId}/questions/${n}`);
  return q.json.question.id;
}

test("the practice entry is in the navigation", async ({ browser, baseURL }, info) => {
  const student = await freshStudent(browser, baseURL as string, info);
  try {
    const { page } = await student.open("/practice");
    const { errors } = watchConsole(page);
    const nav = isPhone(page)
      ? page.getByRole("navigation", { name: "Tabs" })
      : page.getByRole("navigation", { name: "Main" });
    await expect(nav.getByRole("link", { name: "Practice" })).toHaveAttribute("aria-current", "page");
    await quiet(page);
    expect(errors).toEqual([]);
  } finally {
    await student.done();
  }
});
