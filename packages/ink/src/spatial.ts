import { type Rect, rectIsEmpty, rectsIntersect } from "./geometry";

/**
 * Uniform-grid spatial index over item bounding boxes (su). Lookups for a viewport band or an eraser
 * capsule touch only the cells they overlap, so cost follows what is on screen, not the page total.
 */
export class GridIndex {
  private readonly cells = new Map<string, Set<string>>();
  private readonly boxes = new Map<string, Rect>();
  private readonly cell: number;

  constructor(cellSize = 0.08) {
    this.cell = cellSize;
  }

  get size(): number {
    return this.boxes.size;
  }

  private keysFor(r: Rect): string[] {
    const c = this.cell;
    // Clamp to keep a pathological item from creating millions of cells.
    const cx0 = Math.floor(r.x0 / c);
    const cy0 = Math.floor(r.y0 / c);
    const cx1 = Math.min(Math.floor(r.x1 / c), cx0 + 256);
    const cy1 = Math.min(Math.floor(r.y1 / c), cy0 + 4096);
    const keys: string[] = [];
    for (let x = cx0; x <= cx1; x++) for (let y = cy0; y <= cy1; y++) keys.push(`${x},${y}`);
    return keys;
  }

  insert(id: string, box: Rect): void {
    if (this.boxes.has(id)) this.remove(id);
    if (rectIsEmpty(box)) return;
    this.boxes.set(id, box);
    for (const k of this.keysFor(box)) {
      let set = this.cells.get(k);
      if (!set) {
        set = new Set();
        this.cells.set(k, set);
      }
      set.add(id);
    }
  }

  remove(id: string): void {
    const box = this.boxes.get(id);
    if (!box) return;
    this.boxes.delete(id);
    for (const k of this.keysFor(box)) {
      const set = this.cells.get(k);
      if (!set) continue;
      set.delete(id);
      if (set.size === 0) this.cells.delete(k);
    }
  }

  clear(): void {
    this.cells.clear();
    this.boxes.clear();
  }

  box(id: string): Rect | undefined {
    return this.boxes.get(id);
  }

  /** Ids whose boxes intersect `r` (unordered). */
  query(r: Rect): Set<string> {
    const out = new Set<string>();
    if (rectIsEmpty(r)) return out;
    for (const k of this.keysFor(r)) {
      const set = this.cells.get(k);
      if (!set) continue;
      for (const id of set) {
        if (out.has(id)) continue;
        const b = this.boxes.get(id);
        if (b && rectsIntersect(b, r)) out.add(id);
      }
    }
    return out;
  }
}
