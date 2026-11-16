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

export type AxeResults = Awaited<ReturnType<AxeBuilder["analyze"]>>;

/** The page as the user sees it now, as static HTML: form state kept, no scripts. */
async function freezePage(page: Page): Promise<string> {
  return page.evaluate(() => {
    const live = document.documentElement;
    const copy = live.cloneNode(true) as HTMLElement;
    // Form state lives in properties, not attributes: copy what the user sees into the markup.
    const liveFields = live.querySelectorAll("input, textarea, select");
    const copyFields = copy.querySelectorAll("input, textarea, select");
    liveFields.forEach((el, i) => {
      const c = copyFields[i];
      if (el instanceof HTMLInputElement && c instanceof HTMLInputElement) {
        if (el.type === "checkbox" || el.type === "radio") c.toggleAttribute("checked", el.checked);
        else c.setAttribute("value", el.value);
      } else if (el instanceof HTMLTextAreaElement && c instanceof HTMLTextAreaElement) {
        c.textContent = el.value;
      } else if (el instanceof HTMLSelectElement && c instanceof HTMLSelectElement) {
        Array.from(el.options).forEach((o, j) => c.options[j]?.toggleAttribute("selected", o.selected));
      }
    });
    // No page JavaScript in the copy: drop scripts and module preloads (inline handlers are not used).
    for (const n of copy.querySelectorAll('script, link[rel="modulepreload"]')) n.remove();
    // Inline the applied CSS (linked and constructed sheets), so the copy renders identically even when
    // the test has taken the browser offline. url() references are made absolute first.
    const cssText = [...document.styleSheets, ...document.adoptedStyleSheets]
      .map((sheet) => {
        try {
          const base = sheet.href ?? document.baseURI;
          return Array.from(sheet.cssRules, (r) =>
            r.cssText.replace(/url\((['"]?)(?!data:|https?:|blob:)([^'")]+)\1\)/g, (_m, q, u) => {
              return `url(${q}${new URL(u, base).href}${q})`;
            }),
          ).join("\n");
        } catch {
          return "";
        }
      })
      .join("\n");
    for (const n of copy.querySelectorAll('link[rel="stylesheet"], style')) n.remove();
    const style = document.createElement("style");
    style.textContent = cssText;
    copy.querySelector("head")?.append(style);
    return `<!doctype html>\n${copy.outerHTML}`;
  });
}

/**
 * axe-core against a FROZEN copy of the page, in every browser.
 *
 * Why: Firefox's content process crashes with SIGSEGV (libxul; `dmesg`: "Web Content ... segfault") when
 * axe walks a live page that is still busy (pending/held requests, aria-busy regions, skeleton
 * animations); reviewer-opus reproduced it in CI run 43843684814 (shell.spec loading/error, auth.spec
 * sending/processing, Split View 320) and locally. Running the same rules on a static snapshot (same
 * DOM, same stylesheets and fonts from the same origin, same viewport, no scripts) gives the same
 * results, which e2e/axe-parity.spec.ts checks against live-page axe on Chromium.
 */
export async function axeFrozen(page: Page): Promise<AxeResults> {
  const html = await freezePage(page);
  const frozen = await page.context().newPage();
  try {
    const url = new URL("/__axe-frozen__", page.url()).toString();
    await frozen.route(url, (route) => route.fulfill({ status: 200, contentType: "text/html", body: html }));
    await frozen.goto(url, { waitUntil: "load" });
    await frozen.evaluate(() => document.fonts.ready);
    return await new AxeBuilder({ page: frozen }).withTags(WCAG_TAGS).analyze();
  } finally {
    await frozen.close();
  }
}

export function summarizeViolations(results: AxeResults) {
  return results.violations.map((v) => ({
    id: v.id,
    impact: v.impact,
    help: v.help,
    nodes: v.nodes.slice(0, 5).map((n) => n.target.join(" ")),
  }));
}

export async function expectNoAxeViolations(page: Page): Promise<void> {
  const results = await axeFrozen(page);
  expect(summarizeViolations(results), "axe WCAG 2.2 AA violations").toEqual([]);
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
