import type { NoteOut } from "@naadi/shared";
import type { TextLookup } from "../content/types";

/** "edited today" / "edited yesterday" / "edited 3 Nov" in the student's locale (structured date, worded here). */
export function editedLabel(t: TextLookup, iso: string, now: Date = new Date()): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const day = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((day(now) - day(d)) / 86_400_000);
  if (diff <= 0) return t("notes.edited_today");
  if (diff === 1) return t("notes.edited_yesterday");
  const date = d.toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    ...(d.getFullYear() === now.getFullYear() ? {} : { year: "numeric" }),
  });
  return t("notes.edited_on", { date });
}

/** A note's display title: its own, else its first line, else a word for an empty note. */
export function noteTitle(t: TextLookup, n: Pick<NoteOut, "title" | "excerpt" | "body_text" | "source">): string {
  if (n.title?.trim()) return n.title.trim();
  if (n.source?.section_heading) return n.source.section_heading;
  const first = (n.body_text || n.excerpt || "").split("\n").find((l) => l.trim());
  if (first) return first.trim().slice(0, 80);
  return t("notes.untitled");
}

/** The words of a note's source chip: "Chapter · Section", "Question 4", or null (no visible source). */
export function sourceLabel(t: TextLookup, n: Pick<NoteOut, "source" | "anchor_type">): string | null {
  const s = n.source;
  if (!s) return null;
  if (s.type === "section") {
    return t("notes.source_section", { chapter: s.chapter_title ?? "", section: s.section_heading ?? "" });
  }
  if (s.type === "question") {
    return s.question_number
      ? t("notes.source_question_n", { n: String(s.question_number) })
      : t("notes.source_question");
  }
  return s.title ?? null;
}

/**
 * True when a highlight's quote could not be found in its text any more, or what it was on is gone. `local`: the
 * highlight was made on this device and the server has not answered yet (no source to show yet, not detached).
 */
export function isDetached(n: Pick<NoteOut, "anchor_pos" | "source" | "kind" | "anchor_type">, local = false): boolean {
  if (n.kind !== "highlight") return false;
  if ((n.anchor_pos as { detached?: boolean | null } | null)?.detached) return true;
  return !local && n.source === null && (n.anchor_type === "section" || n.anchor_type === "question");
}
