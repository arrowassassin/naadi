import { useEffect } from "react";
import { isSearchShortcut } from "../search/shortcut";

/**
 * While an exam screen is up, the app shell's navigation (top pill, phone tab bar) is covered by the exam's own
 * full-screen chrome. This makes it `inert` too, so keyboard and screen-reader users cannot reach links that
 * are not visible, and restores it when the screen goes. Leaving is always possible through the exam's own
 * Leave link (the timer keeps running on the server).
 *
 * Ctrl/Cmd+K (Search) is suppressed on exam screens: a capture-phase listener marks the event handled before
 * the shell's `useSearchShortcut` (which ignores `defaultPrevented` events) sees it.
 */
export function useExamChrome(active: boolean): void {
  useEffect(() => {
    if (!active) return;
    const swallow = (e: KeyboardEvent) => {
      if (isSearchShortcut(e)) e.preventDefault();
    };
    window.addEventListener("keydown", swallow, { capture: true });
    const shell = document.querySelector<HTMLElement>("[data-shell]");
    if (!shell) return () => window.removeEventListener("keydown", swallow, { capture: true });
    const chrome = Array.from(shell.children).filter(
      (el): el is HTMLElement => el instanceof HTMLElement && el.tagName !== "MAIN" && !el.hasAttribute("inert"),
    );
    for (const el of chrome) el.setAttribute("inert", "");
    shell.setAttribute("data-exam-active", "");
    return () => {
      window.removeEventListener("keydown", swallow, { capture: true });
      for (const el of chrome) el.removeAttribute("inert");
      shell.removeAttribute("data-exam-active");
    };
  }, [active]);
}
