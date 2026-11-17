import { type ContentDoc, ContentDocSchema } from "@naadi/shared/content";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, isConflict, type Revision } from "../api";

export type SaveState =
  | { kind: "clean" }
  | { kind: "dirty" }
  | { kind: "saving" }
  | { kind: "saved"; at: string }
  | { kind: "invalid"; path: string }
  | { kind: "conflict" }
  | { kind: "error"; error: unknown };

export const AUTOSAVE_MS = 1500;

/** The first failing node path of a document (e.g. "content.3.attrs.mediaId"), or null when it is valid. */
export function invalidPath(doc: unknown): string | null {
  const r = ContentDocSchema.safeParse(doc);
  if (r.success) return null;
  const issue = r.error.issues[0];
  return issue ? ["body", ...issue.path.map(String)].join(".") : "body";
}

export interface DraftMeta {
  id: number;
  updated_at: string;
  parent_revision_id: number | null;
}

/**
 * Debounced draft autosave with optimistic concurrency. One PUT at a time; edits made while a save is in flight
 * are saved right after it. A 409 stops autosaving (`conflict`) until the caller resolves it; any other failure
 * keeps the edit pending and shows `error` (retry with `flush`). Nothing is sent for an invalid document.
 */
export function useAutosave(sectionId: string, initialDraft: Revision | null, liveRevisionId: number | null) {
  const [state, setState] = useState<SaveState>({ kind: "clean" });
  const draft = useRef<DraftMeta | null>(
    initialDraft
      ? {
          id: initialDraft.id,
          updated_at: initialDraft.updated_at,
          parent_revision_id: initialDraft.parent_revision_id,
        }
      : null,
  );
  const live = useRef(liveRevisionId);
  live.current = liveRevisionId;
  const pending = useRef<ContentDoc | null>(null);
  const inFlight = useRef<Promise<void> | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const blocked = useRef(false);
  const mounted = useRef(true);
  const set = useCallback((s: SaveState) => {
    if (mounted.current) setState(s);
  }, []);

  const save = useCallback(async (): Promise<void> => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    while (inFlight.current) await inFlight.current;
    const doc = pending.current;
    if (!doc || blocked.current) return;
    const bad = invalidPath(doc);
    if (bad) {
      set({ kind: "invalid", path: bad });
      return;
    }
    pending.current = null;
    set({ kind: "saving" });
    let failed = false;
    const run = (async () => {
      try {
        const rev = await api.putDraft(sectionId, {
          body: doc as unknown as Record<string, unknown>,
          parent_revision_id: draft.current?.parent_revision_id ?? live.current,
          expected_updated_at: draft.current?.updated_at ?? null,
        });
        draft.current = { id: rev.id, updated_at: rev.updated_at, parent_revision_id: rev.parent_revision_id };
        if (!pending.current) set({ kind: "saved", at: rev.updated_at });
      } catch (err) {
        // Keep the newest edit pending: the next save (retry or a newer edit) sends it.
        pending.current = pending.current ?? doc;
        failed = true;
        if (isConflict(err)) {
          blocked.current = true;
          set({ kind: "conflict" });
        } else {
          set({ kind: "error", error: err });
        }
      }
    })();
    inFlight.current = run;
    await run;
    inFlight.current = null;
    // An edit made during a successful save is saved next; after a failure the state stays "error" until the next
    // edit or a retry (no automatic hammering while offline).
    if (pending.current && !blocked.current && !failed) {
      set({ kind: "dirty" });
      timer.current = setTimeout(() => void save(), AUTOSAVE_MS);
    }
  }, [sectionId, set]);

  /** Record an edit; it is saved after AUTOSAVE_MS of quiet. */
  const change = useCallback(
    (doc: ContentDoc) => {
      pending.current = doc;
      if (blocked.current) return;
      set({ kind: "dirty" });
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => void save(), AUTOSAVE_MS);
    },
    [save, set],
  );

  /** Save now (before publish, on retry). Resolves with the draft that holds the latest edit, or null. */
  const flush = useCallback(async (): Promise<DraftMeta | null> => {
    await save();
    return pending.current ? null : draft.current;
  }, [save]);

  /**
   * After a conflict, a rollback, a publish or a discard: adopt the server's draft (null = none) as the base.
   * `keepPending` re-sends the local edit on top of it ("keep mine").
   */
  const reset = useCallback(
    (next: Revision | null, opts: { keepPending?: boolean; state?: SaveState } = {}) => {
      draft.current = next
        ? { id: next.id, updated_at: next.updated_at, parent_revision_id: next.parent_revision_id }
        : null;
      blocked.current = false;
      if (!opts.keepPending) pending.current = null;
      set(opts.state ?? { kind: "clean" });
      if (opts.keepPending && pending.current) void save();
    },
    [save, set],
  );

  const hasUnsaved = () => pending.current !== null || inFlight.current !== null;

  useEffect(() => {
    mounted.current = true;
    const warn = (e: BeforeUnloadEvent) => {
      if (pending.current || inFlight.current) e.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => {
      mounted.current = false;
      window.removeEventListener("beforeunload", warn);
      if (timer.current) clearTimeout(timer.current);
      // Leaving the editor with an edit still waiting: send it now rather than lose it.
      if (pending.current && !blocked.current) void save();
    };
  }, [save]);

  return { state, change, flush, reset, draft, hasUnsaved };
}
