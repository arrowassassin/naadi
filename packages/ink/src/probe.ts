/**
 * Page-wide main-thread probe for the ink engines (one per page, like the canvas budget). While it runs,
 * every engine callback records when it started and how long it took. That covers scroll, animation
 * frames, resize, settle timers, the encode worker's replies, and the promise continuations of encoding,
 * image decoding and restoring a parked slab. The ink e2e gate sums this per animation frame.
 *
 * A callback's time includes the microtasks it queued itself: the end is taken in a microtask queued
 * after the callback returns, which runs after those (microtasks run first in, first out). A callback
 * that runs inside another timed callback is counted once, as part of the outer one.
 */
export class MainThreadProbe {
  private samples: number[] | null = null;
  private depth = 0;

  get running(): boolean {
    return this.samples !== null;
  }

  start(): void {
    this.samples = [];
  }

  /** Stop and return [start, ms, start, ms, ...] in the performance.now() time base. */
  stop(): number[] {
    const s = this.samples ?? [];
    this.samples = null;
    return s;
  }

  time(fn: () => void): void {
    if (!this.samples || this.depth > 0) {
      fn();
      return;
    }
    const samples = this.samples;
    const t0 = performance.now();
    this.depth++;
    try {
      fn();
    } finally {
      this.depth--;
      queueMicrotask(() => {
        if (this.samples === samples && samples.length < 400_000) samples.push(t0, performance.now() - t0);
      });
    }
  }
}

export const inkProbe = new MainThreadProbe();
