import type { InkColor, InkItem, InkPoint, InkStroke, InkTextBox } from "@naadi/shared";
import { getStroke, type StrokeOptions } from "perfect-freehand";

/**
 * Stroke geometry is built once per item in a fixed reference space (REF px per su) and drawn at any
 * scale through the canvas transform, so zoom/resize never re-runs perfect-freehand.
 */
export const REF = 1000;

export type InkTheme = "paper" | "dark";

export type Palette = Record<"ink" | "violet" | "lime" | "amber", string> & { selection: string; eraser: string };

export function inkPaint(palette: Palette, color: InkColor): string {
  return palette[color as keyof Palette] ?? color;
}

/** Which design token each colour role maps to, per surface theme. */
export const PALETTE_TOKENS: Record<InkTheme, Record<keyof Palette, string>> = {
  paper: {
    ink: "--color-ground",
    violet: "--color-ink-violet",
    lime: "--color-lime",
    amber: "--color-mark-amber",
    selection: "--color-ink-violet",
    eraser: "--color-paper-text-2",
  },
  dark: {
    ink: "--color-paper",
    violet: "--color-violet-text",
    lime: "--color-lime",
    amber: "--color-amber",
    selection: "--color-violet-text",
    eraser: "--color-text-3",
  },
};

/** Resolve the palette from the live CSS custom properties (tokens.css); nothing is hardcoded here. */
export function resolvePalette(el: Element, theme: InkTheme, override: Partial<Palette> = {}): Palette {
  const cs = getComputedStyle(el);
  const tokens = PALETTE_TOKENS[theme];
  const out = {} as Palette;
  for (const key of Object.keys(tokens) as (keyof Palette)[]) {
    const v = override[key] ?? cs.getPropertyValue(tokens[key]).trim();
    if (!v) throw new Error(`ink: design token ${tokens[key]} is not defined; import @naadi/ui/tokens.css`);
    out[key] = v;
  }
  return out;
}

/** True when every point carries the neutral 0.5 pressure that mouse/touch input is stored with. */
export function hasNoRealPressure(points: readonly InkPoint[]): boolean {
  for (const p of points) if (p[2] !== 0.5) return false;
  return true;
}

export function freehandOptions(stroke: Pick<InkStroke, "tool" | "size" | "points">, complete: boolean): StrokeOptions {
  const simulate = hasNoRealPressure(stroke.points);
  if (stroke.tool === "marker") {
    return {
      size: stroke.size * REF,
      thinning: 0,
      smoothing: 0.6,
      streamline: 0.3,
      simulatePressure: false,
      start: { cap: true, taper: 0 },
      end: { cap: true, taper: 0 },
      last: complete,
    };
  }
  return {
    size: stroke.size * REF,
    thinning: simulate ? 0.5 : 0.62,
    smoothing: 0.55,
    // Low streamline: less smoothing lag between the pen tip and the ink.
    streamline: 0.25,
    simulatePressure: simulate,
    easing: (t) => t,
    start: { cap: true, taper: 0 },
    end: { cap: true, taper: 0 },
    last: complete,
  };
}

/** perfect-freehand outline polygon in reference space. */
export function strokeOutline(stroke: Pick<InkStroke, "tool" | "size" | "points">, complete = true): number[][] {
  const pts = stroke.points.map((p) => [p[0] * REF, p[1] * REF, p[2]]);
  return getStroke(pts, freehandOptions(stroke, complete));
}

/** Smooth closed path through the outline (quadratic midpoints), in reference space. */
export function outlineToPath(outline: number[][]): Path2D {
  const path = new Path2D();
  const n = outline.length;
  if (n === 0) return path;
  const first = outline[0] as number[];
  if (n < 3) {
    path.arc(first[0] as number, first[1] as number, 0.5, 0, Math.PI * 2);
    return path;
  }
  path.moveTo(first[0] as number, first[1] as number);
  for (let i = 0; i < n; i++) {
    const a = outline[i] as number[];
    const b = outline[(i + 1) % n] as number[];
    path.quadraticCurveTo(
      a[0] as number,
      a[1] as number,
      ((a[0] as number) + (b[0] as number)) / 2,
      ((a[1] as number) + (b[1] as number)) / 2,
    );
  }
  path.closePath();
  return path;
}

/** Clean geometry (auto-straightened shapes) is stroked with a uniform width, not perfect-freehand. */
export function polylinePath(points: readonly InkPoint[]): Path2D {
  const path = new Path2D();
  points.forEach((p, i) => {
    if (i === 0) path.moveTo(p[0] * REF, p[1] * REF);
    else path.lineTo(p[0] * REF, p[1] * REF);
  });
  return path;
}

interface CachedGeometry {
  fill?: Path2D;
  line?: Path2D;
}

const geometryCache = new WeakMap<InkItem, CachedGeometry>();

function geometry(item: InkStroke): CachedGeometry {
  let g = geometryCache.get(item);
  if (!g) {
    g = item.shape ? { line: polylinePath(item.points) } : { fill: outlineToPath(strokeOutline(item, true)) };
    geometryCache.set(item, g);
  }
  return g;
}

/** The context's transform must map reference space to device px (scale k / REF, translate band). */
export function drawStroke(ctx: CanvasRenderingContext2D, item: InkStroke, palette: Palette, live = false): void {
  ctx.fillStyle = inkPaint(palette, item.color);
  ctx.strokeStyle = inkPaint(palette, item.color);
  if (item.shape) {
    const g = live ? { line: polylinePath(item.points) } : geometry(item);
    ctx.lineWidth = item.size * REF * 0.8;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.stroke(g.line as Path2D);
    return;
  }
  const path = live ? outlineToPath(strokeOutline(item, false)) : (geometry(item).fill as Path2D);
  ctx.fill(path);
}

const LIVE_CHUNK = 24;
const LIVE_OVERLAP = 4;

/**
 * The stroke being drawn, rendered with bounded work per frame: points older than the last chunk are
 * frozen into cached Path2D chunks (overlapping by a few points so the joins are invisible), and only the
 * tail plus the predicted points go through perfect-freehand each frame. The committed stroke is built
 * once from all points when the pen lifts.
 */
export class LiveStroke {
  private id = "";
  private chunks: Path2D[] = [];
  private frozen = 0;

  reset(): void {
    this.id = "";
    this.chunks = [];
    this.frozen = 0;
  }

  /** Chunks computed so far (for tests and diagnostics). */
  get frozenPoints(): number {
    return this.frozen;
  }

  /** Freeze whatever is old enough and return every frozen chunk so far (in order). */
  frozenChunks(item: InkStroke): readonly Path2D[] {
    if (item.id !== this.id) {
      this.reset();
      this.id = item.id;
    }
    const pts = item.points;
    while (pts.length - this.frozen > LIVE_CHUNK + LIVE_CHUNK / 2) {
      const start = Math.max(0, this.frozen - LIVE_OVERLAP);
      const end = this.frozen + LIVE_CHUNK;
      this.chunks.push(outlineToPath(strokeOutline({ ...item, points: pts.slice(start, end + 1) }, false)));
      this.frozen = end;
    }
    return this.chunks;
  }

  /** The points not yet frozen (with a small overlap), plus the predicted ones. */
  tail(item: InkStroke, predicted: readonly InkPoint[]): InkPoint[] {
    return [...item.points.slice(Math.max(0, this.frozen - LIVE_OVERLAP)), ...predicted];
  }

  /** Everything at once (one canvas): chunks then the tail. */
  draw(ctx: CanvasRenderingContext2D, item: InkStroke, predicted: readonly InkPoint[], palette: Palette): void {
    ctx.fillStyle = inkPaint(palette, item.color);
    for (const c of this.frozenChunks(item)) ctx.fill(c);
    ctx.fill(outlineToPath(strokeOutline({ ...item, points: this.tail(item, predicted) }, false)));
  }
}

/** Wrap text to the box width; returns lines. */
export function wrapText(ctx: CanvasRenderingContext2D, text: string, maxWidth: number): string[] {
  const out: string[] = [];
  for (const para of text.split("\n")) {
    const words = para.split(/(\s+)/);
    let line = "";
    for (const w of words) {
      const next = line + w;
      if (line && ctx.measureText(next).width > maxWidth) {
        out.push(line.trimEnd());
        line = w.trimStart();
      } else line = next;
    }
    out.push(line);
  }
  return out;
}

export function drawTextBox(ctx: CanvasRenderingContext2D, item: InkTextBox, palette: Palette, font: string): void {
  const px = item.size * REF;
  ctx.fillStyle = inkPaint(palette, item.color);
  ctx.font = `500 ${px}px ${font}`;
  ctx.textBaseline = "top";
  const lines = wrapText(ctx, item.text, item.w * REF);
  lines.forEach((ln, i) => {
    ctx.fillText(ln, item.x * REF, item.y * REF + i * px * 1.3);
  });
}

/** Build (and cache) an item's geometry ahead of its first paint. */
export function prepareItem(item: InkItem): void {
  if (item.tool !== "text") geometry(item);
}

export function drawItem(
  ctx: CanvasRenderingContext2D,
  item: InkItem,
  palette: Palette,
  font: string,
  live = false,
): void {
  if (item.tool === "text") drawTextBox(ctx, item, palette, font);
  else drawStroke(ctx, item, palette, live);
}
