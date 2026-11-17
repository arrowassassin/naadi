import { useEffect, useRef, useSyncExternalStore } from "react";
import { MAX_TITLE_CHARS, type PageContextIn, type PageType } from "./protocol";

/**
 * Page context (HANDOFF §6.3) per route: the page the student is on registers what it shows; the assistant reads
 * the latest registration when it sends. Pages pass ids and a display title only. A question page passes the
 * question id and NEVER whether it was answered or what was chosen: the server checks the attempts.
 *
 * A video timestamp changes every second; pass `readVideo` (called at send time) instead of re-registering.
 */
export interface PageContextRegistration {
  type: PageType;
  id?: string | null;
  title?: string | null;
  chapter_id?: string | null;
  video?: { media_id: string; t: number } | null;
  readVideo?: () => { media_id: string; t: number } | null;
  /** Question pages: the question id (also in `id`). */
  question?: { id: string } | null;
}

interface Entry {
  token: number;
  ctx: PageContextRegistration;
}

let entries: Entry[] = [];
let counter = 0;
const listeners = new Set<() => void>();

function publish(): void {
  for (const l of listeners) l();
}

function current(): PageContextRegistration | null {
  return entries.at(-1)?.ctx ?? null;
}

/** Register (or update) a page's context while mounted; the latest mounted registration is the current one. */
export function useRegisterPageContext(ctx: PageContextRegistration | null): void {
  const token = useRef<number | null>(null);
  const readVideo = useRef(ctx?.readVideo);
  readVideo.current = ctx?.readVideo;
  const key = ctx
    ? JSON.stringify([
        ctx.type,
        ctx.id ?? null,
        ctx.title ?? null,
        ctx.chapter_id ?? null,
        ctx.video ?? null,
        ctx.question?.id ?? null,
        Boolean(ctx.readVideo),
      ])
    : null;
  // biome-ignore lint/correctness/useExhaustiveDependencies: `key` captures every field the registration publishes
  useEffect(() => {
    if (key === null || !ctx) return;
    counter += 1;
    const mine = counter;
    token.current = mine;
    const stable: PageContextRegistration = {
      ...ctx,
      readVideo: ctx.readVideo ? () => readVideo.current?.() ?? null : undefined,
    };
    entries = [...entries, { token: mine, ctx: stable }];
    publish();
    return () => {
      entries = entries.filter((e) => e.token !== mine);
      publish();
    };
  }, [key]);
}

function subscribe(l: () => void): () => void {
  listeners.add(l);
  return () => listeners.delete(l);
}

/** The current page's registration (null on pages that register none). */
export function usePageContext(): PageContextRegistration | null {
  return useSyncExternalStore(subscribe, current, () => null);
}

export function getPageContext(): PageContextRegistration | null {
  return current();
}

function clip(s: string | null | undefined, max: number): string | null {
  if (!s) return null;
  const t = s.trim();
  return t ? Array.from(t).slice(0, max).join("") : null;
}

/** The wire form (§6.3) for a send: route from the location, everything else from the registration. */
export function toPageContextIn(ctx: PageContextRegistration | null, route: string): PageContextIn {
  if (!ctx) return { route, type: "page" };
  const video = ctx.readVideo?.() ?? ctx.video ?? null;
  const out: PageContextIn = { route, type: ctx.type };
  if (ctx.id) out.id = ctx.id;
  const title = clip(ctx.title, MAX_TITLE_CHARS);
  if (title) out.title = title;
  if (ctx.chapter_id) out.chapter_id = ctx.chapter_id;
  if (video?.media_id && Number.isFinite(video.t) && video.t >= 0) {
    out.video = { media_id: video.media_id, t: Math.floor(video.t) };
  }
  const qid = ctx.question?.id ?? (ctx.type === "question" ? ctx.id : null);
  if (qid) out.question = { id: qid };
  return out;
}

/** Tests only. */
export function resetPageContextForTests(): void {
  entries = [];
  publish();
}
