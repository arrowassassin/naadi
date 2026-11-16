import {
  INK_MAX_ITEMS,
  INK_MAX_POINTS,
  type InkColor,
  type InkDiff,
  type InkItem,
  type InkPoint,
  type InkStroke,
  type InkSurfaceType,
  type InkTextBox,
  roundSu,
  uuidv7,
} from "@naadi/shared";
import { type CanvasLease, canvasBudget } from "./budget";
import { clientToSurface, type SurfaceFrame } from "./coords";
import { InkDocument } from "./document";
import { encodePng } from "./encode";
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
import { drawItem, type InkTheme, LiveStroke, type Palette, prepareItem, REF, resolvePalette } from "./render";
import { recognizeShape } from "./shapes";
import { activateShortcutTarget, registerShortcutTarget, type ShortcutAction } from "./shortcuts";

export type InkToolName = "pen" | "marker" | "eraser" | "lasso" | "shapes" | "text";
export type EraserMode = "stroke" | "pixel";
/**
 * Where marker ink sits: "under" the surface content (plain text: the highlight shows through the glyphs)
 * or "multiply" over it with a multiply blend (images, PDF pages, tables with opaque fills).
 */
export type MarkerMode = "under" | "multiply";

/** The marker placement that suits each kind of surface (HANDOFF §5.3). */
export function markerModeFor(surface: InkSurfaceType): MarkerMode {
  return surface === "pdf_page" ? "multiply" : "under";
}

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

/**
 * A lasso selection. It may hold no ink at all ("circle to ask" around printed text or a figure): then
 * `ids` is empty and `bbox`/`path` describe the circled region.
 */
export interface SelectionInfo {
  ids: string[];
  /** Bounding box in su: of the selected ink, or of the lasso loop when no ink was selected. */
  bbox: Rect;
  /** `bbox` in CSS px relative to the surface. */
  rect: PxRect;
  /** The lasso loop in su (the bbox outline for selections made by copy). */
  path: Vec[];
}

/**
 * What "Convert to text", "Circle to ask Naadi" and lasso receive: the selected ink, the circled region,
 * and an ink-only picture of the selection (`png` is null when no ink was selected). The PNG never contains
 * the page under the ink: the consumer (assistant, ingest) composites the page content and the surrounding
 * text for the region itself, since only it knows what the surface shows.
 */
export interface InkSelectionPayload {
  ids: string[];
  items: InkItem[];
  bbox: Rect;
  rect: PxRect;
  path: Vec[];
  png: Blob | null;
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
  /** Marker placement; defaults to "under" (see markerModeFor). */
  markerMode?: MarkerMode;
  penOnly?: boolean;
  sizes?: Partial<ToolSizes>;
  /** Use getPredictedEvents() for the live preview (default true). */
  predict?: boolean;
  latency?: LatencyRecorder;
  now?: () => number;
  onStrokesChange?: (diff: InkDiff) => void;
  /** A lasso closed: with the ink it selected (png = ink-only picture) or none (png = null). */
  onLasso?: (selection: SelectionInfo, png: Blob | null) => void;
  onConvertToText?: (payload: InkSelectionPayload) => void;
  onAsk?: (payload: InkSelectionPayload) => void;
  onStateChange?: (state: InkEngineState) => void;
  onPenActiveChange?: (active: boolean) => void;
  onError?: (message: string) => void;
}

/** Device pixels per slab canvas: bounds memory on tall surfaces at high device pixel ratios. */
const MAX_SLAB_PIXELS = 8_000_000;
/** Slabs (canvas elements + settled images) kept per surface, in device px: most pages fit whole. */
const MAX_SLABS_PIXELS = 16_000_000;
/** Quiet time after the last change before canvases are swapped for images. */
const SETTLE_MS = 700;
/** Room the live canvas keeps around its content, so a stroke rarely outgrows it. */
const LIVE_MARGIN_PX = 160;
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
  | { kind: "erase"; pointerId: number; last: Vec; lastT: number; pending: Vec[] }
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
  readonly doc: InkDocument;
  readonly stats: SampleStats = newStats();
  private readonly o: InkEngineOptions;
  private readonly host: HTMLElement;
  private readonly layer: HTMLDivElement;
  private readonly liveCanvas: HTMLCanvasElement;
  private readonly slabs = new Map<number, Slab>();
  private slabH = 0;
  /** Which kinds of ink each painted tile holds (to hide empty slab canvases). */
  private readonly tileKinds = new Map<string, { m: boolean; k: boolean }>();
  /** Where the live canvas sits, surface CSS px. */
  private live = { x0: 0, y0: 0, w: 0, h: 0 };
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
  private settleTimer: ReturnType<typeof setTimeout> | null = null;
  private settling = 0;
  private liveLease: CanvasLease | null = null;
  /** The visible part of the surface (surface CSS px), refreshed on scroll/resize. */
  private vis = { top: 0, bottom: 0, onScreen: false };
  private markerMode: MarkerMode;
  /** Main-thread time spent starting settle encodes (the encode itself runs in a worker where possible). */
  readonly renderStats = { settleMainMs: 0 };
  private readonly liveStroke = new LiveStroke();

  constructor(options: InkEngineOptions) {
    this.o = options;
    this.doc = new InkDocument({ now: () => this.now() });
    this.host = options.host;
    this.theme = options.theme ?? "paper";
    this.markerMode = options.markerMode ?? "under";
    this.sizes = { ...DEFAULT_SIZES, ...options.sizes };
    this.palm = new PalmRejection({ penOnly: options.penOnly ?? true, penPresent: penPresent() });
    this.state = {
      enabled: options.enabled ?? false,
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
    this.liveCanvas = this.makeCanvas("live");
    this.liveCanvas.style.display = "none";
    this.layer.append(this.liveCanvas);
    this.host.append(this.layer);
    this.liveCtx = this.liveCanvas.getContext("2d", { desynchronized: true }) as CanvasRenderingContext2D | null;
    if (!this.liveCtx) this.fail("This browser could not create a drawing canvas.");
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

  setMarkerMode(mode: MarkerMode): void {
    if (this.markerMode === mode) return;
    this.markerMode = mode;
    this.applyThemeBlend();
    this.liveDirty = true;
    this.requestFrame();
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
    if (!sel || sel.ids.length === 0) return;
    const entries = sel.ids.map((id) => this.doc.entry(id)).filter((e) => e !== undefined);
    this.clearSelection();
    this.doc.commit({ before: entries, after: [] });
  }

  /** Copy the selection (slightly offset) and select the copies. */
  duplicateSelection(): void {
    const sel = this.selection;
    if (!sel || sel.ids.length === 0) return;
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
    return (
      this.dirtyTiles.size > 0 ||
      this.raf !== 0 ||
      this.prewarmTimer !== null ||
      this.settleTimer !== null ||
      this.settling > 0
    );
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
    if (this.settleTimer) clearTimeout(this.settleTimer);
    for (const c of this.cleanups) c();
    for (const slab of this.slabs.values()) this.releaseSlab(slab);
    this.slabs.clear();
    this.liveLease?.release();
    this.liveLease = null;
    this.liveCanvas.width = 0;
    this.liveCanvas.height = 0;
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
    for (const slab of this.slabs.values()) this.applySlabStyle(slab);
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

  // ── Geometry, slabs and tiles ─────────────────────────────────────────────
  //
  // Committed ink lives in "slabs": canvases covering a horizontal strip of the surface (the whole
  // surface when it is small). Slabs near the viewport exist and scroll natively with the content, so
  // scrolling never repaints ink; slabs that come into range are painted tile by tile (TILE×TILE CSS px,
  // time-sliced), and slabs far away are released to bound memory. Each slab has a marker canvas (under
  // the content) and an ink canvas (over it); an empty one is hidden because even an empty canvas costs
  // a composited layer on every scrolled frame.

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
      for (const slab of this.slabs.values()) this.releaseSlab(slab);
      this.slabs.clear();
      this.tileKinds.clear();
      this.dirtyTiles.clear();
      const rows = Math.floor(MAX_SLAB_PIXELS / Math.max(1, w * dpr * dpr) / TILE);
      this.slabH = Math.max(1, rows) * TILE;
      this.refreshSelectionRect();
      this.positionEditor();
    }
    if (changed) {
      this.live = { x0: 0, y0: 0, w: 0, h: 0 }; // re-fit at the new size / pixel ratio
      this.liveDirty = true;
      this.requestFrame();
    }
    this.updateSlabs();
  }

  private onScroll(): void {
    this.frameStale = true;
    if (this.gesture) return; // the frame is re-read with the next sample
    this.updateSlabs();
  }

  /** The part of the surface inside the viewport, in surface CSS px. */
  private visibleRange(): { top: number; bottom: number; vh: number; onScreen: boolean } {
    const r = this.host.getBoundingClientRect();
    const vh = window.innerHeight || this.heightPx;
    const top = Math.min(this.heightPx, Math.max(0, -r.top));
    const bottom = Math.max(top, Math.min(this.heightPx, vh - r.top));
    return { top, bottom, vh, onScreen: r.bottom > 0 && r.top < vh };
  }

  /**
   * Keep slab objects (a canvas element and a settled image per kind) for the slabs nearest the visible
   * part, as many as MAX_SLABS_PIXELS allows; release the rest, farthest first. Backing stores are
   * allocated only while a slab is being painted and released once it has settled into images, all under
   * the page-wide canvas budget.
   */
  private updateSlabs(): void {
    const w = this.widthPx;
    const h = this.heightPx;
    if (w <= 0 || h <= 0 || this.slabH <= 0) return;
    const { top, bottom, onScreen } = this.visibleRange();
    this.vis = { top, bottom, onScreen };
    const count = Math.ceil(h / this.slabH);
    const perSlab = Math.max(1, Math.round(w * this.dpr) * Math.round(this.slabH * this.dpr));
    const max = Math.max(1, Math.floor(MAX_SLABS_PIXELS / perSlab));
    const dist = (i: number) => {
      const y0 = i * this.slabH;
      const y1 = y0 + this.slabH;
      return y1 < top ? top - y1 : y0 > bottom ? y0 - bottom : 0;
    };
    const keep = new Set(
      Array.from({ length: count }, (_, i) => i)
        .sort((x, y) => dist(x) - dist(y))
        .slice(0, max),
    );
    for (const [i, slab] of this.slabs) {
      if (keep.has(i)) continue;
      this.releaseSlab(slab);
      this.slabs.delete(i);
    }
    for (const i of keep) if (!this.slabs.has(i)) this.createSlab(i);
  }

  /** A slab on screen (or being drawn on) is never evicted from the canvas budget. */
  private slabPinned(slab: Slab): boolean {
    if (this.destroyed) return false;
    if (!this.vis.onScreen) return false;
    const margin = 64;
    return slab.y0 < this.vis.bottom + margin && slab.y0 + slab.h > this.vis.top - margin;
  }

  private createSlab(i: number): void {
    const y0 = i * this.slabH;
    const h = Math.min(this.slabH, this.heightPx - y0);
    if (h <= 0) return;
    const layer = (kind: SlabKind): SlabLayer => ({
      kind,
      canvas: null,
      ctx: null,
      lease: null,
      img: null,
      settledVersion: -1,
      version: 0,
      touched: false,
      stale: false,
    });
    const slab: Slab = { i, y0, h, marker: layer("marker"), ink: layer("ink"), has: { m: false, k: false } };
    this.slabs.set(i, slab);
    this.markDirtyPx({ x0: 0, y0, x1: this.widthPx, y1: y0 + h });
  }

  /** Allocate (or touch) a layer's backing store; restores the settled pixels when it was released. */
  private backing(slab: Slab, layer: SlabLayer): CanvasRenderingContext2D | null {
    if (layer.lease && layer.ctx) {
      layer.lease.touch();
      return layer.ctx;
    }
    const devW = Math.max(1, Math.round(this.widthPx * this.dpr));
    const devH = Math.max(1, Math.round(slab.h * this.dpr));
    const lease = canvasBudget.acquire(
      `ink:${layer.kind}:${slab.i}`,
      devW * devH,
      () => this.slabPinned(slab),
      () => this.evictLayer(slab, layer),
    );
    let c = layer.canvas;
    if (!c) {
      c = this.makeCanvas(layer.kind);
      c.dataset.slab = String(slab.i);
      Object.assign(c.style, {
        width: `${this.widthPx}px`,
        height: `${slab.h}px`,
        top: `${slab.y0}px`,
        display: "none",
      });
      // DOM order is paint order within a z-index: marker below ink, both below the live canvas.
      const before = layer.kind === "marker" ? (slab.ink.canvas ?? slab.ink.img ?? this.liveCanvas) : this.liveCanvas;
      this.layer.insertBefore(c, before);
      layer.canvas = c;
      this.applySlabStyle(slab);
    }
    c.width = devW;
    c.height = devH;
    const ctx = c.getContext("2d");
    if (!ctx) {
      lease.release();
      this.fail("This browser could not create a drawing canvas (out of canvas memory).");
      return null;
    }
    layer.lease = lease;
    layer.ctx = ctx;
    const img = layer.img;
    if (layer.stale) {
      // Evicted before it settled: the pixels are gone, repaint the slab from the document.
      layer.stale = false;
      this.markDirtyPx({ x0: 0, y0: slab.y0, x1: this.widthPx, y1: slab.y0 + slab.h });
    } else if (img && layer.settledVersion >= 0) {
      if (img.complete && img.naturalWidth > 0) ctx.drawImage(img, 0, 0, devW, devH);
      else this.markDirtyPx({ x0: 0, y0: slab.y0, x1: this.widthPx, y1: slab.y0 + slab.h });
    }
    return ctx;
  }

  /** Give a layer's backing store back (its content lives on in the settled image, or is stale). */
  private dropBacking(layer: SlabLayer): void {
    if (layer.canvas) {
      layer.canvas.width = 0;
      layer.canvas.height = 0;
    }
    layer.ctx = null;
    layer.lease?.release();
    layer.lease = null;
  }

  /** The canvas budget took this layer's memory (it was off screen and least recently used). */
  private evictLayer(_slab: Slab, layer: SlabLayer): void {
    layer.lease = null; // already removed from the budget
    if (layer.canvas) {
      layer.canvas.width = 0;
      layer.canvas.height = 0;
      layer.canvas.style.display = "none";
    }
    layer.ctx = null;
    const settled = layer.img !== null && layer.settledVersion === layer.version;
    if (settled && layer.img) layer.img.style.display = "";
    else layer.stale = true;
  }

  private releaseSlab(slab: Slab): void {
    for (const layer of [slab.marker, slab.ink]) {
      this.dropBacking(layer);
      layer.canvas?.remove();
      if (layer.img) {
        if (layer.img.dataset.url) URL.revokeObjectURL(layer.img.dataset.url);
        layer.img.remove();
      }
    }
    for (const key of [...this.tileKinds.keys()]) {
      const ty = Number(key.split(",")[1]);
      if (ty * TILE >= slab.y0 && ty * TILE < slab.y0 + slab.h) this.tileKinds.delete(key);
    }
  }

  /**
   * Marker placement. "under": the marker layer sits under the surface's content (z-index -1 inside its
   * stacking context) and strokes multiply with each other, so on plain text the highlight shows through
   * the dark glyphs like a multiply would, at no compositing cost. "multiply": over the content with
   * `mix-blend-mode: multiply`, for surfaces whose content is opaque (images, PDF pages, tables with row
   * fills) that would hide an under-layer. On a dark surface the marker is translucent instead.
   */
  private applySlabStyle(slab: Slab): void {
    const over = this.markerMode === "multiply";
    for (const el of [slab.marker.canvas, slab.marker.img]) {
      if (!el) continue;
      el.style.zIndex = over ? "1" : "-1";
      el.style.mixBlendMode = over && this.theme === "paper" ? "multiply" : "normal";
      el.style.opacity = this.theme === "paper" ? "1" : "0.45";
    }
    for (const el of [slab.ink.canvas, slab.ink.img]) if (el) el.style.zIndex = "1";
  }

  /**
   * The live canvas covers only what is live (the stroke being drawn, a lasso, a selection, the hover
   * cursor) plus a margin, not the viewport: its size is what the compositor uploads on every inked frame,
   * which at device pixel ratio 2-3 decides pen-to-pixel latency. When the content outgrows it, it is
   * moved/resized (renderLive redraws everything each frame anyway).
   */
  private ensureLive(contentSu: Rect): boolean {
    const w = this.widthPx;
    const c = this.liveCanvas;
    if (rectIsEmpty(contentSu) || w <= 0) return false;
    const need = {
      x0: Math.max(0, contentSu.x0 * w - 8),
      y0: Math.max(0, contentSu.y0 * w - 8),
      x1: Math.min(w, contentSu.x1 * w + 8),
      y1: Math.min(this.heightPx, contentSu.y1 * w + 8),
    };
    const L = this.live;
    const fits =
      this.liveLease !== null && need.x0 >= L.x0 && need.y0 >= L.y0 && need.x1 <= L.x0 + L.w && need.y1 <= L.y0 + L.h;
    if (fits) return true;
    const m = LIVE_MARGIN_PX;
    const x0 = Math.floor(Math.max(0, need.x0 - m));
    const y0 = Math.floor(Math.max(0, need.y0 - m));
    const x1 = Math.ceil(Math.min(w, need.x1 + m));
    const y1 = Math.ceil(Math.min(this.heightPx, need.y1 + m));
    const devW = Math.max(1, Math.round((x1 - x0) * this.dpr));
    const devH = Math.max(1, Math.round((y1 - y0) * this.dpr));
    if (!this.liveLease) {
      this.liveLease = canvasBudget.acquire(
        "ink:live",
        devW * devH,
        () => !this.destroyed && (this.gesture !== null || this.selection !== null || this.hover !== null),
        () => {
          this.liveLease = null;
          c.width = 0;
          c.height = 0;
          c.style.display = "none";
          this.liveRect = EMPTY_RECT;
        },
      );
    } else this.liveLease.resize(devW * devH);
    c.width = devW; // resizing also clears it
    c.height = devH;
    Object.assign(c.style, { left: `${x0}px`, top: `${y0}px`, width: `${x1 - x0}px`, height: `${y1 - y0}px` });
    this.live = { x0, y0, w: x1 - x0, h: y1 - y0 };
    this.liveRect = EMPTY_RECT;
    return true;
  }

  /** Everything the live layer will draw this frame, in su. */
  private liveContent(): Rect {
    let box = EMPTY_RECT;
    const g = this.gesture;
    const pt = (p: Vec) => ({ x0: p.x, y0: p.y, x1: p.x, y1: p.y });
    if (g?.kind === "stroke") {
      const pred = this.state.predict ? g.predicted : [];
      box = unionRect(box, bboxOfItem(pred.length > 0 ? { ...g.item, points: [...g.item.points, ...pred] } : g.item));
    }
    if (g?.kind === "lasso") for (const p of g.path) box = unionRect(box, pt(p));
    if (this.selection) {
      const dx = g?.kind === "drag" ? g.dx : 0;
      const dy = g?.kind === "drag" ? g.dy : 0;
      const b = this.selection.bbox;
      const pad = 10 / Math.max(1, this.widthPx);
      box = unionRect(box, { x0: b.x0 + dx - pad, y0: b.y0 + dy - pad, x1: b.x1 + dx + pad, y1: b.y1 + dy + pad });
      for (const p of this.selection.path) box = unionRect(box, pt(p));
    }
    if (this.hover && !g) {
      const r = (Math.max(this.sizes.eraser, this.toolSizePx()) + 4) / Math.max(1, this.widthPx);
      box = unionRect(box, { x0: this.hover.x - r, y0: this.hover.y - r, x1: this.hover.x + r, y1: this.hover.y + r });
    }
    return box;
  }

  private invalidateAll(): void {
    this.markDirtyPx({ x0: 0, y0: 0, x1: this.widthPx, y1: this.heightPx });
    this.liveDirty = true;
    this.requestFrame();
  }

  /** Mark tiles dirty for a rectangle in su. */
  private markDirtySu(r: Rect): void {
    if (rectIsEmpty(r)) return;
    const w = this.widthPx;
    const p = padRect(r, 2 / Math.max(1, w));
    this.markDirtyPx({ x0: p.x0 * w, y0: p.y0 * w, x1: p.x1 * w, y1: p.y1 * w });
  }

  /** Mark tiles dirty for a rectangle in CSS px (surface-relative); only tiles of existing slabs matter. */
  private markDirtyPx(r: Rect): void {
    const x0 = Math.max(0, r.x0);
    const x1 = Math.min(this.widthPx, r.x1);
    const y0 = Math.max(0, r.y0);
    const y1 = Math.min(this.heightPx, r.y1);
    if (!(x1 > x0 && y1 > y0) || this.slabH <= 0) return;
    for (let ty = Math.floor(y0 / TILE); ty <= Math.floor((y1 - 0.001) / TILE); ty++) {
      if (!this.slabs.has(Math.floor((ty * TILE) / this.slabH))) continue;
      for (let tx = Math.floor(x0 / TILE); tx <= Math.floor((x1 - 0.001) / TILE); tx++)
        this.dirtyTiles.add(`${tx},${ty}`);
    }
    this.requestFrame();
  }

  private requestFrame(): void {
    if (this.raf || this.destroyed) return;
    this.raf = requestAnimationFrame((ts) => this.onFrame(ts));
  }

  /** After painting tiles: show the canvases that hold the newest pixels, hide stale images, re-settle. */
  private updateSlabVisibility(slab: Slab): void {
    let m = false;
    let k = false;
    const first = slab.y0 / TILE;
    const last = Math.ceil((slab.y0 + slab.h) / TILE);
    for (const [key, kinds] of this.tileKinds) {
      const ty = Number(key.split(",")[1]);
      if (ty < first || ty >= last) continue;
      m ||= kinds.m;
      k ||= kinds.k;
      if (m && k) break;
    }
    slab.has = { m, k };
    for (const [layer, on] of [
      [slab.marker, m],
      [slab.ink, k],
    ] as const) {
      if (!on) {
        // Nothing of this kind left in the slab: no memory, nothing shown.
        this.dropBacking(layer);
        if (layer.canvas) layer.canvas.style.display = "none";
        if (layer.img) layer.img.style.display = "none";
        layer.version++;
        continue;
      }
      if (!layer.touched) continue; // untouched layers keep showing what they showed
      layer.touched = false;
      layer.version++;
      if (layer.canvas) layer.canvas.style.display = layer.ctx ? "" : "none";
      if (layer.img) layer.img.style.display = "none";
    }
    this.scheduleSettle();
  }

  /**
   * Settling: once the ink has been still for a moment, each slab's canvases are swapped for plain
   * images of the same pixels and their backing stores are released. Images are painted into the
   * page's own cached raster tiles, whereas every visible canvas is a layer the compositor re-blends on
   * each scrolled frame. PNG encoding runs in a worker where the browser allows it.
   */
  private scheduleSettle(): void {
    if (this.settleTimer) clearTimeout(this.settleTimer);
    this.settleTimer = setTimeout(() => {
      this.settleTimer = null;
      if (this.destroyed) return;
      if (this.gesture || this.dirtyTiles.size > 0) {
        this.scheduleSettle();
        return;
      }
      for (const slab of this.slabs.values()) this.settleSlab(slab);
    }, SETTLE_MS);
  }

  private settleSlab(slab: Slab): void {
    for (const layer of [slab.marker, slab.ink]) {
      const v = layer.version;
      const on = layer.kind === "marker" ? slab.has.m : slab.has.k;
      const canvas = layer.canvas;
      if (!on || !canvas || !layer.ctx || layer.settledVersion === v) continue;
      this.settling++;
      const t0 = performance.now();
      const encoding = encodePng(canvas);
      this.renderStats.settleMainMs += performance.now() - t0;
      void encoding.then((blob) => {
        const done = () => {
          this.settling--;
        };
        if (!blob || this.destroyed || this.slabs.get(slab.i) !== slab || layer.version !== v) return done();
        const url = URL.createObjectURL(blob);
        let img = layer.img;
        if (!img) {
          img = this.host.ownerDocument.createElement("img");
          img.alt = "";
          img.setAttribute("aria-hidden", "true");
          img.draggable = false;
          img.dataset.inkImage = layer.kind;
          Object.assign(img.style, {
            position: "absolute",
            left: "0",
            top: `${slab.y0}px`,
            width: `${this.widthPx}px`,
            height: `${slab.h}px`,
            pointerEvents: "none",
            display: "none",
          });
          canvas.after(img);
          layer.img = img;
          this.applySlabStyle(slab);
        }
        const target = img;
        const previous = target.dataset.url;
        target.src = url;
        target
          .decode()
          .catch(() => undefined)
          .then(() => {
            if (previous) URL.revokeObjectURL(previous);
            target.dataset.url = url;
            if (this.slabs.get(slab.i) === slab && layer.version === v && !this.destroyed) {
              target.style.display = "";
              canvas.style.display = "none";
              layer.settledVersion = v;
              this.dropBacking(layer);
            }
            done();
          });
      });
    }
  }

  private onFrame(_ts: number): void {
    this.raf = 0;
    if (this.destroyed) return;
    const start = performance.now();
    // Eraser work queued by the input handler runs here, never inside the pointer event.
    const g = this.gesture;
    if (g?.kind === "erase" && g.pending.length > 0) {
      const path = [g.last, ...g.pending];
      g.last = path[path.length - 1] as Vec;
      g.pending = [];
      this.eraseAlong(path);
    }
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
    this.pendingLatencyTs = null;
    // Committed tiles: keep frames short while a stroke is live, finish the rest next frame.
    const budget = this.gesture ? 4 : 10;
    if (this.dirtyTiles.size > 0) {
      const touched = new Set<Slab>();
      // Visible tiles first, so a slab coming into view fills where the student is looking.
      const { top, bottom } = this.vis;
      const tiles = [...this.dirtyTiles].sort((a, b) => tileDistance(a, top, bottom) - tileDistance(b, top, bottom));
      for (const key of tiles) {
        this.dirtyTiles.delete(key);
        const slab = this.renderTile(key);
        if (slab) touched.add(slab);
        if (performance.now() - start > budget) break;
      }
      for (const slab of touched) this.updateSlabVisibility(slab);
      if (this.dirtyTiles.size > 0) this.requestFrame();
    }
  }

  /** Reference space → device px of a canvas whose top edge is at `top` CSS px of the surface. */
  private setDrawTransform(ctx: CanvasRenderingContext2D, top: number): void {
    const k = (this.widthPx * this.dpr) / REF;
    ctx.setTransform(k, 0, 0, k, 0, -top * this.dpr);
  }

  private renderTile(key: string): Slab | null {
    const [txs, tys] = key.split(",");
    const tx = Number(txs);
    const ty = Number(tys);
    const slab = this.slabs.get(Math.floor((ty * TILE) / this.slabH));
    if (!slab) return null;
    const dpr = this.dpr;
    const x0 = tx * TILE;
    const y0 = Math.max(ty * TILE, slab.y0);
    const x1 = Math.min(this.widthPx, (tx + 1) * TILE);
    const y1 = Math.min((ty + 1) * TILE, slab.y0 + slab.h);
    if (!(x1 > x0 && y1 > y0)) return slab;
    const dev = {
      x: Math.floor(x0 * dpr),
      y: Math.floor((y0 - slab.y0) * dpr),
      w: Math.ceil((x1 - x0) * dpr) + 1,
      h: Math.ceil((y1 - y0) * dpr) + 1,
    };
    const w = this.widthPx;
    const query = { x0: x0 / w, y0: y0 / w, x1: x1 / w, y1: y1 / w };
    const entries = this.doc.query(padRect(query, 1 / w)).filter((e) => !this.hidden.has(e.item.id));
    // A layer is painted when the tile has (or had) something of its kind, or it is already allocated.
    const before = this.tileKinds.get(key);
    const needs = (layer: SlabLayer, now: boolean, had: boolean) => now || had || layer.ctx !== null || layer.stale;
    const mctx = needs(
      slab.marker,
      entries.some((e) => e.item.tool === "marker"),
      before?.m ?? false,
    )
      ? this.backing(slab, slab.marker)
      : null;
    const ictx = needs(
      slab.ink,
      entries.some((e) => e.item.tool !== "marker"),
      before?.k ?? false,
    )
      ? this.backing(slab, slab.ink)
      : null;
    if (mctx) slab.marker.touched = true;
    if (ictx) slab.ink.touched = true;
    for (const ctx of [mctx, ictx]) {
      if (!ctx) continue;
      ctx.save();
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(dev.x, dev.y, dev.w, dev.h);
      ctx.beginPath();
      ctx.rect(dev.x, dev.y, dev.w, dev.h);
      ctx.clip();
      this.setDrawTransform(ctx, slab.y0);
    }
    const kinds = { m: false, k: false };
    for (const e of entries) {
      const marker = e.item.tool === "marker";
      const ctx = marker ? mctx : ictx;
      if (!ctx) continue;
      if (marker) kinds.m = true;
      else kinds.k = true;
      ctx.globalCompositeOperation = marker ? "multiply" : "source-over";
      drawItem(ctx, e.item, this.palette, this.font);
    }
    mctx?.restore();
    ictx?.restore();
    if (kinds.m || kinds.k) this.tileKinds.set(key, kinds);
    else this.tileKinds.delete(key);
    return slab;
  }

  /** Repaint the live layer: current stroke (+ prediction), lasso, selection, drag preview, hover cursor. */
  private renderLive(): void {
    const ctx = this.liveCtx;
    if (!ctx) return;
    const content = this.liveContent();
    if (!this.ensureLive(content)) {
      if (!rectIsEmpty(this.liveRect) && this.liveLease) {
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.clearRect(0, 0, this.liveCanvas.width, this.liveCanvas.height);
      }
      this.liveRect = EMPTY_RECT;
      this.liveCanvas.style.display = "none";
      return;
    }
    this.liveLease?.touch();
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    if (!rectIsEmpty(this.liveRect)) {
      const r = this.liveRect;
      ctx.clearRect(r.x0 - 2, r.y0 - 2, r.x1 - r.x0 + 4, r.y1 - r.y0 + 4);
    }
    let drawn = EMPTY_RECT;
    const k = this.widthPx * this.dpr;
    const liveLeft = this.live.x0 * this.dpr;
    const liveTop = this.live.y0 * this.dpr;
    const toDev = (r: Rect): Rect => ({
      x0: r.x0 * k - liveLeft,
      y0: r.y0 * k - liveTop,
      x1: r.x1 * k - liveLeft,
      y1: r.y1 * k - liveTop,
    });
    ctx.setTransform(k / REF, 0, 0, k / REF, -liveLeft, -liveTop);
    const g = this.gesture;
    // A live marker stroke is previewed where it will land (under the content, or multiplied over it).
    const liveMarker = g?.kind === "stroke" && g.item.tool === "marker";
    const over = this.markerMode === "multiply";
    this.liveCanvas.style.zIndex = liveMarker && !over ? "-1" : "1";
    this.liveCanvas.style.mixBlendMode = liveMarker && over && this.theme === "paper" ? "multiply" : "normal";
    // Translucent marker on dark surfaces: the whole live layer fades, so chunk overlaps don't darken.
    this.liveCanvas.style.opacity = liveMarker && this.theme !== "paper" ? "0.45" : "1";
    if (g?.kind === "stroke") {
      const predicted = this.state.predict ? g.predicted : [];
      if (g.item.shape) drawItem(ctx, g.item, this.palette, this.font, true);
      else this.liveStroke.draw(ctx, g.item, predicted, this.palette);
      const box = bboxOfItem(predicted.length > 0 ? { ...g.item, points: [...g.item.points, ...predicted] } : g.item);
      drawn = unionRect(drawn, toDev(box));
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
      if (this.selection.ids.length > 0) {
        ctx.strokeRect(box.x0 * REF, box.y0 * REF, (box.x1 - box.x0) * REF, (box.y1 - box.y0) * REF);
      } else {
        // A region with no ink: show the loop the student drew.
        ctx.beginPath();
        for (const [i, p] of this.selection.path.entries()) {
          if (i === 0) ctx.moveTo(p.x * REF, p.y * REF);
          else ctx.lineTo(p.x * REF, p.y * REF);
        }
        ctx.closePath();
        ctx.stroke();
      }
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
    // An empty canvas still costs a composited layer on every scrolled frame (badly so with software
    // compositing): only show the live layer while it has something on it.
    this.liveCanvas.style.display = rectIsEmpty(drawn) ? "none" : "";
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
      // The erase itself runs in the next animation frame, never inside the input handler.
      this.gesture = { kind: "erase", pointerId: e.pointerId, last: pt, lastT: t, pending: [pt] };
      this.requestFrame();
      return;
    }
    if (tool === "lasso") {
      if (
        this.selection &&
        this.selection.ids.length > 0 &&
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
    if (this.pendingLatencyTs === null) this.pendingLatencyTs = t;
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
    // Latency is measured from the first input not yet painted (the oldest one waiting for a frame).
    if (this.pendingLatencyTs === null) this.pendingLatencyTs = e.timeStamp;
    if (g.kind === "stroke") this.extendStroke(g, samples, e);
    else if (g.kind === "erase") {
      for (const s of samples) g.pending.push(this.toSu(s));
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
      if (g.pending.length > 0) this.eraseAlong([g.last, ...g.pending]);
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
    // Even with no ink inside, the circled region is a selection: "Ask Naadi" works on printed content.
    if (Math.min(box.x1 - box.x0, box.y1 - box.y0) * this.widthPx < 8) return;
    this.setSelection(ids, path);
    const sel = this.selection;
    if (sel && this.o.onLasso) {
      void this.renderPng(sel).then((png) => {
        if (this.selection === sel) this.o.onLasso?.(sel, png);
      });
    }
  }

  private setSelection(ids: string[], path?: Vec[]): void {
    let bbox = EMPTY_RECT;
    for (const id of ids) {
      const item = this.doc.get(id);
      if (item) bbox = unionRect(bbox, bboxOfItem(item));
    }
    if (ids.length === 0 && path && path.length >= 3) {
      for (const p of path) bbox = unionRect(bbox, { x0: p.x, y0: p.y, x1: p.x, y1: p.y });
    }
    const loop =
      path && path.length >= 3
        ? path
        : [
            { x: bbox.x0, y: bbox.y0 },
            { x: bbox.x1, y: bbox.y0 },
            { x: bbox.x1, y: bbox.y1 },
            { x: bbox.x0, y: bbox.y1 },
          ];
    this.selection = rectIsEmpty(bbox) ? null : { ids, bbox, rect: this.suRectToPx(bbox), path: loop };
    this.liveDirty = true;
    this.requestFrame();
    this.emitState();
  }

  private refreshSelectionAfterChange(): void {
    const sel = this.selection;
    if (!sel || sel.ids.length === 0) return; // a region-only selection is unaffected by ink changes
    const ids = sel.ids.filter((id) => this.doc.get(id));
    if (ids.length === 0) this.clearSelection();
    else this.setSelection(ids, sel.path);
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
    const items = sel.ids.map((id) => this.doc.get(id)).filter((i) => i !== undefined);
    return { ids: sel.ids, items, bbox: sel.bbox, rect: sel.rect, path: sel.path, png };
  }

  /** A cropped picture of the selected ink on the surface colour, at 2× (max 2048 px a side). */
  private renderPng(sel: SelectionInfo): Promise<Blob | null> {
    if (sel.ids.length === 0) return Promise.resolve(null);
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

type SlabKind = "marker" | "ink";

interface SlabLayer {
  kind: SlabKind;
  /** The canvas element (kept); its backing store exists only while `lease` is held. */
  canvas: HTMLCanvasElement | null;
  ctx: CanvasRenderingContext2D | null;
  lease: CanvasLease | null;
  /** The settled image of this layer. */
  img: HTMLImageElement | null;
  settledVersion: number;
  /** Bumped whenever tiles were painted into the layer. */
  version: number;
  /** Painted since the last visibility update. */
  touched: boolean;
  /** Evicted before it settled: its pixels must be repainted from the document. */
  stale: boolean;
}

interface Slab {
  i: number;
  /** Top edge and height in surface CSS px. */
  y0: number;
  h: number;
  marker: SlabLayer;
  ink: SlabLayer;
  has: { m: boolean; k: boolean };
}

/** How far a tile ("tx,ty") is from the visible range, in CSS px (0 = visible). */
function tileDistance(key: string, top: number, bottom: number): number {
  const ty = Number(key.split(",")[1]);
  const y0 = ty * TILE;
  const y1 = y0 + TILE;
  return y1 < top ? top - y1 : y0 > bottom ? y0 - bottom : 0;
}
