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
  /** Also allow the LangGraph SDK's own log line for the same failed agent request (`HTTP 401: {...}`):
   * the SDK console.errors a failed run before handing it to the app, which shows it as an error state. */
  agentSdk?: boolean;
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
      const sdk = /^\w+: HTTP (\d{3}): /.exec(line);
      if (sdk) return !allowed.some((a) => a.agentSdk === true && a.status === Number(sdk[1]));
      // Firefox prints the same SDK log line as a bare "Error" (the message is not part of the text).
      if (/^Error @ /.test(line) && allowed.some((a) => a.agentSdk === true)) return false;
      const m = /Failed to load resource: the server responded with a status of (\d{3})/.exec(line);
      if (!m) return true;
      const status = Number(m[1]);
      return !allowed.some((a) => a.status === status && line.includes(a.urlPart));
    });
    expect(unexpected, "console errors").toEqual([]);
  },
});

export { expect };

/**
 * A frozen, script-free copy of the page as it is now, in a fresh browser context with the same
 * emulation (viewport, touch, mobile, scale) and headers, at the same URL (same origin, so fonts and
 * relative URLs resolve). Scripts are removed, so no page code exists to run (JavaScript itself stays
 * on: axe-core hung in a context created with javaScriptEnabled: false), and every stylesheet is
 * inlined from the live CSSOM, so nothing can load, fetch, re-render or animate while axe walks it.
 * A separate context also stays online when the test has taken the live context offline.
 */
async function frozenCopy(page: Page): Promise<{ frozen: Page; close: () => Promise<void> }> {
  const { html, url } = await page.evaluate(() => {
    const css = Array.from(document.styleSheets, (sheet) => Array.from(sheet.cssRules, (r) => r.cssText).join("\n"));
    const root = document.documentElement.cloneNode(true) as HTMLElement;
    for (const n of root.querySelectorAll(
      'script, link[rel="stylesheet"], link[rel="modulepreload"], link[rel="preload"], style',
    )) {
      n.remove();
    }
    // Third-party embeds (the YouTube IFrame player) keep their element, so the frame itself is audited
    // (title, size), but not their document: it is not ours to fix and would load and run in the copy.
    for (const f of root.querySelectorAll("iframe[src]")) {
      const src = f.getAttribute("src") ?? "";
      if (new URL(src, location.href).origin !== location.origin) f.setAttribute("src", "about:blank");
    }
    const style = document.createElement("style");
    style.textContent = css.join("\n");
    root.querySelector("head")?.append(style);
    return { html: `<!doctype html>\n${root.outerHTML}`, url: location.href };
  });
  const browser = page.context().browser();
  if (!browser) throw new Error("frozenCopy needs a browser-backed page");
  const use = base.info().project.use;
  const context = await browser.newContext({
    viewport: page.viewportSize(),
    hasTouch: use.hasTouch,
    isMobile: use.isMobile,
    deviceScaleFactor: use.deviceScaleFactor,
    extraHTTPHeaders: use.extraHTTPHeaders,
    colorScheme: use.colorScheme,
    reducedMotion: use.reducedMotion,
  });
  const frozen = await context.newPage();
  await frozen.route(url, (route) =>
    route.fulfill({ status: 200, contentType: "text/html; charset=utf-8", body: html }),
  );
  await frozen.goto(url, { waitUntil: "load" });
  return { frozen, close: () => context.close() };
}

/**
 * WCAG 2.2 AA audit with the full rule set, run on a frozen copy of the page (see frozenCopy) rather
 * than on the live page, in every browser so all three engines audit the same thing.
 *
 * Why (segment 01 review 1, defect 6): Playwright's Firefox (build 1572, @playwright/test 1.65.1, the
 * latest) killed the content process with SIGSEGV at one fixed libxul address in about 1 of 12 axe runs
 * on the live page: core dumps show the fault in native code called from JS, and markers in an
 * instrumented axe-core show every crash after axe's last rule and teardown, while the result travels
 * back from a page that is still running the app. It reproduced with 1 worker or 4 and with the JIT,
 * content sandbox, Fission, incremental GC, GPU prefs, reduced motion or the network off; it never
 * happened without axe. On the frozen copy it does not happen (see the defect 6 commit for the runs).
 */
export async function expectNoAxeViolations(page: Page): Promise<void> {
  const { frozen, close } = await frozenCopy(page);
  try {
    const results = await new AxeBuilder({ page: frozen }).withTags(WCAG_TAGS).analyze();
    const summary = results.violations.map((v) => ({
      id: v.id,
      impact: v.impact,
      help: v.help,
      nodes: v.nodes.slice(0, 5).map((n) => n.target.join(" ")),
    }));
    expect(summary, "axe WCAG 2.2 AA violations").toEqual([]);
  } finally {
    await close();
  }
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

/** Screenshot into test-results/screens/<segment>/ (`-live` suffix against the deployed Space). */
export async function snapSegment(
  page: Page,
  testInfo: TestInfo,
  guard: ConsoleGuard,
  segment: string,
  state: string,
  fullPage = false,
): Promise<void> {
  const dir = path.resolve(
    import.meta.dirname,
    "../../..",
    "test-results/screens",
    process.env.E2E_BASE_URL ? `${segment}-live` : segment,
  );
  mkdirSync(dir, { recursive: true });
  await page.evaluate(() => document.fonts.ready);
  await guard.duringTool(() =>
    page.screenshot({
      path: path.join(dir, `${state}--${testInfo.project.name}.png`),
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
