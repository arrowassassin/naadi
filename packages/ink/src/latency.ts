/**
 * Input → paint latency. For every painted frame with new pen input, the engine records
 * - `paint`: performance.now() right after the live stroke was drawn, minus the newest input event's
 *   event.timeStamp (both on the performance.now() timebase): how long our code took to get ink on the
 *   canvas.
 * - `frame`: the timestamp of the following requestAnimationFrame (≈ the vsync at which that canvas
 *   content is presented) minus the same event timestamp: the browser-side part of pen-to-pixel.
 * The panel's real-world number comes from the 240 fps slow-motion protocol (docs/DEVICE_CHECKLIST.md),
 * which also covers digitiser and display latency that no API can see.
 */
export interface LatencySummary {
  count: number;
  p50: number;
  p95: number;
  max: number;
  mean: number;
}

export function percentile(sorted: readonly number[], p: number): number {
  if (sorted.length === 0) return 0;
  const idx = Math.min(sorted.length - 1, Math.max(0, Math.ceil((p / 100) * sorted.length) - 1));
  return sorted[idx] as number;
}

export function summarize(values: readonly number[]): LatencySummary {
  if (values.length === 0) return { count: 0, p50: 0, p95: 0, max: 0, mean: 0 };
  const sorted = [...values].sort((a, b) => a - b);
  const sum = sorted.reduce((s, v) => s + v, 0);
  const r = (v: number) => Math.round(v * 100) / 100;
  return {
    count: sorted.length,
    p50: r(percentile(sorted, 50)),
    p95: r(percentile(sorted, 95)),
    max: r(sorted[sorted.length - 1] as number),
    mean: r(sum / sorted.length),
  };
}

export class LatencyRecorder {
  private readonly capacity: number;
  readonly paint: number[] = [];
  readonly frame: number[] = [];
  private readonly listeners = new Set<() => void>();

  constructor(capacity = 4000) {
    this.capacity = capacity;
  }

  private push(arr: number[], v: number): void {
    if (!Number.isFinite(v) || v < 0 || v > 1000) return; // clock mismatch or a stalled tab, not latency
    arr.push(v);
    if (arr.length > this.capacity) arr.shift();
  }

  recordPaint(eventTs: number, paintedAt: number): void {
    this.push(this.paint, paintedAt - eventTs);
    this.emit();
  }

  recordFrame(eventTs: number, frameTs: number): void {
    this.push(this.frame, frameTs - eventTs);
    this.emit();
  }

  reset(): void {
    this.paint.length = 0;
    this.frame.length = 0;
    this.emit();
  }

  summary(): { paint: LatencySummary; frame: LatencySummary } {
    return { paint: summarize(this.paint), frame: summarize(this.frame) };
  }

  subscribe(fn: () => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  private emit(): void {
    for (const fn of this.listeners) fn();
  }
}
