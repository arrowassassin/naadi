import type { ChapterProgress, Coverage } from "@naadi/shared";
import type { TextLookup } from "../content/types";

/**
 * Counted wording from app_config.ui_copy: `<base>_one` for 1, `<base>_other` otherwise, with `{n}` filled
 * (e.g. learn.count_section_one = "{n} section", learn.count_section_other = "{n} sections").
 */
export function count(t: TextLookup, base: string, n: number): string {
  return t(`${base}_${n === 1 ? "one" : "other"}`, { n: String(n) });
}

export function percent(read: number, total: number): number {
  return total > 0 ? Math.round((read / total) * 100) : 0;
}

/** "16 sections · 7 figures · 45 questions · 12 cards · about 80 min": only what exists. */
export function coverageLine(t: TextLookup, c: Coverage): string {
  const parts = [count(t, "learn.count_section", c.sections)];
  if (c.figures) parts.push(count(t, "learn.count_figure", c.figures));
  if (c.videos) parts.push(count(t, "learn.count_video", c.videos));
  if (c.questions) parts.push(count(t, "learn.count_question", c.questions));
  if (c.cards) parts.push(count(t, "learn.count_card", c.cards));
  if (c.reading_minutes) parts.push(minutesLabel(t, c.reading_minutes));
  return parts.join(t("learn.separator"));
}

export function statusWord(t: TextLookup, p: ChapterProgress): string {
  if (p.status === "done") return t("learn.status_done");
  if (p.status === "in_progress") {
    return t("learn.status_progress", { read: String(p.sections_read), total: String(p.sections_total) });
  }
  return t("learn.status_not_started");
}

export function minutesLabel(t: TextLookup, m: number): string {
  return count(t, "learn.count_minute", Math.max(1, m));
}
