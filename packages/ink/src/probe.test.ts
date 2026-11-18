import { describe, expect, it } from "vitest";
import { MainThreadProbe, PROBE_KINDS } from "./probe";

const busy = (ms: number) => {
  const t = performance.now();
  while (performance.now() - t < ms) {
    // busy-wait
  }
};

const flush = () => new Promise<void>((r) => setTimeout(r, 0));

describe("MainThreadProbe", () => {
  it("records nothing while stopped", async () => {
    const p = new MainThreadProbe();
    p.time(() => busy(1), "scroll");
    await flush();
    expect(p.stop()).toEqual([]);
  });

  it("counts a callback and the microtasks it queued", async () => {
    const p = new MainThreadProbe();
    p.start();
    p.time(() => {
      queueMicrotask(() => busy(15));
      void Promise.resolve().then(() => busy(5));
    }, "frame");
    await flush();
    const s = p.stop();
    expect(s).toHaveLength(3);
    expect(s[1]).toBeGreaterThanOrEqual(19);
    expect(s[2]).toBe(PROBE_KINDS.indexOf("frame"));
  });

  it("does not count work that waits between a callback and its microtasks", async () => {
    const p = new MainThreadProbe();
    p.start();
    // Someone else's 10 ms microtask, queued before the callback, runs between the callback's return and its
    // own microtasks (as the browser's rendering may on WebKit): it is not the callback's time.
    queueMicrotask(() => busy(10));
    p.time(() => {
      busy(2);
      queueMicrotask(() => busy(3));
    }, "scroll");
    await flush();
    const s = p.stop();
    expect(s).toHaveLength(3);
    expect(s[1]).toBeGreaterThanOrEqual(5);
    expect(s[1]).toBeLessThan(10);
  });

  it("counts a nested timed callback once", async () => {
    const p = new MainThreadProbe();
    p.start();
    p.time(() => {
      busy(3);
      p.time(() => busy(3), "decode");
    }, "settle");
    await flush();
    const s = p.stop();
    expect(s).toHaveLength(3);
    expect(s[1]).toBeGreaterThanOrEqual(6);
    expect(s[2]).toBe(PROBE_KINDS.indexOf("settle"));
  });

  it("drops samples of a run that was stopped before they finished", async () => {
    const p = new MainThreadProbe();
    p.start();
    p.time(() => busy(1), "scroll");
    const first = p.stop();
    p.start();
    await flush();
    expect(first).toEqual([]);
    expect(p.stop()).toEqual([]);
  });
});
