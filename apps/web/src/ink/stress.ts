import { type InkItem, type InkPoint, roundSu, uuidv7 } from "@naadi/shared";

/** Deterministic PRNG (Park–Miller), so every run measures the same page. */
function prng(seed: number) {
  let s = seed % 2147483647 || 1;
  return () => {
    s = (s * 16807) % 2147483647;
    return s / 2147483647;
  };
}

/**
 * A page of handwriting-like ink: rows of short pressure-varying "word" strokes, with some marker
 * highlights and violet strokes, `count` items over a page `heightSu` tall (su = page width).
 */
export function stressStrokes(count: number, heightSu: number, seed = 42, now = Date.now()): InkItem[] {
  const rnd = prng(seed);
  const items: InkItem[] = [];
  const rows = Math.max(1, Math.floor(heightSu / 0.035));
  const perRow = Math.ceil(count / rows);
  for (let i = 0; i < count; i++) {
    const row = Math.floor(i / perRow);
    const col = i % perRow;
    const y = 0.03 + row * 0.035 + rnd() * 0.006;
    const x = 0.04 + (col / perRow) * 0.92 + rnd() * 0.004;
    const marker = i % 41 === 0;
    const n = marker ? 6 : 14 + Math.floor(rnd() * 16);
    const len = marker ? 0.08 + rnd() * 0.08 : 0.012 + rnd() * 0.02;
    const pts: InkPoint[] = [];
    for (let k = 0; k < n; k++) {
      const t = k / (n - 1);
      const wob = marker ? 0 : Math.sin(t * Math.PI * (2 + rnd() * 3)) * 0.006;
      pts.push([
        roundSu(x + t * len),
        roundSu(y + wob + (rnd() - 0.5) * 0.001),
        Math.round((0.35 + 0.5 * Math.sin(t * Math.PI)) * 1000) / 1000,
        k * 4,
      ]);
    }
    items.push({
      id: uuidv7(now),
      tool: marker ? "marker" : "pen",
      color: marker ? "lime" : i % 7 === 0 ? "violet" : "ink",
      size: marker ? 0.02 : 0.0028,
      points: pts,
      createdAt: now,
      updatedAt: now,
    });
  }
  return items;
}

export interface ScrollMeasurement {
  frames: number;
  durationMs: number;
  fps: number;
  p95FrameMs: number;
  /** Frames longer than 1.5× the median frame (dropped at the display rate). */
  longFrames: number;
}

/**
 * Scroll the window from `fromY` to `toY` over `durationMs`, one step per animation frame, and report
 * the frame rate the page sustained while the ink layer kept up.
 */
export function measureScroll(fromY: number, toY: number, durationMs: number): Promise<ScrollMeasurement> {
  return new Promise((resolve) => {
    const deltas: number[] = [];
    let start = -1;
    let prev = -1;
    window.scrollTo(0, fromY);
    const step = (ts: number) => {
      if (start < 0) {
        start = ts;
        prev = ts;
        requestAnimationFrame(step);
        return;
      }
      deltas.push(ts - prev);
      prev = ts;
      const t = Math.min(1, (ts - start) / durationMs);
      window.scrollTo(0, fromY + (toY - fromY) * t);
      if (t < 1) {
        requestAnimationFrame(step);
        return;
      }
      const sorted = [...deltas].sort((a, b) => a - b);
      const median = sorted[Math.floor(sorted.length / 2)] ?? 16.7;
      const p95 = sorted[Math.min(sorted.length - 1, Math.ceil(sorted.length * 0.95) - 1)] ?? 0;
      const total = ts - start;
      resolve({
        frames: deltas.length,
        durationMs: Math.round(total),
        fps: Math.round((deltas.length / total) * 1000 * 10) / 10,
        p95FrameMs: Math.round(p95 * 10) / 10,
        longFrames: deltas.filter((d) => d > median * 1.5).length,
      });
    };
    requestAnimationFrame(step);
  });
}
