/**
 * Notebook page bookkeeping (pure): order, page numbers, which page to show after a delete, the paper a page
 * uses, and the optimistic page list (server pages with the device's queued changes applied on top).
 */
import { isPaper, type Paper } from "./paper";

export interface PageLike {
  id: string;
  sort: number;
  paper?: string | null;
  canvas?: string;
}

/** Pages in display order: `sort`, then id (the server's tiebreak). */
export function orderPages<T extends PageLike>(pages: readonly T[]): T[] {
  return [...pages].sort((a, b) => a.sort - b.sort || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
}

/** Move the item at `from` to `to` (both clamped); returns a new array. */
export function moveItem<T>(items: readonly T[], from: number, to: number): T[] {
  const out = [...items];
  if (out.length === 0) return out;
  const f = Math.max(0, Math.min(out.length - 1, from));
  const t = Math.max(0, Math.min(out.length - 1, to));
  if (f === t) return out;
  const [item] = out.splice(f, 1);
  out.splice(t, 0, item as T);
  return out;
}

/** The new order of ids after moving `id` by `delta` places (−1 earlier, +1 later). Unknown id: unchanged. */
export function movePageBy(ids: readonly string[], id: string, delta: number): string[] {
  const i = ids.indexOf(id);
  return i < 0 ? [...ids] : moveItem(ids, i, i + delta);
}

/** Re-sort `pages` to follow `ids` (pages missing from `ids` keep their relative order at the end). */
export function applyOrder<T extends PageLike>(pages: readonly T[], ids: readonly string[]): T[] {
  const rank = new Map(ids.map((id, i) => [id, i]));
  const ordered = orderPages(pages);
  const known = ordered.filter((p) => rank.has(p.id)).sort((a, b) => (rank.get(a.id) ?? 0) - (rank.get(b.id) ?? 0));
  const rest = ordered.filter((p) => !rank.has(p.id));
  return [...known, ...rest].map((p, i) => ({ ...p, sort: i }));
}

/** Insert a page at 0-based `position` (default: the end), renumbering `sort` 0..n-1. */
export function insertPage<T extends PageLike>(pages: readonly T[], page: T, position?: number | null): T[] {
  const ordered = orderPages(pages).filter((p) => p.id !== page.id);
  const at = position == null ? ordered.length : Math.max(0, Math.min(ordered.length, position));
  ordered.splice(at, 0, page);
  return ordered.map((p, i) => ({ ...p, sort: i }));
}

export function removePage<T extends PageLike>(pages: readonly T[], id: string): T[] {
  return orderPages(pages)
    .filter((p) => p.id !== id)
    .map((p, i) => ({ ...p, sort: i }));
}

/**
 * The page to open after deleting `id`: the one that takes its place (the next), else the previous, else none.
 * Returns its 1-based page number.
 */
export function pageAfterDelete(pages: readonly PageLike[], id: string): number | null {
  const ordered = orderPages(pages);
  const i = ordered.findIndex((p) => p.id === id);
  if (i < 0) return ordered.length ? 1 : null;
  if (ordered.length <= 1) return null;
  return i < ordered.length - 1 ? i + 1 : i;
}

/** The route's `$page` (1-based) clamped to the notebook; `null` for an empty notebook. */
export function clampPageNumber(raw: unknown, count: number): number | null {
  if (count <= 0) return null;
  const n = typeof raw === "number" ? raw : Number.parseInt(String(raw ?? ""), 10);
  if (!Number.isFinite(n) || n < 1) return 1;
  return Math.min(Math.floor(n), count);
}

/** The paper a page is drawn on: its own override, else the notebook's, else ruled. */
export function pagePaper(page: Pick<PageLike, "paper"> | null | undefined, notebookPaper: unknown): Paper {
  if (isPaper(page?.paper)) return page.paper;
  return isPaper(notebookPaper) ? notebookPaper : "ruled";
}
