import {
  createNote,
  deleteNote,
  getNote,
  listNotes,
  type NoteCreate,
  type NoteList,
  type NoteOut,
  type NotePatch,
  updateNote,
} from "@naadi/shared";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useSyncExternalStore } from "react";
import { authed } from "../auth/api";
import { ApiError, UNREACHABLE } from "../auth/apiError";
import { AUTH_STORAGE_KEY, useAuth } from "../auth/session";
import { useOnlineStatus } from "../health/useOnlineStatus";
import { installOfflineCacheReset, readOffline, writeOffline } from "../learn/offlineCache";
import {
  flushOps,
  loadOps,
  type NoteFields,
  type NoteOp,
  type NotesTransport,
  noteOpsVersion,
  overlay,
  pendingOps,
  queueCreate,
  queueDelete,
  queueUpdate,
  type SendResult,
  subscribeNoteOps,
} from "./outbox";
import type { NoteFilters } from "./types";

installOfflineCacheReset();

export type Note = NoteOut;

export function useUserId(): string | null {
  const auth = useAuth();
  return auth.status === "signed-in" ? auth.session.user.id : null;
}

/** A failure a retry can fix: no answer, the server down or busy, or a session being refreshed. */
export function isRetryable(e: unknown): boolean {
  return e instanceof ApiError && (e.status === null || e.status >= 500 || e.status === 429 || e.status === 401);
}

const KNOWN = new Set([
  "note_not_found",
  "note_question_not_attempted",
  "note_anchor_not_found",
  "note_body_too_large",
  "note_invalid",
  "note_body_invalid",
  "note_conflict",
  "note_section_has_note",
  "note_id_conflict",
  "note_limit",
  "notes_unavailable",
]);

/** The ui_copy key for a Notes API error (`notes.error.<code>`), else a generic one. */
export function errorKey(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.code && KNOWN.has(e.code)) return `notes.error.${e.code}`;
    if (e.status === 422) return "notes.error.note_invalid";
    if (e.status === null || e.status === 503) return "notes.error.notes_unavailable";
  }
  return "notes.error.generic";
}

/** The refusal code of an entry the server will not take, as a ui_copy key. */
export function refusedKey(code: string): string {
  return KNOWN.has(code) ? `notes.error.${code}` : "notes.error.generic";
}

/** Known offline: do not even try (no failed request, no console noise); the write waits on the device. */
function assertOnline(): void {
  if (typeof navigator !== "undefined" && navigator.onLine === false) throw new ApiError(UNREACHABLE, null, null, null);
}

function outcome(e: unknown): SendResult {
  if (isRetryable(e) || !(e instanceof ApiError)) return "unreachable";
  if (e.status === 404 && e.code === "note_not_found") return "gone";
  return { refused: e.code ?? `http_${e.status}` };
}

/** The Notes API through the generated SDK (validated against the OpenAPI contract). */
export const apiTransport: NotesTransport = {
  async create(id, fields) {
    try {
      assertOnline();
      const out = await authed<NoteOut>((headers) => createNote({ headers, body: { ...(fields as NoteCreate), id } }));
      // Another device made this section's page note first: the server answers with that note. Keep both texts
      // (theirs, then this device's) instead of losing what was typed here.
      const mine = (fields.body as { content?: unknown } | undefined)?.content;
      if (out.id !== id && Array.isArray(mine) && mine.length) {
        const prior = (out.body as { content?: unknown } | null)?.content;
        const theirs = Array.isArray(prior) ? prior : [];
        await authed<NoteOut>((headers) =>
          updateNote({
            headers,
            path: { note_id: out.id },
            body: { body: { type: "doc", content: [...theirs, ...mine] } } as NotePatch,
          }),
        );
      }
      return "ok";
    } catch (e) {
      return outcome(e);
    }
  },
  async update(id, fields) {
    try {
      assertOnline();
      await authed<NoteOut>((headers) => updateNote({ headers, path: { note_id: id }, body: fields as NotePatch }));
      return "ok";
    } catch (e) {
      return outcome(e);
    }
  },
  async remove(id) {
    try {
      assertOnline();
      await authed<unknown>((headers) => deleteNote({ headers, path: { note_id: id } }));
      return "ok";
    } catch (e) {
      return outcome(e);
    }
  },
};

/** Bodies a keepalive request may carry (the browser's limit is 64 KB for all of a page's keepalive requests). */
const BEACON_MAX_BYTES = 60_000;
const beaconed = new Set<string>();

function storedToken(): string | null {
  try {
    const raw = localStorage.getItem(AUTH_STORAGE_KEY);
    const token = raw ? (JSON.parse(raw) as { access_token?: unknown }).access_token : null;
    return typeof token === "string" ? token : null;
  } catch {
    return null;
  }
}

/**
 * The page is going away (`pagehide`): send this user's unsent entries with keepalive requests, synchronously, so
 * a note typed a moment before closing the tab or reloading still arrives. The entries stay queued on the device
 * either way and are sent again later; every request is idempotent (create by id, PATCH of the same fields,
 * DELETE of a gone note). `fetch` directly: the SDK resolves the token asynchronously, too late for an unload.
 */
export function beaconNotes(userId: string | null): void {
  if (!userId || (typeof navigator !== "undefined" && navigator.onLine === false)) return;
  const token = storedToken();
  if (!token) return;
  let budget = BEACON_MAX_BYTES;
  for (const op of pendingOps(userId)) {
    if (op.refused) continue;
    const key = `${op.id}:${op.v}`;
    if (beaconed.has(key)) continue;
    const method = op.deleted ? "DELETE" : op.create ? "POST" : "PATCH";
    const url = op.create && !op.deleted ? "/api/notes" : `/api/notes/${encodeURIComponent(op.id)}`;
    const body = op.deleted ? undefined : JSON.stringify(op.create ? { ...op.fields, id: op.id } : op.fields);
    const size = body?.length ?? 0;
    if (size > budget) continue;
    budget -= size;
    beaconed.add(key);
    try {
      void fetch(url, {
        method,
        keepalive: true,
        headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
        body,
      }).catch(() => undefined);
    } catch {
      // the browser refused the keepalive request: the entry is sent on the next visit
    }
  }
}

/** This user's unsent entries, kept current. */
export function usePendingOps(): NoteOp[] {
  const userId = useUserId();
  const version = useSyncExternalStore(subscribeNoteOps, noteOpsVersion, noteOpsVersion);
  // biome-ignore lint/correctness/useExhaustiveDependencies: `version` is the change signal of the store
  return useMemo(() => (userId ? pendingOps(userId) : []), [userId, version]);
}

const listKey = (userId: string | null, f: NoteFilters) =>
  [
    "notes",
    userId,
    f.kind ?? null,
    f.color ?? null,
    f.anchor_type ?? null,
    f.anchor_id ?? null,
    f.notebook_id ?? null,
    f.q ?? null,
    f.limit ?? null,
  ] as const;

function offlineId(f: NoteFilters): string {
  return JSON.stringify(listKey(null, f).slice(2));
}

export interface NotesResult {
  data: NoteList;
  /** The network failed and this is the copy saved on this device. */
  fromCache: boolean;
}

/** Does a locally made note belong in a list with these filters? */
function matches(f: NoteFilters, fields: NoteFields): boolean {
  if (f.q) return false;
  if (f.kind && fields.kind !== f.kind) return false;
  if (f.color && fields.highlight_color !== f.color) return false;
  if (f.anchor_type && fields.anchor_type !== f.anchor_type) return false;
  if (f.anchor_id && fields.anchor_id !== f.anchor_id) return false;
  if (f.notebook_id && fields.notebook_id !== f.notebook_id) return false;
  return true;
}

/** A locally made note in the list's shape until the server has it (no source chip yet). */
function localNote(id: string, fields: NoteFields): Note {
  const now = new Date().toISOString();
  return {
    id,
    kind: (fields.kind as Note["kind"]) ?? "note",
    title: (fields.title as string | null) ?? null,
    notebook_id: (fields.notebook_id as string | null) ?? null,
    anchor_type: (fields.anchor_type as Note["anchor_type"]) ?? null,
    anchor_id: (fields.anchor_id as string | null) ?? null,
    anchor_pos: (fields.anchor_pos as Note["anchor_pos"]) ?? null,
    highlight_color: (fields.highlight_color as Note["highlight_color"]) ?? null,
    body: (fields.body as Note["body"]) ?? null,
    body_omitted: false,
    body_text: "",
    excerpt: "",
    paper: null,
    canvas: "paper",
    sort: 0,
    source: null,
    has_ink: false,
    created_at: (fields.created_at as string) ?? now,
    updated_at: (fields.updated_at as string) ?? now,
  } as Note;
}

/**
 * GET /api/notes with these filters, with this device's unsent changes applied on top. When the network fails the
 * last copy saved on this device is shown (`fromCache`); a list never seen before shows the error.
 */
export function useNotes(filters: NoteFilters, opts: { enabled?: boolean } = {}) {
  const userId = useUserId();
  const online = useOnlineStatus();
  const ops = usePendingOps();
  const query = useQuery({
    queryKey: listKey(userId, filters),
    enabled: userId !== null && opts.enabled !== false,
    staleTime: 15_000,
    retry: (n, e) => n < 1 && isRetryable(e) && online,
    queryFn: async ({ signal }): Promise<NotesResult> => {
      const id = offlineId(filters);
      try {
        assertOnline();
        const data = await authed<NoteList>((headers) =>
          listNotes({
            signal,
            headers,
            query: {
              kind: filters.kind,
              color: filters.color,
              anchor_type: filters.anchor_type,
              anchor_id: filters.anchor_id,
              notebook_id: filters.notebook_id,
              q: filters.q || undefined,
              limit: filters.limit ?? 100,
            },
          }),
        );
        if (userId) void writeOffline(userId, "notes", id, data);
        return { data, fromCache: false };
      } catch (e) {
        if (userId && isRetryable(e)) {
          const saved = await readOffline<NoteList>(userId, "notes", id);
          if (saved) return { data: saved, fromCache: true };
        }
        throw e;
      }
    },
  });

  const items = useMemo(() => {
    const base = query.data?.data.items ?? [];
    const merged = overlay(
      base,
      ops.filter((o) => !o.refused),
      (fields) => matches(filters, fields),
    );
    return merged.map((n) => (n.created_at ? n : localNote(n.id, n as unknown as NoteFields)));
  }, [query.data, ops, filters]);

  return { ...query, items, fromCache: query.data?.fromCache ?? false, counts: query.data?.data.counts ?? null };
}

/** Keeps this user's queue moving: read it from the device, send it now, on reconnect and every 30 s while pending. */
export function useNotesSync(transport: NotesTransport = apiTransport) {
  const userId = useUserId();
  const qc = useQueryClient();
  const online = useOnlineStatus();
  const ops = usePendingOps();
  const pending = ops.filter((o) => !o.refused).length;

  const flush = useCallback(async () => {
    if (!userId) return;
    await loadOps(userId);
    const cleared = await flushOps(userId, transport);
    if (cleared > 0) await qc.invalidateQueries({ queryKey: ["notes", userId] });
  }, [userId, transport, qc]);

  useEffect(() => {
    if (online) void flush();
  }, [online, flush]);

  useEffect(() => {
    const onHide = () => beaconNotes(userId);
    window.addEventListener("pagehide", onHide);
    return () => window.removeEventListener("pagehide", onHide);
  }, [userId]);

  useEffect(() => {
    if (!online || pending === 0) return;
    const id = window.setInterval(() => void flush(), 30_000);
    return () => window.clearInterval(id);
  }, [online, pending, flush]);

  return { flush, pending, refused: ops.filter((o) => o.refused) };
}

export interface NoteWriter {
  create(fields: NoteFields): string;
  update(id: string, patch: NoteFields): void;
  remove(id: string): void;
}

/** Write notes: recorded on the device first, then sent (see outbox.ts). Returns ids at once (made here). */
export function useNoteWriter(transport: NotesTransport = apiTransport): NoteWriter {
  const userId = useUserId();
  const qc = useQueryClient();
  return useMemo(() => {
    const kick = () => {
      if (!userId) return;
      void flushOps(userId, transport).then((n) => {
        if (n > 0) void qc.invalidateQueries({ queryKey: ["notes", userId] });
      });
    };
    return {
      create(fields) {
        const id = crypto.randomUUID();
        if (userId) void queueCreate(userId, id, { ...fields }).then(kick);
        return id;
      },
      update(id, patch) {
        if (userId) void queueUpdate(userId, id, patch).then(kick);
      },
      remove(id) {
        if (userId) void queueDelete(userId, id).then(kick);
      },
    };
  }, [userId, transport, qc]);
}

/** One note by id (a search hit or a link to a note outside the loaded list). */
export function useNote(id: string | undefined, enabled: boolean) {
  const userId = useUserId();
  return useQuery({
    queryKey: ["notes", userId, "one", id ?? null],
    enabled: enabled && userId !== null && Boolean(id),
    staleTime: 15_000,
    retry: (n, e) => n < 1 && isRetryable(e),
    queryFn: ({ signal }) =>
      authed<NoteOut>((headers) => getNote({ signal, headers, path: { note_id: id as string } })),
  });
}
