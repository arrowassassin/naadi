import { type InkAnchor, type InkItem, roundSu } from "@naadi/shared";
import { bboxOfItem, translateItem } from "./geometry";

/**
 * Reflow anchoring (segment 12b). On a text surface (a chapter section) the content reflows with the width: a
 * paragraph that starts 0.30 su down on iPad landscape may start 0.55 su down on a phone. An item drawn beside a
 * paragraph stores `anchor {pm, dy}`: the ProseMirror position of the top-level block under its top edge and its
 * top edge's offset from that block's top. Rendering puts the item's top at `offset(pm) + dy` for the current
 * layout; x is already width-relative (su). Items without an anchor (or whose pm no longer resolves) keep their
 * plain surface coordinates. Display translation never changes `anchor` or `updatedAt`.
 */
export interface InkAnchorResolver {
  /** The anchor for an item whose top edge is at `y` (su): the top-level block under it; null for none. */
  at(y: number): InkAnchor | null;
  /** Top of the block at `pm` in the current layout (su); null when `pm` does not resolve. */
  offset(pm: number): number | null;
}

const EPS = 1e-5;

/** How far the item must move so its top sits where its anchor says (0 when unanchored or unresolvable). */
export function anchorShift(item: InkItem, r: InkAnchorResolver | null): number {
  if (!r || !item.anchor) return 0;
  const top = r.offset(item.anchor.pm);
  if (top === null) return 0;
  const shift = top + (item.anchor.dy ?? 0) - bboxOfItem(item).y0;
  return Math.abs(shift) < EPS ? 0 : shift;
}

/** The item placed for the current layout (same object when nothing moves). */
export function resolveAnchor(item: InkItem, r: InkAnchorResolver | null): InkItem {
  const shift = anchorShift(item, r);
  return shift === 0 ? item : translateItem(item, 0, shift, item.updatedAt); // display only: same updatedAt
}

/** The item with the anchor of the block under its top edge now (same object when unchanged). */
export function stampAnchor(item: InkItem, r: InkAnchorResolver | null): InkItem {
  if (!r || item.deleted) return item;
  const y0 = bboxOfItem(item).y0;
  const a = r.at(y0);
  if (!a) {
    if (!item.anchor) return item;
    const { anchor: _drop, ...rest } = item;
    return rest as InkItem;
  }
  const next: InkAnchor = { pm: a.pm, dy: roundSu(a.dy ?? 0) };
  if (a.dx !== undefined) next.dx = roundSu(a.dx);
  const cur = item.anchor;
  if (cur && cur.pm === next.pm && cur.dy === next.dy && cur.dx === next.dx) return item;
  return { ...item, anchor: next };
}

/**
 * A resolver over a list of block tops (su, ascending), as ContentRenderer exposes them: block i starts at
 * `tops[i].y` and is ProseMirror position `tops[i].pm`. `at(y)` picks the last block starting at or above y
 * (the first block for y above everything).
 */
export function blockResolver(blocks: readonly { pm: number; y: number }[]): InkAnchorResolver | null {
  if (blocks.length === 0) return null;
  const byPm = new Map(blocks.map((b) => [b.pm, b.y]));
  return {
    at(y) {
      let pick = blocks[0] as { pm: number; y: number };
      for (const b of blocks) {
        if (b.y <= y + EPS) pick = b;
        else break;
      }
      return { pm: pick.pm, dy: y - pick.y };
    },
    offset(pm) {
      return byPm.get(pm) ?? null;
    },
  };
}
