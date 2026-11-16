import { describe, expect, it } from "vitest";
import { coalescedSamples, newStats, PalmRejection, predictedSamples, samplePressure } from "./input";
import { LatencyRecorder, percentile, summarize } from "./latency";
import { shortcutFor } from "./shortcuts";

const DRAW = { drawingTool: true };

describe("palm rejection", () => {
  it("pen down → touches are ignored (resting palm)", () => {
    const p = new PalmRejection({ penOnly: true });
    expect(p.down(1, "pen", DRAW).action).toBe("draw");
    expect(p.down(2, "touch", DRAW)).toEqual({ action: "ignore", cancel: null });
    expect(p.isActive(1)).toBe(true);
    expect(p.isActive(2)).toBe(false);
    p.up(1);
    p.up(2);
    expect(p.penIsDown).toBe(false);
  });

  it("with a pen present and pen-only on, one finger scrolls (never draws)", () => {
    const p = new PalmRejection({ penOnly: true });
    p.down(1, "pen", DRAW);
    p.up(1);
    expect(p.penPresent).toBe(true);
    expect(p.down(5, "touch", DRAW).action).toBe("pass");
    expect(p.fingersDraw()).toBe(false);
  });

  it("with no pen detected, a finger draws; a second finger cancels it and pans/zooms", () => {
    const p = new PalmRejection({ penOnly: true });
    expect(p.down(5, "touch", DRAW).action).toBe("draw");
    expect(p.down(6, "touch", DRAW)).toEqual({ action: "pass", cancel: 5 });
    expect(p.active).toBeNull();
  });

  it("a palm stroke in progress is cancelled when the pen lands", () => {
    const p = new PalmRejection({ penOnly: false });
    expect(p.down(5, "touch", DRAW).action).toBe("draw");
    expect(p.down(1, "pen", DRAW)).toEqual({ action: "draw", cancel: 5 });
    expect(p.isActive(1)).toBe(true);
  });

  it("pen-only off lets fingers draw even with a pen present", () => {
    const p = new PalmRejection({ penOnly: false, penPresent: true });
    expect(p.down(5, "touch", DRAW).action).toBe("draw");
  });

  it("hover marks the pen present; mouse draws with the primary button only", () => {
    const p = new PalmRejection();
    expect(p.hover("pen")).toBe(true);
    expect(p.hover("pen")).toBe(false);
    expect(p.penPresent).toBe(true);
    const m = new PalmRejection();
    expect(m.down(9, "mouse", DRAW, 2).action).toBe("pass");
    expect(m.down(9, "mouse", DRAW, 0).action).toBe("draw");
    expect(m.down(10, "mouse", { drawingTool: false }).action).toBe("pass");
  });
});

type Ev = {
  pointerId: number;
  pointerType: string;
  clientX: number;
  clientY: number;
  pressure: number;
  buttons: number;
  timeStamp: number;
  getCoalescedEvents?: () => Ev[];
  getPredictedEvents?: () => Ev[];
};
const ev = (x: number, t: number, over: Partial<Ev> = {}): Ev => ({
  pointerId: 3,
  pointerType: "pen",
  clientX: x,
  clientY: 10,
  pressure: 0.4,
  buttons: 1,
  timeStamp: t,
  ...over,
});

describe("coalesced samples", () => {
  it("uses every coalesced sample when the browser provides them", () => {
    const stats = newStats();
    const e = ev(30, 12, { getCoalescedEvents: () => [ev(10, 4), ev(20, 8), ev(30, 12)] });
    expect(coalescedSamples(e, 0, stats).map((s) => s.x)).toEqual([10, 20, 30]);
    expect(stats.coalescedBatches).toBe(1);
  });

  it("falls back to the event itself when the list is empty or missing", () => {
    const stats = newStats();
    expect(coalescedSamples(ev(5, 3, { getCoalescedEvents: () => [] }), 0, stats)).toHaveLength(1);
    expect(coalescedSamples(ev(6, 4), 0, stats)).toHaveLength(1);
  });

  it("WebKit 18.2: coalesced events with a foreign pointerId still belong to their parent (counted)", () => {
    const stats = newStats();
    const e = ev(30, 12, { getCoalescedEvents: () => [ev(10, 4, { pointerId: 0 }), ev(30, 12, { pointerId: 0 })] });
    expect(coalescedSamples(e, 0, stats).map((s) => s.x)).toEqual([10, 30]);
    expect(stats.idMismatch).toBe(2);
  });

  it("skips samples already consumed (rawupdate + pointermove), non-finite and out-of-order ones", () => {
    const stats = newStats();
    const e = ev(40, 16, {
      getCoalescedEvents: () => [ev(10, 4), ev(Number.NaN, 6), ev(20, 8), ev(15, 7), ev(40, 16)],
    });
    expect(coalescedSamples(e, 5, stats).map((s) => s.x)).toEqual([20, 40]);
    expect(stats.dropped).toBe(3);
  });

  it("appends the parent when the coalesced list ends elsewhere", () => {
    const stats = newStats();
    const e = ev(50, 20, { getCoalescedEvents: () => [ev(10, 4)] });
    expect(coalescedSamples(e, 0, stats).map((s) => s.x)).toEqual([10, 50]);
  });

  it("survives a throwing getCoalescedEvents", () => {
    const e = ev(1, 1, {
      getCoalescedEvents: () => {
        throw new Error("nope");
      },
    });
    expect(coalescedSamples(e, 0, newStats())).toHaveLength(1);
  });

  it("predicted samples are capped in time and distance", () => {
    const stats = newStats();
    const e = ev(100, 10, {
      getPredictedEvents: () => [ev(108, 18), ev(116, 26), ev(400, 30), ev(120, 90)],
    });
    expect(predictedSamples(e, stats).map((s) => s.x)).toEqual([108, 116]);
    expect(stats.predicted).toBe(2);
  });

  it("pressure: pen keeps its own (never 0), mouse/touch store 0.5", () => {
    expect(samplePressure("pen", 0.73)).toBe(0.73);
    expect(samplePressure("pen", 0)).toBe(0.5);
    expect(samplePressure("mouse", 1)).toBe(0.5);
    expect(samplePressure("touch", 0)).toBe(0.5);
  });
});

describe("latency", () => {
  it("percentiles and summary", () => {
    const v = Array.from({ length: 100 }, (_, i) => i + 1);
    expect(percentile(v, 50)).toBe(50);
    expect(percentile(v, 95)).toBe(95);
    expect(summarize(v)).toEqual({ count: 100, p50: 50, p95: 95, max: 100, mean: 50.5 });
    expect(summarize([])).toEqual({ count: 0, p50: 0, p95: 0, max: 0, mean: 0 });
  });

  it("recorder ignores impossible values and caps capacity", () => {
    const r = new LatencyRecorder(3);
    r.recordPaint(10, 9); // negative
    r.recordPaint(10, 5000); // stalled tab
    for (const d of [4, 5, 6, 7]) r.recordPaint(0, d);
    expect(r.paint).toEqual([5, 6, 7]);
  });
});

describe("shortcuts", () => {
  const k = (key: string, mods: Partial<Record<"metaKey" | "ctrlKey" | "altKey" | "shiftKey", boolean>> = {}) => ({
    key,
    metaKey: false,
    ctrlKey: false,
    altKey: false,
    shiftKey: false,
    ...mods,
  });
  it("maps P/M/E/L and Cmd/Ctrl+Z, Shift+Cmd+Z", () => {
    expect(shortcutFor(k("p"))).toBe("pen");
    expect(shortcutFor(k("M"))).toBe("marker");
    expect(shortcutFor(k("e"))).toBe("eraser");
    expect(shortcutFor(k("l"))).toBe("lasso");
    expect(shortcutFor(k("z", { metaKey: true }))).toBe("undo");
    expect(shortcutFor(k("z", { ctrlKey: true, shiftKey: true }))).toBe("redo");
    expect(shortcutFor(k("y", { ctrlKey: true }))).toBe("redo");
  });
  it("never takes Cmd/Ctrl+K or modified letters", () => {
    expect(shortcutFor(k("k", { metaKey: true }))).toBeNull();
    expect(shortcutFor(k("p", { metaKey: true }))).toBeNull();
    expect(shortcutFor(k("p", { shiftKey: true }))).toBeNull();
    expect(shortcutFor(k("e", { altKey: true }))).toBeNull();
  });
});
