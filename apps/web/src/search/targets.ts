import type { Target } from "@naadi/shared";

export interface Destination {
  to: string;
  params: Record<string, string>;
  search?: Record<string, string>;
}

/**
 * Where a hit opens. The server names what a result is (`target`); this turns it into a route, and only into
 * one that exists in this build (`routesByPath`), so a result for a screen that has not shipped yet is shown
 * without a link instead of a dead one.
 *
 * Segment 13: a note opens in Notes (`/notes?note=<id>`; a notebook page id is forwarded to its notebook there).
 * Handwriting opens the surface it was written on: the section, the question, or the note / notebook page.
 */
export function resolveTarget(target: Target, routes: Record<string, unknown>): Destination | null {
  const reader = "/learn/$chapterId/$sectionId";
  const question = "/practice/question/$questionId";
  const notes = "/notes";
  const toReader = (): Destination | null =>
    reader in routes && target.chapter_id && target.section_id
      ? { to: reader, params: { chapterId: target.chapter_id, sectionId: target.section_id } }
      : null;
  const toNote = (id: string | null | undefined): Destination | null =>
    notes in routes && id ? { to: notes, params: {}, search: { note: id } } : null;
  switch (target.type) {
    case "section":
      return toReader();
    case "note":
      return toNote(target.note_id) ?? toReader();
    case "ink":
      if (target.surface_type === "section") return toReader();
      if (target.surface_type === "question") {
        return question in routes && target.question_id
          ? { to: question, params: { questionId: target.question_id } }
          : null;
      }
      if (target.surface_type === "notebook_page") return toNote(target.note_id ?? target.surface_id);
      return toReader();
    case "question":
      if (question in routes && target.question_id) return { to: question, params: { questionId: target.question_id } };
      return null;
    default:
      return null;
  }
}
