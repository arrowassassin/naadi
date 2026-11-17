import type { QueueRef } from "../api";

/** The admin page a queue item's ref points at (deep link), or null for refs without an admin page. */
export function refLink(
  ref: QueueRef | null | undefined,
): { to: string; params?: Record<string, string>; search?: Record<string, string> } | null {
  if (!ref) return null;
  switch (ref.type) {
    case "section":
      return { to: "/admin/sections/$sectionId/edit", params: { sectionId: ref.id } };
    case "question":
      return { to: "/admin/questions/$questionId", params: { questionId: ref.id } };
    case "chapter":
      return { to: "/admin/chapters/$chapterId", params: { chapterId: ref.id } };
    case "media":
    case "youtube_candidate":
      return { to: "/admin/media", search: { q: ref.id } };
    case "report":
      return { to: "/admin/reports" };
    default:
      return null;
  }
}
