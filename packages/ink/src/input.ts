/**
 * Input pipeline: palm rejection / pen detection, and full-rate sample extraction from Pointer Events.
 * Pure logic (no DOM writes), so it is unit-tested directly.
 */

export type PointerKind = "pen" | "touch" | "mouse";

export function pointerKind(type: string): PointerKind {
  return type === "pen" ? "pen" : type === "touch" ? "touch" : "mouse";
}

/** What to do with a pointerdown. */
export type Decision =
  /** Start drawing with this pointer. */
  | { action: "draw"; cancel: number | null }
  /** Leave it to the browser (scroll, pan, zoom, click). `cancel` = a stroke to abandon. */
  | { action: "pass"; cancel: number | null }
  /** Swallow it (a palm while the pen is down). */
  | { action: "ignore"; cancel: null };

export interface PolicyInput {
  /** A drawing tool (pen, marker, eraser, lasso, shapes, text) is active and ink is on. */
  drawingTool: boolean;
}

/**
 * Palm rejection state machine.
 * - The pen always draws (with a drawing tool) and marks a pen as present.
 * - While a pen is down, touches are ignored (a resting palm).
 * - With a pen present and pen-only on, touches never draw: one finger scrolls, two fingers zoom/pan.
 * - With no pen detected (phone, laptop) or pen-only off, one finger draws; a second finger cancels
 *   that stroke and hands the gesture to the browser (two-finger pan/zoom).
 * - A touch stroke in progress when the pen lands was a palm: it is cancelled.
 */
export class PalmRejection {
  penPresent: boolean;
  penOnly: boolean;
  /** The pointer currently drawing, if any. */
  active: { id: number; kind: PointerKind } | null = null;
  private readonly touches = new Set<number>();
  private penDown = new Set<number>();

  constructor(opts: { penOnly?: boolean; penPresent?: boolean } = {}) {
    this.penOnly = opts.penOnly ?? true;
    this.penPresent = opts.penPresent ?? false;
  }

  get penIsDown(): boolean {
    return this.penDown.size > 0;
  }

  /** Fingers may draw right now (used to choose the surface's touch-action). */
  fingersDraw(): boolean {
    return !(this.penPresent && this.penOnly);
  }

  /** A pen was seen without touching (Pencil hover). */
  hover(kind: PointerKind): boolean {
    if (kind !== "pen" || this.penPresent) return false;
    this.penPresent = true;
    return true;
  }

  down(id: number, kind: PointerKind, policy: PolicyInput, button = 0): Decision {
    if (kind === "pen") {
      this.penPresent = true;
      this.penDown.add(id);
      // A finger/palm stroke already in progress is abandoned: the pen wins.
      const cancel = this.active && this.active.kind !== "pen" ? this.active.id : null;
      if (cancel !== null) this.active = null;
      if (!policy.drawingTool || this.active) return { action: "pass", cancel };
      this.active = { id, kind };
      return { action: "draw", cancel };
    }
    if (kind === "touch") {
      this.touches.add(id);
      if (this.penDown.size > 0) return { action: "ignore", cancel: null };
      if (!policy.drawingTool || !this.fingersDraw()) return { action: "pass", cancel: null };
      if (this.touches.size > 1) {
        // Second finger: this is a pan/zoom gesture, not writing.
        const cancel = this.active && this.active.kind === "touch" ? this.active.id : null;
        if (cancel !== null) this.active = null;
        return { action: "pass", cancel };
      }
      if (this.active) return { action: "pass", cancel: null };
      this.active = { id, kind };
      return { action: "draw", cancel: null };
    }
    if (!policy.drawingTool || button !== 0 || this.active) return { action: "pass", cancel: null };
    this.active = { id, kind };
    return { action: "draw", cancel: null };
  }

  /** Is this pointer the one drawing? */
  isActive(id: number): boolean {
    return this.active?.id === id;
  }

  /** pointerup / pointercancel / lostpointercapture. Returns true if it was the drawing pointer. */
  up(id: number): boolean {
    this.touches.delete(id);
    this.penDown.delete(id);
    if (this.active?.id === id) {
      this.active = null;
      return true;
    }
    return false;
  }

  reset(): void {
    this.touches.clear();
    this.penDown.clear();
    this.active = null;
  }
}

/** One input sample in client coordinates. */
export interface Sample {
  x: number;
  y: number;
  pressure: number;
  /** event.timeStamp (ms, performance.now() timebase) */
  t: number;
}

export interface SampleStats {
  /** Coalesced events whose pointerId differed from their parent's (WebKit 18.2 bug). */
  idMismatch: number;
  /** Coalesced samples dropped as duplicates/out of order/non-finite. */
  dropped: number;
  /** Total samples accepted. */
  accepted: number;
  /** Events that delivered coalesced samples. */
  coalescedBatches: number;
  /** Predicted samples used for the live preview. */
  predicted: number;
}

export function newStats(): SampleStats {
  return { idMismatch: 0, dropped: 0, accepted: 0, coalescedBatches: 0, predicted: 0 };
}

interface PointerLike {
  pointerId: number;
  pointerType: string;
  clientX: number;
  clientY: number;
  pressure: number;
  buttons: number;
  timeStamp: number;
}

type WithLists = PointerLike & {
  getCoalescedEvents?: () => PointerLike[];
  getPredictedEvents?: () => PointerLike[];
};

/**
 * Pressure for storage: the pen's own pressure (never 0 while touching: some digitisers report 0 on the
 * first sample), and 0.5 for mouse/touch, which the renderer turns into simulated (velocity) pressure.
 */
export function samplePressure(kind: PointerKind, pressure: number): number {
  if (kind !== "pen") return 0.5;
  if (!Number.isFinite(pressure) || pressure <= 0) return 0.5;
  return Math.min(1, Math.max(0.02, pressure));
}

function safeList(fn: (() => PointerLike[]) | undefined, self: PointerLike): PointerLike[] {
  if (typeof fn !== "function") return [];
  try {
    return fn.call(self) ?? [];
  } catch {
    return [];
  }
}

/**
 * Full-rate samples for one pointermove/pointerrawupdate. Uses getCoalescedEvents() when it has
 * entries. WebKit 18.2 can stamp coalesced events with a different pointerId than their parent; they
 * still belong to the parent (they come from its list), so they are routed by the parent's id and the
 * mismatch is only counted. Samples at or before `lastT` were already consumed (Chromium delivers the
 * same input through both pointerrawupdate and pointermove) and are skipped.
 */
export function coalescedSamples(e: WithLists, lastT: number, stats: SampleStats): Sample[] {
  const kind = pointerKind(e.pointerType);
  const list = safeList(e.getCoalescedEvents, e);
  const raw = list.length > 0 ? list : [e];
  if (list.length > 0) stats.coalescedBatches++;
  const out: Sample[] = [];
  let prevT = lastT;
  for (const c of raw) {
    if (c.pointerId !== e.pointerId) stats.idMismatch++;
    if (!Number.isFinite(c.clientX) || !Number.isFinite(c.clientY) || !Number.isFinite(c.timeStamp)) {
      stats.dropped++;
      continue;
    }
    if (c.timeStamp <= prevT) {
      stats.dropped++;
      continue;
    }
    prevT = c.timeStamp;
    out.push({ x: c.clientX, y: c.clientY, pressure: samplePressure(kind, c.pressure), t: c.timeStamp });
  }
  // The parent is the newest sample; make sure its position is the last one we use.
  const lastOut = out[out.length - 1];
  if (
    list.length > 0 &&
    e.timeStamp > prevT &&
    Number.isFinite(e.clientX) &&
    (!lastOut || lastOut.x !== e.clientX || lastOut.y !== e.clientY)
  ) {
    out.push({ x: e.clientX, y: e.clientY, pressure: samplePressure(kind, e.pressure), t: e.timeStamp });
  }
  stats.accepted += out.length;
  return out;
}

/**
 * Predicted samples (Safari 18.2+, Chromium) for the live preview only, never stored. Capped to a short
 * horizon so a bad prediction can't fling the preview across the page.
 */
export function predictedSamples(e: WithLists, stats: SampleStats, horizonMs = 40, maxDistPx = 60): Sample[] {
  const kind = pointerKind(e.pointerType);
  const list = safeList(e.getPredictedEvents, e);
  const out: Sample[] = [];
  for (const p of list) {
    if (!Number.isFinite(p.clientX) || !Number.isFinite(p.clientY)) continue;
    if (p.timeStamp > 0 && e.timeStamp > 0 && p.timeStamp - e.timeStamp > horizonMs) continue;
    if (Math.hypot(p.clientX - e.clientX, p.clientY - e.clientY) > maxDistPx) continue;
    out.push({ x: p.clientX, y: p.clientY, pressure: samplePressure(kind, p.pressure || e.pressure), t: p.timeStamp });
  }
  stats.predicted += out.length;
  return out;
}

/** Feature detection for the lab and diagnostics. */
export function inputCapabilities(): { coalesced: boolean; predicted: boolean; rawUpdate: boolean } {
  const proto = typeof PointerEvent !== "undefined" ? PointerEvent.prototype : undefined;
  return {
    coalesced: Boolean(proto && "getCoalescedEvents" in proto),
    predicted: Boolean(proto && "getPredictedEvents" in proto),
    rawUpdate: typeof window !== "undefined" && "onpointerrawupdate" in window,
  };
}
