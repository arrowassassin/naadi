import { type InkPoint, type InkShape, roundSu } from "@naadi/shared";
import { bboxOfPoints, distSqPointSeg } from "./geometry";

export interface RecognizedShape {
  shape: InkShape;
  points: InkPoint[];
}

type P = readonly [number, number, ...number[]];

function pathLength(pts: readonly P[]): number {
  let len = 0;
  for (let i = 1; i < pts.length; i++) {
    const a = pts[i - 1] as P;
    const b = pts[i] as P;
    len += Math.hypot(b[0] - a[0], b[1] - a[1]);
  }
  return len;
}

/** Ramer–Douglas–Peucker; returns the indices of the kept vertices. */
export function rdp(pts: readonly P[], eps: number): number[] {
  if (pts.length < 3) return pts.map((_, i) => i);
  const keep = new Uint8Array(pts.length);
  keep[0] = 1;
  keep[pts.length - 1] = 1;
  const stack: [number, number][] = [[0, pts.length - 1]];
  const epsSq = eps * eps;
  while (stack.length > 0) {
    const [s, e] = stack.pop() as [number, number];
    const a = pts[s] as P;
    const b = pts[e] as P;
    let maxD = -1;
    let idx = -1;
    for (let i = s + 1; i < e; i++) {
      const p = pts[i] as P;
      const d = distSqPointSeg(p[0], p[1], a[0], a[1], b[0], b[1]);
      if (d > maxD) {
        maxD = d;
        idx = i;
      }
    }
    if (idx >= 0 && maxD > epsSq) {
      keep[idx] = 1;
      stack.push([s, idx], [idx, e]);
    }
  }
  const out: number[] = [];
  keep.forEach((k, i) => {
    if (k) out.push(i);
  });
  return out;
}

function pt(x: number, y: number, p: number, t: number): InkPoint {
  return [roundSu(x), roundSu(y), p, t];
}

function avgPressure(pts: readonly InkPoint[]): number {
  let s = 0;
  for (const p of pts) s += p[2];
  return Math.round((s / pts.length) * 1000) / 1000;
}

function angleAt(a: P, b: P, c: P): number {
  const v1x = a[0] - b[0];
  const v1y = a[1] - b[1];
  const v2x = c[0] - b[0];
  const v2y = c[1] - b[1];
  const d = Math.hypot(v1x, v1y) * Math.hypot(v2x, v2y);
  if (d === 0) return 0;
  return Math.acos(Math.max(-1, Math.min(1, (v1x * v2x + v1y * v2y) / d)));
}

function ellipsePoints(cx: number, cy: number, rx: number, ry: number, p: number, t: number): InkPoint[] {
  const n = 64;
  const out: InkPoint[] = [];
  for (let i = 0; i <= n; i++) {
    const a = (i / n) * Math.PI * 2;
    out.push(pt(cx + rx * Math.cos(a), cy + ry * Math.sin(a), p, t));
  }
  return out;
}

/**
 * Recognise a hand-drawn line, arrow, rectangle, circle or ellipse and return clean geometry, or null
 * when the stroke is not confidently one of them (it then stays as drawn).
 */
export function recognizeShape(points: readonly InkPoint[]): RecognizedShape | null {
  if (points.length < 3) return null;
  const first = points[0] as InkPoint;
  const last = points[points.length - 1] as InkPoint;
  const t = last[3];
  const p = avgPressure(points);
  const box = bboxOfPoints(points);
  const diag = Math.hypot(box.x1 - box.x0, box.y1 - box.y0);
  if (diag < 0.01) return null; // too small to mean a shape (about 8 px on an iPad page)
  const len = pathLength(points);
  const ends = Math.hypot(last[0] - first[0], last[1] - first[1]);

  // Line: the path is barely longer than the chord and no point strays from it.
  if (ends > 0 && len / ends < 1.08) {
    let maxDev = 0;
    for (const q of points) maxDev = Math.max(maxDev, distSqPointSeg(q[0], q[1], first[0], first[1], last[0], last[1]));
    if (Math.sqrt(maxDev) / ends < 0.06) {
      return { shape: "line", points: [pt(first[0], first[1], p, 0), pt(last[0], last[1], p, t)] };
    }
  }

  const idx = rdp(points, diag * 0.06);
  const verts = idx.map((i) => points[i] as InkPoint);
  const closed = ends < diag * 0.2;

  // Arrow: a long straight shaft, then a short head drawn back around its tip.
  if (!closed && verts.length >= 3 && verts.length <= 6) {
    const a = verts[0] as InkPoint;
    const b = verts[1] as InkPoint;
    const shaft = Math.hypot(b[0] - a[0], b[1] - a[1]);
    const rest = verts.slice(2);
    const nearTip = rest.every((v) => Math.hypot(v[0] - b[0], v[1] - b[1]) <= shaft * 0.4);
    const headLen = Math.max(...rest.map((v) => Math.hypot(v[0] - b[0], v[1] - b[1])));
    const shaftStraight = (() => {
      const i1 = idx[1] as number;
      let maxDev = 0;
      for (let i = 0; i <= i1; i++) {
        const q = points[i] as InkPoint;
        maxDev = Math.max(maxDev, Math.sqrt(distSqPointSeg(q[0], q[1], a[0], a[1], b[0], b[1])));
      }
      return maxDev <= shaft * 0.08;
    })();
    if (shaft >= len * 0.5 && nearTip && shaftStraight && headLen >= shaft * 0.08) {
      const ang = Math.atan2(b[1] - a[1], b[0] - a[0]);
      const h = Math.min(headLen, shaft * 0.35);
      const spread = (27 * Math.PI) / 180;
      const c1 = pt(b[0] - h * Math.cos(ang - spread), b[1] - h * Math.sin(ang - spread), p, t);
      const c2 = pt(b[0] - h * Math.cos(ang + spread), b[1] - h * Math.sin(ang + spread), p, t);
      const tip = pt(b[0], b[1], p, t);
      return { shape: "arrow", points: [pt(a[0], a[1], p, 0), tip, c1, tip, c2] };
    }
  }

  if (!closed) return null;

  // Rectangle: four corners, each close to a right angle.
  let corners = verts;
  const v0 = corners[0] as InkPoint;
  const vn = corners[corners.length - 1] as InkPoint;
  if (corners.length >= 2 && Math.hypot(vn[0] - v0[0], vn[1] - v0[1]) < diag * 0.2) corners = corners.slice(0, -1);
  if (corners.length === 4) {
    const right = corners.every((c, i) => {
      const prev = corners[(i + 3) % 4] as InkPoint;
      const next = corners[(i + 1) % 4] as InkPoint;
      return Math.abs(angleAt(prev, c, next) - Math.PI / 2) < (22 * Math.PI) / 180;
    });
    if (right) {
      const edgesAxisAligned = corners.every((c, i) => {
        const n = corners[(i + 1) % 4] as InkPoint;
        const a = Math.abs(Math.atan2(n[1] - c[1], n[0] - c[0])) % (Math.PI / 2);
        return Math.min(a, Math.PI / 2 - a) < (15 * Math.PI) / 180;
      });
      if (edgesAxisAligned) {
        return {
          shape: "rect",
          points: [
            pt(box.x0, box.y0, p, 0),
            pt(box.x1, box.y0, p, t),
            pt(box.x1, box.y1, p, t),
            pt(box.x0, box.y1, p, t),
            pt(box.x0, box.y0, p, t),
          ],
        };
      }
      const loop = [...corners, corners[0] as InkPoint].map((c, i) => pt(c[0], c[1], p, i === 0 ? 0 : t));
      return { shape: "rect", points: loop };
    }
  }

  // Circle / ellipse: distance from the centre is (nearly) constant / matches the bbox ellipse.
  const cx = (box.x0 + box.x1) / 2;
  const cy = (box.y0 + box.y1) / 2;
  const rx = (box.x1 - box.x0) / 2;
  const ry = (box.y1 - box.y0) / 2;
  if (rx <= 0 || ry <= 0) return null;
  let sum = 0;
  let sumSq = 0;
  let ellErr = 0;
  for (const q of points) {
    const d = Math.hypot(q[0] - cx, q[1] - cy);
    sum += d;
    sumSq += d * d;
    ellErr += Math.abs(Math.sqrt(((q[0] - cx) / rx) ** 2 + ((q[1] - cy) / ry) ** 2) - 1);
  }
  const n = points.length;
  const mean = sum / n;
  const cv = Math.sqrt(Math.max(0, sumSq / n - mean * mean)) / mean;
  ellErr /= n;
  if (verts.length < 5) return null; // a triangle or a scribble, not a round shape
  if (cv < 0.09 && Math.max(rx, ry) / Math.min(rx, ry) < 1.25) {
    return { shape: "circle", points: ellipsePoints(cx, cy, mean, mean, p, t) };
  }
  if (ellErr < 0.09) return { shape: "ellipse", points: ellipsePoints(cx, cy, rx, ry, p, t) };
  return null;
}
