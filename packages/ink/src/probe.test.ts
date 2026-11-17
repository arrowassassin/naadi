import { describe, expect, it } from "vitest";
import { MainThreadProbe } from "./probe";

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
    p.time(() => busy(1));
    await flush();
    expect(p.stop()).toEqual([]);
  });

  it("counts a callback and the microtasks it queued", async () => {
    const p = new MainThreadProbe();
    p.start();
    p.time(() => {
      queueMicrotask(() => busy(15));
      void Promise.resolve().then(() => busy(5));
    });
    await flush();
    const s = p.stop();
    expect(s).toHaveLength(2);
    expect(s[1]).toBeGreaterThanOrEqual(19);
  });

  it("counts a nested timed callback once", async () => {
    const p = new MainThreadProbe();
    p.start();
    p.time(() => {
      busy(3);
      p.time(() => busy(3));
    });
    await flush();
    const s = p.stop();
    expect(s).toHaveLength(2);
    expect(s[1]).toBeGreaterThanOrEqual(6);
  });

  it("drops samples of a run that was stopped before they finished", async () => {
    const p = new MainThreadProbe();
    p.start();
    p.time(() => busy(1));
    const first = p.stop();
    p.start();
    await flush();
    expect(first).toEqual([]);
    expect(p.stop()).toEqual([]);
  });
});
