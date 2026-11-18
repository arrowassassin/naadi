/**
 * Ink items → vector drawing operations in page points (pure). Strokes use the ink engine's own perfect-freehand
 * outline (`strokeOutline` from @naadi/ink, the same geometry the canvas fills), turned into an SVG path with the
 * same quadratic-midpoint smoothing as the engine's `outlineToPath`; auto-straightened shapes are stroked
 * polylines; text boxes are wrapped text. Markers come first so pen ink sits on top of them, as on screen.
 */
import { REF, strokeOutline } from "@naadi/ink";
import type { InkColor, InkItem, InkStroke, InkTextBox } from "@naadi/shared";
import { type FontKey, type Measurer, wrapPlain } from "./layout";

export type InkOp =
  | { op: "fill"; d: string; color: InkColor; marker: boolean }
  | { op: "line"; d: string; color: InkColor; width: number; marker: boolean }
  | { op: "text"; x: number; y: number; text: string; size: number; color: InkColor; font: FontKey };

const fmt = (v: number) => {
  const r = Math.round(v * 100) / 100;
  return Object.is(r, -0) ? "0" : String(r);
};

/**
 * The engine's closed quadratic-midpoint path through an outline (reference px), scaled to points. Each
 * quadratic segment is written as the exact equivalent cubic (C): pdf-lib turns an SVG `Q` into PDF's `v`
 * operator, which is a different curve.
 */
export function outlinePathD(outline: readonly (readonly number[])[], scale: number): string {
  const n = outline.length;
  if (n === 0) return "";
  const p = (i: number) => outline[i % n] as readonly number[];
  const x = (i: number) => (p(i)[0] ?? 0) * scale;
  const y = (i: number) => (p(i)[1] ?? 0) * scale;
  if (n < 3) {
    // A dot: a tiny circle, like the engine's arc.
    const r = 0.5 * scale;
    return `M ${fmt(x(0) - r)} ${fmt(y(0))} a ${fmt(r)} ${fmt(r)} 0 1 0 ${fmt(2 * r)} 0 a ${fmt(r)} ${fmt(r)} 0 1 0 ${fmt(-2 * r)} 0 Z`;
  }
  let cx = x(0);
  let cy = y(0);
  const parts = [`M ${fmt(cx)} ${fmt(cy)}`];
  for (let i = 0; i < n; i++) {
    const qx = x(i);
    const qy = y(i);
    const ex = (x(i) + x(i + 1)) / 2;
    const ey = (y(i) + y(i + 1)) / 2;
    const c1x = cx + (2 / 3) * (qx - cx);
    const c1y = cy + (2 / 3) * (qy - cy);
    const c2x = ex + (2 / 3) * (qx - ex);
    const c2y = ey + (2 / 3) * (qy - ey);
    parts.push(`C ${fmt(c1x)} ${fmt(c1y)} ${fmt(c2x)} ${fmt(c2y)} ${fmt(ex)} ${fmt(ey)}`);
    cx = ex;
    cy = ey;
  }
  parts.push("Z");
  return parts.join(" ");
}

function polylineD(stroke: InkStroke, pageWidth: number): string {
  return stroke.points
    .map((pt, i) => `${i === 0 ? "M" : "L"} ${fmt(pt[0] * pageWidth)} ${fmt(pt[1] * pageWidth)}`)
    .join(" ");
}

function strokeOps(s: InkStroke, pageWidth: number): InkOp | null {
  const marker = s.tool === "marker";
  if (s.shape)
    return { op: "line", d: polylineD(s, pageWidth), color: s.color, width: s.size * pageWidth * 0.8, marker };
  const d = outlinePathD(strokeOutline(s, true), pageWidth / REF);
  return d ? { op: "fill", d, color: s.color, marker } : null;
}

function textOps(t: InkTextBox, pageWidth: number, m: Measurer): InkOp[] {
  const size = t.size * pageWidth;
  const lineH = size * 1.3;
  return wrapPlain(t.text, "sans", size, t.w * pageWidth, m)
    .map((text, i) => ({
      op: "text" as const,
      x: t.x * pageWidth,
      // The engine draws each line from its em-box top (textBaseline "top") every 1.3 em; the baseline of an Inter
      // line sits about 0.8 em below that top.
      y: t.y * pageWidth + i * lineH + size * 0.8,
      text,
      size,
      color: t.color,
      font: "sans" as FontKey,
    }))
    .filter((o) => o.text.length > 0);
}

/** Every live item of a layer as drawing operations: markers, then pen strokes and shapes, then text boxes. */
export function inkToOps(items: readonly InkItem[], pageWidth: number, m: Measurer): InkOp[] {
  const live = items.filter((i) => !i.deleted);
  const markers: InkOp[] = [];
  const pens: InkOp[] = [];
  const texts: InkOp[] = [];
  for (const item of live) {
    if (item.tool === "text") texts.push(...textOps(item, pageWidth, m));
    else {
      const op = strokeOps(item, pageWidth);
      if (op) (item.tool === "marker" ? markers : pens).push(op);
    }
  }
  return [...markers, ...pens, ...texts];
}
