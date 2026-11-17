/**
 * Page-wide main-thread probe for the ink engines (one per page, like the canvas budget). While it runs,
 * every engine callback records when it started and how long it took. That covers scroll, animation
 * frames, resize, settle timers, the encode worker's replies, and the promise continuations of encoding
 * and image decoding. The ink e2e gate sums this per animation frame.
 *
 * A callback's time is its own run plus the microtasks it queued. A marker microtask is queued before the
 * callback runs and a closing one after it returns. Microtasks run first in, first out, so the span from
 * the marker to the closing one is exactly the microtasks the callback queued. Whatever runs between the
 * callback's return and its microtasks (microtasks queued earlier by someone else) is not counted. A callback that runs
 * inside another timed callback is counted once, as part of the outer one.
 */
/** What a probe sample was: which engine callback ran. */
export type ProbeKind = "scroll" | "resize" | "frame" | "settle" | "encode" | "decode";

export const PROBE_KINDS: readonly ProbeKind[] = ["scroll", "resize", "frame", "settle", "encode", "decode"];

export class MainThreadProbe {
  private samples: number[] | null = null;
  private depth = 0;

  get running(): boolean {
    return this.samples !== null;
  }

  start(): void {
    this.samples = [];
  }

  /**
   * Stop and return [start, ms, kind, start, ms, kind, ...]: start in the performance.now() time base, kind
   * an index into PROBE_KINDS.
   */
  stop(): number[] {
    const s = this.samples ?? [];
    this.samples = null;
    return s;
  }

  time(fn: () => void, kind: ProbeKind): void {
    if (!this.samples || this.depth > 0) {
      fn();
      return;
    }
    const samples = this.samples;
    let microStart = 0;
    queueMicrotask(() => {
      microStart = performance.now();
    });
    const t0 = performance.now();
    this.depth++;
    let run = 0;
    try {
      fn();
    } finally {
      run = performance.now() - t0;
      this.depth--;
      queueMicrotask(() => {
        if (this.samples === samples && samples.length < 600_000)
          samples.push(t0, run + (performance.now() - microStart), PROBE_KINDS.indexOf(kind));
      });
    }
  }
}

export const inkProbe = new MainThreadProbe();
