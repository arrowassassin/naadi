import type { Target } from "@naadi/shared";

export interface Destination {
  to: string;
  params: Record<string, string>;
}

/**
 * Where a hit opens. The server names what a result is (`target`); this turns it into a route, and only into
 * one that exists in this build (`routesByPath`), so a result for a screen that has not shipped yet is shown
 * without a link instead of a dead one.
 */
export function resolveTarget(target: Target, routes: Record<string, unknown>): Destination | null {
  const reader = "/learn/$chapterId/$sectionId";
  const question = "/practice/question/$questionId";
  switch (target.type) {
    case "section":
    case "note":
    case "ink":
      if (reader in routes && target.chapter_id && target.section_id) {
        return { to: reader, params: { chapterId: target.chapter_id, sectionId: target.section_id } };
      }
      return null;
    case "question":
      if (question in routes && target.question_id) return { to: question, params: { questionId: target.question_id } };
      return null;
    default:
      return null;
  }
}
