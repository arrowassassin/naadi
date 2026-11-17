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
 * Segment 18: the airplane-mode study day against the real API and auth server. Download the week, go offline,
 * answer tutor questions and review cards on the device, come back online: everything syncs once (no double count)
 * and no answer, explanation, why-not or objective ever reached IndexedDB or Cache Storage before an attempt synced.
 */
test.skip(!canMintUsers(), "signed-in tests need the service role (run pnpm e2e:live-auth against the Space)");

const QUESTIONS = 10;
const CARDS = 10;
const HIDDEN = ["answer", "answer_html", "explanation", "explanation_html", "why_not_html", "educational_objective"];

const snap = (page: Page, info: TestInfo, guard: ConsoleGuard, state: string, full = false) =>
  guard.duringTool(async () => {
    await snapSegment(page, info, guard, "18", state, full);
  });

function email(info: TestInfo, role: string): string {
  const run = String.fromCharCode(97 + (info.repeatEachIndex % 26), 97 + (info.retry % 26));
  return `naadi-e2e-offline${role}${info.project.name.replace(/[^a-z]/g, "")}${run}-${process.env.E2E_RUN_ID}@example.com`;
}

async function freshStudent(browser: Browser, baseURL: string, info: TestInfo, role: string) {
  const address = email(info, role);
  runUsers(["delete", "--email", address]);
  const contexts: BrowserContext[] = [];
  const open = async (path: string): Promise<{ page: Page; context: BrowserContext }> => {
    const context = await browser.newContext({ ...info.project.use, baseURL });
    contexts.push(context);
    const page = await context.newPage();
    trackApi(page);
    const link = toLink(runUsers(["link", "--email", address]));
    await page.goto(callbackPath(link, path));
    await page.waitForURL((u) => u.pathname === path, { timeout: 30_000 });
    return { page, context };
  };
  const done = async () => {
    try {
      for (const c of contexts) await c.close().catch(() => undefined);
    } finally {
      runUsers(["delete", "--email", address]);
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

/** Client-side navigation (no request for the page): what a tap on a link does offline. */
async function go(page: Page, path: string): Promise<void> {
  await page.evaluate((p) => {
    window.history.pushState({}, "", p);
    window.dispatchEvent(new PopStateEvent("popstate", { state: {} }));
  }, path);
  await page.waitForURL((u) => u.pathname === path);
}

/** Every value in every IndexedDB database and every Cache Storage entry of this origin, as text. */
async function deviceStores(page: Page): Promise<{ idb: unknown[]; caches: string[] }> {
  return page.evaluate(async () => {
    const idb: unknown[] = [];
    for (const info of await indexedDB.databases()) {
      if (!info.name) continue;
      const db = await new Promise<IDBDatabase>((resolve, reject) => {
        const r = indexedDB.open(info.name as string);
        r.onsuccess = () => resolve(r.result);
        r.onerror = () => reject(r.error);
      });
      for (const name of Array.from(db.objectStoreNames)) {
        const rows = await new Promise<unknown[]>((resolve, reject) => {
          const r = db.transaction(name, "readonly").objectStore(name).getAll();
          r.onsuccess = () => resolve(r.result as unknown[]);
          r.onerror = () => reject(r.error);
        });
        idb.push(...rows.map((row) => ({ db: info.name, store: name, row })));
      }
      db.close();
    }
    const texts: string[] = [];
    for (const name of await caches.keys()) {
      const cache = await caches.open(name);
      for (const req of await cache.keys()) {
        const res = await cache.match(req);
        const type = res?.headers.get("content-type") ?? "";
        texts.push(`${name} ${req.url} ${type}`);
        // Data responses only: precached app code (JS/CSS) names these fields in its schemas, which is not data.
        if (res && /json/.test(type)) texts.push(await res.text());
      }
    }
    return { idb, caches: texts };
  });
}

/** Hidden keys holding a value outside an object that proves a graded attempt (mirror of src/offline/guard.ts). */
function leaks(value: unknown, path = "$", allowed = false): string[] {
  if (Array.isArray(value)) return value.flatMap((v, i) => leaks(v, `${path}[${i}]`, allowed));
  if (typeof value !== "object" || value === null) return [];
  const o = value as Record<string, unknown>;
  const attempt = o.attempt as { correct?: unknown } | null | undefined;
  const ok = allowed || (typeof attempt === "object" && attempt !== null && typeof attempt.correct === "boolean");
  return Object.entries(o).flatMap(([k, v]) => [
    ...(HIDDEN.includes(k) && !ok && v !== null && v !== undefined ? [`${path}.${k}`] : []),
    ...leaks(v, `${path}.${k}`, ok),
  ]);
}

/** Console errors the app causes. Offline, the browser's own "failed to load" lines for refused requests are not. */
function watchConsole(page: Page) {
  const errors: string[] = [];
  let tool = 0;
  page.on("console", (m) => {
    if (m.type() !== "error") return;
    if (tool > 0 && m.text().startsWith("Refused to apply a stylesheet")) return;
    if (/Failed to load resource|net::ERR_INTERNET_DISCONNECTED|NetworkError|Load failed/.test(m.text())) return;
    // A report-only policy (Trusted Types) reports, it does not fail: not an app error (S22's rule, ink spec too).
    if (m.text().startsWith("[Report Only]")) return;
    errors.push(m.text());
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
  return { errors: () => [...errors], guard };
}

const syncLine = (page: Page) => page.locator("[data-sync-phase]");

test.describe("offline week", () => {
  test("airplane-mode study day: download, answer 10 tutor questions and review 10 cards offline, sync once", async ({
    browser,
    baseURL,
  }, info) => {
    test.setTimeout(240_000);
    const student = await freshStudent(browser, baseURL as string, info, "day");
    try {
      const { page, context } = await student.open("/settings/offline");
      const { errors, guard } = watchConsole(page);
      const block = await api<{ id: string }>(page, "POST", "/api/blocks", {
        exam: "step1",
        count: QUESTIONS,
        mode: "tutor",
        status: "all",
        seed: 18,
      });
      expect(block.status, JSON.stringify(block.json)).toBe(201);
      for (let i = 0; i < CARDS; i++)
        expect(
          (await api(page, "POST", "/api/cards", { front_text: `Offline ${i}`, back_text: `Back ${i}` })).status,
        ).toBe(201);

      // Settings → Offline: size preview, then the download with progress, then "Ready offline until …".
      await page.getByRole("button", { name: "Download this week" }).click();
      await expect(page.getByText(/^About .+ for the next 7 plan days$/)).toBeVisible();
      await expect(page.locator("[data-offline-parts] li").first()).toBeVisible();
      await expectNoAxeViolations(page);
      await snap(page, info, guard, "settings-preview");
      await page.getByRole("button", { name: "Download", exact: true }).click();
      await expect(page.getByText(/^Ready offline until /)).toBeVisible({ timeout: 60_000 });
      await expectNoHorizontalOverflow(page);
      await snap(page, info, guard, "settings-ready");

      // Leak scan right after the download: nothing hidden on the device for an unanswered question.
      const fresh = await deviceStores(page);
      expect(leaks(fresh.idb)).toEqual([]);
      expect(fresh.caches.filter((t) => HIDDEN.some((k) => t.includes(`"${k}":"`)))).toEqual([]);
      expect(fresh.caches.filter((t) => t.includes("/api/"))).toEqual([]);

      // Once the worker is active it controls the next load; reload online so it does, then go offline.
      await page.evaluate(() => navigator.serviceWorker.ready.then(() => undefined));
      await quiet(page);
      await page.reload();
      await expect(page.getByRole("heading", { level: 1, name: "Offline" })).toBeVisible();
      expect(await page.evaluate(() => Boolean(navigator.serviceWorker.controller))).toBe(true);
      await quiet(page);
      await context.setOffline(true);
      await page.evaluate(() => window.dispatchEvent(new Event("offline")));
      await expect(syncLine(page)).toHaveAttribute("data-sync-phase", "offline");

      // The service worker (production build) serves the app shell: an offline reload still opens the app.
      await page.reload();
      await expect(page.getByRole("heading", { level: 1, name: "Offline" })).toBeVisible();
      await expect(syncLine(page)).toHaveAttribute("data-sync-phase", "offline");
      await expect(page.getByText(/^Ready offline until /)).toBeVisible();
      await snap(page, info, guard, "reload-offline");

      // Tutor questions on the device: each answer is kept, the explanation waits for the server.
      await go(page, `/practice/blocks/${block.json.id}/1`);
      for (let n = 1; n <= QUESTIONS; n++) {
        await expect(page.getByRole("radio")).toHaveCount(5);
        await page.keyboard.press("b");
        await page.keyboard.press("Control+Enter");
        await expect(page.getByText("Explanation after you reconnect")).toBeVisible();
        await expect(page.getByText(/^Why it's/)).toHaveCount(0);
        if (n === 1) await snap(page, info, guard, "tutor-offline");
        if (n < QUESTIONS) {
          await page.keyboard.press("n");
          await page.waitForURL((u) => u.pathname.endsWith(`/${n + 1}`));
        }
      }
      await expect(syncLine(page)).toHaveAttribute("data-sync-pending", String(QUESTIONS));
      await expect(syncLine(page)).toContainText(`Offline · ${QUESTIONS} changes will sync`);

      // Cards from the downloaded due list.
      await go(page, "/cards");
      for (let i = 0; i < CARDS; i++) {
        await expect(page.getByRole("region", { name: "Flashcard" })).toBeVisible();
        await page.locator("body").press("Space");
        await page.locator("body").press("3");
      }
      await expect(syncLine(page)).toHaveAttribute("data-sync-pending", String(QUESTIONS + CARDS));
      await snap(page, info, guard, "cards-offline");

      // The sync sheet lists what waits.
      await syncLine(page).click();
      await expect(page.locator("[data-sync-sheet]")).toContainText("Answer");
      await expect(page.locator("[data-sync-sheet]")).toContainText("Card rating");
      await expectNoAxeViolations(page);
      await snap(page, info, guard, "sync-sheet-offline");
      await page.keyboard.press("Escape");

      // Still nothing hidden anywhere on the device: the answers were not graded yet.
      expect(leaks((await deviceStores(page)).idb)).toEqual([]);

      // Back online: everything syncs once.
      await context.setOffline(false);
      await page.evaluate(() => window.dispatchEvent(new Event("online")));
      await expect(syncLine(page)).toHaveAttribute("data-sync-pending", "0", { timeout: 60_000 });
      await expect(syncLine(page)).toHaveAttribute("data-sync-failed", "0");
      const detail = await api<{ questions: { answered: boolean }[] }>(page, "GET", `/api/blocks/${block.json.id}`);
      expect(detail.json.questions.filter((q) => q.answered)).toHaveLength(QUESTIONS);
      await expect
        .poll(
          async () =>
            (await api<{ reviewed_today: number }>(page, "GET", "/api/cards/stats?tz=UTC")).json.reviewed_today,
        )
        .toBe(CARDS);
      // A second replay pass (the 30 s timer, another `online`) changes nothing: no double counting.
      await page.evaluate(() => window.dispatchEvent(new Event("online")));
      await quiet(page);
      expect((await api<{ reviewed_today: number }>(page, "GET", "/api/cards/stats?tz=UTC")).json.reviewed_today).toBe(
        CARDS,
      );
      expect(errors()).toEqual([]);
    } finally {
      await student.done();
    }
  });
});
