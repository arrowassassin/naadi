import { type AnchorBlock, blockResolver, type InkAnchorResolver } from "@naadi/ink";
import { type RefObject, useEffect, useState } from "react";

/** Block tops (su) of every `[data-block-pm]` element inside the ink surface, measured against its ink host, with
 * the block's fingerprint (`data-block-fp`). */
export function measureBlocks(surface: HTMLElement): AnchorBlock[] {
  const host = surface.querySelector<HTMLElement>("[data-ink-host]") ?? surface;
  const width = host.clientWidth;
  if (width <= 0) return [];
  const top = host.getBoundingClientRect().top;
  const out: AnchorBlock[] = [];
  for (const el of surface.querySelectorAll<HTMLElement>("[data-block-pm]")) {
    const pm = Number(el.dataset.blockPm);
    const fp = el.dataset.blockFp;
    if (!Number.isInteger(pm) || pm < 0 || !fp) continue;
    out.push({ pm, fp, y: (el.getBoundingClientRect().top - top) / width });
  }
  return out.sort((a, b) => a.y - b.y);
}

function same(a: AnchorBlock[], b: AnchorBlock[]): boolean {
  return (
    a.length === b.length &&
    a.every((x, i) => x.pm === b[i]?.pm && x.fp === b[i]?.fp && Math.abs(x.y - (b[i]?.y ?? 0)) < 1e-6)
  );
}

/**
 * A resolver that measures the blocks when asked (cached until the next animation frame), so an anchor is always
 * stamped against the layout the student is looking at, never against a measurement taken before fonts, figures
 * or the save-state row settled (a stale measurement would make the stroke jump once re-measured).
 */
export function liveBlockResolver(surface: HTMLElement): InkAnchorResolver {
  let cached: InkAnchorResolver | null = null;
  let frame = 0;
  const current = (): InkAnchorResolver | null => {
    if (!cached) {
      cached = blockResolver(measureBlocks(surface));
      if (!frame && typeof requestAnimationFrame === "function") {
        frame = requestAnimationFrame(() => {
          frame = 0;
          cached = null;
        });
      }
    }
    return cached;
  };
  return {
    at: (y) => current()?.at(y) ?? null,
    offset: (pm, fp) => current()?.offset(pm, fp) ?? null,
  };
}

/**
 * The anchor resolver for a text surface (segment 12b): ContentRenderer marks each top-level block with
 * `data-block-pm` (its ProseMirror position). The resolver measures them live (liveBlockResolver); this hook hands
 * the engine a new one whenever the blocks move (rotation, Split View, a figure or a web font loading), so the
 * engine re-places anchored ink beside the same paragraph. Null until there are blocks (ink keeps plain
 * coordinates).
 */
export function useBlockAnchors(surface: RefObject<HTMLElement | null>, key: unknown): InkAnchorResolver | null {
  const [resolver, setResolver] = useState<InkAnchorResolver | null>(null);
  // biome-ignore lint/correctness/useExhaustiveDependencies: `key` (the content) is the reason to re-measure
  useEffect(() => {
    const el = surface.current;
    if (!el) return;
    let last: AnchorBlock[] = [];
    let raf = 0;
    const run = () => {
      raf = 0;
      const blocks = measureBlocks(el);
      if (same(blocks, last)) return;
      last = blocks;
      setResolver(blocks.length ? liveBlockResolver(el) : null);
    };
    const soon = () => {
      if (!raf) raf = requestAnimationFrame(run);
    };
    run();
    const ro = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(soon);
    ro?.observe(el);
    for (const child of el.querySelectorAll<HTMLElement>("[data-block-pm]")) ro?.observe(child);
    window.addEventListener("resize", soon, { passive: true });
    // A web font arriving re-wraps the text without resizing the surface's own box.
    const fonts = typeof document !== "undefined" ? document.fonts : undefined;
    fonts?.addEventListener?.("loadingdone", soon);
    void fonts?.ready.then(soon);
    return () => {
      if (raf) cancelAnimationFrame(raf);
      ro?.disconnect();
      window.removeEventListener("resize", soon);
      fonts?.removeEventListener?.("loadingdone", soon);
    };
  }, [surface, key]);
  return resolver;
}
