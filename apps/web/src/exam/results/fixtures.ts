import type { BlockResults, QuestionView, ResultQuestion } from "@naadi/shared";

/** A recorded 40-question exam-mode block: 29 right, 10 wrong, 1 omitted, 4 flagged, some changed answers. */
export const BLOCK_ID = "6f1c1b7e-8a2d-4c3e-9f10-2b3c4d5e6f70";

const WRONG = new Set([3, 7, 8, 12, 15, 19, 22, 27, 31, 34]);
const OMITTED = new Set([38]);
const FLAGGED = new Set([5, 12, 20, 33]);
const CHANGES: Record<number, ResultQuestion["change"]> = {
  7: "right_to_wrong",
  9: "wrong_to_right",
  22: "right_to_wrong",
};

export const QUESTIONS: ResultQuestion[] = Array.from({ length: 40 }, (_, i) => {
  const n = i + 1;
  return {
    n,
    section_n: 1,
    question_id: `q-${n}`,
    outcome: OMITTED.has(n) ? "omitted" : WRONG.has(n) ? "incorrect" : "correct",
    flagged: FLAGGED.has(n),
    change: CHANGES[n] ?? null,
    seconds: OMITTED.has(n) ? null : 70 + n,
  };
});

export const RESULTS: BlockResults = {
  block_id: BLOCK_ID,
  exam: "step1",
  mode: "exam",
  mock: "mock1",
  finished_at: "2026-11-14T10:30:00Z",
  total: 40,
  correct: 29,
  omitted: 1,
  percent: 72.5,
  time: { mean_s: 82.4, median_s: 80, target_s: 90, total_s: 3214 },
  changes: { changed: 3, right_to_wrong: 2, wrong_to_right: 1, wrong_to_wrong: 0 },
  flags: { flagged: 4, flagged_correct: 3 },
  questions: QUESTIONS,
  systems: [
    { id: "sys-renal", label: "Renal", kind: "system", total: 8, correct: 7, percent: 87.5, weak: false },
    { id: "sys-cardio", label: "Cardiovascular", kind: "system", total: 10, correct: 5, percent: 50, weak: true },
  ],
  disciplines: [
    { id: "dis-pharm", label: "Pharmacology", kind: "discipline", total: 12, correct: 9, percent: 75, weak: false },
  ],
  competencies: [],
  weights: [
    {
      id: "sys-cardio",
      label: "Cardiovascular",
      weight_min: 6,
      weight_max: 10,
      share: 25,
      total: 10,
      correct: 5,
      percent: 50,
    },
    { id: "sys-renal", label: "Renal", weight_min: 5, weight_max: 9, share: 20, total: 8, correct: 7, percent: 87.5 },
    { id: "sys-skin", label: "Skin", weight_min: 4, weight_max: 8, share: 0, total: 0, correct: 0, percent: null },
  ],
  trend: [
    {
      block_id: "a0000000-0000-4000-8000-000000000001",
      finished_at: "2026-11-01T10:00:00Z",
      score: 54,
      mode: "timed",
      current: false,
    },
    {
      block_id: "a0000000-0000-4000-8000-000000000002",
      finished_at: "2026-11-08T10:00:00Z",
      score: 66,
      mode: "timed",
      current: false,
    },
    { block_id: BLOCK_ID, finished_at: "2026-11-14T10:30:00Z", score: 72.5, mode: "exam", current: true },
  ],
  insights: [
    { kind: "weakest_topic", vars: { topic: "Cardiovascular", correct: 5, total: 10 } },
    { kind: "changes_hurt", vars: { lost: 2, gained: 1 } },
    // Missing the `previous` value its template names: the sentence is left out, never shown half-filled.
    { kind: "trend_up", vars: { delta: 7 } },
  ],
};

export function questionView(n: number): QuestionView {
  const rq = QUESTIONS[n - 1] as ResultQuestion;
  const chosen = rq.outcome === "omitted" ? null : rq.outcome === "correct" ? "A" : "C";
  return {
    block_id: BLOCK_ID,
    n,
    total: 40,
    mode: "exam",
    section_n: 1,
    flagged: rq.flagged,
    lesson: null,
    question: {
      id: rq.question_id,
      difficulty: "medium",
      topics: ["Renal"],
      stem_html: `<p>Stem of question ${n}.</p>`,
      options: ["A", "B", "C", "D", "E"].map((label) => ({ label, html: `<p>Option ${label}</p>` })),
    },
    attempt: chosen === null ? null : { chosen, seconds: rq.seconds, correct: rq.outcome === "correct" },
    explanation: {
      answer: "A",
      answer_html: null,
      explanation_html: `<p>Why A for ${n}.</p>`,
      why_not_html: "Why the others are wrong: B — not this. C — nor this.",
      educational_objective: "Recognise the pattern.",
    },
  };
}
