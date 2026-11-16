import { describe, expect, it } from "vitest";
import { CanvasBudget } from "./budget";

/** A fake surface: canvases with a pinned flag, evicted ones recorded. */
function surface(budget: CanvasBudget, name: string) {
  const evicted: string[] = [];
  const leases = new Map<string, ReturnType<CanvasBudget["acquire"]>>();
  let pinned = new Set<string>();
  return {
    evicted,
    alloc(id: string, px: number) {
      leases.set(
        id,
        budget.acquire(
          `${name}:${id}`,
          px,
          () => pinned.has(id),
          () => evicted.push(id),
        ),
      );
    },
    touch(id: string) {
      leases.get(id)?.touch();
    },
    release(id: string) {
      leases.get(id)?.release();
    },
    pin(ids: string[]) {
      pinned = new Set(ids);
    },
  };
}

describe("canvas budget", () => {
  it("evicts least recently used, unpinned canvases across all surfaces", () => {
    const b = new CanvasBudget(100);
    const a = surface(b, "a");
    const c = surface(b, "c");
    a.alloc("1", 40);
    c.alloc("1", 40);
    a.touch("1"); // c:1 is now the least recently used
    a.alloc("2", 40);
    expect(c.evicted).toEqual(["1"]);
    expect(a.evicted).toEqual([]);
    expect(b.stats()).toMatchObject({ total: 80, canvases: 2, evictions: 1, overPinned: false });
  });

  it("never evicts pinned (on-screen) canvases; reports going over when only pinned ones remain", () => {
    const b = new CanvasBudget(100);
    const a = surface(b, "a");
    a.pin(["1", "2", "3"]);
    a.alloc("1", 50);
    a.alloc("2", 50);
    a.alloc("3", 50);
    expect(a.evicted).toEqual([]);
    expect(b.stats()).toMatchObject({ total: 150, overPinned: true });
    a.pin([]);
    a.alloc("4", 10);
    expect(a.evicted.length).toBeGreaterThanOrEqual(1);
    expect(b.stats().total).toBeLessThanOrEqual(100);
  });

  it("N surfaces (12 pages) stay within the budget", () => {
    const b = new CanvasBudget(24_000_000);
    const pages = Array.from({ length: 12 }, (_, i) => surface(b, `p${i}`));
    // Each page: an ink canvas 1640×2360 (iPad dpr 2) and a live canvas; only the last page is on screen.
    pages.forEach((p, i) => {
      p.pin(i === 11 ? ["ink", "live"] : []);
      p.alloc("ink", 1640 * 2360);
      p.alloc("live", 1640 * 2360);
    });
    expect(b.stats().total).toBeLessThanOrEqual(24_000_000);
    expect(pages[11]?.evicted).toEqual([]);
    expect(b.stats().evictions).toBeGreaterThan(0);
  });

  it("release and resize keep the total exact", () => {
    const b = new CanvasBudget(1000);
    const lease = b.acquire(
      "x",
      300,
      () => false,
      () => undefined,
    );
    lease.resize(500);
    expect(b.stats().total).toBe(500);
    lease.release();
    lease.release();
    expect(b.stats().total).toBe(0);
  });
});
