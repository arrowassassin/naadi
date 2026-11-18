import { useNavigate, useRouterState } from "@tanstack/react-router";
import { useEffect } from "react";

/** Dispatched when Ctrl/Cmd+K is pressed on the Search screen itself: focus and select the field. */
export const SEARCH_FOCUS_EVENT = "naadi:search-focus";

/**
 * True when the key press belongs to something else: a text field, a dialog, a scroll region the user is
 * working in (focus inside `[data-no-shortcuts]`, which editors and the ink canvas set), or media.
 */
export function shortcutBelongsToTarget(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false;
  if (target.closest("input, textarea, select, [contenteditable]:not([contenteditable=false]), video, audio"))
    return true;
  return target.closest("[role=dialog], dialog, [data-no-shortcuts]") !== null;
}

/** True for Ctrl+K and Cmd+K, without Shift or Alt (those belong to the browser and the OS). */
export function isSearchShortcut(
  e: Pick<KeyboardEvent, "key" | "ctrlKey" | "metaKey" | "shiftKey" | "altKey">,
): boolean {
  return (e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && e.key.toLowerCase() === "k";
}

let suppressed = 0;

export const searchShortcutSuppressed = (): boolean => suppressed > 0;

/**
 * A screen that needs Ctrl/Cmd+K for itself (an exam block, a rich-text editor, the ink canvas) calls
 * `useSuppressSearchShortcut(true)` while it is mounted: the shortcut then does nothing, and the browser
 * or the screen gets the key. Several screens may suppress at once; the shortcut returns when all let go.
 */
export function useSuppressSearchShortcut(active = true): void {
  useEffect(() => {
    if (!active) return;
    suppressed += 1;
    return () => {
      suppressed -= 1;
    };
  }, [active]);
}

/**
 * Ctrl/Cmd+K opens Search from anywhere in the app (the Search button in the navigation does the same).
 * It stays out of text fields, dialogs, media and `[data-no-shortcuts]` regions, and is announced on the
 * buttons with `aria-keyshortcuts`.
 */
export function useSearchShortcut(enabled: boolean): void {
  const navigate = useNavigate();
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  useEffect(() => {
    if (!enabled) return;
    const onKey = (e: KeyboardEvent) => {
      if (suppressed > 0 || e.defaultPrevented || !isSearchShortcut(e) || shortcutBelongsToTarget(e.target)) return;
      e.preventDefault();
      if (pathname === "/search") window.dispatchEvent(new Event(SEARCH_FOCUS_EVENT));
      else void navigate({ to: "/search" });
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [enabled, pathname, navigate]);
}
