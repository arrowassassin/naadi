import { useEffect, useSyncExternalStore } from "react";

/**
 * Pen-surface registration (contract S03 <-> S12a). An ink surface calls `usePenSurface(active)` while its
 * pen tool is engaged; the shell reads `usePenActive()` to move its chrome out of the way. Ref-counted, so
 * several surfaces on one page are fine, and no provider is needed (works outside the shell too).
 * While any surface is active, `<html data-pen-active>` is set.
 */
let activeCount = 0;
const listeners = new Set<() => void>();

function publish(): void {
  const root = document.documentElement;
  if (activeCount > 0) root.setAttribute("data-pen-active", "");
  else root.removeAttribute("data-pen-active");
  for (const l of listeners) l();
}

function subscribe(l: () => void): () => void {
  listeners.add(l);
  return () => listeners.delete(l);
}

const snapshot = (): boolean => activeCount > 0;

export function usePenActive(): boolean {
  return useSyncExternalStore(subscribe, snapshot, () => false);
}

export function usePenSurface(active: boolean): void {
  useEffect(() => {
    if (!active) return;
    activeCount += 1;
    publish();
    return () => {
      activeCount = Math.max(0, activeCount - 1);
      publish();
    };
  }, [active]);
}

/**
 * Shared reader navigation slot in the measured shell bottom bar, above the phone tabs or assistant dock.
 */
let bottomSlot: HTMLElement | null = null;
const slotListeners = new Set<() => void>();

export function setShellBottomSlot(el: HTMLElement | null): void {
  if (bottomSlot === el) return;
  bottomSlot = el;
  for (const l of slotListeners) l();
}

function subscribeSlot(l: () => void): () => void {
  slotListeners.add(l);
  return () => slotListeners.delete(l);
}

export function useShellBottomSlot(): HTMLElement | null {
  return useSyncExternalStore(
    subscribeSlot,
    () => bottomSlot,
    () => null,
  );
}
