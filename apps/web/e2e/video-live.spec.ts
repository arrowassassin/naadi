import { mkdirSync, readFileSync } from "node:fs";
import path from "node:path";
import type { Route } from "@playwright/test";
import { canMintUsers } from "./auth-users";
import { expect, quiet, test } from "./fixtures";
import { freshStudent, richChapter } from "./support/student";

/**
 * @live: the player page's own code with the REAL YouTube IFrame API and embed, from the player's real origin. The
 * deployed page refuses loopback parents (production: the Naadi Space only), so the first test serves the repo's
 * deploy/player-space files at the player origin with the development-only loopback switch on (the only change), while
 * YouTube itself is real. The second test hits the DEPLOYED Space and checks that it refuses this loopback app.
 * Needs the network; runs only on demand:
 *
 *   E2E_LIVE_YOUTUBE=1 pnpm --filter @naadi/web exec playwright test video-live.spec.ts --project chromium-laptop-1440
 *
 * Behind a proxy set E2E_LIVE_PROXY=http://host:port (loopback is bypassed; the proxy's CA is accepted).
 */
test.skip(process.env.E2E_LIVE_YOUTUBE !== "1", "real YouTube: opt in with E2E_LIVE_YOUTUBE=1");
test.skip(!canMintUsers(), "signed-in tests need the service role");

const PLAYER_ORIGIN = process.env.E2E_PLAYER_ORIGIN ?? "https://arrowassassin-naadi-player.static.hf.space";
const SHOTS = path.resolve(import.meta.dirname, "../../../.tmp/shots/video-live");

if (process.env.E2E_LIVE_PROXY) {
  test.use({ proxy: { server: process.env.E2E_LIVE_PROXY, bypass: "127.0.0.1,localhost" } });
}

const PLAYER_DIR = path.resolve(import.meta.dirname, "../../../deploy/player-space");

test.describe("@live YouTube player page", () => {
  test("one press: the real player readies, plays, seeks and changes speed through the bridge", async ({
    browser,
    baseURL,
  }, info) => {
    test.setTimeout(240_000);
    const student = await freshStudent(browser, baseURL as string, info, "videolive");
    try {
      const page = await student.open("/learn");
      // the player origin serves the repo's player files (loopback parent on); YouTube is not stubbed
      await page.route(`${PLAYER_ORIGIN}/**`, (route: Route) => {
        const file = new URL(route.request().url()).pathname === "/player.js" ? "player.js" : "index.html";
        route.fulfill({
          status: 200,
          contentType: file === "player.js" ? "text/javascript" : "text/html",
          body: readFileSync(path.join(PLAYER_DIR, file), "utf8").replace(
            "var ALLOW_LOOPBACK = false;",
            "var ALLOW_LOOPBACK = true;",
          ),
        });
      });
      const rich = richChapter("create", student.email);
      await page.goto(`/learn/${rich.chapter_id}/${rich.section_id}`);
      await quiet(page);

      const video = page.locator("[data-node=video]");
      const nested: string[] = [];
      page.on("framenavigated", (f) => nested.push(f.url()));

      // before the press: no frame, so nothing from YouTube or the player Space
      await expect(video.locator("iframe")).toHaveCount(0);
      await video.getByRole("button", { name: /^Play video: Anion gap/ }).click();

      // the real player becomes ready: the controls (gated on `ready` from the page) appear
      await expect(video.getByRole("button", { name: "1×", exact: true })).toBeVisible({ timeout: 90_000 });
      expect(nested.some((u) => u.startsWith(`${PLAYER_ORIGIN}/`))).toBe(true);
      expect(nested.some((u) => u.startsWith("https://www.youtube-nocookie.com/embed/"))).toBe(true);

      // playback advances (the "Note at" label follows the position the page reports)
      const noteAt = video.getByRole("button", { name: /^Note at / });
      await expect.poll(async () => (await noteAt.textContent()) ?? "", { timeout: 60_000 }).not.toBe("Note at 0:00");

      // fullscreen is allowed in the real embed (the Permissions-Policy and the frame's `allow` delegate it)
      const embed = page.frames().find((f) => f.url().startsWith("https://www.youtube-nocookie.com/embed/"));
      expect(embed, "the nested YouTube embed frame").toBeDefined();
      expect(await embed?.evaluate(() => document.fullscreenEnabled)).toBe(true);

      // speed over the bridge: the real player reports the new rate back as a pressed button
      await video.getByRole("button", { name: "1.5×", exact: true }).click();
      await expect(video.getByRole("button", { name: "1.5×", exact: true })).toHaveAttribute("aria-pressed", "true");

      // a chapter seek moves the real playhead
      await video.getByRole("button", { name: /^0:30\s*Part/ }).click();
      await expect
        .poll(async () => (await noteAt.textContent()) ?? "", { timeout: 30_000 })
        .toMatch(/^Note at 0:(3\d|4\d)/);

      mkdirSync(SHOTS, { recursive: true });
      await page.screenshot({ path: path.join(SHOTS, `player--${info.project.name}.png`), animations: "disabled" });
    } finally {
      try {
        richChapter("delete", student.email);
      } finally {
        await student.done();
      }
    }
  });

  test("the DEPLOYED page refuses a loopback parent: no hello, no player, nothing from YouTube", async ({
    browser,
    baseURL,
  }, info) => {
    test.setTimeout(120_000);
    const student = await freshStudent(browser, baseURL as string, info, "videoref");
    try {
      const page = await student.open("/learn");
      const asked: string[] = [];
      page.on("request", (r) => {
        if (/youtube|ytimg/.test(new URL(r.url()).hostname)) asked.push(r.url());
      });
      const rich = richChapter("create", student.email);
      await page.goto(`/learn/${rich.chapter_id}/${rich.section_id}`);
      await quiet(page);
      const video = page.locator("[data-node=video]");
      await video.getByRole("button", { name: /^Play video: Anion gap/ }).click();
      await expect(video.locator("iframe")).toHaveAttribute("src", new RegExp(`^${PLAYER_ORIGIN}/`));
      // the production page stays silent for a loopback embedder: the controls never appear, YouTube is never asked
      await page.waitForTimeout(8_000);
      await expect(video.getByRole("button", { name: "1×", exact: true })).toHaveCount(0);
      expect(asked).toEqual([]);
    } finally {
      try {
        richChapter("delete", student.email);
      } finally {
        await student.done();
      }
    }
  });
});
