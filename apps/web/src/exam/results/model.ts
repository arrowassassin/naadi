/**
 * Pure helpers of the Results and Review screens (segment 09): review lists, trend and weight geometry, and
 * the "What Naadi sees" sentences. Nothing here reads the DOM or the network, so it is unit tested alone.
 */
import type { Insight, ResultQuestion, TrendPoint, WeightOverlay } from "@naadi/shared";

// ── Review lists ──

export const REVIEW_FILTERS = ["all", "missed", "flagged", "changed"] as const;
export type ReviewFilter = (typeof REVIEW_FILTERS)[number];

export function isReviewFilter(v: unknown): v is ReviewFilter {
  return typeof v === "string" && (REVIEW_FILTERS as readonly string[]).includes(v);
}

/** Missed = answered wrong or left unanswered: everything a review of misses should show. */
export function isMissed(q: Pick<ResultQuestion, "outcome">): boolean {
  return q.outcome !== "correct";
}

function inFilter(q: ResultQuestion, f: ReviewFilter): boolean {
  switch (f) {
    case "all":
      return true;
    case "missed":
      return isMissed(q);
    case "flagged":
      return q.flagged;
    case "changed":
      return q.change !== null;
  }
}

/** The question numbers (1-based, block order) a filter shows. */
export function filterList(questions: readonly ResultQuestion[], f: ReviewFilter): number[] {
  return questions
    .filter((q) => inFilter(q, f))
    .map((q) => q.n)
    .sort((a, b) => a - b);
}

export function filterCounts(questions: readonly ResultQuestion[]): Record<ReviewFilter, number> {
  return {
    all: questions.length,
    missed: questions.filter(isMissed).length,
    flagged: questions.filter((q) => q.flagged).length,
    changed: questions.filter((q) => q.change !== null).length,
  };
}

export interface Neighbours {
  prev: number | null;
  next: number | null;
  /** 1-based position of `n` in the list; null when `n` is not in it (prev/next are then the nearest). */
  index: number | null;
}

/** Previous and next question of a review list around `n`, which need not be in the list itself. */
export function neighbours(list: readonly number[], n: number): Neighbours {
  let prev: number | null = null;
  let next: number | null = null;
  let index: number | null = null;
  list.forEach((m, i) => {
    if (m < n) prev = m;
    else if (m === n) index = i + 1;
    else if (next === null) next = m;
  });
  return { prev, next, index };
}

/** Where a filter chip goes: stay on `n` when the new list has it, else the first question of the list. */
export function landingFor(list: readonly number[], n: number): number | null {
  if (list.includes(n)) return n;
  return list[0] ?? null;
}

export type ReviewKey = "next" | "prev";

export interface ReviewKeyLike {
  key: string;
  metaKey: boolean;
  ctrlKey: boolean;
  altKey: boolean;
  shiftKey: boolean;
}

/** Review keys: N or ArrowRight next, ArrowLeft previous; any modifier leaves the key to the browser. */
export function reviewKey(e: ReviewKeyLike): ReviewKey | null {
  if (e.metaKey || e.ctrlKey || e.altKey || e.shiftKey) return null;
  if (e.key === "ArrowRight" || e.key.toLowerCase() === "n") return "next";
  if (e.key === "ArrowLeft") return "prev";
  return null;
}

/** Arrow keys inside media, sliders or anything scrollable sideways belong to that element, not to us. */
export function ownsArrowKeys(t: EventTarget | null): boolean {
  if (!(t instanceof Element)) return false;
  if (t.closest("video, audio, iframe, [role='slider'], [role='tablist'], [role='listbox'], [role='menu']"))
    return true;
  for (let el: Element | null = t; el && el !== document.body; el = el.parentElement) {
    if (el instanceof HTMLElement && el.scrollWidth > el.clientWidth) {
      const overflow = getComputedStyle(el).overflowX;
      if (overflow === "auto" || overflow === "scroll") return true;
    }
  }
  return false;
}

// ── Header ──

/** The previous timed/exam score before this block (trend is oldest first), or null when there is none. */
export function previousScore(trend: readonly TrendPoint[]): number | null {
  const at = trend.findIndex((p) => p.current);
  const before = at === -1 ? trend.slice(0, -1) : trend.slice(0, at);
  const last = before[before.length - 1];
  return last ? last.score : null;
}

// ── Trend line ──

export interface TrendGeometry {
  path: string;
  dots: { x: number; y: number; score: number; current: boolean }[];
}

/**
 * Points of the trend line in a `width` x `height` box with `pad` around it. Scores are percent (0-100, the
 * axis is fixed so blocks compare at a glance); x is evenly spaced; a single point sits in the middle.
 */
export function trendGeometry(
  points: readonly TrendPoint[],
  width: number,
  height: number,
  pad: number,
): TrendGeometry {
  const innerW = Math.max(0, width - 2 * pad);
  const innerH = Math.max(0, height - 2 * pad);
  const step = points.length > 1 ? innerW / (points.length - 1) : 0;
  const dots = points.map((p, i) => {
    const score = Math.min(100, Math.max(0, p.score));
    const x = points.length > 1 ? pad + i * step : width / 2;
    const y = pad + innerH * (1 - score / 100);
    return { x: round(x), y: round(y), score: p.score, current: p.current };
  });
  const path = dots.map((d, i) => `${i === 0 ? "M" : "L"}${d.x} ${d.y}`).join(" ");
  return { path, dots };
}

function round(v: number): number {
  return Math.round(v * 100) / 100;
}

// ── Official weights ──

/** The axis end (percent of the exam) for the weight rows: the largest band or share, up to the next 5. */
export function weightScale(rows: readonly WeightOverlay[]): number {
  const top = Math.max(0, ...rows.map((w) => Math.max(w.weight_max ?? w.weight_min ?? 0, w.share)));
  return Math.max(5, Math.ceil(top / 5) * 5);
}

export interface WeightGeometry {
  /** The official band as [x, width] on a 0-100 axis; null when the exam has no official weight here. */
  band: { x: number; w: number } | null;
  /** This block's share on the same axis. */
  share: number;
  /** Where the share falls against the band. */
  place: "below" | "within" | "above" | null;
}

export function weightGeometry(w: WeightOverlay, scale: number): WeightGeometry {
  const at = (v: number) => round(Math.min(100, Math.max(0, (100 * v) / scale)));
  const lo = w.weight_min;
  const hi = w.weight_max ?? w.weight_min;
  if (lo === null || hi === null) return { band: null, share: at(w.share), place: null };
  const [a, b] = lo <= hi ? [lo, hi] : [hi, lo];
  const x = at(a);
  // A point weight (min = max) still shows as a visible sliver.
  const width = Math.max(at(b) - x, 0.75);
  const place = w.share < a ? "below" : w.share > b ? "above" : "within";
  return { band: { x, w: round(width) }, share: at(w.share), place };
}

/** "12" or "12.5": percent values as the student reads them. */
export function num(v: number): string {
  return Number.isInteger(v) ? String(v) : String(Math.round(v * 10) / 10);
}

// ── What Naadi sees ──

export type Translate = (key: string, vars?: Record<string, string | number>) => string;

/**
 * One insight sentence from its ui_copy template (`results.insight.<kind>`) and the server's values. A
 * template the config lacks, or one that names a value the server did not send, gives null: the sentence is
 * left out rather than shown with a "{placeholder}" in it.
 */
export function insightText(t: Translate, insight: Insight): string | null {
  const key = `results.insight.${insight.kind}`;
  const vars: Record<string, string> = {};
  for (const [k, v] of Object.entries(insight.vars)) vars[k] = typeof v === "number" ? num(v) : String(v);
  const text = t(key, vars);
  if (!text.trim() || text === key || /\{\w+\}/.test(text)) return null;
  return text;
}

/** `/practice/blocks/$blockId/review/$n?filter=` (validated by the route before this page's chunk loads). */
export interface ReviewSearch {
  filter: ReviewFilter;
}

export function parseReviewSearch(s: Record<string, unknown>): ReviewSearch {
  return { filter: isReviewFilter(s.filter) ? s.filter : "all" };
}
