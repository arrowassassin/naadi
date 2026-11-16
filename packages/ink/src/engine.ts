import {
  INK_MAX_ITEMS,
  INK_MAX_POINTS,
  type InkColor,
  type InkDiff,
  type InkItem,
  type InkPoint,
  type InkStroke,
  type InkTextBox,
  roundSu,
  uuidv7,
} from "@naadi/shared";
import { clientToSurface, type SurfaceFrame } from "./coords";
import { InkDocument } from "./document";
import {
  bboxOfItem,
  EMPTY_RECT,
  eraseRuns,
  itemHitByCapsule,
  padRect,
  type Rect,
  rectContainsPoint,
  rectIsEmpty,
  selectedByLasso,
  translateItem,
  unionRect,
  type Vec,
} from "./geometry";
import {
  coalescedSamples,
  type Decision,
  newStats,
  PalmRejection,
  pointerKind,
  predictedSamples,
  type Sample,
  type SampleStats,
} from "./input";
import type { LatencyRecorder } from "./latency";
import { markPenPresent, onPenPresence, penPresent } from "./presence";
import { drawItem, type InkTheme, type Palette, prepareItem, REF, resolvePalette } from "./render";
import { recognizeShape } from "./shapes";
import { activateShortcutTarget, registerShortcutTarget, type ShortcutAction } from "./shortcuts";

export type InkToolName = "pen" | "marker" | "eraser" | "lasso" | "shapes" | "text";
export type EraserMode = "stroke" | "pixel";

/** Default tool sizes in CSS px at the moment of drawing (stored in su, so ink then scales with the page). */
export interface ToolSizes {
  pen: number;
  marker: number;
  /** Eraser radius. */
  eraser: number;
  /** Text box font size. */
  text: number;
}

export const DEFAULT_SIZES: ToolSizes = { pen: 2.4, marker: 18, eraser: 12, text: 17 };

/** A rectangle in CSS px relative to the surface's top-left corner. */
export interface PxRect {
  left: number;
  top: number;
  width: number;
  height: number;
}

export interface SelectionInfo {
  ids: string[];
  /** Bounding box in su. */
  bbox: Rect;
  /** Bounding box in CSS px relative to the surface. */
  rect: PxRect;
}

/** What "Convert to text", "Circle to ask Naadi" and lasso receive: the ink, where it is, and a picture of it. */
export interface InkSelectionPayload {
  ids: string[];
  items: InkItem[];
  bbox: Rect;
  rect: PxRect;
  png: Blob;
}

export interface InkEngineState {
  enabled: boolean;
  tool: InkToolName;
  color: InkColor;
  eraserMode: EraserMode;
  penOnly: boolean;
  penPresent: boolean;
  /** Fingers draw right now (no pen detected, or pen-only off). */
  fingersDraw: boolean;
  canUndo: boolean;
  canRedo: boolean;
  count: number;
  selection: SelectionInfo | null;
  editingText: boolean;
  predict: boolean;
}

export interface InkEngineOptions {
  /**
   * The ink host: a positioned element with no padding covering the surface content (e.g. `position:
   * absolute; inset: 0` as the surface's last child). The surface must be a stacking context
   * (`isolation: isolate`) so the marker layer can sit under its content.
   */
  host: HTMLElement;
  theme?: InkTheme;
  palette?: Partial<Palette>;
  /** Font family for text boxes (defaults to the --font-sans token). */
  font?: string;
  items?: readonly InkItem[];
  enabled?: boolean;
  tool?: InkToolName;
  color?: InkColor;
  eraserMode?: EraserMode;
  penOnly?: boolean;
  sizes?: Partial<ToolSizes>;
  /** Use getPredictedEvents() for the live preview (default true). */
  predict?: boolean;
  latency?: LatencyRecorder;
  now?: () => number;
  onStrokesChange?: (diff: InkDiff) => void;
  onLasso?: (selection: SelectionInfo, png: Blob) => void;
  onConvertToText?: (payload: InkSelectionPayload) => void;
  onAsk?: (payload: InkSelectionPayload) => void;
  onStateChange?: (state: InkEngineState) => void;
  onPenActiveChange?: (active: boolean) => void;
  onError?: (message: string) => void;
}

/** Device pixels one canvas may hold; beyond this the committed layers cover a band around the viewport. */
const MAX_CANVAS_PIXELS = 4_000_000;
/** Committed layers are repainted in TILE×TILE CSS px tiles. */
const TILE = 256;
const HOLD_MS = 550;
const HOLD_SLOP_PX = 4;
const TAP_SLOP_PX = 8;

type Gesture =
  | {
      kind: "stroke";
      pointerId: number;
      item: InkStroke;
      t0: number;
      lastT: number;
      predicted: InkPoint[];
      holdAnchor: Vec;
      holdTimer: ReturnType<typeof setTimeout> | null;
      snapped: boolean;
    }
  | { kind: "erase"; pointerId: number; last: Vec; lastT: number }
  | { kind: "lasso"; pointerId: number; path: Vec[]; lastT: number }
  | { kind: "drag"; pointerId: number; start: Vec; dx: number; dy: number; lastT: number }
  | { kind: "tap"; pointerId: number; startClient: Vec; lastT: number };

interface TextEditor {
  el: HTMLTextAreaElement;
  existing: InkTextBox | null;
  x: number;
  y: number;
  w: number;
  size: number;
  color: InkColor;
}

/**
 * The ink engine for one surface: input pipeline, palm rejection, rendering (committed layers in cached
 * tiles + a live layer), tools, undo/redo and events. Framework-free; see react/InkSurface for React.
 */
export class InkEngine {
  readonly doc = new InkDocument();
  readonly stats: SampleStats = newStats();
  private readonly o: InkEngineOptions;
  private readonly host: HTMLElement;
  private readonly layer: HTMLDivElement;
  private readonly markerCanvas: HTMLCanvasElement;
  private readonly inkCanvas: HTMLCanvasElement;
  private readonly liveCanvas: HTMLCanvasElement;
  private markerCtx: CanvasRenderingContext2D | null = null;
  private inkCtx: CanvasRenderingContext2D | null = null;
  private liveCtx: CanvasRenderingContext2D | null = null;
  private readonly palm: PalmRejection;
  private palette: Palette;
  private theme: InkTheme;
  private font: string;
  private sizes: ToolSizes;
  private state: Omit<InkEngineState, "canUndo" | "canRedo" | "count" | "penPresent" | "fingersDraw" | "selection">;
  private selection: SelectionInfo | null = null;
  private readonly hidden = new Set<string>();
  private gesture: Gesture | null = null;
  private editor: TextEditor | null = null;
  private hover: { x: number; y: number } | null = null;

  // Geometry
  private frame: SurfaceFrame = { left: 0, top: 0, width: 0, height: 0 };
  private frameStale = true;
  private widthPx = 0;
  private heightPx = 0;
  private dpr = 1;
  private band = { y0: 0, h: 0 };
  private readonly dirtyTiles = new Set<string>();
  private liveRect: Rect = EMPTY_RECT; // device px on the live canvas
  private liveDirty = false;
  private raf = 0;
  private pendingLatencyTs: number | null = null;
  private destroyed = false;
  private readonly cleanups: (() => void)[] = [];
  private lastPenActive = false;
  private lastStateJson = "";
  private prewarmTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(options: InkEngineOptions) {
    this.o = options;
    this.host = options.host;
    this.theme = options.theme ?? "paper";
    this.sizes = { ...DEFAULT_SIZES, ...options.sizes };
    this.palm = new PalmRejection({ penOnly: options.penOnly ?? true, penPresent: penPresent() });
    this.state = {
      enabled: options.enabled ?? true,
      tool: options.tool ?? "pen",
      color: options.color ?? "ink",
      eraserMode: options.eraserMode ?? "stroke",
      penOnly: options.penOnly ?? true,
      editingText: false,
      predict: options.predict ?? true,
    };
    this.palette = resolvePalette(this.host, this.theme, options.palette);
    this.font = options.font ?? getComputedStyle(this.host).getPropertyValue("--font-sans").trim();
    if (!this.font) throw new Error("ink: design token --font-sans is not defined; import @naadi/ui/tokens.css");

    const doc = this.host.ownerDocument;
    this.layer = doc.createElement("div");
    this.layer.dataset.inkLayer = "";
    Object.assign(this.layer.style, {
      position: "absolute",
      inset: "0",
      overflow: "hidden",
      userSelect: "none",
      webkitUserSelect: "none",
      webkitTouchCallout: "none",
    } satisfies Partial<CSSStyleDeclaration> & Record<string, string>);
    this.markerCanvas = this.makeCanvas("marker");
    this.inkCanvas = this.makeCanvas("ink");
    this.liveCanvas = this.makeCanvas("live");
    this.layer.append(this.markerCanvas, this.inkCanvas, this.liveCanvas);
    this.host.append(this.layer);
    this.markerCtx = this.markerCanvas.getContext("2d");
    this.inkCtx = this.inkCanvas.getContext("2d");
    this.liveCtx = this.liveCanvas.getContext("2d", { desynchronized: true }) as CanvasRenderingContext2D | null;
    if (!this.markerCtx || !this.inkCtx || !this.liveCtx) this.fail("This browser could not create a drawing canvas.");
    this.applyThemeBlend();

    if (options.items) {
      this.doc.load(options.items);
      this.prewarm();
    }
    this.cleanups.push(
      this.doc.onDirty((r) => this.markDirtySu(r)),
      this.doc.onDiff((d) => {
        this.o.onStrokesChange?.(d);
        this.refreshSelectionAfterChange();
        this.emitState();
      }),
      registerShortcutTarget(this),
      onPenPresence((p) => {
        this.palm.penPresent = p;
        this.applyTouchPolicy();
        this.emitState();
      }),
    );
    this.listen();
    this.measure();
    this.applyTouchPolicy();
    this.emitState();
  }

  // ── Public API ────────────────────────────────────────────────────────────

  getState(): InkEngineState {
    return {
      ...this.state,
      canUndo: this.doc.canUndo(),
      canRedo: this.doc.canRedo(),
      count: this.doc.size,
      penPresent: this.palm.penPresent,
      fingersDraw: this.palm.fingersDraw(),
      selection: this.selection,
    };
  }

  getItems(): InkItem[] {
    return this.doc.items();
  }

  /** Replace the layer's content (e.g. loaded from storage). Clears undo history; emits no diff. */
  load(items: readonly InkItem[]): void {
    this.cancelGesture();
    this.clearSelection();
    this.doc.load(items);
    this.prewarm();
    this.emitState();
  }

  setEnabled(enabled: boolean): void {
    if (this.state.enabled === enabled) return;
    if (!enabled) {
      this.cancelGesture();
      this.commitText();
      this.clearSelection();
    }
    this.state.enabled = enabled;
    this.applyTouchPolicy();
    this.emitState();
  }

  setTool(tool: InkToolName): void {
    if (this.state.tool === tool) return;
    this.cancelGesture();
    this.commitText();
    if (tool !== "lasso") this.clearSelection();
    this.state.tool = tool;
    if (tool === "marker" && (this.state.color === "ink" || this.state.color === "violet")) this.state.color = "lime";
    if (
      (tool === "pen" || tool === "shapes" || tool === "text") &&
      (this.state.color === "lime" || this.state.color === "amber")
    )
      this.state.color = "ink";
    this.hover = null;
    this.liveDirty = true;
    this.requestFrame();
    this.applyTouchPolicy();
    this.emitState();
  }

  setColor(color: InkColor): void {
    this.state.color = color;
    if (this.editor) {
      this.editor.color = color;
      this.editor.el.style.color = this.palette[color];
    }
    this.emitState();
  }

  setEraserMode(mode: EraserMode): void {
    this.state.eraserMode = mode;
    this.emitState();
  }

  setPenOnly(penOnly: boolean): void {
    this.state.penOnly = penOnly;
    this.palm.penOnly = penOnly;
    this.applyTouchPolicy();
    this.emitState();
  }

  setPredict(predict: boolean): void {
    this.state.predict = predict;
    this.emitState();
  }

  setTheme(theme: InkTheme, palette?: Partial<Palette>): void {
    this.theme = theme;
    this.palette = resolvePalette(this.host, theme, palette ?? this.o.palette);
    this.applyThemeBlend();
    this.invalidateAll();
  }

  undo(): void {
    if (this.gesture || this.editor) return;
    this.clearSelection();
    this.doc.undo();
  }

  redo(): void {
    if (this.gesture || this.editor) return;
    this.clearSelection();
    this.doc.redo();
  }

  /** Remove everything (the UI asks for confirmation first). Undoable. */
  clear(): void {
    this.cancelGesture();
    this.closeEditor();
    this.clearSelection();
    const all = this.doc
      .items()
      .map((item) => this.doc.entry(item.id))
      .filter((e) => e !== undefined);
    this.doc.commit({ before: all, after: [] });
  }

  deleteSelection(): void {
    const sel = this.selection;
    if (!sel) return;
    const entries = sel.ids.map((id) => this.doc.entry(id)).filter((e) => e !== undefined);
    this.clearSelection();
    this.doc.commit({ before: entries, after: [] });
  }

  /** Copy the selection (slightly offset) and select the copies. */
  duplicateSelection(): void {
    const sel = this.selection;
    if (!sel) return;
    const now = this.now();
    const offset = 16 / Math.max(1, this.widthPx);
    const copies = sel.ids
      .map((id) => this.doc.get(id))
      .filter((i) => i !== undefined)
      .map((i) => translateItem({ ...i, id: uuidv7(now), createdAt: now }, offset, offset, now));
    this.doc.commit({ before: [], after: this.doc.fresh(copies) });
    this.setSelection(copies.map((c) => c.id));
  }

  clearSelection(): void {
    if (!this.selection) return;
    this.selection = null;
    this.liveDirty = true;
    this.requestFrame();
    this.emitState();
  }

  async convertSelection(): Promise<void> {
    const p = await this.selectionPayload();
    if (p) this.o.onConvertToText?.(p);
  }

  async askAboutSelection(): Promise<void> {
    const p = await this.selectionPayload();
    if (p) this.o.onAsk?.(p);
  }

  /** CSS px bounds of an item relative to the surface (used by tests and overlays). */
  /** True while committed tiles or stroke geometry are still being built (first paint of a big page). */
  renderPending(): boolean {
    return this.dirtyTiles.size > 0 || this.raf !== 0 || this.prewarmTimer !== null;
  }

  itemRect(id: string): PxRect | null {
    const item = this.doc.get(id);
    if (!item) return null;
    return this.suRectToPx(bboxOfItem(item));
  }

  handleShortcut(action: ShortcutAction): boolean {
    if (!this.state.enabled && action !== "undo" && action !== "redo") return false;
    if (action === "undo") {
      if (!this.doc.canUndo()) return false;
      this.undo();
      return true;
    }
    if (action === "redo") {
      if (!this.doc.canRedo()) return false;
      this.redo();
      return true;
    }
    this.setTool(action);
    return true;
  }

  destroy(): void {
    if (this.destroyed) return;
    this.destroyed = true;
    this.cancelGesture();
    this.closeEditor();
    cancelAnimationFrame(this.raf);
    if (this.prewarmTimer) clearTimeout(this.prewarmTimer);
    for (const c of this.cleanups) c();
    this.layer.remove();
    if (this.lastPenActive) this.o.onPenActiveChange?.(false);
  }

  // ── Setup ─────────────────────────────────────────────────────────────────

  /**
   * Build stroke geometry for a loaded page in 6 ms slices between frames, so scrolling a big page never
   * has to run perfect-freehand for strokes it reaches (Safari has no requestIdleCallback).
   */
  private prewarm(): void {
    if (this.prewarmTimer) clearTimeout(this.prewarmTimer);
    const queue = this.doc.items();
    let i = 0;
    const slice = () => {
      this.prewarmTimer = null;
      if (this.destroyed) return;
      const start = performance.now();
      while (i < queue.length && performance.now() - start < 6) prepareItem(queue[i++] as InkItem);
      if (i < queue.length) this.prewarmTimer = setTimeout(slice, 0);
    };
    this.prewarmTimer = setTimeout(slice, 0);
  }

  private makeCanvas(name: string): HTMLCanvasElement {
    const c = this.host.ownerDocument.createElement("canvas");
    c.dataset.inkCanvas = name;
    c.setAttribute("aria-hidden", "true");
    Object.assign(c.style, { position: "absolute", left: "0", top: "0", pointerEvents: "none" });
    return c;
  }

  /**
   * Marker = multiply blend. The marker canvas sits UNDER the surface's content (z-index -1 inside the
   * surface's stacking context) and its strokes multiply with each other inside the canvas, so on paper
   * the highlight shows through dark text exactly as a multiply over the page would, without a CSS
   * mix-blend-mode, which makes the compositor re-blend the whole surface on every scrolled frame.
   * On a dark surface the marker layer is translucent instead (multiply against dark would vanish).
   */
  private applyThemeBlend(): void {
    this.markerCanvas.style.zIndex = "-1";
    this.markerCanvas.style.opacity = this.theme === "paper" ? "1" : "0.45";
    this.inkCanvas.style.zIndex = "1";
    this.liveCanvas.style.zIndex = "1";
  }

  private fail(message: string): void {
    this.o.onError?.(message);
  }

  private now(): number {
    return Math.floor((this.o.now ?? Date.now)());
  }

  private listen(): void {
    const l = this.layer;
    const on = <K extends keyof HTMLElementEventMap>(
      target: HTMLElement | Window | VisualViewport,
      type: K | string,
      fn: (e: never) => void,
      opts?: AddEventListenerOptions,
    ) => {
      target.addEventListener(type, fn as EventListener, opts);
      this.cleanups.push(() => target.removeEventListener(type, fn as EventListener, opts));
    };
    on(l, "pointerdown", (e: PointerEvent) => this.onPointerDown(e));
    on(l, "pointermove", (e: PointerEvent) => this.onPointerMove(e));
    // Chromium: pointerrawupdate arrives as soon as the OS delivers input instead of once per frame.
    if ("onpointerrawupdate" in window) on(l, "pointerrawupdate", (e: PointerEvent) => this.onPointerMove(e));
    on(l, "pointerup", (e: PointerEvent) => this.onPointerUp(e, false));
    on(l, "pointercancel", (e: PointerEvent) => this.onPointerUp(e, true));
    on(l, "lostpointercapture", (e: PointerEvent) => {
      if (this.gesture && this.gesture.pointerId === e.pointerId) this.onPointerUp(e, true);
    });
    on(l, "pointerleave", (e: PointerEvent) => {
      if (e.pointerType !== "pen") return;
      if (!this.gesture) this.applyTouchPolicy();
      if (this.hover) {
        this.hover = null;
        this.liveDirty = true;
        this.requestFrame();
      }
    });
    on(l, "pointerenter", (e: PointerEvent) => {
      // Pencil/stylus hover before contact: claim the gesture for drawing before the tip lands.
      if (e.pointerType === "pen") {
        if (this.palm.hover("pen")) markPenPresent();
        if (this.state.enabled) l.style.touchAction = "none";
      }
    });
    // Safari scrolls the page with Apple Pencil unless touchstart is cancelled; a palm resting while the
    // pen writes must not scroll either. Fingers alone keep native scrolling.
    const blockPenTouch = (e: TouchEvent) => {
      if (!this.state.enabled) return;
      const stylus = Array.from(e.touches).some((t) => (t as Touch & { touchType?: string }).touchType === "stylus");
      if ((stylus || this.palm.penIsDown) && e.cancelable) e.preventDefault();
    };
    on(l, "touchstart", blockPenTouch, { passive: false });
    on(l, "touchmove", blockPenTouch, { passive: false });
    on(l, "contextmenu", (e: Event) => {
      if (this.state.enabled) e.preventDefault();
    });
    on(window, "scroll", () => this.onScroll(), { passive: true, capture: true });
    on(window, "resize", () => this.measure(), { passive: true });
    if (window.visualViewport) on(window.visualViewport, "resize", () => this.measure(), { passive: true });
    const ro = new ResizeObserver(() => this.measure());
    ro.observe(this.host);
    this.cleanups.push(() => ro.disconnect());
  }

  private applyTouchPolicy(): void {
    const s = this.layer.style;
    if (!this.state.enabled) {
      s.pointerEvents = "none";
      s.touchAction = "";
      s.cursor = "";
    } else {
      s.pointerEvents = "auto";
      // Fingers draw → block one-finger panning but keep two-finger pan/zoom (`pinch-zoom` permits
      // multi-finger panning and zooming). A pen is present and pen-only → fingers scroll natively.
      s.touchAction = this.palm.fingersDraw() ? "pinch-zoom" : "auto";
      s.cursor = this.state.tool === "text" ? "text" : "crosshair";
    }
    const active = this.state.enabled;
    if (active !== this.lastPenActive) {
      this.lastPenActive = active;
      this.o.onPenActiveChange?.(active);
    }
  }

  // ── Geometry, band and tiles ──────────────────────────────────────────────

  private refreshFrame(): SurfaceFrame {
    if (this.frameStale) {
      const r = this.host.getBoundingClientRect();
      this.frame = {
        left: r.left + this.host.clientLeft,
        top: r.top + this.host.clientTop,
        width: this.host.clientWidth,
        height: this.host.clientHeight,
      };
      this.frameStale = false;
    }
    return this.frame;
  }

  private effectiveDpr(): number {
    const zoom = window.visualViewport?.scale ?? 1;
    const dpr = (window.devicePixelRatio || 1) * Math.max(1, zoom);
    return Math.min(3, Math.max(1, dpr));
  }

  private measure(): void {
    if (this.destroyed) return;
    this.frameStale = true;
    const w = this.host.clientWidth;
    const h = this.host.clientHeight;
    const dpr = this.effectiveDpr();
    const changed = w !== this.widthPx || h !== this.heightPx || dpr !== this.dpr;
    this.widthPx = w;
    this.heightPx = h;
    this.dpr = dpr;
    if (changed) {
      this.layoutBand(true);
      this.refreshSelectionRect();
      this.positionEditor();
    } else this.layoutBand(false);
  }

  private onScroll(): void {
    this.frameStale = true;
    if (this.gesture) return; // the frame is re-read with the next sample
    this.layoutBand(false);
  }

  /** Choose which vertical band of the surface the committed canvases cover. */
  private layoutBand(force: boolean): void {
    const w = this.widthPx;
    const h = this.heightPx;
    if (w <= 0 || h <= 0) return;
    const dpr = this.dpr;
    const maxH = Math.max(TILE, Math.floor(MAX_CANVAS_PIXELS / (w * dpr * dpr)));
    let y0 = 0;
    let bh = h;
    if (h > maxH) {
      const r = this.host.getBoundingClientRect();
      const vh = window.innerHeight || h;
      const visTop = Math.min(h, Math.max(0, -r.top));
      const visBot = Math.max(visTop, Math.min(h, vh - r.top));
      bh = maxH;
      const margin = Math.min(vh * 0.5, (bh - (visBot - visTop)) / 2);
      const cur = this.band;
      const inside =
        cur.h === bh &&
        visTop >= cur.y0 + Math.max(0, margin * 0.25) &&
        visBot <= cur.y0 + cur.h - Math.max(0, margin * 0.25);
      if (inside && !force) return;
      const mid = (visTop + visBot) / 2;
      y0 = Math.round(Math.min(h - bh, Math.max(0, mid - bh / 2)));
      if (!force && cur.h === bh && Math.abs(cur.y0 - y0) < 1) return;
    } else if (!force && this.band.y0 === 0 && this.band.h === h) return;
    this.moveBand(y0, bh, force);
  }

  private moveBand(y0: number, bh: number, force: boolean): void {
    const dpr = this.dpr;
    const devW = Math.max(1, Math.round(this.widthPx * dpr));
    const devH = Math.max(1, Math.round(bh * dpr));
    const old = this.band;
    const resized = force || this.inkCanvas.width !== devW || this.inkCanvas.height !== devH;
    for (const c of [this.markerCanvas, this.inkCanvas, this.liveCanvas]) {
      if (resized) {
        c.width = devW;
        c.height = devH;
        c.style.width = `${this.widthPx}px`;
        c.style.height = `${bh}px`;
      }
      c.style.top = `${y0}px`;
    }
    this.band = { y0, h: bh };
    this.liveRect = EMPTY_RECT;
    this.liveDirty = true;
    if (resized) {
      this.allTilesDirty();
    } else {
      // Same size, new position: keep the pixels that are still in the band and repaint only the strip
      // that scrolled in.
      const shift = Math.round((old.y0 - y0) * dpr);
      for (const ctx of [this.markerCtx, this.inkCtx]) {
        if (!ctx) continue;
        ctx.save();
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.globalCompositeOperation = "copy";
        ctx.drawImage(ctx.canvas, 0, shift);
        ctx.restore();
      }
      const exposed =
        shift > 0
          ? { top: y0, bottom: Math.min(y0 + bh, old.y0) }
          : { top: Math.max(y0, old.y0 + old.h), bottom: y0 + bh };
      if (Math.abs(shift) >= devH) this.allTilesDirty();
      else this.markDirtyPx({ x0: 0, y0: exposed.top, x1: this.widthPx, y1: exposed.bottom });
    }
    this.requestFrame();
  }

  private allTilesDirty(): void {
    this.markDirtyPx({ x0: 0, y0: this.band.y0, x1: this.widthPx, y1: this.band.y0 + this.band.h });
  }

  private invalidateAll(): void {
    this.allTilesDirty();
    this.liveDirty = true;
    this.requestFrame();
  }

  /** Mark tiles dirty for a rectangle in su. */
  private markDirtySu(r: Rect): void {
    if (rectIsEmpty(r)) return;
    const w = this.widthPx;
    const pad = 2 / Math.max(1, w);
    const p = padRect(r, pad);
    this.markDirtyPx({
      x0: Math.max(0, p.x0 * w),
      y0: Math.max(this.band.y0, p.y0 * w),
      x1: Math.min(w, p.x1 * w),
      y1: Math.min(this.band.y0 + this.band.h, p.y1 * w),
    });
  }

  /** Mark tiles dirty for a rectangle in CSS px (surface-relative), clipped to the band. */
  private markDirtyPx(r: Rect): void {
    const y0 = Math.max(r.y0, this.band.y0);
    const y1 = Math.min(r.y1, this.band.y0 + this.band.h);
    const x0 = Math.max(0, r.x0);
    const x1 = Math.min(this.widthPx, r.x1);
    if (!(x1 > x0 && y1 > y0)) return;
    for (let tx = Math.floor(x0 / TILE); tx <= Math.floor((x1 - 0.001) / TILE); tx++)
      for (let ty = Math.floor(y0 / TILE); ty <= Math.floor((y1 - 0.001) / TILE); ty++)
        this.dirtyTiles.add(`${tx},${ty}`);
    this.requestFrame();
  }

  private requestFrame(): void {
    if (this.raf || this.destroyed) return;
    this.raf = requestAnimationFrame((ts) => this.onFrame(ts));
  }

  private onFrame(_ts: number): void {
    this.raf = 0;
    if (this.destroyed) return;
    const start = performance.now();
    if (this.liveDirty) {
      this.liveDirty = false;
      this.renderLive();
      const evTs = this.pendingLatencyTs;
      if (evTs !== null && this.o.latency) {
        this.pendingLatencyTs = null;
        this.o.latency.recordPaint(evTs, performance.now());
        const rec = this.o.latency;
        requestAnimationFrame((next) => rec.recordFrame(evTs, next));
      }
    }
    // Committed tiles: keep frames short while a stroke is live, finish the rest next frame.
    const budget = this.gesture ? 4 : 10;
    if (this.dirtyTiles.size > 0) {
      const tiles = [...this.dirtyTiles];
      for (const key of tiles) {
        this.dirtyTiles.delete(key);
        this.renderTile(key);
        if (performance.now() - start > budget) break;
      }
      if (this.dirtyTiles.size > 0) this.requestFrame();
    }
  }

  /** Reference space → device px of the band canvases. */
  private setDrawTransform(ctx: CanvasRenderingContext2D): void {
    const k = (this.widthPx * this.dpr) / REF;
    ctx.setTransform(k, 0, 0, k, 0, -this.band.y0 * this.dpr);
  }

  private renderTile(key: string): void {
    const [txs, tys] = key.split(",");
    const tx = Number(txs);
    const ty = Number(tys);
    const dpr = this.dpr;
    const x0 = tx * TILE;
    const y0 = Math.max(ty * TILE, this.band.y0);
    const x1 = Math.min(this.widthPx, (tx + 1) * TILE);
    const y1 = Math.min((ty + 1) * TILE, this.band.y0 + this.band.h);
    if (!(x1 > x0 && y1 > y0)) return;
    const dev = {
      x: Math.floor(x0 * dpr),
      y: Math.floor((y0 - this.band.y0) * dpr),
      w: Math.ceil((x1 - x0) * dpr) + 1,
      h: Math.ceil((y1 - y0) * dpr) + 1,
    };
    for (const ctx of [this.markerCtx, this.inkCtx]) {
      if (!ctx) continue;
      ctx.save();
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(dev.x, dev.y, dev.w, dev.h);
      ctx.beginPath();
      ctx.rect(dev.x, dev.y, dev.w, dev.h);
      ctx.clip();
      this.setDrawTransform(ctx);
    }
    const w = this.widthPx;
    const query = { x0: x0 / w, y0: y0 / w, x1: x1 / w, y1: y1 / w };
    for (const e of this.doc.query(padRect(query, 1 / w))) {
      if (this.hidden.has(e.item.id)) continue;
      const ctx = e.item.tool === "marker" ? this.markerCtx : this.inkCtx;
      if (!ctx) continue;
      ctx.globalCompositeOperation = e.item.tool === "marker" ? "multiply" : "source-over";
      drawItem(ctx, e.item, this.palette, this.font);
    }
    this.markerCtx?.restore();
    this.inkCtx?.restore();
  }

  /** Repaint the live layer: current stroke (+ prediction), lasso, selection, drag preview, hover cursor. */
  private renderLive(): void {
    const ctx = this.liveCtx;
    if (!ctx) return;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    if (!rectIsEmpty(this.liveRect)) {
      const r = this.liveRect;
      ctx.clearRect(r.x0 - 2, r.y0 - 2, r.x1 - r.x0 + 4, r.y1 - r.y0 + 4);
    }
    let drawn = EMPTY_RECT;
    const k = this.widthPx * this.dpr;
    const toDev = (r: Rect): Rect => ({
      x0: r.x0 * k,
      y0: r.y0 * k - this.band.y0 * this.dpr,
      x1: r.x1 * k,
      y1: r.y1 * k - this.band.y0 * this.dpr,
    });
    this.setDrawTransform(ctx);
    const g = this.gesture;
    // A live marker stroke is previewed under the content, where it will land; everything else on top.
    this.liveCanvas.style.zIndex = g?.kind === "stroke" && g.item.tool === "marker" ? "-1" : "1";
    if (g?.kind === "stroke") {
      const item: InkStroke =
        g.predicted.length > 0 && this.state.predict
          ? { ...g.item, points: [...g.item.points, ...g.predicted] }
          : g.item;
      ctx.globalAlpha = item.tool === "marker" && this.theme !== "paper" ? 0.45 : 1;
      drawItem(ctx, item, this.palette, this.font, true);
      ctx.globalAlpha = 1;
      drawn = unionRect(drawn, toDev(bboxOfItem(item)));
    }
    const lineW = REF / Math.max(1, this.widthPx); // 1 CSS px in reference units
    if (g?.kind === "lasso" && g.path.length > 1) {
      ctx.save();
      ctx.strokeStyle = this.palette.selection;
      ctx.lineWidth = 1.5 * lineW;
      ctx.setLineDash([6 * lineW, 5 * lineW]);
      ctx.beginPath();
      for (const [i, p] of g.path.entries()) {
        if (i === 0) ctx.moveTo(p.x * REF, p.y * REF);
        else ctx.lineTo(p.x * REF, p.y * REF);
      }
      ctx.closePath();
      ctx.stroke();
      ctx.restore();
      let box = EMPTY_RECT;
      for (const p of g.path) box = unionRect(box, { x0: p.x, y0: p.y, x1: p.x, y1: p.y });
      drawn = unionRect(drawn, padRect(toDev(box), 4));
    }
    if (this.selection) {
      const dx = g?.kind === "drag" ? g.dx : 0;
      const dy = g?.kind === "drag" ? g.dy : 0;
      if (g?.kind === "drag") {
        ctx.save();
        ctx.translate(dx * REF, dy * REF);
        for (const id of this.selection.ids) {
          const item = this.doc.get(id);
          if (item) drawItem(ctx, item, this.palette, this.font);
        }
        ctx.restore();
      }
      const b = this.selection.bbox;
      const pad = 6 / Math.max(1, this.widthPx);
      const box = { x0: b.x0 + dx - pad, y0: b.y0 + dy - pad, x1: b.x1 + dx + pad, y1: b.y1 + dy + pad };
      ctx.save();
      ctx.strokeStyle = this.palette.selection;
      ctx.lineWidth = 1.5 * lineW;
      ctx.setLineDash([6 * lineW, 4 * lineW]);
      ctx.strokeRect(box.x0 * REF, box.y0 * REF, (box.x1 - box.x0) * REF, (box.y1 - box.y0) * REF);
      ctx.restore();
      drawn = unionRect(drawn, padRect(toDev(box), 4));
    }
    if (this.hover && !g) {
      const r = (this.state.tool === "eraser" ? this.sizes.eraser : this.toolSizePx() / 2) / Math.max(1, this.widthPx);
      ctx.save();
      ctx.strokeStyle = this.state.tool === "eraser" ? this.palette.eraser : this.palette[this.state.color];
      ctx.lineWidth = lineW;
      ctx.beginPath();
      ctx.arc(this.hover.x * REF, this.hover.y * REF, Math.max(r, 2 / Math.max(1, this.widthPx)) * REF, 0, Math.PI * 2);
      ctx.stroke();
      ctx.restore();
      drawn = unionRect(
        drawn,
        padRect(toDev({ x0: this.hover.x - r, y0: this.hover.y - r, x1: this.hover.x + r, y1: this.hover.y + r }), 4),
      );
    }
    this.liveRect = drawn;
  }

  private suRectToPx(r: Rect): PxRect {
    const w = this.widthPx;
    return { left: r.x0 * w, top: r.y0 * w, width: (r.x1 - r.x0) * w, height: (r.y1 - r.y0) * w };
  }

  // ── Pointer input ─────────────────────────────────────────────────────────

  private toSu(s: { x: number; y: number }): Vec {
    const [x, y] = clientToSurface(this.refreshFrame(), s.x, s.y);
    return { x, y };
  }

  private toolSizePx(): number {
    const t = this.state.tool;
    return t === "marker" ? this.sizes.marker : t === "text" ? this.sizes.text : this.sizes.pen;
  }

  private onPointerDown(e: PointerEvent): void {
    if (!this.state.enabled || this.destroyed) return;
    const kind = pointerKind(e.pointerType);
    if (kind === "pen") markPenPresent();
    // A pointer id that is still "down" lost its pointerup somewhere: start clean instead of jamming.
    if (this.palm.isActive(e.pointerId)) {
      this.cancelGesture();
      this.palm.up(e.pointerId);
    }
    const decision: Decision = this.palm.down(e.pointerId, kind, { drawingTool: true }, e.button);
    if (decision.cancel !== null && this.gesture?.pointerId === decision.cancel) this.cancelGesture();
    this.applyTouchPolicy();
    if (decision.action === "ignore") {
      e.preventDefault();
      return;
    }
    if (decision.action === "pass") return;
    e.preventDefault();
    try {
      this.layer.setPointerCapture(e.pointerId);
    } catch {
      // Synthetic or already-released pointers cannot be captured; events still reach the layer.
    }
    activateShortcutTarget(this);
    this.frameStale = true;
    this.hover = null;
    const pt = this.toSu({ x: e.clientX, y: e.clientY });
    const t = e.timeStamp;
    const tool = this.state.tool;

    if (tool === "text") {
      this.gesture = { kind: "tap", pointerId: e.pointerId, startClient: { x: e.clientX, y: e.clientY }, lastT: t };
      return;
    }
    if (tool === "eraser") {
      this.doc.begin();
      this.gesture = { kind: "erase", pointerId: e.pointerId, last: pt, lastT: t };
      this.eraseAlong([pt]);
      return;
    }
    if (tool === "lasso") {
      if (
        this.selection &&
        rectContainsPoint(padRect(this.selection.bbox, 12 / Math.max(1, this.widthPx)), pt.x, pt.y)
      ) {
        this.gesture = { kind: "drag", pointerId: e.pointerId, start: pt, dx: 0, dy: 0, lastT: t };
        for (const id of this.selection.ids) this.hidden.add(id);
        this.markDirtySu(this.selection.bbox);
      } else {
        this.clearSelection();
        this.gesture = { kind: "lasso", pointerId: e.pointerId, path: [pt], lastT: t };
      }
      this.liveDirty = true;
      this.requestFrame();
      return;
    }
    if (this.doc.size >= INK_MAX_ITEMS) {
      this.palm.up(e.pointerId);
      this.fail(
        `This page holds the maximum of ${INK_MAX_ITEMS.toLocaleString("en")} strokes. Erase some to keep writing.`,
      );
      return;
    }
    const now = this.now();
    const strokeTool = tool === "marker" ? "marker" : "pen";
    const sizeSu = roundSu(this.toolSizePx() / Math.max(1, this.widthPx));
    const item: InkStroke = {
      id: uuidv7(now),
      tool: strokeTool,
      color: this.state.color,
      size: Math.min(0.1, Math.max(0.00002, sizeSu)),
      points: [[pt.x, pt.y, kind === "pen" ? clampPressure(e.pressure) : 0.5, 0]],
      createdAt: now,
      updatedAt: now,
    };
    this.gesture = {
      kind: "stroke",
      pointerId: e.pointerId,
      item,
      t0: t,
      lastT: t,
      predicted: [],
      holdAnchor: { x: e.clientX, y: e.clientY },
      holdTimer: null,
      snapped: false,
    };
    this.armHold();
    this.pendingLatencyTs = t;
    this.liveDirty = true;
    this.requestFrame();
  }

  private onPointerMove(e: PointerEvent): void {
    if (this.destroyed) return;
    const g = this.gesture;
    if (!g || g.pointerId !== e.pointerId || !this.palm.isActive(e.pointerId)) {
      // Hover (Pencil hover, stylus or mouse without buttons) only drives the cursor preview.
      if (e.pointerType === "pen" && e.buttons === 0 && this.state.enabled && !g) {
        if (this.palm.hover("pen")) markPenPresent();
        const p = this.toSu({ x: e.clientX, y: e.clientY });
        this.hover = p;
        this.liveDirty = true;
        this.requestFrame();
      }
      return;
    }
    const samples = coalescedSamples(e, g.lastT, this.stats);
    if (samples.length === 0) return;
    g.lastT = (samples[samples.length - 1] as Sample).t;
    this.pendingLatencyTs = e.timeStamp;
    if (g.kind === "stroke") this.extendStroke(g, samples, e);
    else if (g.kind === "erase") {
      const path = [g.last, ...samples.map((s) => this.toSu(s))];
      g.last = path[path.length - 1] as Vec;
      this.eraseAlong(path);
    } else if (g.kind === "lasso") {
      for (const s of samples) g.path.push(this.toSu(s));
    } else if (g.kind === "drag") {
      const p = this.toSu(samples[samples.length - 1] as Sample);
      g.dx = p.x - g.start.x;
      g.dy = p.y - g.start.y;
    }
    this.liveDirty = true;
    this.requestFrame();
  }

  private extendStroke(g: Extract<Gesture, { kind: "stroke" }>, samples: Sample[], e: PointerEvent): void {
    if (g.snapped) return; // a recognised shape holds until the pen lifts
    const pts = g.item.points;
    for (const s of samples) {
      const p = this.toSu(s);
      const t = Math.max(0, Math.min(86_400_000, Math.round(s.t - g.t0)));
      const prev = pts[pts.length - 1] as InkPoint;
      const tt = Math.max(prev[3], t);
      if (prev[0] === p.x && prev[1] === p.y) {
        prev[2] = Math.max(prev[2], s.pressure);
        continue;
      }
      pts.push([p.x, p.y, s.pressure, tt]);
      if (Math.hypot(s.x - g.holdAnchor.x, s.y - g.holdAnchor.y) > HOLD_SLOP_PX) {
        g.holdAnchor = { x: s.x, y: s.y };
        this.armHold();
      }
    }
    if (pts.length >= INK_MAX_POINTS) this.splitLongStroke(g);
    g.predicted =
      this.state.predict && g.item.tool !== "marker"
        ? predictedSamples(e, this.stats).map((s) => {
            const p = this.toSu(s);
            return [p.x, p.y, s.pressure, (pts[pts.length - 1] as InkPoint)[3]] as InkPoint;
          })
        : [];
  }

  /** Commit a stroke that hit the point limit and continue seamlessly in a new one. */
  private splitLongStroke(g: Extract<Gesture, { kind: "stroke" }>): void {
    const last = g.item.points[g.item.points.length - 1] as InkPoint;
    this.commitStroke(g.item);
    const now = this.now();
    g.item = { ...g.item, id: uuidv7(now), createdAt: now, updatedAt: now, points: [[last[0], last[1], last[2], 0]] };
    g.t0 = g.lastT;
  }

  private armHold(): void {
    const g = this.gesture;
    if (g?.kind !== "stroke") return;
    if (g.holdTimer) clearTimeout(g.holdTimer);
    g.holdTimer = setTimeout(() => {
      const cur = this.gesture;
      if (cur !== g || g.snapped || g.item.points.length < 3) return;
      const shape = recognizeShape(g.item.points);
      if (!shape) return;
      g.item = { ...g.item, points: shape.points, shape: shape.shape };
      g.snapped = true;
      g.predicted = [];
      this.liveDirty = true;
      this.requestFrame();
    }, HOLD_MS);
  }

  private onPointerUp(e: PointerEvent, cancelled: boolean): void {
    const wasActive = this.palm.up(e.pointerId);
    this.applyTouchPolicy();
    const g = this.gesture;
    if (!g || g.pointerId !== e.pointerId || !wasActive) return;
    if (cancelled) {
      // The browser took the gesture (scroll/zoom) or the system interrupted: it was not deliberate ink.
      this.cancelGesture();
      return;
    }
    if (g.kind === "stroke") {
      const samples = coalescedSamples(e, g.lastT, this.stats);
      if (samples.length > 0) this.extendStroke(g, samples, e);
      if (g.holdTimer) clearTimeout(g.holdTimer);
      this.gesture = null;
      let item = g.item;
      if (this.state.tool === "shapes" && !g.snapped) {
        const shape = recognizeShape(item.points);
        if (shape) item = { ...item, points: shape.points, shape: shape.shape };
      }
      this.commitStroke(item);
    } else if (g.kind === "erase") {
      this.gesture = null;
      this.doc.end();
    } else if (g.kind === "lasso") {
      this.gesture = null;
      this.finishLasso(g.path);
    } else if (g.kind === "drag") {
      this.gesture = null;
      this.finishDrag(g.dx, g.dy);
    } else if (g.kind === "tap") {
      this.gesture = null;
      if (Math.hypot(e.clientX - g.startClient.x, e.clientY - g.startClient.y) <= TAP_SLOP_PX)
        this.openTextAt(this.toSu({ x: e.clientX, y: e.clientY }));
    }
    this.liveDirty = true;
    this.requestFrame();
  }

  private commitStroke(item: InkStroke): void {
    const points = item.points.map(
      (p) => [roundSu(p[0]), roundSu(p[1]), Math.round(p[2] * 1000) / 1000, p[3]] as InkPoint,
    );
    const final: InkStroke = { ...item, points };
    this.doc.commit({ before: [], after: this.doc.fresh([final]) });
  }

  private cancelGesture(): void {
    const g = this.gesture;
    if (!g) return;
    this.gesture = null;
    if (g.kind === "stroke" && g.holdTimer) clearTimeout(g.holdTimer);
    if (g.kind === "erase") this.doc.rollback();
    if (g.kind === "drag" && this.selection) {
      for (const id of this.selection.ids) this.hidden.delete(id);
      this.markDirtySu(this.selection.bbox);
    }
    this.liveDirty = true;
    this.requestFrame();
  }

  // ── Eraser ────────────────────────────────────────────────────────────────

  private eraseAlong(path: Vec[]): void {
    const r = this.sizes.eraser / Math.max(1, this.widthPx);
    let box = EMPTY_RECT;
    for (const p of path) box = unionRect(box, { x0: p.x, y0: p.y, x1: p.x, y1: p.y });
    const candidates = this.doc.query(padRect(box, r + 0.05));
    const segs: [Vec, Vec][] = path.length === 1 ? [[path[0] as Vec, path[0] as Vec]] : [];
    for (let i = 1; i < path.length; i++) segs.push([path[i - 1] as Vec, path[i] as Vec]);
    const now = this.now();
    for (const e of candidates) {
      const item = e.item;
      if (!segs.some(([a, b]) => itemHitByCapsule(item, a, b, r))) continue;
      if (this.state.eraserMode === "stroke" || item.tool === "text") {
        this.doc.commit({ before: [e], after: [] });
        continue;
      }
      const runs = eraseRuns(item, path, r);
      if (!runs) continue;
      const fragments: InkStroke[] = runs
        .filter((run) => run.length > 0)
        .map((run) => {
          const { shape: _shape, ...rest } = item;
          return { ...rest, id: uuidv7(now), points: run.slice(0, INK_MAX_POINTS), updatedAt: now };
        });
      // Fragments keep the original's place in the z-order.
      const after = fragments.map((f, i) => ({ item: f, seq: e.seq + (i + 1) / (fragments.length + 1) }));
      this.doc.commit({ before: [e], after });
    }
  }

  // ── Lasso ─────────────────────────────────────────────────────────────────

  private finishLasso(path: Vec[]): void {
    if (path.length < 3) return;
    let box = EMPTY_RECT;
    for (const p of path) box = unionRect(box, { x0: p.x, y0: p.y, x1: p.x, y1: p.y });
    const ids = this.doc
      .query(box)
      .filter((e) => selectedByLasso(e.item, path, box))
      .map((e) => e.item.id);
    if (ids.length === 0) return;
    this.setSelection(ids);
    const sel = this.selection;
    if (sel && this.o.onLasso) {
      void this.renderPng(sel).then((png) => {
        if (png && this.selection === sel) this.o.onLasso?.(sel, png);
      });
    }
  }

  private setSelection(ids: string[]): void {
    let bbox = EMPTY_RECT;
    for (const id of ids) {
      const item = this.doc.get(id);
      if (item) bbox = unionRect(bbox, bboxOfItem(item));
    }
    this.selection = ids.length > 0 ? { ids, bbox, rect: this.suRectToPx(bbox) } : null;
    this.liveDirty = true;
    this.requestFrame();
    this.emitState();
  }

  private refreshSelectionAfterChange(): void {
    if (!this.selection) return;
    const ids = this.selection.ids.filter((id) => this.doc.get(id));
    if (ids.length === 0) this.clearSelection();
    else this.setSelection(ids);
  }

  private refreshSelectionRect(): void {
    if (this.selection) this.selection = { ...this.selection, rect: this.suRectToPx(this.selection.bbox) };
    this.emitState();
  }

  private finishDrag(dx: number, dy: number): void {
    const sel = this.selection;
    if (!sel) return;
    for (const id of sel.ids) this.hidden.delete(id);
    if (Math.hypot(dx, dy) * this.widthPx < 1) {
      this.markDirtySu(sel.bbox);
      return;
    }
    const now = this.now();
    const before = sel.ids.map((id) => this.doc.entry(id)).filter((e) => e !== undefined);
    const after = before.map((e) => ({ item: translateItem(e.item, dx, dy, now), seq: e.seq }));
    this.markDirtySu(sel.bbox);
    this.doc.commit({ before, after });
  }

  private async selectionPayload(): Promise<InkSelectionPayload | null> {
    const sel = this.selection;
    if (!sel) return null;
    const png = await this.renderPng(sel);
    if (!png) return null;
    const items = sel.ids.map((id) => this.doc.get(id)).filter((i) => i !== undefined);
    return { ids: sel.ids, items, bbox: sel.bbox, rect: sel.rect, png };
  }

  /** A cropped picture of the selected ink on the surface colour, at 2× (max 2048 px a side). */
  private renderPng(sel: SelectionInfo): Promise<Blob | null> {
    const padPx = 12;
    const w = this.widthPx;
    const cssW = (sel.bbox.x1 - sel.bbox.x0) * w + padPx * 2;
    const cssH = (sel.bbox.y1 - sel.bbox.y0) * w + padPx * 2;
    const scale = Math.min(2, 2048 / Math.max(cssW, cssH));
    const c = this.host.ownerDocument.createElement("canvas");
    c.width = Math.max(1, Math.round(cssW * scale));
    c.height = Math.max(1, Math.round(cssH * scale));
    const ctx = c.getContext("2d");
    if (!ctx) return Promise.resolve(null);
    const bg = getComputedStyle(this.host)
      .getPropertyValue(this.theme === "paper" ? "--color-paper" : "--color-surface-1")
      .trim();
    ctx.fillStyle = bg || this.palette.selection;
    ctx.fillRect(0, 0, c.width, c.height);
    const k = (w * scale) / REF;
    ctx.setTransform(k, 0, 0, k, (padPx - sel.bbox.x0 * w) * scale, (padPx - sel.bbox.y0 * w) * scale);
    const markers = sel.ids.map((id) => this.doc.get(id)).filter((i) => i !== undefined);
    for (const item of markers.filter((i) => i.tool === "marker")) {
      ctx.globalCompositeOperation = this.theme === "paper" ? "multiply" : "source-over";
      ctx.globalAlpha = this.theme === "paper" ? 1 : 0.45;
      drawItem(ctx, item, this.palette, this.font);
    }
    ctx.globalCompositeOperation = "source-over";
    ctx.globalAlpha = 1;
    for (const item of markers.filter((i) => i.tool !== "marker")) drawItem(ctx, item, this.palette, this.font);
    return new Promise((resolve) => c.toBlob((b) => resolve(b), "image/png"));
  }

  // ── Text boxes ────────────────────────────────────────────────────────────

  private openTextAt(p: Vec): void {
    this.commitText();
    const hit = this.doc
      .query({ x0: p.x, y0: p.y, x1: p.x, y1: p.y })
      .reverse()
      .find((e) => e.item.tool === "text");
    const w = Math.max(1, this.widthPx);
    if (hit && hit.item.tool === "text") {
      this.openEditor(hit.item.x, hit.item.y, hit.item.w, hit.item.size, hit.item.color, hit.item);
      return;
    }
    const size = this.sizes.text / w;
    const boxW = Math.max(120 / w, Math.min(0.45, 1 - p.x - 12 / w));
    this.openEditor(
      p.x,
      Math.max(0, p.y - size * 0.65),
      boxW,
      size,
      this.state.color === "lime" || this.state.color === "amber" ? "ink" : this.state.color,
      null,
    );
  }

  private openEditor(
    x: number,
    y: number,
    w: number,
    size: number,
    color: InkColor,
    existing: InkTextBox | null,
  ): void {
    const el = this.host.ownerDocument.createElement("textarea");
    el.dataset.inkText = "";
    el.setAttribute("aria-label", existing ? "Edit text box" : "New text box");
    el.rows = 1;
    el.value = existing?.text ?? "";
    el.maxLength = 2000;
    Object.assign(el.style, {
      position: "absolute",
      margin: "0",
      padding: "0",
      border: "0",
      outline: `1.5px dashed ${this.palette.selection}`,
      outlineOffset: "4px",
      background: "transparent",
      color: this.palette[color],
      fontFamily: this.font,
      fontWeight: "500",
      lineHeight: "1.3",
      resize: "none",
      overflow: "hidden",
      zIndex: "2",
      touchAction: "manipulation",
      userSelect: "text",
      webkitUserSelect: "text",
    } satisfies Partial<CSSStyleDeclaration> & Record<string, string>);
    this.editor = { el, existing, x, y, w, size, color };
    if (existing) {
      this.hidden.add(existing.id);
      this.markDirtySu(bboxOfItem(existing));
    }
    this.positionEditor();
    el.addEventListener("input", () => this.autosizeEditor());
    el.addEventListener("keydown", (ev) => {
      ev.stopPropagation(); // ink shortcuts must not fire while typing
      if (ev.key === "Escape") {
        ev.preventDefault();
        this.closeEditor();
      } else if (ev.key === "Enter" && (ev.metaKey || ev.ctrlKey)) {
        ev.preventDefault();
        this.commitText();
      }
    });
    el.addEventListener("blur", () => this.commitText());
    el.addEventListener("pointerdown", (ev) => ev.stopPropagation());
    this.layer.append(el);
    this.state.editingText = true;
    this.emitState();
    el.focus({ preventScroll: true });
    this.autosizeEditor();
  }

  private positionEditor(): void {
    const ed = this.editor;
    if (!ed) return;
    const w = this.widthPx;
    Object.assign(ed.el.style, {
      left: `${ed.x * w}px`,
      top: `${ed.y * w}px`,
      width: `${ed.w * w}px`,
      fontSize: `${ed.size * w}px`,
    });
    this.autosizeEditor();
  }

  private autosizeEditor(): void {
    const el = this.editor?.el;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }

  /** Commit the open text box (empty text deletes an existing box). */
  commitText(): void {
    const ed = this.editor;
    if (!ed) return;
    const text = ed.el.value.replace(/\s+$/u, "");
    this.removeEditor();
    const now = this.now();
    if (ed.existing) {
      const entry = this.doc.entry(ed.existing.id);
      this.hidden.delete(ed.existing.id);
      this.markDirtySu(bboxOfItem(ed.existing));
      if (!entry) return;
      if (!text) this.doc.commit({ before: [entry], after: [] });
      else if (text !== ed.existing.text || ed.color !== ed.existing.color)
        this.doc.commit({
          before: [entry],
          after: [{ item: { ...ed.existing, text, color: ed.color, updatedAt: now }, seq: entry.seq }],
        });
      return;
    }
    if (!text) return;
    const item: InkTextBox = {
      id: uuidv7(now),
      tool: "text",
      color: ed.color,
      size: roundSu(Math.min(0.2, ed.size)),
      x: roundSu(ed.x),
      y: roundSu(ed.y),
      w: roundSu(Math.min(4, ed.w)),
      text,
      createdAt: now,
      updatedAt: now,
    };
    this.doc.commit({ before: [], after: this.doc.fresh([item]) });
  }

  private closeEditor(): void {
    const ed = this.editor;
    if (!ed) return;
    this.removeEditor();
    if (ed.existing) {
      this.hidden.delete(ed.existing.id);
      this.markDirtySu(bboxOfItem(ed.existing));
    }
  }

  private removeEditor(): void {
    const ed = this.editor;
    if (!ed) return;
    this.editor = null;
    ed.el.remove();
    this.state.editingText = false;
    this.emitState();
  }

  // ── State ─────────────────────────────────────────────────────────────────

  private emitState(): void {
    if (!this.o.onStateChange || this.destroyed) return;
    const s = this.getState();
    const json = JSON.stringify(s);
    if (json === this.lastStateJson) return;
    this.lastStateJson = json;
    this.o.onStateChange(s);
  }
}

function clampPressure(p: number): number {
  return !Number.isFinite(p) || p <= 0 ? 0.5 : Math.min(1, Math.max(0.02, p));
}
