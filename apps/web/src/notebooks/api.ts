/**
 * Notebooks on the web: TanStack queries over the generated SDK (validated against the OpenAPI contract), a
 * device copy for offline reloads, and every write going through the outbox (outbox.ts) so it survives a dead
 * network, a reload or a closed tab. The views read `overlay*` of server data + queue, so an edit shows at once.
 */
import {
  createNotebook,
  createNotebookPage,
  deleteNote,
  deleteNotebook,
  getNote,
  getNotebook,
  listNotebooks,
  type NotebookDetail,
  type NotebookList,
  type NotebookOut,
  type NoteOut,
  reorderNotebookPages,
  updateNote,
  updateNotebook,
} from "@naadi/shared";
import { type QueryClient, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useSyncExternalStore } from "react";
import { authed } from "../auth/api";
import { ApiError } from "../auth/apiError";
import { onAuthChange, useAuth } from "../auth/session";
import { useOnlineStatus } from "../health/useOnlineStatus";
import {
  clearNotebookSnapshots,
  dexieOutbox,
  flushOutbox,
  type NbOp,
  type OutboxStore,
  opNotebook,
  type QueuedOp,
  type SendOutcome,
} from "./outbox";
import { overlayDetail, overlayList, overlayNote } from "./overlay";
import { prepareSceneForUpload } from "./whiteboard/files";

export function useUserId(): string | null {
  const auth = useAuth();
  return auth.status === "signed-in" ? auth.session.user.id : null;
}

export const notebooksKey = (u: string | null) => ["notebooks", u] as const;
export const notebookKey = (u: string | null, id: string) => ["notebook", u, id] as const;
export const noteKey = (u: string | null, id: string) => ["notebook-note", u, id] as const;

/** No HTTP answer, a gateway error or rate limiting: the edit waits on the device and is sent later. */
export function isRetryable(err: unknown): boolean {
  if (!(err instanceof ApiError)) return true;
  return err.status === null || err.status === 401 || err.status === 429 || err.status >= 500;
}

export function isUnreachable(err: unknown): boolean {
  return (
    err instanceof ApiError && (err.status === null || err.status === 502 || err.status === 503 || err.status === 504)
  );
}

/** The API error codes worded as `notebooks.error.<code>`. */
export const NOTEBOOK_ERROR_CODES: ReadonlySet<string> = new Set([
  "notebook_not_found",
  "note_not_found",
  "notebook_limit",
  "notebook_page_limit",
  "note_limit",
  "note_body_too_large",
  "note_body_invalid",
  "notebook_pages_changed",
  "note_id_conflict",
  "notebook_id_conflict",
  "notes_unavailable",
  "note_anchor_not_found",
  "rate_limited",
]);

/** The ui_copy key for an error: `notebooks.error.<code>` for the API's codes, else offline/generic. */
export function errorKey(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.code && NOTEBOOK_ERROR_CODES.has(err.code)) return `notebooks.error.${err.code}`;
    if (err.status === 422) return "notebooks.error.invalid";
    if (isUnreachable(err)) return "notebooks.error.unreachable";
  }
  return "notebooks.error.generic";
}

// ── Sending one queued op ──

async function sendOp(userId: string, op: NbOp): Promise<void> {
  switch (op.kind) {
    case "createNotebook":
      await authed<NotebookDetail>((headers) => createNotebook({ body: op.body, headers }));
      return;
    case "patchNotebook":
      await authed<NotebookDetail>((headers) =>
        updateNotebook({ body: op.patch, headers, path: { notebook_id: op.id } }),
      );
      return;
    case "deleteNotebook":
      await authed<unknown>((headers) => deleteNotebook({ headers, path: { notebook_id: op.id } }));
      return;
    case "createPage": {
      const body = op.body.body
        ? { ...op.body, body: await prepareSceneForUpload(userId, op.id, op.body.body) }
        : op.body;
      await authed<NoteOut>((headers) => createNotebookPage({ body, headers, path: { notebook_id: op.notebookId } }));
      return;
    }
    case "patchNote": {
      const patch = op.patch.body
        ? { ...op.patch, body: await prepareSceneForUpload(userId, op.id, op.patch.body) }
        : op.patch;
      await authed<NoteOut>((headers) => updateNote({ body: patch, headers, path: { note_id: op.id } }));
      return;
    }
    case "deleteNote":
      await authed<unknown>((headers) => deleteNote({ headers, path: { note_id: op.id } }));
      return;
    case "reorder":
      await authed<NotebookDetail>((headers) =>
        reorderNotebookPages({ body: { page_ids: op.pageIds }, headers, path: { notebook_id: op.notebookId } }),
      );
      return;
  }
}

/** Gone already counts as done for a delete; anything else the server refused is dropped and reported. */
function outcomeOf(op: NbOp, err: unknown): { outcome: SendOutcome; code: string | null } {
  if (isRetryable(err)) return { outcome: "retry", code: null };
  const e = err as ApiError;
  if ((op.kind === "deleteNote" || op.kind === "deleteNotebook") && e.status === 404)
    return { outcome: "sent", code: null };
  return { outcome: "dropped", code: errorKey(err) };
}

// ── The per-user sync object (queue mirror, flushing, save state) ──

export type NotebookSaveState = "saved" | "saving" | "local" | "error";

export class NotebookSync {
  queue: QueuedOp[] = [];
  state: NotebookSaveState = "saved";
  /** ui_copy key of the last refused edit (shown until the next successful save). */
  lastError: string | null = null;
  ready = false;
  private inflight = new Set<number>();
  private flushing: Promise<void> | null = null;
  private again = false;
  private listeners = new Set<() => void>();
  private timer: ReturnType<typeof setTimeout> | null = null;

  constructor(
    readonly userId: string,
    readonly store: OutboxStore,
    private readonly send: (op: NbOp) => Promise<void>,
    private readonly onSettled: (ops: NbOp[]) => void,
    /** Called the moment the server confirmed an op, before it leaves the queue (no flicker in between). */
    private readonly onSent: (op: NbOp) => void = () => undefined,
  ) {}

  subscribe = (l: () => void) => {
    this.listeners.add(l);
    return () => this.listeners.delete(l);
  };
  private emit() {
    for (const l of this.listeners) l();
  }

  async load(): Promise<void> {
    try {
      this.queue = await this.store.read();
    } catch {
      this.queue = [];
    }
    this.ready = true;
    this.state = this.queue.length ? "local" : "saved";
    this.emit();
  }

  /** Queue an edit (written to IndexedDB before any request) and send it soon. */
  async add(op: NbOp): Promise<void> {
    await this.store.add(op, this.inflight);
    this.queue = await this.store.read();
    this.state = "saving";
    this.emit();
    this.schedule(0);
  }

  schedule(ms: number): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => {
      this.timer = null;
      void this.flush();
    }, ms);
  }

  flush(): Promise<void> {
    if (this.flushing) {
      this.again = true;
      return this.flushing;
    }
    if (typeof navigator !== "undefined" && navigator.onLine === false) {
      this.state = this.queue.length ? "local" : "saved";
      this.emit();
      return Promise.resolve();
    }
    this.flushing = (async () => {
      do {
        this.again = false;
        if (!this.queue.length) break;
        this.state = "saving";
        this.emit();
        let refused: string | null = null;
        const result = await flushOutbox(this.store, this.inflight, async (op) => {
          try {
            await this.send(op);
            this.onSent(op);
            return "sent";
          } catch (err) {
            const { outcome, code } = outcomeOf(op, err);
            if (code) refused = code;
            return outcome;
          }
        });
        this.queue = await this.store.read();
        const settled = [...result.done, ...result.dropped];
        if (settled.length) this.onSettled(settled);
        if (refused) this.lastError = refused;
        else if (result.done.length) this.lastError = null;
        if (result.stopped) {
          this.state = "local";
          this.emit();
          // Back off; the `online` event and the next edit flush sooner.
          this.schedule(15_000);
          break;
        }
        this.state = this.lastError ? "error" : "saved";
        this.emit();
      } while (this.again);
    })().finally(() => {
      this.flushing = null;
    });
    return this.flushing;
  }

  /** The student read the refusal: back to the plain save state. */
  dismissError(): void {
    this.lastError = null;
    this.state = this.queue.length ? "local" : "saved";
    this.emit();
    if (this.queue.length) void this.flush();
  }

  /** Tests and sign-out: stop the timers. */
  stop(): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
  }
}

const syncs = new Map<string, NotebookSync>();
let resetInstalled = false;

function installReset(): void {
  if (resetInstalled) return;
  resetInstalled = true;
  onAuthChange((event, prev, next) => {
    if (event === "SIGNED_OUT" || (prev && next && prev !== next)) {
      for (const s of syncs.values()) s.stop();
      syncs.clear();
      void clearNotebookSnapshots();
    }
  });
}

/**
 * Fold a confirmed op into the cached server data at once, so the view never shows the gap between the op leaving
 * the queue and the refetch arriving (a new page would vanish and the route clamp back for a moment).
 */
function applyToCache(qc: QueryClient, userId: string, op: NbOp): void {
  const q: QueuedOp[] = [{ seq: 0, op, queuedAt: Date.now() }];
  qc.setQueryData<FromDevice<NotebookList>>(notebooksKey(userId), (old) =>
    old ? { ...old, data: { ...old.data, items: overlayList(old.data.items, q) } } : old,
  );
  const nb = opNotebook(op);
  qc.setQueryData<FromDevice<NotebookDetail | null>>(notebookKey(userId, nb), (old) =>
    old
      ? { ...old, data: overlayDetail(nb, old.data, q) }
      : op.kind === "createNotebook"
        ? { data: overlayDetail(nb, null, q), fromDevice: false }
        : old,
  );
  if (op.kind === "patchNote" || op.kind === "createPage")
    qc.setQueryData<FromDevice<NoteOut>>(noteKey(userId, op.id), (old) => {
      const next = old ? overlayNote(op.id, old.data, q) : null;
      return old && next ? { ...old, data: next } : old;
    });
}

function invalidateFor(qc: QueryClient, userId: string, ops: readonly NbOp[]): void {
  void qc.invalidateQueries({ queryKey: notebooksKey(userId) });
  for (const id of new Set(ops.map(opNotebook))) void qc.invalidateQueries({ queryKey: notebookKey(userId, id) });
  for (const op of ops)
    if (op.kind === "patchNote" || op.kind === "createPage")
      void qc.invalidateQueries({ queryKey: noteKey(userId, op.id) });
}

export function notebookSync(userId: string, qc: QueryClient): NotebookSync {
  installReset();
  let s = syncs.get(userId);
  if (!s) {
    s = new NotebookSync(
      userId,
      dexieOutbox(userId),
      (op) => sendOp(userId, op),
      (ops) => invalidateFor(qc, userId, ops),
      (op) => applyToCache(qc, userId, op),
    );
    syncs.set(userId, s);
    void s.load().then(() => s?.flush());
  }
  return s;
}

/** The sync object for the signed-in user, re-rendering on every queue or state change. */
export function useNotebookSync(): NotebookSync | null {
  const userId = useUserId();
  const qc = useQueryClient();
  const online = useOnlineStatus();
  const sync = userId ? notebookSync(userId, qc) : null;
  const subscribe = useCallback((l: () => void) => (sync ? sync.subscribe(l) : () => undefined), [sync]);
  useSyncExternalStore(
    subscribe,
    () =>
      sync ? `${sync.ready}:${sync.state}:${sync.queue.length}:${sync.queue.at(-1)?.seq ?? 0}:${sync.lastError}` : "",
    () => "",
  );
  useEffect(() => {
    if (online && sync?.queue.length) void sync.flush();
  }, [online, sync]);
  return sync;
}

// ── Reads (server, else the device copy) ──

export interface FromDevice<T> {
  data: T;
  /** The network failed and this is the copy saved on this device. */
  fromDevice: boolean;
}

async function withDeviceCopy<T>(store: OutboxStore, key: string, fetcher: () => Promise<T>): Promise<FromDevice<T>> {
  try {
    const data = await fetcher();
    void store.writeSnapshot(key, data).catch(() => undefined);
    return { data, fromDevice: false };
  } catch (err) {
    if (isUnreachable(err) || !(err instanceof ApiError)) {
      const saved = await store.readSnapshot<T>(key).catch(() => undefined);
      if (saved !== undefined) return { data: saved, fromDevice: true };
    }
    throw err;
  }
}

export function useNotebooks() {
  const userId = useUserId();
  const sync = useNotebookSync();
  const query = useQuery({
    queryKey: notebooksKey(userId),
    // Offline the device copy answers (withDeviceCopy): never pause the query waiting for a connection.
    networkMode: "always",
    enabled: userId !== null && sync !== null,
    // A device copy is stale at once, so reconnecting (refetchOnReconnect) replaces it with the server's.
    staleTime: (q: { state: { data?: unknown } }) =>
      (q.state.data as FromDevice<unknown> | undefined)?.fromDevice ? 0 : 15_000,
    retry: (n, err) => n < 1 && !isUnreachable(err),
    queryFn: ({ signal }) =>
      withDeviceCopy((sync as NotebookSync).store, "list", () =>
        authed<NotebookList>((headers) => listNotebooks({ signal, headers })),
      ),
  });
  const items = useMemo<NotebookOut[] | undefined>(
    () => (query.data && sync ? overlayList(query.data.data.items, sync.queue) : undefined),
    [query.data, sync, sync?.queue],
  );
  // Offline with no device copy, the queue alone still lists notebooks made on this device.
  const local = useMemo(
    () => (sync && !query.data && query.isError ? overlayList([], sync.queue) : undefined),
    [query.data, query.isError, sync, sync?.queue],
  );
  return { query, items: items ?? (local?.length ? local : undefined), fromDevice: query.data?.fromDevice ?? false };
}

export function useNotebook(id: string) {
  const userId = useUserId();
  const sync = useNotebookSync();
  // Made on this device and its create not confirmed yet: the queue is the only copy, the server has none.
  const creating = sync?.queue.some((q) => q.op.kind === "createNotebook" && q.op.id === id) ?? false;
  const query = useQuery({
    queryKey: notebookKey(userId, id),
    networkMode: "always",
    enabled: userId !== null && sync !== null && !creating,
    staleTime: (q: { state: { data?: unknown } }) =>
      (q.state.data as FromDevice<unknown> | undefined)?.fromDevice ? 0 : 10_000,
    retry: (n, err) => n < 1 && !isUnreachable(err) && !(err instanceof ApiError && err.status === 404),
    queryFn: async ({ signal }) => {
      try {
        return await withDeviceCopy((sync as NotebookSync).store, `nb:${id}`, () =>
          authed<NotebookDetail>((headers) => getNotebook({ signal, headers, path: { notebook_id: id } })),
        );
      } catch (err) {
        // Made on this device and not sent yet: the queue holds it.
        if (
          err instanceof ApiError &&
          err.status === 404 &&
          sync?.queue.some((q) => q.op.kind === "createNotebook" && q.op.id === id)
        )
          return { data: null, fromDevice: false };
        throw err;
      }
    },
  });
  // Back online with a device copy on screen: load the server's (the reconnect event may come before React
  // re-enables the query, so do it here too).
  const online = useOnlineStatus();
  const refetch = query.refetch;
  const stale = query.data?.fromDevice ?? false;
  useEffect(() => {
    if (online && stale) void refetch();
  }, [online, stale, refetch]);
  const detail = useMemo(
    () =>
      sync && (query.data || query.isError || creating)
        ? overlayDetail(id, query.data?.data ?? null, sync.queue)
        : undefined,
    [id, query.data, query.isError, creating, sync, sync?.queue],
  );
  return { query, detail, fromDevice: query.data?.fromDevice ?? false };
}

/** One page with its body (whiteboards: the lists omit their scene). */
export function usePageNote(page: NoteOut | null) {
  const userId = useUserId();
  const sync = useNotebookSync();
  const id = page?.id ?? "";
  const needsFetch = page !== null && (page.body_omitted ?? false);
  const query = useQuery({
    queryKey: noteKey(userId, id),
    networkMode: "always",
    enabled: userId !== null && sync !== null && needsFetch,
    staleTime: 0,
    gcTime: 60_000,
    refetchOnWindowFocus: false,
    retry: (n, err) => n < 1 && !isUnreachable(err),
    queryFn: ({ signal }) =>
      withDeviceCopy((sync as NotebookSync).store, `note:${id}`, () =>
        authed<NoteOut>((headers) => getNote({ signal, headers, path: { note_id: id } })),
      ),
  });
  const note = useMemo(() => {
    if (!page || !sync) return undefined;
    if (!needsFetch) return overlayNote(id, page, sync.queue);
    if (!query.data && !query.isError) return undefined;
    // A page made offline has no server copy: the queued create carries its body.
    return overlayNote(id, query.data?.data ?? (query.isError ? null : page), sync.queue);
  }, [page, sync, sync?.queue, needsFetch, id, query.data, query.isError]);
  return { query, note, fromDevice: query.data?.fromDevice ?? false };
}
