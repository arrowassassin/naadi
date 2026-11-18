/** "A - why" rows from the seeded why-not text ("Why the others are wrong: A — ... B — ..."). */
export interface WhyNotRow {
  label: string;
  text: string;
}

const LEAD = /^\s*why the others are wrong\s*:?\s*/i;
const ROW = /(?:^|\s)([A-J])\s[—–-]\s/g;

/**
 * Splits plain why-not text into one row per option. Returns null when the text is not in that shape (or has
 * markup), and the caller then shows it as written.
 */
export function splitWhyNot(text: string | null | undefined): WhyNotRow[] | null {
  if (!text || /[<>]/.test(text)) return null;
  const body = text.replace(LEAD, "");
  const marks = [...body.matchAll(ROW)];
  if (marks.length < 2) return null;
  const rows: WhyNotRow[] = [];
  marks.forEach((m, i) => {
    const start = (m.index ?? 0) + m[0].length;
    const end = i + 1 < marks.length ? (marks[i + 1]?.index ?? body.length) : body.length;
    const label = m[1];
    const t = body.slice(start, end).trim();
    if (label && t) rows.push({ label, text: t });
  });
  return rows.length >= 2 ? rows : null;
}

export function percent(correct: number, total: number): number {
  return total === 0 ? 0 : Math.round((100 * correct) / total);
}

/** The next question to open in a block: the first unanswered, else the first. */
export function resumeIndex(answered: number, total: number): number {
  return Math.min(Math.max(answered + 1, 1), Math.max(total, 1));
}

export function shortDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return new Intl.DateTimeFormat("en", { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" }).format(
    d,
  );
}
