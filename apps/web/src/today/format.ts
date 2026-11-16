import type { CoachSuggestionOut, PlanItemOut, TodayOut } from "@naadi/shared";

export type T = (key: string, vars?: Readonly<Record<string, string>>) => string;

/** "45 min", "2 h", "1 h 30 min": durations only, never clock times. */
export function duration(t: T, minutes: number): string {
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  if (h === 0) return t("today.item_minutes", { n: String(m) });
  if (m === 0) return t("today.item_hours", { h: String(h) });
  return t("today.item_hours_minutes", { h: String(h), m: String(m) });
}

/** Items added by the coach carry a structured ref; their wording comes from ui_copy. */
export function itemTitle(t: T, item: PlanItemOut): string {
  if (item.origin === "assistant") {
    if (item.ref_type === "weak_spot") return t("today.added_weak_spot", { title: item.title });
    if (item.ref_type === "qbank_incorrect") return t("today.added_misses");
    if (item.ref_type === "cards") return t("today.added_cards");
    if (item.ref_type === "chapter") return t("today.added_reread", { title: item.title });
  }
  return item.title;
}

export type Action =
  | { kind: "resume_block"; blockId: string; n: number; diagnostic: boolean }
  | { kind: "resume_reading"; chapterId: string; sectionId: string }
  | { kind: "start_reading"; chapterId: string }
  | { kind: "block"; exam: string; count: number; status: "unused" | "incorrect" }
  | { kind: "weak_spot"; exam: string; count: number }
  | { kind: "cards" }
  | { kind: "practice" };

const MAX_BLOCK = 40;

/** What an item leads to inside Naadi, or null (outside resources, admin tasks, rest, text-only rows). */
export function itemAction(item: PlanItemOut, today: TodayOut): Action | null {
  const count = Math.min(MAX_BLOCK, Math.max(1, item.target_count ?? MAX_BLOCK));
  switch (item.ref_type) {
    case "chapter": {
      if (!item.ref_id) return null;
      const r = today.resume;
      if (r?.kind === "reading" && r.chapter_id === item.ref_id) {
        return { kind: "resume_reading", chapterId: r.chapter_id, sectionId: r.section_id };
      }
      return { kind: "start_reading", chapterId: item.ref_id };
    }
    case "qbank":
      return { kind: "block", exam: item.ref_id ?? today.exam, count, status: "unused" };
    case "qbank_incorrect":
      return { kind: "block", exam: item.ref_id ?? today.exam, count, status: "incorrect" };
    case "weak_spot":
      return { kind: "weak_spot", exam: item.ref_id ?? today.exam, count };
    case "cards":
      return today.cards.reviewable ? { kind: "cards" } : null;
    default:
      return null;
  }
}

/** The one lime action: an open block first, then the current plan item, then the last reading, then Practice. */
export function primaryAction(today: TodayOut): Action {
  const r = today.resume;
  if (r?.kind === "block") return { kind: "resume_block", blockId: r.block_id, n: r.next_n, diagnostic: r.diagnostic };
  const items = today.plan?.items ?? [];
  const open = items.filter((i) => i.status !== "done" && i.status !== "skipped");
  for (const i of open) {
    const a = itemAction(i, today);
    if (a) return a;
  }
  if (r?.kind === "reading") return { kind: "resume_reading", chapterId: r.chapter_id, sectionId: r.section_id };
  return { kind: "practice" };
}

export function actionLabel(t: T, a: Action): string {
  switch (a.kind) {
    case "resume_block":
      return t(a.diagnostic ? "today.cta_resume_diagnostic" : "today.cta_resume_block");
    case "resume_reading":
      return t("today.cta_resume_lesson");
    case "start_reading":
      return t("today.cta_start_reading");
    case "block":
      return t(a.status === "incorrect" ? "today.cta_misses_block" : "today.cta_start_block", {
        count: String(a.count),
      });
    case "weak_spot":
      return t("today.cta_weak_block", { count: String(a.count) });
    case "cards":
      return t("today.cta_review_cards");
    case "practice":
      return t("today.cta_practice");
  }
}

/** Rules suggestions are worded here from ui_copy with their facts; LLM ones carry their own text. */
export function suggestionText(t: T, s: CoachSuggestionOut): { title: string; body: string } {
  if (s.source === "llm" && s.title && s.body) return { title: s.title, body: s.body };
  const vars = Object.fromEntries(Object.entries(s.facts).map(([k, v]) => [k, String(v)]));
  const body =
    s.kind === "cards_catchup" && s.facts.retention !== undefined
      ? t("coach.rules.cards_catchup.body_retention", vars)
      : t(`coach.rules.${s.kind}.body`, vars);
  return { title: t(`coach.rules.${s.kind}.title`, vars), body };
}

export const FACT_KEYS = [
  "system",
  "percent",
  "attempts",
  "correct",
  "misses",
  "days",
  "questions",
  "due",
  "retention",
  "section",
] as const;

export function factValue(t: T, key: string, value: unknown): string {
  if (key === "percent" || key === "retention") return t("today.fact_percent_value", { n: String(value) });
  return String(value);
}
