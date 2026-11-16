import type { ChapterProgress, Coverage } from "@naadi/shared";

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

export function percent(read: number, total: number): number {
  return total > 0 ? Math.round((read / total) * 100) : 0;
}

/** "16 sections · 7 figures · 45 questions · 12 cards · about 80 min": only what exists. */
export function coverageLine(c: Coverage): string {
  const parts = [plural(c.sections, "section")];
  if (c.figures) parts.push(plural(c.figures, "figure"));
  if (c.videos) parts.push(plural(c.videos, "video"));
  if (c.questions) parts.push(plural(c.questions, "question"));
  if (c.cards) parts.push(plural(c.cards, "card"));
  if (c.reading_minutes) parts.push(`about ${c.reading_minutes} min`);
  return parts.join(" · ");
}

export function statusWord(p: ChapterProgress): string {
  if (p.status === "done") return "Done";
  if (p.status === "in_progress") return `${p.sections_read} of ${p.sections_total} read`;
  return "Not started";
}

export function minutesLabel(m: number): string {
  return m <= 1 ? "about 1 min" : `about ${m} min`;
}
