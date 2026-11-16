import { blockResolver, type InkAnchorResolver } from "@naadi/ink";
import { type RefObject, useEffect, useState } from "react";

/** Block tops (su) of every `[data-block-pm]` element inside the ink surface, measured against its ink host. */
export function measureBlocks(surface: HTMLElement): { pm: number; y: number }[] {
  const host = surface.querySelector<HTMLElement>("[data-ink-host]") ?? surface;
  const width = host.clientWidth;
  if (width <= 0) return [];
  const top = host.getBoundingClientRect().top;
  const out: { pm: number; y: number }[] = [];
  for (const el of surface.querySelectorAll<HTMLElement>("[data-block-pm]")) {
    const pm = Number(el.dataset.blockPm);
    if (!Number.isInteger(pm) || pm < 0) continue;
    out.push({ pm, y: (el.getBoundingClientRect().top - top) / width });
  }
  return out.sort((a, b) => a.y - b.y);
}

function same(a: { pm: number; y: number }[], b: { pm: number; y: number }[]): boolean {
  return a.length === b.length && a.every((x, i) => x.pm === b[i]?.pm && Math.abs(x.y - (b[i]?.y ?? 0)) < 1e-6);
}

/**
 * The anchor resolver for a text surface (segment 12b): ContentRenderer marks each top-level block with
 * `data-block-pm` (its ProseMirror position); this hook measures where those blocks are now and re-measures when
 * the surface or its content changes size (rotation, Split View, a figure loading), so the engine can re-place
 * anchored ink beside the same paragraph. Null until the blocks are measured (ink then keeps plain coordinates).
 */
export function useBlockAnchors(surface: RefObject<HTMLElement | null>, key: unknown): InkAnchorResolver | null {
  const [resolver, setResolver] = useState<InkAnchorResolver | null>(null);
  // biome-ignore lint/correctness/useExhaustiveDependencies: `key` (the content) is the reason to re-measure
  useEffect(() => {
    const el = surface.current;
    if (!el) return;
    let last: { pm: number; y: number }[] = [];
    let raf = 0;
    const run = () => {
      raf = 0;
      const blocks = measureBlocks(el);
      if (same(blocks, last)) return;
      last = blocks;
      setResolver(blockResolver(blocks));
    };
    const soon = () => {
      if (!raf) raf = requestAnimationFrame(run);
    };
    run();
    const ro = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(soon);
    ro?.observe(el);
    for (const child of el.querySelectorAll<HTMLElement>("[data-block-pm]")) ro?.observe(child);
    window.addEventListener("resize", soon, { passive: true });
    return () => {
      if (raf) cancelAnimationFrame(raf);
      ro?.disconnect();
      window.removeEventListener("resize", soon);
    };
  }, [surface, key]);
  return resolver;
}
