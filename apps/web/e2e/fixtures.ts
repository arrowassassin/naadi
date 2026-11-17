import { mkdirSync } from "node:fs";
import path from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { test as base, expect, type Page, type Request, type TestInfo } from "@playwright/test";

const SCREENS_DIR = path.resolve(
  import.meta.dirname,
  "../../..",
  process.env.E2E_SCREENS_DIR ??
    (process.env.E2E_BASE_URL ? "test-results/screens/01-live" : "test-results/screens/01"),
);

export const WCAG_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

/** A failed request the test itself caused or observed: a "Failed to load resource" line or a main-frame
 * response with that status (status 0: the request failed without a response, e.g. a route the test aborted). */
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
 * screenshot still prove the app itself triggers no CSP violation. Matched as the exact full line, so any other
 * CSP report (an inline style the app itself set, a diagram's stylesheet) is never hidden by it.
 */
export const TOOL_CSP_NOISE =
  "Refused to apply a stylesheet because its hash, its nonce, or 'unsafe-inline' does not appear in the style-src directive of the Content Security Policy.";

/**
 * Hosts of third-party embeds whose own documents log to the console: the YouTube IFrame player (and Google's
 * scripts inside it) probes features the embed is not granted and logs freely. Only console lines whose source
 * is one of these hosts are ignored, and never a "Failed to load resource" line: browsers attribute that line to
 * the failed resource's URL, so a cross-origin image or fetch of the app's own page would otherwise vanish.
 */
const EMBED_HOSTS = ["youtube-nocookie.com", "youtube.com", "google.com", "gstatic.com"];

const FAILED_RESOURCE = "Failed to load resource";

export function fromEmbedHost(sourceUrl: string): boolean {
  if (!sourceUrl) return false;
  try {
    const host = new URL(sourceUrl).hostname;
    return EMBED_HOSTS.some((h) => host === h || host.endsWith(`.${h}`));
  } catch {
    return false;
  }
}

/** The service worker script (segment 18): the browser fetches it itself to check for an update. */
export function isServiceWorkerScript(url: string): boolean {
  try {
    return new URL(url).pathname === "/sw.js";
  } catch {
    return false;
  }
}

/** A console error line the guard may ignore: from an embed host's document, and not a network failure. */
export function ignorableConsoleLine(text: string, sourceUrl: string): boolean {
  // The browser re-checks the service worker script on every navigation; with no connection that check fails and
  // WebKit logs it. The page itself is served from the worker's precache, so it is not an app error.
  if (text.includes(FAILED_RESOURCE) && isServiceWorkerScript(sourceUrl)) return true;
  return !text.includes(FAILED_RESOURCE) && fromEmbedHost(sourceUrl);
}

/**
 * Request failures that are not errors: the browser or the test cancelled the request (a navigation, an
 * unmounted component's aborted fetch) or the test took the context offline on purpose.
 */
const BENIGN_FAILURE =
  /ERR_ABORTED|NS_BINDING_ABORTED|cancelled|canceled|ERR_INTERNET_DISCONNECTED|NS_ERROR_OFFLINE|appears to be offline/i;

/**
 * The browser's own CSP report delivery (report-uri, segment 22) is not a request of the app: a violation is judged
 * from its own console line; the report POST itself may fail (e.g. while a test is offline).
 */
export function isCspReport(url: string): boolean {
  try {
    return new URL(url).pathname === "/api/csp-report";
  } catch {
    return false;
  }
}

export function isBenignRequestFailure(errorText: string): boolean {
  return BENIGN_FAILURE.test(errorText);
}

export const test = base.extend<Fixtures>({
  consoleErrors: async ({ page }, use) => {
    const errors: string[] = [];
    const allowed: ExpectedHttpError[] = [];
    let toolActive = 0;
    const network: { url: string; status: number; what: string }[] = [];
    page.on("console", (msg) => {
      if (msg.type() !== "error") return;
      if (toolActive > 0 && msg.text() === TOOL_CSP_NOISE) return;
      if (ignorableConsoleLine(msg.text(), msg.location().url)) return;
      if (msg.text().includes(FAILED_RESOURCE) && isCspReport(msg.location().url)) return;
      errors.push(`${msg.text()} @ ${msg.location().url}`);
    });
    page.on("pageerror", (err) => errors.push(`pageerror: ${err.message}`));
    // A renderer crash (out of memory, a GPU or decoder fault) kills the page without a pageerror: it is an
    // error like any other, never a silent pass of whatever assertions ran before it (segment 22, from S04).
    page.on("crash", () => errors.push("crash: the page's renderer crashed"));
    // Network failures are judged by frame, not by console attribution: every request the app's own document
    // makes (images, fetches, scripts, on any origin) counts; requests of embedded third-party frames do not.
    // Service-worker requests have no frame and are the worker's own business (it retries and caches).
    const fromMainFrame = (req: import("@playwright/test").Request): boolean => {
      if (isCspReport(req.url())) return false;
      try {
        return req.frame() === page.mainFrame();
      } catch {
        return false;
      }
    };
    page.on("response", (res) => {
      if (res.status() >= 400 && fromMainFrame(res.request())) {
        network.push({ url: res.url(), status: res.status(), what: `HTTP ${res.status()}` });
      }
    });
    page.on("requestfailed", (req) => {
      const why = req.failure()?.errorText ?? "failed";
      // The browser's own service-worker update check (segment 18) fails while offline: not the app's request.
      if (isServiceWorkerScript(req.url())) return;
      if (!isBenignRequestFailure(why) && fromMainFrame(req)) network.push({ url: req.url(), status: 0, what: why });
    });
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
    const failedRequests = network
      .filter((n) => !allowed.some((a) => a.status === n.status && n.url.includes(a.urlPart)))
      .map((n) => `${n.what} ${n.url}`);
    expect(unexpected, "console errors").toEqual([]);
    expect(failedRequests, "failed requests of the app's own page").toEqual([]);
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

/** Load every declared font face, including every unicode-range subset (a failed face counts as settled). */
export async function loadAllFonts(page: Page): Promise<void> {
  await page.evaluate(() => Promise.all([...document.fonts].map((f) => f.load().catch(() => f))).then(() => undefined));
}

/**
 * Call after every navigation and before the next one, or before going offline. Firefox requests a subset
 * (e.g. the Greek one for "α") only when a glyph needs it, and logs that download as an error when the next
 * navigation cancels it; loading every face first means nothing is left in flight.
 */
export async function settle(page: Page): Promise<void> {
  await loadAllFonts(page);
  await page.waitForFunction(() => document.fonts.status === "loaded");
}

/**
 * Requests to the app's own /api that have started and not yet finished or failed, counted per page. `quiet()`
 * attaches it on a page's first call (about:blank, before the first navigation); a page that navigates on its
 * own before that (a sign-in callback, say) should call `trackApi(page)` first, so no request is missed.
 */
const apiTrackers = new WeakMap<Page, { open: () => number }>();
export function trackApi(page: Page): { open: () => number } {
  const known = apiTrackers.get(page);
  if (known) return known;
  const inFlight = new Set<Request>();
  const isApi = (r: Request) => {
    try {
      return new URL(r.url()).pathname.startsWith("/api/");
    } catch {
      return false;
    }
  };
  page.on("request", (r) => {
    if (isApi(r)) inFlight.add(r);
  });
  page.on("requestfinished", (r) => inFlight.delete(r));
  page.on("requestfailed", (r) => inFlight.delete(r));
  const tracker = { open: () => inFlight.size };
  apiTrackers.set(page, tracker);
  return tracker;
}

/**
 * Nothing in flight on the current page, waited on real signals only (no timeouts): every font face loaded
 * (`settle`), no request to the app's own /api open (WebKit reports one cut off by navigation, e.g. a late
 * /api/public-config, as "... due to access control checks"), no /learn page with a reading-position save or
 * offline cache write pending (`data-learn-idle`), and a reader section's ink layer loaded (`data-ink-idle`).
 * Call it before every page.goto / reload and before going offline. A request a test holds open with
 * page.route must be released first.
 */
export async function quiet(page: Page): Promise<void> {
  const api = trackApi(page);
  if (page.url() === "about:blank") return;
  await settle(page);
  await expect.poll(() => api.open(), { message: "open /api requests before navigating" }).toBe(0);
  await page.waitForFunction(() => document.querySelector("[data-learn-page]:not([data-learn-idle])") === null);
  if (await page.locator("[data-section-ink]").count()) await expect(page.locator("[data-ink-idle]")).toBeAttached();
}
