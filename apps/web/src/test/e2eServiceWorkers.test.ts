import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

/**
 * playwright.config.ts blocks service workers in every context by default: in WebKit a page that /sw.js
 * controls sends its fetches past page.route, so stubbed /api failures would never reach the page. That
 * default must not hide real service-worker bugs, so a spec that exercises the worker has to opt in with
 * `serviceWorkers: "allow"` (or drive its own persistent browser context, as Lighthouse does) when it touches
 * `navigator.serviceWorker` or `context.serviceWorkers()`. A spec whose file is named after offline, push, sw or
 * pwa behaviour must at least decide explicitly: `serviceWorkers: "allow"`, or a deliberate `"block"` (e.g. a
 * test of code that was never cached), never the inherited default.
 */
const E2E_DIR = path.resolve(__dirname, "../../e2e");
const CONFIG = path.resolve(__dirname, "../../playwright.config.ts");

const SW_FILE_NAME = /(^|[-_.])(offline|push|sw|pwa)([-_.]|$)/i;
const TOUCHES_SW = /navigator\.serviceWorker|\.serviceWorkers\(\)/;
const OPTS_IN = /serviceWorkers:\s*["']allow["']|launchPersistentContext\(/;
const DECIDES = /serviceWorkers:\s*["'](allow|block)["']/;

/** Why a spec breaks the rule, or null when it follows it. */
function serviceWorkerOptInProblem(file: string, source: string): string | null {
  if (OPTS_IN.test(source)) return null;
  if (SW_FILE_NAME.test(path.basename(file, ".spec.ts")) && !DECIDES.test(source)) {
    return `${file} is named after service-worker behaviour but never sets serviceWorkers explicitly`;
  }
  if (TOUCHES_SW.test(source)) {
    return `${file} uses the service worker but never sets serviceWorkers: "allow"`;
  }
  return null;
}

describe("e2e service-worker opt-in", () => {
  it("the config blocks service workers by default (the premise of this guard)", () => {
    expect(readFileSync(CONFIG, "utf8")).toMatch(/serviceWorkers:\s*"block"/);
  });

  it("every spec that exercises the service worker opts in", () => {
    const specs = readdirSync(E2E_DIR).filter((f) => f.endsWith(".spec.ts"));
    expect(specs.length).toBeGreaterThan(10);
    const problems = specs
      .map((f) => serviceWorkerOptInProblem(f, readFileSync(path.join(E2E_DIR, f), "utf8")))
      .filter((p): p is string => p !== null);
    expect(problems).toEqual([]);
  });

  it("catches a spec that would silently test with the worker blocked", () => {
    expect(serviceWorkerOptInProblem("offline.spec.ts", "test('x', async ({ page }) => {})")).not.toBeNull();
    expect(serviceWorkerOptInProblem("push-sw.spec.ts", "")).not.toBeNull();
    expect(
      serviceWorkerOptInProblem("planner.spec.ts", "await page.evaluate(() => navigator.serviceWorker.ready)"),
    ).not.toBeNull();
    expect(serviceWorkerOptInProblem("planner.spec.ts", "ctx.serviceWorkers().find(Boolean)")).not.toBeNull();
    expect(
      serviceWorkerOptInProblem("offline.spec.ts", 'newContext({ serviceWorkers: "allow" }); navigator.serviceWorker'),
    ).toBeNull();
    expect(serviceWorkerOptInProblem("cards.spec.ts", "await page.context().setOffline(true)")).toBeNull();
    expect(serviceWorkerOptInProblem("swipe.spec.ts", "")).toBeNull();
    // a deliberate, explicit block is a decision; the worker API still needs "allow"
    expect(serviceWorkerOptInProblem("chunk-offline.spec.ts", 'test.use({ serviceWorkers: "block" })')).toBeNull();
    expect(
      serviceWorkerOptInProblem("offline.spec.ts", 'test.use({ serviceWorkers: "block" }); navigator.serviceWorker'),
    ).not.toBeNull();
  });
});
