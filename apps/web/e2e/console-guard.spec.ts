import type { Page } from "@playwright/test";
import { expect, ignorableConsoleLine, isBenignRequestFailure, test } from "./fixtures";

/**
 * The console guard itself (fixtures.ts): a failed request of the app's own page must fail the test, on any
 * origin. Browsers attribute "Failed to load resource" to the failed resource's URL, so a guard that judged
 * lines by their source origin let a broken cross-origin image pass (S04 final check, B2 follow-up).
 * The probe image is fulfilled by the test (404), never fetched from the real host; that host is in the
 * app's img-src, so the browser really requests it.
 */
const PROBE = "https://upload.wikimedia.org/naadi-console-guard-probe.png";
const FETCH_PROBE = "https://upload.wikimedia.org/naadi-console-guard-probe.json";

async function loadBrokenImage(page: Page): Promise<void> {
  await page.route(PROBE, (route) => route.fulfill({ status: 404, contentType: "text/plain", body: "" }));
  await page.goto("/");
  const ok = await page.evaluate(
    (src) =>
      new Promise<boolean>((done) => {
        const img = new Image();
        img.onload = () => done(true);
        img.onerror = () => done(false);
        img.src = src;
        document.body.append(img);
      }),
    PROBE,
  );
  expect(ok, "the probe image must fail to load").toBe(false);
  // Console and network events arrive asynchronously; let them land before the guard runs.
  await page.waitForTimeout(500);
}

test.describe("console guard", () => {
  test("a broken cross-origin image on an app page fails the guard", async ({ page, consoleErrors }) => {
    test.fail(true, "the guard must report the failed image; this test passes only when it does");
    void consoleErrors;
    await loadBrokenImage(page);
  });

  test("the same failure passes when the test declares it expected", async ({ page, consoleErrors }) => {
    consoleErrors.allowHttpError({ urlPart: "naadi-console-guard-probe", status: 404 });
    await loadBrokenImage(page);
  });

  test("a failed cross-origin fetch of the app's page fails the guard", async ({ page, consoleErrors }) => {
    test.fail(true, "the guard must report the failed fetch");
    void consoleErrors;
    await page.route(FETCH_PROBE, (route) => route.fulfill({ status: 500, contentType: "text/plain", body: "" }));
    await page.goto("/");
    await page.evaluate((u) => fetch(u, { mode: "no-cors" }).catch(() => undefined), FETCH_PROBE);
    await page.waitForTimeout(500);
  });
});

test.describe("console guard rules", () => {
  test("only embed hosts' own console lines are ignorable, never a failed resource", () => {
    expect(
      ignorableConsoleLine(
        "Permissions policy violation: compute-pressure",
        "https://www.youtube-nocookie.com/s/player/base.js",
      ),
    ).toBe(true);
    expect(ignorableConsoleLine("x", "https://www.google.com/js/th/x.js")).toBe(true);
    expect(ignorableConsoleLine("x", "https://fonts.gstatic.com/a.js")).toBe(true);
    expect(
      ignorableConsoleLine(
        "Failed to load resource: the server responded with a status of 404 ()",
        "https://www.youtube-nocookie.com/x.png",
      ),
    ).toBe(false);
    expect(ignorableConsoleLine("x", "https://upload.wikimedia.org/a.png")).toBe(false);
    expect(ignorableConsoleLine("x", "http://localhost:54369/storage/v1/object/a.png")).toBe(false);
    expect(ignorableConsoleLine("x", "https://evil-youtube.com/a.js")).toBe(false);
    expect(ignorableConsoleLine("x", "")).toBe(false);
  });

  test("only cancelled or deliberately offline requests are benign failures", () => {
    for (const t of ["net::ERR_ABORTED", "NS_BINDING_ABORTED", "cancelled", "net::ERR_INTERNET_DISCONNECTED"]) {
      expect(isBenignRequestFailure(t)).toBe(true);
    }
    for (const t of [
      "net::ERR_NAME_NOT_RESOLVED",
      "net::ERR_CONNECTION_REFUSED",
      "NS_ERROR_CONNECTION_REFUSED",
      "failed",
    ]) {
      expect(isBenignRequestFailure(t)).toBe(false);
    }
  });
});
