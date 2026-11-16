import { useNavigate, useRouterState } from "@tanstack/react-router";
import { useEffect } from "react";

/** Dispatched when Ctrl/Cmd+K is pressed on the Search screen itself: focus and select the field. */
export const SEARCH_FOCUS_EVENT = "naadi:search-focus";

/** True for Ctrl+K and Cmd+K, without Shift or Alt (those belong to the browser and the OS). */
export function isSearchShortcut(
  e: Pick<KeyboardEvent, "key" | "ctrlKey" | "metaKey" | "shiftKey" | "altKey">,
): boolean {
  return (e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && e.key.toLowerCase() === "k";
}

/**
 * Ctrl/Cmd+K opens Search from anywhere in the app (the Search button in the navigation does the same).
 * It works while a text field has focus too, as a command palette does, and is announced on the buttons
 * with `aria-keyshortcuts`.
 */
export function useSearchShortcut(enabled: boolean): void {
  const navigate = useNavigate();
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  useEffect(() => {
    if (!enabled) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || !isSearchShortcut(e)) return;
      e.preventDefault();
      if (pathname === "/search") window.dispatchEvent(new Event(SEARCH_FOCUS_EVENT));
      else void navigate({ to: "/search" });
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [enabled, pathname, navigate]);
}
