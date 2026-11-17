import { mkdirSync } from "node:fs";
import path from "node:path";
import type { Page, Route } from "@playwright/test";
import { canMintUsers } from "./auth-users";
import { expect, expectNoAxeViolations, expectNoHorizontalOverflow, quiet, test } from "./fixtures";
import { freshStudent, richChapter, watchConsole } from "./support/student";

/**
 * Segment 20: the YouTube IFrame Player API player, its chapter list, speed control and timestamped notes, against
 * the real API and database. The official player runs in a sandboxed page with an opaque origin and talks to the
 * app over a typed postMessage bridge (src/video/bridge.ts). CI never contacts YouTube: the API script is answered
 * here and a fake `YT.Player` stands in for the player inside that page (the real one is covered by the device
 * checklist).
 */
test.skip(!canMintUsers(), "signed-in tests need the service role (run pnpm e2e:live-auth against the Space)");

const SHOTS = path.resolve(import.meta.dirname, "../../../.tmp/shots/video");

interface Fake {
  time: number;
  rate: number;
  seeks: number[];
  played: number;
}
declare global {
  interface Window {
    __yt: Fake;
  }
}

/** The fake player lives in the sandboxed player page (an opaque-origin frame), not in the app's page. */
function sandbox(page: Page) {
  const f = page.frames().find((fr) => fr.url().endsWith("/sandbox/youtube.html"));
  if (!f) throw new Error("the sandboxed player frame is not there");
  return f;
}
const fake = <T>(page: Page, read: (y: Fake) => T): Promise<T> =>
  sandbox(page).evaluate(`window.__yt ? (${read.toString()})(window.__yt) : undefined`) as Promise<T>;
const setTime = (page: Page, t: number) => sandbox(page).evaluate(`window.__yt.time = ${t}`);

const FAKE_API = `
window.__yt = { time: 0, rate: 1, seeks: [], played: 0 };
window.YT = { Player: function (el, opts) {
  this.playVideo = function () { window.__yt.played++; };
  this.pauseVideo = function () {};
  this.seekTo = function (s) { window.__yt.seeks.push(s); window.__yt.time = s; };
  this.getCurrentTime = function () { return window.__yt.time; };
  this.getPlayerState = function () { return 2; };
  this.setPlaybackRate = function (r) { window.__yt.rate = r; };
  this.getPlaybackRate = function () { return window.__yt.rate; };
  this.getAvailablePlaybackRates = function () { return [0.5, 1, 1.5, 2]; };
  this.destroy = function () {};
  var self = this;
  setTimeout(function () { opts.events.onReady({ target: self }); }, 20);
} };
setTimeout(function () { window.onYouTubeIframeAPIReady(); }, 0);
`;

/** Answer YouTube's two hosts locally; returns the hosts that were asked for (to prove nothing loads before play). */
async function stubYouTube(page: Page): Promise<string[]> {
  const asked: string[] = [];
  page.on("request", (r) => {
    const host = new URL(r.url()).hostname;
    if (host.endsWith("youtube.com") || host.endsWith("youtube-nocookie.com") || host.endsWith("ytimg.com")) {
      asked.push(r.url());
    }
  });
  await page.route("https://www.youtube.com/iframe_api", (route: Route) =>
    route.fulfill({ status: 200, contentType: "text/javascript", body: FAKE_API }),
  );
  await page.route("https://www.youtube-nocookie.com/**", (route: Route) =>
    route.fulfill({ status: 200, contentType: "text/html", body: "<!doctype html><title>player</title>" }),
  );
  return asked;
}

test.describe("YouTube player and video notes", () => {
  test("facade → play → chapters, speed, note at a time → tap seeks → reload keeps the note", async ({
    browser,
    baseURL,
  }, info) => {
    test.setTimeout(180_000);
    const student = await freshStudent(browser, baseURL as string, info, "video");
    try {
      let asked: string[] = [];
      const page = await student.open("/learn", async (p) => {
        asked = await stubYouTube(p);
      });
      const { errors, guard } = watchConsole(page);
      const rich = richChapter("create", student.email);
      await page.goto(`/learn/${rich.chapter_id}/${rich.section_id}`);
      await quiet(page);

      const play = page.getByRole("button", { name: /^Play video: Anion gap/ });
      await expect(play).toBeVisible();
      expect(asked, "nothing is requested from YouTube before the press").toEqual([]);
      await expectNoAxeViolations(page);

      await play.scrollIntoViewIfNeeded();
      await play.click();
      const frame = page.locator("[data-node=video] iframe");
      await expect(frame).toHaveAttribute("referrerpolicy", "strict-origin-when-cross-origin");
      await expect(frame).toHaveAttribute("src", "/sandbox/youtube.html");
      await expect(frame).toHaveAttribute("sandbox", "allow-scripts");
      // init over the bridge reached the player page, which started the (fake) official player and played
      await expect.poll(() => fake(page, (y) => y.played)).toBe(1);
      // the app's own document allows no third-party script; the player page has its own, sandboxed policy
      const appCsp = (await page.request.get("/")).headers()["content-security-policy"] ?? "";
      expect(appCsp).toContain("script-src 'self';");
      expect(appCsp).not.toContain("youtube");
      const playerCsp = (await page.request.get("/sandbox/youtube.html")).headers()["content-security-policy"] ?? "";
      expect(playerCsp).toContain("script-src 'self' https://www.youtube.com");
      expect(playerCsp).toContain("sandbox allow-scripts");
      expect(playerCsp).not.toContain("allow-same-origin");

      // source line and Terms link under the player
      const video = page.locator("[data-node=video]");
      await expect(video.getByRole("link", { name: "YouTube Terms of Service" })).toHaveAttribute(
        "href",
        "https://www.youtube.com/t/terms",
      );

      // chapters (the node starts at 0:30) seek on tap; speed uses the player's rates
      await video.getByRole("button", { name: /^0:30\s*Part/ }).click();
      await expect.poll(() => fake(page, (y) => y.seeks)).toEqual([30]);
      await video.getByRole("button", { name: "1.5×", exact: true }).click();
      await expect.poll(() => fake(page, (y) => y.rate)).toBe(1.5);
      await expect(video.getByRole("button", { name: "1.5×", exact: true })).toHaveAttribute("aria-pressed", "true");

      // a note at 12:34
      await setTime(page, 754);
      const noteAt = video.getByRole("button", { name: "Note at 12:34" });
      await expect(noteAt).toBeVisible();
      await noteAt.click();
      await video.getByRole("textbox").fill("Anion gap is Na minus Cl and HCO3");
      const saved = page.waitForResponse(
        (r) => new URL(r.url()).pathname === "/api/notes" && r.request().method() === "POST" && r.status() === 201,
      );
      await video.getByRole("button", { name: "Save note" }).click();
      const created = (await (await saved).request().postDataJSON()) as {
        anchor_type: string;
        anchor_pos: { t: number };
      };
      expect(created.anchor_type).toBe("video");
      expect(created.anchor_pos).toEqual({ t: 754 });
      const item = video.locator('button[data-note-t="754"]');
      await expect(item).toContainText("Anion gap is Na minus Cl and HCO3");

      mkdirSync(SHOTS, { recursive: true });
      await expectNoAxeViolations(page);
      await expectNoHorizontalOverflow(page);
      await guard.duringTool(() =>
        page.screenshot({
          path: path.join(SHOTS, `player--${info.project.name}.png`),
          fullPage: true,
          animations: "disabled",
        }),
      );

      // tap the note: the player jumps there
      await setTime(page, 0);
      await item.click();
      await expect.poll(() => fake(page, (y) => y.seeks.at(-1))).toBe(754);

      // the note is on the server: reload, play, the list shows it again
      await page.reload();
      await quiet(page);
      await page.getByRole("button", { name: /^Play video: Anion gap/ }).click();
      await expect(page.locator("[data-node=video]").locator('button[data-note-t="754"]')).toBeVisible();
      expect(errors, "console errors").toEqual([]);
    } finally {
      try {
        richChapter("delete", student.email);
      } finally {
        await student.done();
      }
    }
  });
});
