/**
 * One canvas-memory budget for every ink surface on the page. Each canvas backing store (slab ink and
 * marker canvases, live canvases) registers its device-pixel count; when a new allocation would go over
 * the limit, the least recently used canvases that are not pinned (on screen, or being drawn on) are
 * evicted: their backing store is released and the owner repaints them from the document if they are
 * needed again. iPad Safari kills a tab that goes over its total canvas memory, so this is a hard cap,
 * not a hint; only pinned canvases may exceed it (reported in `stats().overPinned`).
 */
export interface CanvasLease {
  /** Mark as used now (LRU order). */
  touch(): void;
  /** The canvas was resized. */
  resize(pixels: number): void;
  /** The backing store was released by its owner. */
  release(): void;
  readonly pixels: number;
}

interface Entry {
  id: number;
  label: string;
  pixels: number;
  lastUsed: number;
  pinned: () => boolean;
  evict: () => void;
}

/** ~96 MB of 4-byte pixels: well inside iPad Safari's total canvas memory. */
export const DEFAULT_CANVAS_BUDGET = 24_000_000;

export class CanvasBudget {
  limit: number;
  private readonly entries = new Map<number, Entry>();
  private nextId = 1;
  private clock = 0;
  private total = 0;
  evictions = 0;

  constructor(limit = DEFAULT_CANVAS_BUDGET) {
    this.limit = limit;
  }

  /**
   * Make room for `pixels` and register a canvas that will hold them. `evict` must release the backing
   * store (width = height = 0) and remember that the content has to be repainted.
   */
  acquire(label: string, pixels: number, pinned: () => boolean, evict: () => void): CanvasLease {
    this.makeRoom(pixels);
    const id = this.nextId++;
    const entry: Entry = { id, label, pixels, lastUsed: ++this.clock, pinned, evict };
    this.entries.set(id, entry);
    this.total += pixels;
    const self = this;
    return {
      touch() {
        if (self.entries.has(id)) entry.lastUsed = ++self.clock;
      },
      resize(px: number) {
        if (!self.entries.has(id)) return;
        self.total += px - entry.pixels;
        entry.pixels = px;
        entry.lastUsed = ++self.clock;
        if (self.total > self.limit) self.makeRoom(0, id);
      },
      release() {
        if (!self.entries.delete(id)) return;
        self.total -= entry.pixels;
      },
      get pixels() {
        return entry.pixels;
      },
    };
  }

  /** Evict least recently used, unpinned canvases until `pixels` more fit. */
  private makeRoom(pixels: number, keep?: number): void {
    if (this.total + pixels <= this.limit) return;
    const candidates = [...this.entries.values()]
      .filter((e) => e.id !== keep && !e.pinned())
      .sort((a, b) => a.lastUsed - b.lastUsed);
    for (const e of candidates) {
      if (this.total + pixels <= this.limit) break;
      this.entries.delete(e.id);
      this.total -= e.pixels;
      this.evictions++;
      e.evict();
    }
  }

  stats(): { total: number; limit: number; canvases: number; evictions: number; overPinned: boolean } {
    return {
      total: this.total,
      limit: this.limit,
      canvases: this.entries.size,
      evictions: this.evictions,
      overPinned: this.total > this.limit,
    };
  }
}

/** The page-wide budget every InkEngine uses. */
export const canvasBudget = new CanvasBudget();
