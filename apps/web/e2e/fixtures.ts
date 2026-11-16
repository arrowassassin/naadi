import { mkdirSync } from "node:fs";
import path from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { test as base, expect, type Page, type TestInfo } from "@playwright/test";

const SCREENS_DIR = path.resolve(
  import.meta.dirname,
  "../../..",
  process.env.E2E_SCREENS_DIR ??
    (process.env.E2E_BASE_URL ? "test-results/screens/01-live" : "test-results/screens/01"),
);

export const WCAG_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

/** A browser "Failed to load resource" line for a status the test itself caused or observed. */
export interface ExpectedHttpError {
  urlPart: string;
  status: number;
}

export interface ConsoleGuard {
  allowHttpError: (e: ExpectedHttpError) => void;
  /** Run a Playwright tool action that is known to trip the page's CSP (see TOOL_CSP_NOISE). */
  duringTool: <T>(fn: () => Promise<T>) => Promise<T>;
}

interface Fixtures {
  consoleErrors: ConsoleGuard;
}

/**
 * Playwright's WebKit screenshot injects a <style> element into the page; our CSP (style-src 'self',
 * no 'unsafe-inline') correctly refuses it and WebKit logs this line. It is caused by the test tool,
 * never by the app, so it is ignored only while a screenshot is being taken. Tests that take no
 * screenshot still prove the app itself triggers no CSP violation.
 */
const TOOL_CSP_NOISE =
  "Refused to apply a stylesheet because its hash, its nonce, or 'unsafe-inline' does not appear in the style-src directive";

export const test = base.extend<Fixtures>({
  consoleErrors: async ({ page }, use) => {
    const errors: string[] = [];
    const allowed: ExpectedHttpError[] = [];
    let toolActive = 0;
    page.on("console", (msg) => {
      if (msg.type() !== "error") return;
      if (toolActive > 0 && msg.text().startsWith(TOOL_CSP_NOISE)) return;
      errors.push(`${msg.text()} @ ${msg.location().url}`);
    });
    page.on("pageerror", (err) => errors.push(`pageerror: ${err.message}`));
    await use({
      allowHttpError: (e) => allowed.push(e),
      duringTool: async (fn) => {
        toolActive++;
        try {
          return await fn();
        } finally {
          // Console events are delivered asynchronously; give the refusal line time to arrive.
          await page.waitForTimeout(300);
          toolActive--;
        }
      },
    });
    const unexpected = errors.filter((line) => {
      const m = /Failed to load resource: the server responded with a status of (\d{3})/.exec(line);
      if (!m) return true;
      const status = Number(m[1]);
      return !allowed.some((a) => a.status === status && line.includes(a.urlPart));
    });
    expect(unexpected, "console errors").toEqual([]);
  },
});

export { expect };

export async function expectNoAxeViolations(page: Page): Promise<void> {
  const results = await new AxeBuilder({ page }).withTags(WCAG_TAGS).analyze();
  const summary = results.violations.map((v) => ({
    id: v.id,
    impact: v.impact,
    help: v.help,
    nodes: v.nodes.slice(0, 5).map((n) => n.target.join(" ")),
  }));
  expect(summary, "axe WCAG 2.2 AA violations").toEqual([]);
}

export async function snap(
  page: Page,
  testInfo: TestInfo,
  guard: ConsoleGuard,
  state: string,
  fullPage = false,
): Promise<void> {
  mkdirSync(SCREENS_DIR, { recursive: true });
  await page.evaluate(() => document.fonts.ready);
  await guard.duringTool(() =>
    page.screenshot({
      path: path.join(SCREENS_DIR, `${state}--${testInfo.project.name}.png`),
      fullPage,
      animations: "disabled",
    }),
  );
}

export async function expectNoHorizontalOverflow(page: Page): Promise<void> {
  const { scrollWidth, innerWidth } = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    innerWidth: window.innerWidth,
  }));
  expect(scrollWidth, "page must not scroll horizontally").toBeLessThanOrEqual(innerWidth);
}

export const healthPanel = (page: Page) => page.locator("section[aria-labelledby='health-h']");
