/**
 * One document-level keyboard listener for all mounted ink surfaces. Shortcuts go to the ACTIVE surface
 * (the last one touched, or the only one), never while focus is in an editable field, and never for
 * Cmd/Ctrl+K (reserved by the shell for search). Consumed keys are preventDefault()ed so the shell's own
 * shortcuts can skip them.
 */
export type ShortcutAction = "pen" | "marker" | "eraser" | "lasso" | "undo" | "redo";

export interface ShortcutTarget {
  handleShortcut(action: ShortcutAction): boolean;
}

const surfaces: ShortcutTarget[] = [];
let active: ShortcutTarget | null = null;
let installed = false;

const LETTERS: Record<string, ShortcutAction> = { p: "pen", m: "marker", e: "eraser", l: "lasso" };

function isEditable(t: EventTarget | null): boolean {
  if (!(t instanceof HTMLElement)) return false;
  if (t.isContentEditable) return true;
  const tag = t.tagName;
  if (tag === "TEXTAREA" || tag === "SELECT") return true;
  if (tag === "INPUT") {
    const type = (t as HTMLInputElement).type;
    return !["button", "checkbox", "radio", "range", "submit", "reset", "color"].includes(type);
  }
  return false;
}

export function shortcutFor(e: Pick<KeyboardEvent, "key" | "metaKey" | "ctrlKey" | "altKey" | "shiftKey">) {
  const key = e.key.toLowerCase();
  const mod = e.metaKey || e.ctrlKey;
  if (e.altKey) return null;
  if (mod && key === "z") return e.shiftKey ? "redo" : "undo";
  if (mod && key === "y" && e.ctrlKey && !e.metaKey) return "redo"; // Windows convention
  if (mod || e.shiftKey) return null;
  return LETTERS[key] ?? null;
}

function onKeyDown(e: KeyboardEvent): void {
  if (e.defaultPrevented || e.isComposing || isEditable(e.target)) return;
  const action = shortcutFor(e);
  if (!action) return;
  const target = active ?? (surfaces.length === 1 ? surfaces[0] : null);
  if (!target) return;
  if (target.handleShortcut(action)) e.preventDefault();
}

export function registerShortcutTarget(t: ShortcutTarget): () => void {
  surfaces.push(t);
  if (!installed && typeof document !== "undefined") {
    document.addEventListener("keydown", onKeyDown);
    installed = true;
  }
  return () => {
    const i = surfaces.indexOf(t);
    if (i >= 0) surfaces.splice(i, 1);
    if (active === t) active = null;
    if (surfaces.length === 0 && installed) {
      document.removeEventListener("keydown", onKeyDown);
      installed = false;
    }
  };
}

export function activateShortcutTarget(t: ShortcutTarget): void {
  active = t;
}
