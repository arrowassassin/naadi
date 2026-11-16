import { putReadingPosition, type ReadingPosition, type ReadingPositionIn } from "@naadi/shared";
import { type RefObject, useCallback, useEffect, useRef } from "react";
import { getAuthState } from "../auth/session";
import { useSavePosition } from "./api";

/*
 * Reading position = index of the top-most visible top-level block of the section + the fraction of that
 * block already scrolled past (e.g. 3.42). It does not depend on the viewport width, so a spot reached on
 * an iPad is restored on a phone. Saved after scrolling settles, when the section changes and when the page
 * is hidden; restored once per section after the content and fonts are laid out.
 */

const SAVE_AFTER_MS = 1500;
const MIN_CHANGE = 0.05;
const TOKEN_MARGIN_MS = 10_000;

/**
 * The last save of a page that is being hidden or closed: one keepalive request with the session's current
 * access token, read synchronously. Going through the session client here (an async lock) can run after the
 * page's script context has stopped, which WebKit reports as an uncaught "Context is stopped". If the token
 * is about to expire the save is skipped; the position is saved again on the next scroll or section change.
 */
function sendOnLeave(body: ReadingPositionIn): void {
  const auth = getAuthState();
  if (auth.status !== "signed-in") return;
  const expiresAt = (auth.session.expires_at ?? 0) * 1000;
  if (expiresAt < Date.now() + TOKEN_MARGIN_MS) return;
  try {
    void putReadingPosition({
      body,
      keepalive: true,
      headers: { authorization: `Bearer ${auth.session.access_token}` },
    }).catch(() => undefined);
  } catch {
    // the page is already gone
  }
}

function anchorY(): number {
  const top = Number.parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--shell-top")) || 0;
  return top + 24;
}

function blocks(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>("[data-content-root] > [data-block-index]"));
}

export function measureOffset(root: HTMLElement, anchor = anchorY()): number {
  const list = blocks(root);
  if (!list.length) return 0;
  for (const [i, el] of list.entries()) {
    const r = el.getBoundingClientRect();
    if (r.bottom > anchor) {
      if (r.height <= 0) return i;
      const frac = Math.min(1, Math.max(0, (anchor - r.top) / r.height));
      return Math.round((i + frac) * 1e4) / 1e4;
    }
  }
  return list.length;
}

export function scrollToOffset(root: HTMLElement, offset: number, anchor = anchorY()): void {
  const list = blocks(root);
  if (!list.length || offset <= 0) return;
  const i = Math.min(list.length - 1, Math.floor(offset));
  const frac = Math.min(1, offset - i);
  const el = list[i] as HTMLElement;
  const r = el.getBoundingClientRect();
  const y = window.scrollY + r.top + frac * r.height - anchor;
  window.scrollTo({ top: Math.max(0, y), behavior: "instant" });
}

export interface ReadingPositionOptions {
  chapterId: string;
  sectionId: string;
  root: RefObject<HTMLElement | null>;
  end: RefObject<HTMLElement | null>;
  /** This section's saved position (any device), once loaded; undefined while loading. */
  saved: ReadingPosition | null | undefined;
  /** The content is rendered (chapter loaded). */
  ready: boolean;
  /** Saving is off for a copy read from the device cache while offline. */
  enabled: boolean;
}

export function useReadingPosition({ chapterId, sectionId, root, end, saved, ready, enabled }: ReadingPositionOptions) {
  const save = useSavePosition(chapterId);
  const saveRef = useRef(save.mutate);
  saveRef.current = save.mutate;
  const state = useRef({ offset: 0, sent: -1, completed: false, sentCompleted: false, restored: false });
  const timer = useRef<number | undefined>(undefined);

  const flush = useCallback(
    (keepalive = false) => {
      window.clearTimeout(timer.current);
      const s = state.current;
      if (!enabled || !s.restored) return;
      const changed = Math.abs(s.offset - s.sent) >= MIN_CHANGE || (s.completed && !s.sentCompleted);
      if (!changed) return;
      // Offline: keep the latest position and send it when the connection is back (the "online" listener).
      if (typeof navigator !== "undefined" && navigator.onLine === false) return;
      s.sent = s.offset;
      s.sentCompleted = s.completed;
      const body: ReadingPositionIn = {
        chapter_id: chapterId,
        section_id: sectionId,
        offset: s.offset,
        completed: s.completed,
        client_updated_at: new Date().toISOString(),
      };
      if (keepalive) sendOnLeave(body);
      else saveRef.current({ body });
    },
    [chapterId, sectionId, enabled],
  );

  // New section: reset, restore once the content is laid out.
  // biome-ignore lint/correctness/useExhaustiveDependencies: the reset must run whenever the section changes
  useEffect(() => {
    state.current = { offset: 0, sent: -1, completed: false, sentCompleted: false, restored: false };
  }, [sectionId]);

  useEffect(() => {
    const el = root.current;
    const s = state.current;
    if (!ready || !el || s.restored || saved === undefined) return;
    let cancelled = false;
    const target = saved && !saved.completed ? saved.offset : 0;
    s.completed = saved?.completed ?? false;
    s.sentCompleted = s.completed;
    s.sent = saved ? saved.offset : -1;
    const go = async () => {
      try {
        await document.fonts?.ready;
      } catch {
        // fonts failing to load must not block restoring
      }
      if (cancelled) return;
      requestAnimationFrame(() => {
        if (cancelled || !root.current) return;
        if (target > 0) scrollToOffset(root.current, target);
        else window.scrollTo({ top: 0, behavior: "instant" });
        s.offset = target;
        s.restored = true;
        // The first visit records the section as opened, so other devices can resume here.
        if (!saved) flush();
      });
    };
    void go();
    return () => {
      cancelled = true;
    };
  }, [ready, saved, root, flush]);

  // Track scrolling.
  useEffect(() => {
    if (!ready) return;
    let frame = 0;
    const onScroll = () => {
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        const el = root.current;
        if (!el || !state.current.restored) return;
        state.current.offset = measureOffset(el);
        window.clearTimeout(timer.current);
        timer.current = window.setTimeout(() => flush(), SAVE_AFTER_MS);
      });
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      cancelAnimationFrame(frame);
    };
  }, [ready, root, flush]);

  // Reaching the end of the section marks it read.
  useEffect(() => {
    const el = end.current;
    if (!ready || !el || typeof IntersectionObserver === "undefined") return;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting) && state.current.restored && !state.current.completed) {
          state.current.completed = true;
          flush();
        }
      },
      { threshold: 0.01 },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [ready, end, flush]);

  // Leaving: section change / unmount, tab hidden, page closed.
  useEffect(() => {
    const onHide = () => {
      if (document.visibilityState === "hidden") flush(true);
    };
    const onPageHide = () => flush(true);
    const onOnline = () => flush();
    document.addEventListener("visibilitychange", onHide);
    window.addEventListener("pagehide", onPageHide);
    window.addEventListener("online", onOnline);
    return () => {
      document.removeEventListener("visibilitychange", onHide);
      window.removeEventListener("pagehide", onPageHide);
      window.removeEventListener("online", onOnline);
      flush();
    };
  }, [flush]);

  return { flush, saving: save.isPending, saveError: save.error };
}
