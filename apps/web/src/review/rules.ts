import type { ReviewFinding } from "@naadi/shared";

/** Every rule id of config/authoring_rules.md Part B: schema (S), item-writing (L) and blind-solver (B) rules. */
export const LINT_RULE_IDS: readonly string[] = [
  "S01",
  "S02",
  "S03",
  "S04",
  "S05",
  "L01",
  "L02",
  "L03",
  "L04",
  "L05",
  "L06",
  "L07",
  "L08",
  "L09",
  "L10",
  "L11",
  "L12",
  "L13",
  "L14",
  "L15",
  "L16",
  "L17",
  "L18",
  "B01",
  "B02",
  "B03",
  "B04",
];

/** The 13 item-writing flaws a reviewer can tick (the Part A checklist), in the server's order. */
export const FLAW_CODES: readonly string[] = [
  "not_homogeneous",
  "key_too_long",
  "grammatical_cue",
  "logical_cue",
  "word_repeat",
  "convergence",
  "absolute_terms",
  "negative_lead_in",
  "cannot_cover_options",
  "irrelevant_difficulty",
  "hinged",
  "stereotyped",
  "other",
];

export const PSYCH_FLAGS: readonly string[] = [
  "reports",
  "p_too_low",
  "p_too_high",
  "point_biserial_low",
  "distractor_over_key",
  "slow",
  "distractor_pb_positive",
  "nonfunctioning_distractor",
];

/** The copy key of a rule id's sentence (an id the copy lacks falls back to the generic sentence). */
export function lintKey(rule: string, severity?: string): string {
  if (rule === "B02" && severity === "info") return "review.lint.b02_skipped";
  return LINT_RULE_IDS.includes(rule) ? `review.lint.${rule.toLowerCase()}` : "review.lint.unknown";
}

function show(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (Array.isArray(v)) return v.map(show).filter(Boolean).join(", ");
  if (typeof v === "object")
    return Object.values(v as Record<string, unknown>)
      .map(show)
      .filter(Boolean)
      .join(", ");
  if (typeof v === "number") return String(Math.round(v * 100) / 100);
  return String(v);
}

/** The placeholders of a finding's sentence: its params as short text (`{words}`, `{count}`, ...) plus `{rule}`. */
export function findingVars(f: Pick<ReviewFinding, "rule" | "params">): Record<string, string> {
  const out: Record<string, string> = { rule: f.rule };
  for (const [k, v] of Object.entries(f.params)) out[k] = show(v);
  return out;
}
