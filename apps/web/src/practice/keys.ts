/**
 * The question view's keyboard map (HANDOFF 5.1): 1-5 or A-E choose an option, F flags, N goes to the next
 * question, Command or Control + Enter submits. Anything else (including a key typed into a field, or any
 * other modifier) is not ours.
 */
export type KeyAction = { type: "choose"; index: number } | { type: "flag" } | { type: "next" } | { type: "submit" };

export interface KeyLike {
  key: string;
  metaKey: boolean;
  ctrlKey: boolean;
  altKey: boolean;
  shiftKey: boolean;
}

const CHOICES = 5;

export function keyAction(e: KeyLike, optionCount: number): KeyAction | null {
  if (e.key === "Enter") return (e.metaKey || e.ctrlKey) && !e.altKey && !e.shiftKey ? { type: "submit" } : null;
  if (e.metaKey || e.ctrlKey || e.altKey) return null;
  const k = e.key.toLowerCase();
  if (k === "f") return { type: "flag" };
  if (k === "n") return { type: "next" };
  const limit = Math.min(optionCount, CHOICES);
  if (/^[1-5]$/.test(k)) {
    const index = Number(k) - 1;
    return index < limit ? { type: "choose", index } : null;
  }
  if (/^[a-e]$/.test(k)) {
    const index = k.charCodeAt(0) - 97;
    return index < limit ? { type: "choose", index } : null;
  }
  return null;
}

/** True when the key press belongs to a text field, a dialog or the ink surface, never to the shortcuts. */
export function isTypingTarget(t: EventTarget | null): boolean {
  if (!(t instanceof HTMLElement)) return false;
  if (t instanceof HTMLInputElement && ["radio", "checkbox", "range", "button"].includes(t.type)) {
    return t.closest("dialog") !== null;
  }
  return t.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName) || t.closest("dialog") !== null;
}
