import { useEffect } from "react";
import { useSuppressSearchShortcut } from "../search/shortcut";

/**
 * While an exam screen is up, the app shell's navigation (top pill, phone tab bar) is covered by the exam's own
 * full-screen chrome. This makes it `inert` too, so keyboard and screen-reader users cannot reach links that
 * are not visible, and restores it when the screen goes. Leaving is always possible through the exam's own
 * Leave link (the timer keeps running on the server).
 *
 * Ctrl/Cmd+K (Search) does nothing on exam screens (S07's `useSuppressSearchShortcut`); the shell also
 * suppresses it whenever the server reports an active session.
 */
export function useExamChrome(active: boolean): void {
  useSuppressSearchShortcut(active);
  useEffect(() => {
    if (!active) return;
    const shell = document.querySelector<HTMLElement>("[data-shell]");
    if (!shell) return;
    const chrome = Array.from(shell.children).filter(
      (el): el is HTMLElement => el instanceof HTMLElement && el.tagName !== "MAIN" && !el.hasAttribute("inert"),
    );
    for (const el of chrome) el.setAttribute("inert", "");
    shell.setAttribute("data-exam-active", "");
    return () => {
      for (const el of chrome) el.removeAttribute("inert");
      shell.removeAttribute("data-exam-active");
    };
  }, [active]);
}
