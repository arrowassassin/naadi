import type { CoachSuggestionOut, PlanItemOut, TodayOut } from "@naadi/shared";
import { describe, expect, it } from "vitest";
import { actionLabel, duration, itemAction, primaryAction, suggestionText } from "./format";

const t = (key: string, vars?: Readonly<Record<string, string>>) =>
  vars
    ? `${key}(${Object.entries(vars)
        .map(([k, v]) => `${k}=${v}`)
        .join(",")})`
    : key;

function item(over: Partial<PlanItemOut>): PlanItemOut {
  return {
    id: crypto.randomUUID(),
    day: "2026-11-16",
    kind: "read",
    title: "Topic",
    ref_type: null,
    ref_id: null,
    target_minutes: 60,
    target_count: null,
    status: "planned",
    origin: "generator",
    sort: 0,
    plan_day: 1,
    ...over,
  };
}

function today(over: Partial<TodayOut> = {}, items: PlanItemOut[] = []): TodayOut {
  return {
    today: "2026-11-16",
    exam: "step1",
    exam_date: "2027-01-15",
    days_left: 60,
    onboarding_complete: true,
    plan: { plan_id: crypto.randomUUID(), day: null, items, next_day: null, now_item: items[0]?.id ?? null },
    resume: null,
    readiness: { score: null, coverage: 0, answered: 0, needed_answered: 40, needed_coverage: 50, days: 60 },
    systems: { window_days: 14, min_attempts: 5, weak_below: 55, items: [] },
    cards: { due: 0, reviewable: false },
    coach: [],
    pathway: null,
    ...over,
  };
}

describe("durations", () => {
  it("never prints clock times", () => {
    expect(duration(t, 45)).toBe("today.item_minutes(n=45)");
    expect(duration(t, 120)).toBe("today.item_hours(h=2)");
    expect(duration(t, 90)).toBe("today.item_hours_minutes(h=1,m=30)");
  });
});

describe("the one primary action", () => {
  it("resumes an open block first", () => {
    const d = today({
      resume: { kind: "block", block_id: "b1", mode: "tutor", answered: 3, total: 10, next_n: 4, diagnostic: false },
    });
    expect(primaryAction(d)).toEqual({ kind: "resume_block", blockId: "b1", n: 4, diagnostic: false });
  });

  it("follows the current plan item, skipping outside resources and done items", () => {
    const items = [
      item({ kind: "assessment", ref_type: "external", ref_id: "nbme" }),
      item({ status: "done", ref_type: "chapter", ref_id: "ch01" }),
      item({ kind: "questions", ref_type: "qbank", ref_id: "step1", target_count: 80 }),
    ];
    expect(primaryAction(today({}, items))).toEqual({ kind: "block", exam: "step1", count: 40, status: "unused" });
  });

  it("resumes the reading position inside the planned chapter", () => {
    const items = [item({ ref_type: "chapter", ref_id: "ch05" })];
    const d = today(
      {
        resume: {
          kind: "reading",
          chapter_id: "ch05",
          chapter_title: "Cardio",
          section_id: "ch05-s02",
          section_heading: "Murmurs",
          section_n: 2,
        },
      },
      items,
    );
    expect(primaryAction(d)).toEqual({ kind: "resume_reading", chapterId: "ch05", sectionId: "ch05-s02" });
  });

  it("links cards only when the Cards screen exists, else falls back to Practice", () => {
    const items = [item({ kind: "cards", ref_type: "cards" })];
    expect(itemAction(items[0] as PlanItemOut, today({}, items))).toBeNull();
    expect(primaryAction(today({}, items))).toEqual({ kind: "practice" });
    const on = today({ cards: { due: 4, reviewable: true } }, items);
    expect(primaryAction(on)).toEqual({ kind: "cards" });
  });

  it("labels each action from ui_copy", () => {
    expect(actionLabel(t, { kind: "weak_spot", exam: "step1", count: 20 })).toBe("today.cta_weak_block(count=20)");
    expect(actionLabel(t, { kind: "resume_block", blockId: "x", n: 1, diagnostic: true })).toBe(
      "today.cta_resume_diagnostic",
    );
  });
});

describe("coach wording", () => {
  const base: CoachSuggestionOut = {
    id: "s",
    for_date: "2026-11-16",
    kind: "cards_catchup",
    source: "rules",
    title: null,
    body: null,
    facts: { due: 62, retention: 88 },
    action: {},
    status: "new",
  };
  it("words rules suggestions from ui_copy with their facts", () => {
    expect(suggestionText(t, base)).toEqual({
      title: "coach.rules.cards_catchup.title(due=62,retention=88)",
      body: "coach.rules.cards_catchup.body_retention(due=62,retention=88)",
    });
  });
  it("shows the model's own text for llm suggestions", () => {
    expect(suggestionText(t, { ...base, source: "llm", title: "Cards", body: "62 are due." })).toEqual({
      title: "Cards",
      body: "62 are due.",
    });
  });
});
