import type { Page, Route, TestInfo } from "@playwright/test";
import { canMintUsers } from "./auth-users";
import { expect, expectNoAxeViolations, expectNoHorizontalOverflow, test } from "./fixtures";
import { freshStudent, watchConsole } from "./support/student";

/**
 * The Videos section against the real API and the seeded catalogue (data/videos.json, group `videos`): browse, search,
 * filters, the player pane (side pane on wide screens, sheet on portrait and phone), watched marks, "Ask Naadi about
 * this video" and the topic map. Card stills and every other YouTube, player Space or Wikimedia host are answered
 * from here (the real players are video.spec / video-live.spec and the device checklist).
 */
test.skip(!canMintUsers(), "signed-in tests need the service role (run pnpm e2e:live-auth against the Space)");

const PLAYER_ORIGIN = "https://arrowassassin-naadi-player.static.hf.space";
const YT = { id: "yt--0cxs9l9o0a", query: "Ventricular Pressure-Volume Loops", subject: "Physiology" };
const FILE = { id: "wm-acute-cholecystitis", query: "Acute Cholecystitis", subject: "Internal Medicine" };

// studentEmail() keeps only the letters of the project name, so phone-390 and phone-412 would share one student
const who = (kind: string, info: TestInfo) =>
  kind + [...info.project.name.replace(/\D/g, "")].map((d) => "abcdefghij"[Number(d)]).join("");
const isPhone = (page: Page) => (page.viewportSize()?.width ?? 1024) < 640;
/** The open video: the side pane (wide) or the sheet (portrait, phone). */
const pane = (page: Page) => page.locator("[data-pane], dialog[open]");
const card = (page: Page, id: string) => page.locator(`[data-video="${id}"]`);

async function stubHosts(page: Page): Promise<string[]> {
  const asked: string[] = [];
  page.on("request", (r) => {
    const host = new URL(r.url()).hostname;
    // Commons /thumb/ stills load before play on purpose (like i.ytimg.com stills), so they are not recorded
    if (/(youtube\.com|youtube-nocookie\.com|wikimedia\.org)$/.test(host) && !r.url().includes("/thumb/"))
      asked.push(r.url());
  });
  const html = (route: Route) =>
    route.fulfill({ status: 200, contentType: "text/html", body: "<!doctype html><title>x</title>" });
  await page.route("https://www.youtube.com/**", html);
  await page.route("https://www.youtube-nocookie.com/**", html);
  await page.route(`${PLAYER_ORIGIN}/**`, html);
  // thumbnails: a 1x1 gif, so a card never waits on YouTube
  await page.route("https://i.ytimg.com/**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "image/gif",
      body: Buffer.from("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7", "base64"),
    }),
  );
  // the Commons file: answered with an empty body (the player just fails to decode it; nothing is downloaded)
  await page.route("https://upload.wikimedia.org/**", (route) =>
    route.fulfill(
      route.request().url().includes("/thumb/")
        ? {
            status: 200,
            contentType: "image/gif",
            body: Buffer.from("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7", "base64"),
          }
        : { status: 200, contentType: "video/webm", body: Buffer.alloc(0) },
    ),
  );
  return asked;
}

async function openVideos(page: Page) {
  if (isPhone(page)) {
    // the phone's tab bar holds four tabs and More; Videos is in the More sheet
    await page.getByRole("navigation", { name: "Tabs" }).getByRole("button", { name: "More" }).click();
    await page.locator("[data-more-sheet]").getByRole("link", { name: "Videos" }).click();
  } else {
    await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Videos" }).click();
  }
  await expect(page).toHaveURL(/\/videos/);
  await expect(page.locator("section[aria-labelledby=videos-h]")).toBeVisible();
  await expect(page.locator("[data-video]").first()).toBeVisible();
}

async function search(page: Page, q: string) {
  await page.getByRole("searchbox", { name: "Search videos" }).fill(q);
  await expect(page).toHaveURL(new RegExp(`q=${encodeURIComponent(q).replace(/%20/g, "(%20|\\+)")}`));
}

test.describe("Videos", () => {
  test("browse: steps, search, filters and the topic map", async ({ browser, baseURL }, info) => {
    test.setTimeout(120_000);
    const student = await freshStudent(browser, baseURL as string, info, who("videosbrowse", info));
    try {
      const page = await student.open("/learn", async (p) => {
        await stubHosts(p);
      });
      const { errors } = watchConsole(page);
      await openVideos(page);
      await expectNoHorizontalOverflow(page);
      await expectNoAxeViolations(page);

      // steps: a Step 2 CK list holds no Step 1 group, All holds both
      const step = (name: string) => page.getByRole("group", { name: "Exam" }).getByRole("button", { name });
      await step("Step 2 CK").click();
      await expect(page).toHaveURL(/step=step2ck/);
      await expect(step("Step 2 CK")).toHaveAttribute("aria-pressed", "true");
      await expect(page.locator("main h2, section h2").filter({ hasText: /· Step 1$/ })).toHaveCount(0);
      await expect(
        page
          .locator("main h2, section h2")
          .filter({ hasText: /· Step 2 CK$/ })
          .first(),
      ).toBeVisible();
      await step("Step 1").click();
      await expect(page.locator("main h2, section h2").filter({ hasText: /· Step 2 CK$/ })).toHaveCount(0);
      await step("All").click();
      await expect(page).not.toHaveURL(/step=/);

      // search narrows the list to the video, and clears
      await search(page, YT.query);
      await expect(card(page, YT.id)).toBeVisible();
      expect(await page.locator("[data-video]").count()).toBeLessThan(20);
      await page.getByRole("button", { name: "Clear search" }).click();
      await expect(page).not.toHaveURL(/q=/);

      // filters (phone: behind the Filters button): style, core, not watched
      if (isPhone(page)) {
        await page.getByRole("button", { name: /^Filters/ }).click();
      }
      await search(page, YT.query);
      const style = page.getByRole("group", { name: "Filters" }).getByRole("button", { name: "Sketch" });
      await style.click();
      await expect(page).toHaveURL(/style=sketch/);
      await expect(card(page, YT.id)).toBeVisible();
      await page.getByRole("group", { name: "Filters" }).getByRole("button", { name: "Lecture" }).click();
      await expect(page).toHaveURL(/style=lecture/);
      await expect(card(page, YT.id)).toHaveCount(0);
      await page.getByRole("group", { name: "Filters" }).getByRole("button", { name: "Lecture" }).click();
      await page.getByRole("group", { name: "Filters" }).getByRole("button", { name: "Core only" }).click();
      await expect(page).toHaveURL(/core=1/);
      await page.getByRole("group", { name: "Filters" }).getByRole("button", { name: "Not watched yet" }).click();
      await expect(page).toHaveURL(/unwatched=1/);
      await expect(card(page, YT.id)).toBeVisible();

      // topic map: coverage text and counts per subject
      // one subject: sections below the fold are skipped by content-visibility, which axe measures as zero-sized
      await page.goto("/videos?view=topics&step=step1&subject=Physiology");
      await expect(page.getByText(/\d+ of \d+ topics have videos/).first()).toBeVisible();
      await expect(page.locator('[data-coverage="topic"]').first()).toContainText("Has videos");
      await expect(page.locator('[data-coverage="chapter"]').first()).toContainText("Chapter videos only");
      await expectNoHorizontalOverflow(page);
      await expectNoAxeViolations(page);
      // "Show videos" on a covered topic goes back to the list, filtered to it
      await page.locator('[data-coverage="topic"]').first().getByRole("button").click();
      await expect(page).not.toHaveURL(/view=topics/);
      await expect(page.locator("[data-video]").first()).toBeVisible();
      expect(errors, "console errors").toEqual([]);
    } finally {
      await student.done();
    }
  });

  test("a YouTube video: facade, watched persists across reload, Ask Naadi opens with the video attached", async ({
    browser,
    baseURL,
  }, info) => {
    test.setTimeout(120_000);
    const student = await freshStudent(browser, baseURL as string, info, who("videosyoutube", info));
    try {
      let asked: string[] = [];
      const page = await student.open("/learn", async (p) => {
        asked = await stubHosts(p);
      });
      const { errors } = watchConsole(page);
      await openVideos(page);
      await search(page, YT.query);
      await card(page, YT.id).getByRole("button").first().click();
      await expect(page).toHaveURL(new RegExp(`v=${YT.id}`));
      await expect(pane(page)).toBeVisible();
      // the side pane (wide screens) is brought into view, not left below the filters
      if (await page.locator("[data-pane]").count()) {
        await expect
          .poll(() => page.locator("[data-pane]").evaluate((el) => el.getBoundingClientRect().top))
          .toBeLessThan((page.viewportSize()?.height ?? 800) / 3);
      }

      // the card shows its YouTube still; the facade asks nothing from YouTube's player hosts and loads no Commons video file before the press (thumbnails are fine)
      await expect(card(page, YT.id).locator('img[src^="https://i.ytimg.com/vi/"]')).toHaveAttribute(
        "src",
        /mqdefault\.jpg$/,
      );
      const play = pane(page).getByRole("button", { name: /^Play video:/ });
      await expect(play).toBeVisible();
      expect(asked).toEqual([]);
      await expectNoAxeViolations(page);
      await play.click();
      await expect(pane(page).locator("iframe")).toHaveAttribute("src", new RegExp(`^${PLAYER_ORIGIN}/index\\.html`));

      // mark watched: the count and the pane say so; it is still there after a reload (server state)
      const marked = page.waitForResponse((r) => /\/api\/videos\/[^/]+\/watched$/.test(r.url()) && r.ok());
      await pane(page).getByRole("button", { name: "Mark watched", exact: true }).click();
      await marked;
      await expect(pane(page).getByRole("button", { name: "Watched · undo" })).toBeVisible();
      await page.waitForLoadState("networkidle"); // a reload cancels in-flight fetches (WebKit logs those as errors)
      await page.reload();
      await expect(pane(page).getByRole("button", { name: "Watched · undo" })).toBeVisible();

      // Ask Naadi about this video: the assistant opens with the draft prefilled (not sent) and the video attached
      await pane(page).getByRole("button", { name: "Ask Naadi about this video" }).click();
      const box = page.getByRole("textbox", { name: "Ask Naadi" }).filter({ visible: true }).first();
      await expect(box).toHaveValue(/Explain the key points of the video/);
      const chips = page
        .getByRole("list", { name: /attach/i })
        .filter({ visible: true })
        .first();
      await expect(chips).toContainText("This video");
      expect(errors, "console errors").toEqual([]);
    } finally {
      await student.done();
    }
  });

  test("a Commons video: click-to-load file player, nothing fetched before the press", async ({
    browser,
    baseURL,
  }, info) => {
    test.setTimeout(120_000);
    const student = await freshStudent(browser, baseURL as string, info, who("videosfile", info));
    try {
      let asked: string[] = [];
      const page = await student.open("/learn", async (p) => {
        asked = await stubHosts(p);
      });
      const { errors } = watchConsole(page);
      await openVideos(page);
      await search(page, FILE.query);
      await card(page, FILE.id).getByRole("button").first().click();
      await expect(pane(page)).toBeVisible();
      const file = pane(page).locator('[data-node="file-video"]');
      await expect(file.getByRole("button", { name: /^Play video:/ })).toBeVisible();
      await expect(file).toContainText("Loads the file from Wikimedia Commons");
      // card stills come from Commons' /thumb/ path; the video file itself is not asked for before the press
      await expect(
        card(page, FILE.id).locator('img[src^="https://upload.wikimedia.org/wikipedia/commons/thumb/"]'),
      ).toBeVisible();
      expect(asked.filter((u) => u.includes("wikimedia.org") && !u.includes("/thumb/"))).toEqual([]);
      // licence and attribution stay visible under the player
      await expect(file.getByRole("link", { name: "CC BY-SA 4.0" })).toBeVisible();
      await expectNoAxeViolations(page);
      await file.getByRole("button", { name: /^Play video:/ }).click();
      // the stubbed file is empty, so a browser that decodes it errors at once and the fallback replaces the player:
      // either way the page link to the file on Commons is offered, and the file itself is the one asked for
      await expect(file.locator('video[src^="https://upload.wikimedia.org/"], [role="status"]')).toBeVisible();
      if (await file.getByRole("status").count()) {
        await expect(
          file.getByRole("status").getByRole("link", { name: "File page on Wikimedia Commons" }),
        ).toBeVisible();
      }
      expect(
        errors.filter((e) => !/wikimedia|media resource|Failed to load/i.test(e)),
        "console errors",
      ).toEqual([]);
    } finally {
      await student.done();
    }
  });
});
