/**
 * "Is there a pen?" shared by every surface on the page and remembered for the device, so the first
 * finger on a Pencil iPad already scrolls instead of drawing. Storage failures (private mode) are fine:
 * detection simply starts again on the next pen contact or hover.
 */
const KEY = "naadi.ink.penPresent";
const listeners = new Set<(present: boolean) => void>();
let present: boolean | null = null;

function read(): boolean {
  try {
    return globalThis.localStorage?.getItem(KEY) === "1";
  } catch {
    return false;
  }
}

export function penPresent(): boolean {
  if (present === null) present = read();
  return present;
}

export function markPenPresent(): void {
  if (penPresent()) return;
  present = true;
  try {
    globalThis.localStorage?.setItem(KEY, "1");
  } catch {
    // Storage unavailable: keep the in-memory flag only.
  }
  for (const fn of listeners) fn(true);
}

/** Forget the pen (settings → "reset pen detection"). */
export function forgetPen(): void {
  present = false;
  try {
    globalThis.localStorage?.removeItem(KEY);
  } catch {
    // Storage unavailable.
  }
  for (const fn of listeners) fn(false);
}

export function onPenPresence(fn: (present: boolean) => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}
