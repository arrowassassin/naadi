import { type InkItem, type InkPoint, type InkStroke, type InkTextBox, roundSu } from "@naadi/shared";

/** Axis-aligned rectangle (su or px, depending on the caller). */
export interface Rect {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

export interface Vec {
  x: number;
  y: number;
}

export const EMPTY_RECT: Rect = { x0: Number.POSITIVE_INFINITY, y0: Number.POSITIVE_INFINITY, x1: -1, y1: -1 };

export function rectIsEmpty(r: Rect): boolean {
  return !(r.x1 >= r.x0 && r.y1 >= r.y0);
}

export function unionRect(a: Rect, b: Rect): Rect {
  if (rectIsEmpty(a)) return { ...b };
  if (rectIsEmpty(b)) return { ...a };
  return { x0: Math.min(a.x0, b.x0), y0: Math.min(a.y0, b.y0), x1: Math.max(a.x1, b.x1), y1: Math.max(a.y1, b.y1) };
}

export function rectsIntersect(a: Rect, b: Rect): boolean {
  return a.x0 <= b.x1 && b.x0 <= a.x1 && a.y0 <= b.y1 && b.y0 <= a.y1;
}

export function padRect(r: Rect, p: number): Rect {
  return { x0: r.x0 - p, y0: r.y0 - p, x1: r.x1 + p, y1: r.y1 + p };
}

export function rectContainsPoint(r: Rect, x: number, y: number): boolean {
  return x >= r.x0 && x <= r.x1 && y >= r.y0 && y <= r.y1;
}

export function bboxOfPoints(points: readonly (readonly [number, number, ...number[]])[]): Rect {
  let x0 = Number.POSITIVE_INFINITY;
  let y0 = Number.POSITIVE_INFINITY;
  let x1 = Number.NEGATIVE_INFINITY;
  let y1 = Number.NEGATIVE_INFINITY;
  for (const p of points) {
    if (p[0] < x0) x0 = p[0];
    if (p[0] > x1) x1 = p[0];
    if (p[1] < y0) y0 = p[1];
    if (p[1] > y1) y1 = p[1];
  }
  return { x0, y0, x1, y1 };
}

/**
 * Height of a text box in su. Text wraps at `w`; an average Inter glyph is ~0.52 em wide and the
 * line-height is 1.3 em. Used for hit-testing and the spatial index; the renderer measures exactly.
 */
export function textBoxHeight(t: Pick<InkTextBox, "text" | "size" | "w">): number {
  const perLine = Math.max(1, Math.floor(t.w / (t.size * 0.52)));
  let lines = 0;
  for (const para of t.text.split("\n")) lines += Math.max(1, Math.ceil(para.length / perLine));
  return lines * t.size * 1.3;
}

export function bboxOfItem(item: InkItem): Rect {
  if (item.tool === "text") return { x0: item.x, y0: item.y, x1: item.x + item.w, y1: item.y + textBoxHeight(item) };
  // A stroke can be at most `size` wide (perfect-freehand's width at full pressure).
  return padRect(bboxOfPoints(item.points), item.size / 2);
}

export function distSqPointSeg(px: number, py: number, ax: number, ay: number, bx: number, by: number): number {
  const dx = bx - ax;
  const dy = by - ay;
  const len = dx * dx + dy * dy;
  let t = len > 0 ? ((px - ax) * dx + (py - ay) * dy) / len : 0;
  t = Math.max(0, Math.min(1, t));
  const cx = ax + t * dx - px;
  const cy = ay + t * dy - py;
  return cx * cx + cy * cy;
}

function orient(ax: number, ay: number, bx: number, by: number, cx: number, cy: number): number {
  return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax);
}

export function segmentsIntersect(a: Vec, b: Vec, c: Vec, d: Vec): boolean {
  const o1 = orient(a.x, a.y, b.x, b.y, c.x, c.y);
  const o2 = orient(a.x, a.y, b.x, b.y, d.x, d.y);
  const o3 = orient(c.x, c.y, d.x, d.y, a.x, a.y);
  const o4 = orient(c.x, c.y, d.x, d.y, b.x, b.y);
  return o1 * o2 < 0 && o3 * o4 < 0;
}

/** Squared distance between segments ab and cd. */
export function distSqSegSeg(a: Vec, b: Vec, c: Vec, d: Vec): number {
  if (segmentsIntersect(a, b, c, d)) return 0;
  return Math.min(
    distSqPointSeg(a.x, a.y, c.x, c.y, d.x, d.y),
    distSqPointSeg(b.x, b.y, c.x, c.y, d.x, d.y),
    distSqPointSeg(c.x, c.y, a.x, a.y, b.x, b.y),
    distSqPointSeg(d.x, d.y, a.x, a.y, b.x, b.y),
  );
}

/** Even-odd point-in-polygon. */
export function pointInPolygon(x: number, y: number, poly: readonly Vec[]): boolean {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const pi = poly[i] as Vec;
    const pj = poly[j] as Vec;
    if (pi.y > y !== pj.y > y && x < ((pj.x - pi.x) * (y - pi.y)) / (pj.y - pi.y) + pi.x) inside = !inside;
  }
  return inside;
}

/** Does the eraser capsule (segment a→b, radius r) touch this item? */
export function itemHitByCapsule(item: InkItem, a: Vec, b: Vec, r: number): boolean {
  const box = padRect(bboxOfItem(item), r);
  const seg: Rect = { x0: Math.min(a.x, b.x), y0: Math.min(a.y, b.y), x1: Math.max(a.x, b.x), y1: Math.max(a.y, b.y) };
  if (!rectsIntersect(box, seg)) return false;
  if (item.tool === "text") return true;
  const reach = r + item.size / 2;
  const reachSq = reach * reach;
  const pts = item.points;
  if (pts.length === 1) {
    const p = pts[0] as InkPoint;
    return distSqPointSeg(p[0], p[1], a.x, a.y, b.x, b.y) <= reachSq;
  }
  for (let i = 1; i < pts.length; i++) {
    const p = pts[i - 1] as InkPoint;
    const q = pts[i] as InkPoint;
    if (distSqSegSeg({ x: p[0], y: p[1] }, { x: q[0], y: q[1] }, a, b) <= reachSq) return true;
  }
  return false;
}

/** Insert interpolated points so that consecutive points are at most `maxStep` apart. */
export function densify(points: readonly InkPoint[], maxStep: number): InkPoint[] {
  if (points.length < 2 || maxStep <= 0) return points.map((p) => [...p] as InkPoint);
  const out: InkPoint[] = [[...(points[0] as InkPoint)] as InkPoint];
  for (let i = 1; i < points.length; i++) {
    const p = points[i - 1] as InkPoint;
    const q = points[i] as InkPoint;
    const dist = Math.hypot(q[0] - p[0], q[1] - p[1]);
    const steps = Math.min(512, Math.ceil(dist / maxStep));
    for (let s = 1; s < steps; s++) {
      const t = s / steps;
      out.push([
        p[0] + (q[0] - p[0]) * t,
        p[1] + (q[1] - p[1]) * t,
        p[2] + (q[2] - p[2]) * t,
        Math.round(p[3] + (q[3] - p[3]) * t),
      ]);
    }
    out.push([...q] as InkPoint);
  }
  return out;
}

/**
 * Pixel (partial) erase: remove the parts of a stroke within `r` of the eraser path and return the
 * surviving runs of points (each run becomes a new stroke). Returns null when nothing was erased.
 */
export function eraseRuns(stroke: InkStroke, path: readonly Vec[], r: number): InkPoint[][] | null {
  if (path.length === 0) return null;
  const reach = r + stroke.size / 4;
  const reachSq = reach * reach;
  const pts = densify(stroke.points, Math.max(r / 2, 1e-4));
  const keep = pts.map((p) => {
    if (path.length === 1) {
      const a = path[0] as Vec;
      return (p[0] - a.x) ** 2 + (p[1] - a.y) ** 2 > reachSq;
    }
    for (let i = 1; i < path.length; i++) {
      const a = path[i - 1] as Vec;
      const b = path[i] as Vec;
      if (distSqPointSeg(p[0], p[1], a.x, a.y, b.x, b.y) <= reachSq) return false;
    }
    return true;
  });
  if (keep.every(Boolean)) return null;
  const runs: InkPoint[][] = [];
  let run: InkPoint[] = [];
  for (let i = 0; i < pts.length; i++) {
    if (keep[i]) run.push(pts[i] as InkPoint);
    else if (run.length > 0) {
      runs.push(run);
      run = [];
    }
  }
  if (run.length > 0) runs.push(run);
  // A run of one point left over from a wide eraser is a dot the student did not draw: drop it unless the
  // original stroke was itself a dot.
  return runs.filter((rn) => rn.length >= 2).map((rn) => simplifyRun(rn));
}

/** Drop the densified points again where they are collinear, so erased fragments stay compact. */
function simplifyRun(run: InkPoint[]): InkPoint[] {
  if (run.length <= 2) return run;
  const out: InkPoint[] = [run[0] as InkPoint];
  for (let i = 1; i < run.length - 1; i++) {
    const a = out[out.length - 1] as InkPoint;
    const b = run[i] as InkPoint;
    const c = run[i + 1] as InkPoint;
    const area = Math.abs(orient(a[0], a[1], b[0], b[1], c[0], c[1]));
    const pressureChange = Math.abs(b[2] - a[2]) + Math.abs(c[2] - b[2]);
    if (area > 1e-9 || pressureChange > 0.02) out.push(b);
  }
  out.push(run[run.length - 1] as InkPoint);
  return out;
}

/**
 * Lasso selection: a stroke is selected when at least half of its points lie inside the loop (so a
 * loose loop around a word still catches strokes that poke out); a text box when its centre does.
 */
export function selectedByLasso(item: InkItem, poly: readonly Vec[], polyBox: Rect): boolean {
  if (poly.length < 3) return false;
  const box = bboxOfItem(item);
  if (!rectsIntersect(box, polyBox)) return false;
  if (item.tool === "text") return pointInPolygon((box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2, poly);
  const pts = item.points;
  const step = Math.max(1, Math.floor(pts.length / 64));
  let inside = 0;
  let total = 0;
  for (let i = 0; i < pts.length; i += step) {
    const p = pts[i] as InkPoint;
    total++;
    if (pointInPolygon(p[0], p[1], poly)) inside++;
  }
  return inside * 2 >= total;
}

export function translateItem<T extends InkItem>(item: T, dx: number, dy: number, now: number): T {
  if (item.tool === "text") return { ...item, x: roundSu(item.x + dx), y: roundSu(item.y + dy), updatedAt: now };
  return {
    ...item,
    points: item.points.map((p) => [roundSu(p[0] + dx), roundSu(p[1] + dy), p[2], p[3]] as InkPoint),
    updatedAt: now,
  };
}
