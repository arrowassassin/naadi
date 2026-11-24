import { readFileSync } from "node:fs";
import path from "node:path";
import type { APIRequestContext, Browser, Page, TestInfo } from "@playwright/test";
import { canMintUsers, type E2ERole, isLive } from "./auth-users";
import {
  type ConsoleGuard,
  expect,
  expectNoAxeViolations,
  expectNoHorizontalOverflow,
  quiet,
  settle,
  snapSegment,
  test,
} from "./fixtures";
import { signedInState } from "./session";

/**
 * Segment 19: the admin console against the real API, database and Storage of the local stack.
 *
 * - Every project: the console's pages render for an admin with zero axe violations, no console errors and no
 *   horizontal scroll (iPad portrait/landscape, phone, laptop), and a student sees no admin link and gets the
 *   permission state (and 403 from the API).
 * - One project (WebKit iPad portrait, the primary target): the authoring flow writes shared content and global
 *   config, so it runs once and cleans up after itself (chapter retired, feature flag reverted).
 *
 * Words are looked up in the shipped ui_copy defaults (generated from config/app_config.yaml), never hardcoded.
 */
test.skip(!canMintUsers(), "needs the local auth stack's service role to mint the admin and the student");

const shipped = (name: string) =>
  (
    JSON.parse(readFileSync(path.resolve(import.meta.dirname, `../../../packages/shared/${name}`), "utf8")) as {
      values: Record<string, string>;
    }
  ).values;
// The core strings and the admin console's (split so the admin strings stay out of the first load).
const COPY = { ...shipped("ui-copy.defaults.json"), ...shipped("ui-copy.admin.defaults.json") };
/** A ui_copy string with its {placeholders} filled. */
function c(key: string, vars: Record<string, string> = {}): string {
  const s = COPY[key];
  if (!s) throw new Error(`ui_copy key ${key} is not shipped`);
  return s.replace(/\{(\w+)\}/g, (m, n: string) => vars[n] ?? m);
}
/** A pattern for a ui_copy string whose {placeholders} may be anything. */
function cre(key: string): RegExp {
  const s = c(key)
    .replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
    .replace(/\\\{\w+\\\}/g, ".+");
  return new RegExp(`^${s}$`);
}

const snap = (page: Page, info: TestInfo, guard: ConsoleGuard, state: string, full = false) =>
  guard.duringTool(async () => {
    await snapSegment(page, info, guard, "19", state, full);
    // WebKit reports the screenshot tool's refused <style> late after a full-page capture of a page with images.
    await page.waitForTimeout(full ? 1500 : 500);
  });

async function go(page: Page, url: string): Promise<void> {
  await quiet(page);
  await page.goto(url);
  await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
  await settle(page);
}

async function bearer(page: Page): Promise<string> {
  const token = await page.evaluate(() => {
    const raw = localStorage.getItem("naadi.auth");
    return raw ? (JSON.parse(raw) as { access_token: string }).access_token : null;
  });
  expect(token).toBeTruthy();
  return `Bearer ${token}`;
}

async function api<T>(req: APIRequestContext, auth: string, method: string, url: string, data?: unknown): Promise<T> {
  const res = await req.fetch(url, { method, headers: { authorization: auth }, data });
  expect(res.status(), `${method} ${url}: ${await res.text()}`).toBeLessThan(300);
  return (res.status() === 204 ? undefined : await res.json()) as T;
}

/**
 * Every admin request counts against that admin's shared `admin_any` bucket (120/min, segment 22). The console
 * pages run on every project in parallel, so they use the run's second and third admins (the authoring flow uses
 * the first): no single admin's bucket sees two projects at once on the local smoke set.
 */
function pagesAdmin(project: string): E2ERole {
  const fixed: Record<string, E2ERole> = {
    "webkit-ipad-portrait": "adminc",
    "webkit-ipad-landscape": "adminb",
    "webkit-phone-390": "adminc",
    "chromium-laptop-1440": "adminb",
  };
  return fixed[project] ?? ([...project].reduce((n, ch) => n + ch.charCodeAt(0), 0) % 2 ? "adminb" : "adminc");
}

test.describe("admin console pages", () => {
  test.use({
    storageState: async ({ browser, baseURL }: { browser: Browser; baseURL: string | undefined }, use, info) =>
      use(await signedInState(browser, baseURL as string, pagesAdmin(info.project.name))),
  });

  test("every area renders for an admin: words, one lime action, axe clean, no sideways scroll", async ({
    page,
    consoleErrors,
  }, info) => {
    // Fifteen views, each audited with axe and photographed: well past the default 60 s on WebKit.
    test.setTimeout(240_000);
    await go(page, "/settings/account");
    const link = page.getByTestId("admin-link");
    await expect(link).toHaveText(c("admin.account_link"));
    await link.click();
    await expect(page.getByRole("heading", { level: 1, name: c("admin.overview.title") })).toBeVisible();
    await settle(page);
    const nav = page.getByRole("navigation", { name: c("admin.nav.label") });
    for (const k of [
      "overview",
      "chapters",
      "media",
      "questions",
      "queues",
      "reports",
      "coverage",
      "config",
      "agent_ops",
    ]) {
      await expect(nav.getByRole("link", { name: c(`admin.nav.${k}`) })).toBeVisible();
    }
    const pages: [string, string][] = [
      ["/admin", "admin.overview.title"],
      ["/admin/chapters", "admin.chapters.title"],
      ["/admin/media", "admin.media.title"],
      ["/admin/questions", "admin.questions.title"],
      ["/admin/queues", "admin.queues.title"],
      ["/admin/reports", "admin.reports.title"],
      ["/admin/coverage", "admin.coverage.title"],
      ["/admin/config", "admin.config.title"],
      ["/admin/agent-ops", "admin.ops.title"],
    ];
    for (const [url, key] of pages) {
      await go(page, url);
      await expect(page.getByRole("heading", { level: 1, name: c(key) })).toBeVisible();
      await expect(page.locator("[aria-busy='true']")).toHaveCount(0, { timeout: 20_000 });
      await expect(page.getByTestId("admin-permission")).toHaveCount(0);
      // one lime CTA per view at most (status is words + icons, never colour alone)
      expect(await page.locator("main [data-variant='primary'], main a.bg-lime").count()).toBeLessThanOrEqual(1);
      await expectNoHorizontalOverflow(page);
      await expectNoAxeViolations(page);
      await snap(page, info, consoleErrors, url.replace(/\//g, "_").replace(/^_/, "") || "admin");
    }
    // The config editor: form, JSON view and history for one key (read-only here).
    await go(page, "/admin/config/feature_flags");
    await expect(page.getByRole("button", { name: c("admin.config.view_json") })).toBeVisible();
    await expect(page.getByRole("heading", { name: c("admin.config.history") })).toBeVisible();
    await expectNoAxeViolations(page);
    await expectNoHorizontalOverflow(page);
    await snap(page, info, consoleErrors, "config-feature_flags", true);

    // The detail views (read-only here; the authoring flow edits them on one project).
    const auth = await bearer(page);
    const get = async <T>(url: string) => api<T>(page.request, auth, "GET", url);
    await go(page, "/admin/questions/new");
    await expect(page.getByRole("heading", { level: 1, name: c("admin.questions.new_title") })).toBeVisible();
    await expect(page.locator("[data-question-preview]")).toBeVisible();
    await expectNoHorizontalOverflow(page);
    await expectNoAxeViolations(page);
    await snap(page, info, consoleErrors, "question-form", true);

    const chapters = await get<{ items: { id: string; status: string; section_count: number }[] }>(
      "/api/admin/chapters?exam=step1&status=published",
    );
    const withSections = chapters.items.find((ch) => ch.section_count > 0);
    if (withSections) {
      const detail = await get<{ sections: { id: string }[] }>(
        `/api/admin/chapters/${encodeURIComponent(withSections.id)}`,
      );
      const sid = detail.sections[0]?.id;
      if (sid) {
        await go(page, `/admin/sections/${encodeURIComponent(sid)}/edit`);
        await expect(page.getByRole("textbox", { name: c("admin.editor.body_label") })).toBeVisible();
        await expect(page.locator("[data-save-state='clean']")).toBeVisible();
        await expect(page.locator("[aria-busy='true']")).toHaveCount(0, { timeout: 20_000 });
        await expectNoHorizontalOverflow(page);
        await expectNoAxeViolations(page);
        await snap(page, info, consoleErrors, "section-editor");
      }
    }

    const queues = await get<{ items: { kind: string; count: number; decides_here: boolean }[] }>("/api/admin/queues");
    const decidable =
      queues.items.find((q) => q.decides_here && q.count > 0) ?? queues.items.find((q) => q.decides_here);
    if (decidable) {
      await go(page, `/admin/queues/${decidable.kind}`);
      await expect(page.locator("[aria-busy='true']")).toHaveCount(0, { timeout: 20_000 });
      if (decidable.count > 0) await expect(page.locator("[data-focused-item]")).toBeVisible();
      await expectNoHorizontalOverflow(page);
      await expectNoAxeViolations(page);
      await snap(page, info, consoleErrors, "queue-decision", true);
    }
  });
});

test.describe("non-admins", () => {
  test.use({
    storageState: async ({ browser, baseURL }: { browser: Browser; baseURL: string | undefined }, use) =>
      use(await signedInState(browser, baseURL as string, "student")),
  });

  test("see no admin link, get the permission state and a 403 from the API", async ({ page, consoleErrors }, info) => {
    await go(page, "/settings/account");
    await expect(page.getByTestId("admin-link")).toHaveCount(0);
    await expect(page.locator("a[href^='/admin']")).toHaveCount(0);
    await go(page, "/");
    await expect(page.locator("a[href^='/admin']")).toHaveCount(0);
    await go(page, "/admin/chapters");
    await expect(page.getByTestId("admin-permission")).toContainText(c("admin.permission.title"));
    await expectNoAxeViolations(page);
    await snap(page, info, consoleErrors, "permission");
    const auth = await bearer(page);
    for (const url of [
      "/api/admin/chapters",
      "/api/admin/queues",
      "/api/admin/coverage?exam=step1",
      "/api/admin/config",
    ]) {
      const res = await page.request.get(url, { headers: { authorization: auth } });
      expect(res.status(), url).toBe(403);
    }
    const anon = await page.request.get("/api/admin/chapters");
    expect(anon.status()).toBe(401);
  });
});

// A tiny original SVG (drawn for this test; no third-party content) and a one-page PDF.
const SVG = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 60" width="120" height="60"><rect x="4" y="4" width="112" height="52" rx="8" fill="none" stroke="#333" stroke-width="3"/><circle cx="40" cy="30" r="12" fill="#333"/><circle cx="80" cy="30" r="12" fill="none" stroke="#333" stroke-width="3"/></svg>`;
function onePagePdf(): Buffer {
  const objs = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R /Resources << >> >>",
    "<< /Length 0 >>\nstream\n\nendstream",
  ];
  let out = "%PDF-1.4\n";
  const offsets: number[] = [];
  objs.forEach((o, i) => {
    offsets.push(out.length);
    out += `${i + 1} 0 obj\n${o}\nendobj\n`;
  });
  const xref = out.length;
  out += `xref\n0 ${objs.length + 1}\n0000000000 65535 f \n${offsets.map((o) => `${String(o).padStart(10, "0")} 00000 n \n`).join("")}`;
  out += `trailer\n<< /Size ${objs.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return Buffer.from(out, "latin1");
}
// Big Buck Bunny, Blender Foundation (CC BY 3.0), embeddable on YouTube: played only through the IFrame player.
const YOUTUBE_URL = "https://www.youtube.com/watch?v=aqz-KE-bpKQ";

interface Src {
  id: string;
  name: string;
  license: string;
  allow_copy: boolean;
  allow_commercial: boolean;
  verified: boolean;
  usable_for?: string[];
}

/** A disabled, admin-only flag no screen reads: the config edit/history/revert step changes nothing anyone sees. */
const PROBE_FLAG = "e2e_admin_probe";

test.describe("authoring flow", () => {
  test.skip(isLive(), "writes shared content and global config: local stack only, never against a deployed Space");
  test.describe.configure({ mode: "serial", timeout: 300_000 });

  test("admin adds a chapter, section, figure, PDF, YouTube video and question; a student sees each; edit + rollback; config edit + revert; coverage updates", async ({
    browser,
    baseURL,
  }, info) => {
    test.skip(info.project.name !== "webkit-ipad-portrait", "shared content and global config: runs in one project");
    const origin = baseURL as string;
    const adminCtx = await browser.newContext({
      ...info.project.use,
      baseURL: origin,
      storageState: await signedInState(browser, origin, "admin"),
    });
    const studentCtx = await browser.newContext({
      ...info.project.use,
      baseURL: origin,
      storageState: await signedInState(browser, origin, "student"),
    });
    const admin = await adminCtx.newPage();
    const student = await studentCtx.newPage();
    const errors: string[] = [];
    for (const p of [admin, student]) {
      p.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
      p.on("console", (m) => {
        if (m.type() === "error" && !/Failed to load resource/.test(m.text())) {
          errors.push(m.text());
          console.log(`console error @ ${p.url()}: ${m.text()}`);
        }
      });
    }
    const stamp = `${Date.now().toString(36)}${info.repeatEachIndex}${info.retry}`;
    const chapterTitle = `E2E admin chapter ${stamp}`;
    const sectionHeading = `E2E section ${stamp}`;
    let chapterId = "";
    let questionId = "";
    // feature_flags as found, put back in `finally` whatever happens in between.
    let flagsOriginal: { value: unknown; admin_edited_at: string | null } | null = null;

    await go(admin, "/settings/account");
    const auth = await bearer(admin);
    const sources = (await api<{ items: Src[] }>(admin.request, auth, "GET", "/api/admin/sources")).items;
    const usable = (s: Src, use: string) => !s.usable_for || s.usable_for.includes(use);
    // Naadi's own content: usable for stored files, pages and text (uploads, the chapter, the question).
    const original = sources.find((s) => s.id === "naadi-original-v1");
    expect(original, "the naadi-original-v1 source").toBeTruthy();
    for (const use of ["host", "page", "text"]) expect(usable(original as Src, use), use).toBe(true);
    const youtubeSource =
      sources.find((s) => /youtube/i.test(`${s.id} ${s.name} ${s.license}`) && s.verified && usable(s, "embed")) ??
      sources.find((s) => s.verified && usable(s, "embed"));
    expect(youtubeSource, "a source usable for embedded video").toBeTruthy();
    // One topic, chosen and later asserted by id (labels can collide across taxonomy kinds).
    const topic = (
      await api<{ items: { id: string; kind: string; label: string }[] }>(
        admin.request,
        auth,
        "GET",
        "/api/admin/taxonomy?exam=step1",
      )
    ).items
      .filter((n) => n.kind === "topic")
      .sort((a, b) => a.id.localeCompare(b.id))[0];
    expect(topic, "a step1 topic").toBeTruthy();
    const topicId = (topic as { id: string }).id;
    const coverageBefore = await api<{ topics: { id: string; counts: Record<string, number> }[] }>(
      admin.request,
      auth,
      "GET",
      "/api/admin/coverage?exam=step1",
    );

    try {
      // 0. A deterministic feature_flags edit target, set through the admin API (restored in `finally`).
      const flagsNow = await api<{
        value: Record<string, unknown>;
        updated_at: string | null;
        admin_edited_at: string | null;
      }>(admin.request, auth, "GET", "/api/admin/config/feature_flags");
      flagsOriginal = { value: flagsNow.value, admin_edited_at: flagsNow.admin_edited_at };
      const flagsSetup = {
        ...flagsNow.value,
        [PROBE_FLAG]: { enabled: false, audience: "admins", everyone_outside_prod: false },
      };
      await api(admin.request, auth, "PUT", "/api/admin/config/feature_flags", {
        value: flagsSetup,
        expected_updated_at: flagsNow.updated_at,
        admin_edited: true,
      });

      // 1. Chapter + section through the UI.
      await go(admin, "/admin/chapters?exam=step1");
      await admin.getByRole("button", { name: c("admin.chapters.new") }).click();
      const dialog = admin.getByRole("dialog", { name: c("admin.chapters.new_title") });
      await dialog.getByLabel(c("admin.field.title"), { exact: true }).fill(chapterTitle);
      await dialog.getByLabel(c("admin.field.source")).selectOption((original as Src).id);
      await dialog.getByRole("button", { name: c("admin.chapters.create") }).click();
      await admin.waitForURL(/\/admin\/chapters\/[^/]+$/);
      chapterId = decodeURIComponent(new URL(admin.url()).pathname.split("/").pop() ?? "");
      await expect(admin.getByRole("heading", { level: 1, name: chapterTitle })).toBeVisible();
      await admin.getByRole("button", { name: c("admin.sections.new") }).click();
      const sd = admin.getByRole("dialog", { name: c("admin.sections.new_title") });
      await sd.getByLabel(c("admin.field.heading")).fill(sectionHeading);
      await sd.getByRole("button", { name: c("admin.field.taxonomy_open") }).click();
      await sd.getByLabel(c("admin.field.taxonomy_filter")).fill(topicId);
      await sd.locator(`input[type='checkbox'][data-taxonomy-id='${topicId}']`).check();
      await sd.getByRole("button", { name: c("admin.sections.create") }).click();
      await expect(admin.getByText(sectionHeading, { exact: true }).first()).toBeVisible();
      const saved = await api<{ sections: { heading: string; taxonomy_ids: string[] }[] }>(
        admin.request,
        auth,
        "GET",
        `/api/admin/chapters/${encodeURIComponent(chapterId)}`,
      );
      expect(saved.sections.find((x) => x.heading === sectionHeading)?.taxonomy_ids).toContain(topicId);

      // 2. Figure + PDF uploads (raw body, source required) and approval.
      await go(admin, "/admin/media");
      const mediaIds: Record<string, string> = {};
      for (const [name, mime, body, kindWord] of [
        [`e2e-fig-${stamp}.svg`, "image/svg+xml", Buffer.from(SVG), "figure"],
        [`e2e-doc-${stamp}.pdf`, "application/pdf", onePagePdf(), "pdf"],
      ] as const) {
        const chooser = admin.waitForEvent("filechooser");
        await admin.getByRole("button", { name: c("admin.media.choose_file") }).click();
        await (await chooser).setFiles({ name, mimeType: mime, buffer: body });
        const up = admin.getByRole("dialog");
        await up.getByLabel(c("admin.field.source")).selectOption((original as Src).id);
        const created = admin.waitForResponse(
          (r) => r.url().includes("/api/admin/media/files") && r.request().method() === "POST",
        );
        await up.getByRole("button", { name: c("admin.media.upload") }).click();
        const res = await created;
        expect(res.status(), await res.text()).toBe(201);
        mediaIds[kindWord] = ((await res.json()) as { id: string }).id;
        await expect(up).toHaveCount(0);
      }
      // 3. YouTube by URL: lookup shows title, channel, embeddable (word), then add.
      await admin.getByRole("button", { name: c("admin.media.youtube_open") }).click();
      const yt = admin.getByRole("dialog", { name: c("admin.media.youtube_title") });
      await yt.getByLabel(c("admin.media.youtube_url")).fill(YOUTUBE_URL);
      await yt.getByRole("button", { name: c("admin.media.youtube_lookup") }).click();
      const lookup = yt.getByTestId("youtube-lookup");
      await expect(lookup).toContainText(c("admin.media.embeddable_yes"), { timeout: 30_000 });
      const existing = await yt.getByText(cre("admin.media.yt_existing")).count();
      if (existing) {
        mediaIds.video =
          ((await yt.getByText(cre("admin.media.yt_existing")).textContent()) ?? "").match(/\S+(?=\.$)/)?.[0] ?? "";
        await yt.getByRole("button", { name: c("admin.action.close") }).click();
      } else {
        await yt.getByLabel(c("admin.field.source")).selectOption((youtubeSource as Src).id);
        const added = admin.waitForResponse(
          (r) => r.url().endsWith("/api/admin/media/youtube") && r.request().method() === "POST",
        );
        await yt.getByRole("button", { name: c("admin.media.youtube_add") }).click();
        const res = await added;
        expect(res.status(), await res.text()).toBe(201);
        mediaIds.video = ((await res.json()) as { id: string }).id;
      }
      // Approve each in its detail dialog (publishing refuses unapproved media).
      for (const id of Object.values(mediaIds)) {
        await go(admin, `/admin/media?q=${encodeURIComponent(id)}`);
        await admin.locator(`[data-media-id="${id}"]`).click();
        const d = admin.getByRole("dialog");
        const approve = d.getByRole("button", { name: c("admin.action.approve"), exact: true });
        if (await approve.isEnabled()) await approve.click();
        else await d.getByRole("button", { name: c("admin.action.close") }).click();
        await expect(d).toHaveCount(0);
      }

      // 4. A question through the full form, sent through review to published.
      await go(admin, "/admin/questions/new");
      const form = admin.getByRole("region", { name: c("admin.questions.form") });
      await form.getByLabel(c("admin.questions.stem")).fill(`<p>E2E ${stamp}: which option is the test key?</p>`);
      for (const l of ["A", "B", "C", "D", "E"]) {
        await form.getByLabel(c("admin.questions.option", { letter: l })).fill(`<p>Option ${l}</p>`);
      }
      await form.getByLabel(c("admin.questions.key_is", { letter: "C" })).check();
      await form.getByLabel(c("admin.questions.why_not_option", { letter: "A" })).fill("A is a distractor.");
      // A publishable item passes the S21 linter: every wrong option explained, an objective and a difficulty.
      for (const l of ["B", "D", "E"]) {
        await form.getByLabel(c("admin.questions.why_not_option", { letter: l })).fill(`${l} is a distractor.`);
      }
      await form.getByLabel(c("admin.questions.objective")).fill("Recognise the key of a test item.");
      await form.getByLabel(c("admin.questions.difficulty_label")).selectOption("easy");
      await form.getByLabel(c("admin.questions.explanation")).fill("<p>C is the key in this test item.</p>");
      await form.getByLabel(c("admin.field.source")).selectOption((original as Src).id);
      await form.getByRole("button", { name: c("admin.questions.create") }).click();
      await admin.waitForURL(/\/admin\/questions\/(?!new)[^/]+$/);
      questionId = decodeURIComponent(new URL(admin.url()).pathname.split("/").pop() ?? "");
      // The preview hides the key until "Reveal answer".
      const preview = admin.locator("[data-question-preview]");
      await expect(preview.getByText(c("admin.questions.correct_answer"))).toHaveCount(0);
      await preview.getByRole("button", { name: c("admin.questions.reveal_answer") }).click();
      await expect(preview.getByText(c("admin.questions.correct_answer"))).toBeVisible();
      for (const step of ["admin.workflow.to_review", "admin.workflow.to_published"]) {
        await admin
          .getByRole("button", { name: c(step) })
          .first()
          .click();
        await admin
          .getByRole("dialog")
          .getByRole("button", { name: c("admin.action.confirm") })
          .click();
        await expect(admin.getByRole("dialog")).toHaveCount(0);
      }
      await expect(admin.getByText(c("admin.status.published")).first()).toBeVisible();

      // 5. The editor: text, then every media block and the question, autosaved, then Publish (the lime CTA).
      await go(admin, `/admin/chapters/${encodeURIComponent(chapterId)}`);
      await admin
        .getByRole("link", { name: new RegExp(`${c("admin.sections.edit_body")}.*${sectionHeading}`) })
        .click();
      await admin.waitForURL(/\/admin\/sections\/.+\/edit$/);
      const sectionId = decodeURIComponent(new URL(admin.url()).pathname.split("/").slice(-2)[0] ?? "");
      const body = admin.getByRole("textbox", { name: c("admin.editor.body_label") });
      await body.click();
      const v1 = `First version ${stamp}.`;
      await admin.keyboard.type(v1);
      await expect(body).toContainText(v1);
      await expect(admin.locator("[data-save-state='saved']")).toBeVisible({ timeout: 20_000 });
      const insert = async (tool: string, pick: string, extra?: () => Promise<void>) => {
        await admin.getByRole("button", { name: c(tool) }).click();
        const d = admin.getByRole("dialog");
        await d.getByRole("searchbox").first().fill(pick);
        await d.getByRole("searchbox").first().press("Enter");
        await d.getByRole("button", { pressed: false }).filter({ hasText: pick }).first().click();
        if (extra) await extra();
        await d.getByRole("button", { name: c("admin.editor.insert") }).click();
        await expect(d).toHaveCount(0);
      };
      await insert("admin.editor.insert_figure", mediaIds.figure ?? "");
      await insert("admin.editor.insert_pdf", mediaIds.pdf ?? "", async () => {
        await admin.getByRole("dialog").getByLabel(c("admin.editor.pdf_page")).fill("1");
      });
      await insert("admin.editor.insert_video", mediaIds.video ?? "", async () => {
        await admin.getByRole("dialog").getByLabel(c("admin.editor.start_s")).fill("10");
      });
      await insert("admin.editor.insert_question", questionId);
      await expect(admin.locator("[data-save-state='saved']")).toBeVisible({ timeout: 20_000 });
      const preview2 = admin.locator("[data-admin-preview]");
      if ((admin.viewportSize()?.width ?? 0) < 1100) {
        await admin.getByRole("button", { name: c("admin.editor.view_preview"), exact: true }).click();
      }
      await expect(preview2.getByText(v1)).toBeVisible();
      await expect(preview2.locator("[data-node='question-embed']")).toBeVisible();
      await expectNoAxeViolations(admin);
      if ((admin.viewportSize()?.width ?? 0) < 1100) {
        await admin.getByRole("button", { name: c("admin.editor.view_edit"), exact: true }).click();
      }
      const publish = async () => {
        await admin.getByRole("button", { name: c("admin.editor.publish"), exact: true }).click();
        await admin
          .getByRole("dialog")
          .getByRole("button", { name: c("admin.editor.publish_confirm") })
          .click();
        await expect(admin.getByRole("dialog")).toHaveCount(0);
        await expect(admin.locator("[data-save-state='clean']")).toBeVisible();
      };
      await publish();

      // Chapter through review to published.
      await go(admin, `/admin/chapters/${encodeURIComponent(chapterId)}`);
      for (const step of ["admin.workflow.to_review", "admin.workflow.to_published"]) {
        await admin
          .getByRole("button", { name: c(step) })
          .first()
          .click();
        await admin
          .getByRole("dialog")
          .getByRole("button", { name: c("admin.action.confirm") })
          .click();
        await expect(admin.getByRole("dialog")).toHaveCount(0);
      }

      // 6. The student sees all of it, with no redeploy.
      const reader = `/learn/${encodeURIComponent(chapterId)}/${encodeURIComponent(sectionId)}`;
      await go(student, reader);
      await expect(student.locator("[data-content-root]")).toContainText(v1);
      await expect(student.locator(`[data-node='figure'][data-media-id='${mediaIds.figure}'] img`)).toBeVisible();
      await expect(student.locator("[data-node='pdf'] a")).toBeVisible();
      await expect(student.locator(`[data-node='video'][data-media-id='${mediaIds.video}']`)).toBeVisible();
      await expect(student.locator(`[data-node='question-embed'][data-question-id='${questionId}']`)).toBeVisible();

      // 7. Edit + rollback: a second version goes live, then the first is restored and published again.
      await go(admin, `/admin/sections/${encodeURIComponent(sectionId)}/edit`);
      const v2 = ` Second version ${stamp}.`;
      // Caret at the end of the first paragraph: click its far right (Home/End keys differ per platform).
      const first = admin
        .getByRole("textbox", { name: c("admin.editor.body_label") })
        .locator("p")
        .first();
      const box = await first.boundingBox();
      await first.click({ position: { x: Math.max(1, (box?.width ?? 2) - 2), y: (box?.height ?? 2) / 2 } });
      await admin.keyboard.type(v2);
      await expect(admin.locator("[data-save-state='saved']")).toBeVisible({ timeout: 20_000 });
      await publish();
      await go(student, reader);
      await expect(student.locator("[data-content-root]")).toContainText(`${v1}${v2}`);
      await go(admin, `/admin/sections/${encodeURIComponent(sectionId)}/edit`);
      const history = admin.getByRole("region", { name: c("admin.revision.title") });
      await history
        .getByRole("button", { name: c("admin.revision.show_diff") })
        .first()
        .click();
      await expect(history.getByText(c("admin.diff.insert")).first()).toBeVisible();
      // The oldest restorable version is the first publish. Restoring publishes a copy of it at once.
      const restored = admin.waitForResponse((r) => /\/api\/admin\/revisions\/\d+\/rollback$/.test(r.url()));
      await history.locator("[data-rollback]").last().click();
      expect((await restored).status()).toBe(200);
      await expect(admin.getByRole("textbox", { name: c("admin.editor.body_label") })).not.toContainText(v2.trim());
      await expect(admin.locator("[data-save-state='clean']")).toBeVisible();
      await go(student, reader);
      await expect(student.locator("[data-content-root]")).toContainText(v1);
      await expect(student.locator("[data-content-root]")).not.toContainText(v2.trim());

      // 8. Coverage: the topic's published-section count went up.
      const after = await api<{ topics: { id: string; counts: Record<string, number> }[] }>(
        admin.request,
        auth,
        "GET",
        "/api/admin/coverage?exam=step1",
      );
      const before = coverageBefore.topics.find((x) => x.id === topicId)?.counts.sections ?? 0;
      expect(after.topics.find((x) => x.id === topicId)?.counts.sections ?? 0).toBeGreaterThan(before);
      await go(admin, "/admin/coverage?exam=step1");
      await expect(admin.locator(`[data-topic='${topicId}']`)).toBeVisible();
      await expect(admin.getByTestId("coverage-grid")).toBeVisible();

      // 9. feature_flags: edit the probe flag in the UI (diff before save) → history shows it → revert.
      const flagsBefore = await api<{ value: Record<string, Record<string, unknown>> }>(
        admin.request,
        auth,
        "GET",
        "/api/admin/config/feature_flags",
      );
      const edited = {
        ...flagsBefore.value,
        [PROBE_FLAG]: { ...flagsBefore.value[PROBE_FLAG], everyone_outside_prod: true },
      };
      await go(admin, "/admin/config/feature_flags");
      await admin.getByRole("button", { name: c("admin.config.view_json") }).click();
      await admin.getByLabel(c("admin.config.json_label")).fill(JSON.stringify(edited, null, 2));
      await admin.getByRole("button", { name: cre("admin.config.review") }).click();
      await expect(admin.getByRole("region", { name: c("admin.config.diff_label") })).toContainText(
        "everyone_outside_prod",
      );
      await admin.getByRole("button", { name: c("admin.config.save") }).click();
      await expect(admin.getByText(c("admin.config.saved_live"))).toBeVisible();
      const savedFlags = await api<{ value: Record<string, Record<string, unknown>> }>(
        admin.request,
        auth,
        "GET",
        "/api/admin/config/feature_flags",
      );
      expect(savedFlags.value[PROBE_FLAG]?.everyone_outside_prod).toBe(true);
      const hist = admin.getByTestId("config-history");
      await expect(hist.locator("li").first()).toBeVisible();
      await hist
        .getByRole("button", { name: c("admin.config.revert") })
        .first()
        .click();
      await expect
        .poll(async () =>
          JSON.stringify(
            (await api<{ value: unknown }>(admin.request, auth, "GET", "/api/admin/config/feature_flags")).value,
          ),
        )
        .toBe(JSON.stringify(flagsBefore.value));
      expect(errors, "console errors").toEqual([]);
    } finally {
      // Leave nothing a student would see: retire the question and the chapter, and put feature_flags back as they
      // were found (a published test question would stay in every later student's Step 1 pool).
      if (questionId) {
        const url = `/api/admin/questions/${encodeURIComponent(questionId)}`;
        const q = await admin.request
          .get(url, { headers: { authorization: auth } })
          .then((r) => r.json() as Promise<{ status: string; version: string }>)
          .catch(() => null);
        if (q?.status === "published") {
          await admin.request
            .post(`${url}/status`, {
              headers: { authorization: auth },
              data: { status: "retired", expected_version: q.version, note: "e2e cleanup" },
            })
            .catch(() => undefined);
        }
      }
      if (chapterId) {
        await admin.request
          .post(`/api/admin/chapters/${encodeURIComponent(chapterId)}/status`, {
            headers: { authorization: auth },
            data: { status: "retired", note: "e2e cleanup" },
          })
          .catch(() => undefined);
      }
      if (flagsOriginal) {
        const now = await api<{ updated_at: string | null }>(
          admin.request,
          auth,
          "GET",
          "/api/admin/config/feature_flags",
        );
        const res = await admin.request.put("/api/admin/config/feature_flags", {
          headers: { authorization: auth },
          data: {
            value: flagsOriginal.value,
            expected_updated_at: now.updated_at,
            admin_edited: flagsOriginal.admin_edited_at !== null,
          },
        });
        expect(res.status(), "feature_flags restored").toBe(200);
      }
      await adminCtx.close();
      await studentCtx.close();
    }
  });
});
