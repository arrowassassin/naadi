/**
 * The device-store leak guard (segment 18 review blocker): no answer, explanation, why-not or objective is written
 * to any offline store before the student's attempt synced. Server responses already leave these out for an
 * unattempted question; this is the second wall on the device, applied to every question-shaped value before it
 * is stored, and the same list the leak-scan test looks for.
 */
export const HIDDEN_KEYS = [
  "answer",
  "answer_html",
  "explanation",
  "explanation_html",
  "why_not_html",
  "educational_objective",
] as const;

const HIDDEN = new Set<string>(HIDDEN_KEYS);

/**
 * A copy of `value` without any hidden key at any depth, unless the object that holds it carries the server's
 * proof of a graded attempt (`attempt` with `correct` set, as QuestionView and the manifest's attempted questions
 * have). A null hidden field is kept (it says nothing).
 */
export function stripHidden<T>(value: T): T {
  return walk(value, false) as T;
}

function graded(o: Record<string, unknown>): boolean {
  const a = o.attempt;
  return typeof a === "object" && a !== null && typeof (a as { correct?: unknown }).correct === "boolean";
}

function walk(v: unknown, allowed: boolean): unknown {
  if (Array.isArray(v)) return v.map((x) => walk(x, allowed));
  if (typeof v !== "object" || v === null) return v;
  const o = v as Record<string, unknown>;
  const ok = allowed || graded(o);
  const out: Record<string, unknown> = {};
  for (const [k, x] of Object.entries(o)) {
    if (HIDDEN.has(k) && !ok && x !== null && x !== undefined) continue;
    out[k] = walk(x, ok);
  }
  return out;
}

/** The hidden keys with a value found anywhere in `value` outside a graded attempt (the leak scan). */
export function findLeaks(value: unknown, path = "$"): string[] {
  const leaks: string[] = [];
  const scan = (v: unknown, p: string, allowed: boolean) => {
    if (Array.isArray(v)) {
      for (const [i, x] of v.entries()) scan(x, `${p}[${i}]`, allowed);
      return;
    }
    if (typeof v !== "object" || v === null) return;
    const o = v as Record<string, unknown>;
    const ok = allowed || graded(o);
    for (const [k, x] of Object.entries(o)) {
      if (HIDDEN.has(k) && !ok && x !== null && x !== undefined) leaks.push(`${p}.${k}`);
      scan(x, `${p}.${k}`, ok);
    }
  };
  scan(value, path, false);
  return leaks;
}
