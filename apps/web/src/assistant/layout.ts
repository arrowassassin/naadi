import { useSyncExternalStore } from "react";

/**
 * Where the assistant (and the canvas) open, by viewport (HANDOFF §4 breakpoints, Lesson/Tablet/Phone boards):
 * - "panel": laptop (≥ 1100 px) and iPad landscape: a side panel next to the page (Lesson board);
 * - "sheet": iPad portrait / tablet: a bottom sheet over the page (Tablet board);
 * - "fullscreen": phone (< 640 px): a full-screen sheet opened from the tab bar's centre button (Phone board).
 */
export type AssistantLayout = "panel" | "sheet" | "fullscreen";

export const PANEL_QUERY = "(min-width: 68.75rem), (orientation: landscape) and (min-width: 62.5rem)";
export const PHONE_QUERY = "(max-width: 39.99rem)";

function current(): AssistantLayout {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return "panel";
  if (window.matchMedia(PHONE_QUERY).matches) return "fullscreen";
  if (window.matchMedia(PANEL_QUERY).matches) return "panel";
  return "sheet";
}

function subscribe(cb: () => void): () => void {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return () => {};
  const queries = [window.matchMedia(PHONE_QUERY), window.matchMedia(PANEL_QUERY)];
  for (const q of queries) q.addEventListener("change", cb);
  return () => {
    for (const q of queries) q.removeEventListener("change", cb);
  };
}

export function useAssistantLayout(): AssistantLayout {
  return useSyncExternalStore(subscribe, current, () => "panel");
}
