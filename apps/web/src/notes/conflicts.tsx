import type { NoteOut } from "@naadi/shared";
import { cx } from "@naadi/ui";
import { type QueryClient, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { CopyPlus, History, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useSyncExternalStore } from "react";
import { useAuth } from "../auth/session";
import { useUiText } from "../copy/useUiText";
import { noteSeen } from "./editClock";
import { type ConflictHandler, type ConflictRow, type NoteOp, notesDb, queueCreate } from "./outbox";

/**
 * Sync conflicts the student is told about (segment 13 rework: bounded last-writer-wins). When a write made on this
 * device is older than an edit another device already saved, the server keeps the newer copy (409 *_conflict) and
 * this device adopts it. What the student wrote here is never thrown away: a typed note or page note is kept as a
 * new free note ("Conflict copy: …"), a whiteboard page as a new page at the end of its notebook. Highlight colour
 * and delete conflicts just take the server's copy.
 *
 * Each conflict leaves a notice (icon + words, never colour alone; `role="status"`, dismissible, with a link to the
 * kept copy) that survives a reload until dismissed: IndexedDB `naadi-notes`, table `conflicts`, mirrored in memory
 * (a private window still shows it for the life of the page). `ConflictNotices` renders them for one screen;
 * `useConflictNotices()` gives the raw list. `useConflictRevision(id)` changes when a note's server copy was
 * adopted, so an editor keyed on it reloads that copy instead of carrying on from the version that lost.
 */

export type NoteConflictKind = "note_copy" | "note_kept" | "note_delete_kept";
export type NotebookConflictKind =
  | "page_copy"
  | "page_kept"
  | "page_delete_kept"
  | "notebook_kept"
  | "notebook_delete_kept";
export type ConflictKind = NoteConflictKind | NotebookConflictKind;

export interface ConflictNoticeData extends ConflictRow {
  kind: ConflictKind;
  /** The note or page the conflict was about (absent for a notebook's own fields). */
  noteId?: string;
  /** Where it was anchored (a page note: its section), so the Reader's panel can show its own notices. */
  anchorType?: string | null;
  anchorId?: string | null;
  notebookId?: string | null;
  /** The note or page that keeps the student's version. */
  copyId?: string | null;
  /** 1-based number of the kept page (it is added at the end of the notebook). */
  pageNumber?: number | null;
  /** The server copy's title (worded into the message; never sent anywhere). */
  title?: string | null;
}

export interface ConflictFilter {
  scope?: "notes" | "notebooks";
  /** Notices about this note, or whose kept copy it is. */
  noteId?: string;
  /** Leave out the notices `noteId` would match (a screen that shows that note's notices elsewhere). */
  exceptNoteId?: string;
  anchorId?: string;
  notebookId?: string;
}

const NOTEBOOK_KINDS: ReadonlySet<string> = new Set([
  "page_copy",
  "page_kept",
  "page_delete_kept",
  "notebook_kept",
  "notebook_delete_kept",
]);
const MAX_NOTICES = 50;

export function noticeScope(n: Pick<ConflictNoticeData, "kind">): "notes" | "notebooks" {
  return NOTEBOOK_KINDS.has(n.kind) ? "notebooks" : "notes";
}

// ── The notice store ──

const memory = new Map<string, ConflictNoticeData>();
const loaded = new Set<string>();
const listeners = new Set<() => void>();
const revisions = new Map<string, number>();
let version = 0;

function emit(): void {
  version += 1;
  for (const l of listeners) l();
}

function subscribe(l: () => void): () => void {
  listeners.add(l);
  return () => {
    listeners.delete(l);
  };
}

const mkey = (user: string, id: string) => `${user}\u0000${id}`;

/** This user's notices, newest first. */
export function conflictNotices(user: string): ConflictNoticeData[] {
  return [...memory.values()].filter((n) => n.user === user).sort((a, b) => b.at - a.at);
}

/** Read this user's notices from IndexedDB (once per page and user). */
export async function loadConflictNotices(user: string): Promise<void> {
  if (loaded.has(user)) return;
  loaded.add(user);
  try {
    const rows = (await notesDb().conflicts.where("user").equals(user).toArray()) as ConflictNoticeData[];
    for (const r of rows) if (!memory.has(mkey(user, r.id))) memory.set(mkey(user, r.id), r);
    emit();
  } catch {
    // no IndexedDB: the notices made in this page's life are still in memory
  }
}

export async function addConflictNotice(notice: Omit<ConflictNoticeData, "id" | "at"> & { id?: string }) {
  const row = { ...notice, id: notice.id ?? crypto.randomUUID(), at: Date.now() } as ConflictNoticeData;
  memory.set(mkey(row.user, row.id), row);
  const extra = conflictNotices(row.user).slice(MAX_NOTICES);
  for (const n of extra) memory.delete(mkey(n.user, n.id));
  emit();
  try {
    const t = notesDb().conflicts;
    await t.put(row);
    if (extra.length) await t.bulkDelete(extra.map((n) => [n.user, n.id] as unknown as string));
  } catch {
    // no IndexedDB: shown for the life of the page
  }
  return row;
}

export async function dismissConflictNotice(user: string, id: string): Promise<void> {
  memory.delete(mkey(user, id));
  emit();
  try {
    await notesDb().conflicts.delete([user, id] as unknown as string);
  } catch {
    // nothing stored
  }
}

/** A note's server copy was adopted after a conflict: editors keyed on `useConflictRevision(id)` reload it. */
export function bumpConflictRevision(id: string): void {
  revisions.set(id, (revisions.get(id) ?? 0) + 1);
  emit();
}

export function useConflictRevision(id: string | null | undefined): number {
  return useSyncExternalStore(
    subscribe,
    () => (id ? (revisions.get(id) ?? 0) : 0),
    () => 0,
  );
}

/** Test hook. */
export function resetConflictsForTests(): void {
  memory.clear();
  loaded.clear();
  revisions.clear();
  emit();
}

function useUser(): string | null {
  const auth = useAuth();
  return auth.status === "signed-in" ? auth.session.user.id : null;
}

function matches(n: ConflictNoticeData, f: ConflictFilter): boolean {
  if (f.scope && noticeScope(n) !== f.scope) return false;
  if (f.noteId && n.noteId !== f.noteId && n.copyId !== f.noteId) return false;
  if (f.exceptNoteId && (n.noteId === f.exceptNoteId || n.copyId === f.exceptNoteId)) return false;
  if (f.anchorId && n.anchorId !== f.anchorId) return false;
  if (f.notebookId && n.notebookId !== f.notebookId) return false;
  return true;
}

/** The signed-in student's notices for one screen (all of them without a filter), and how to dismiss one. */
export function useConflictNotices(filter: ConflictFilter = {}) {
  const user = useUser();
  useEffect(() => {
    if (user) void loadConflictNotices(user);
  }, [user]);
  const v = useSyncExternalStore(
    subscribe,
    () => version,
    () => 0,
  );
  const { scope, noteId, exceptNoteId, anchorId, notebookId } = filter;
  // biome-ignore lint/correctness/useExhaustiveDependencies: `v` is the store's change signal
  const notices = useMemo(
    () =>
      user
        ? conflictNotices(user).filter((n) => matches(n, { scope, noteId, exceptNoteId, anchorId, notebookId }))
        : [],
    [user, v, scope, noteId, exceptNoteId, anchorId, notebookId],
  );
  const dismiss = useCallback((id: string) => (user ? dismissConflictNotice(user, id) : Promise.resolve()), [user]);
  return { notices, dismiss };
}

// ── Showing a notice ──

function NoticeLink({ notice }: { notice: ConflictNoticeData }) {
  const t = useUiText();
  const cls =
    "inline-flex min-h-11 items-center font-semibold text-text underline underline-offset-2 hover:text-text-2";
  if (noticeScope(notice) === "notes") {
    const target = notice.kind === "note_copy" ? notice.copyId : notice.noteId;
    if (!target) return null;
    return (
      <Link to="/notes" search={{ note: target }} className={cls} data-conflict-link={target}>
        {t(notice.kind === "note_copy" ? "notes.conflict_open_copy" : "notes.conflict_open_note")}
      </Link>
    );
  }
  if (!notice.notebookId) return null;
  const page = notice.kind === "page_copy" && notice.pageNumber ? notice.pageNumber : 1;
  return (
    <Link
      to="/notes/notebooks/$id/$page"
      params={{ id: notice.notebookId, page: String(page) }}
      className={cls}
      data-conflict-link={notice.copyId ?? notice.notebookId}
    >
      {t(notice.kind === "page_copy" ? "notebooks.conflict_open_copy" : "notebooks.conflict_open_notebook")}
    </Link>
  );
}

/** One notice: icon + label + what happened + a link to the kept copy + Dismiss. */
export function ConflictNotice({
  notice,
  onDismiss,
  className,
}: {
  notice: ConflictNoticeData;
  onDismiss: (id: string) => void;
  className?: string;
}) {
  const t = useUiText();
  const scope = noticeScope(notice);
  const copied = notice.kind === "note_copy" || notice.kind === "page_copy";
  const Icon = copied ? CopyPlus : History;
  const vars = { title: notice.title?.trim() || t(`${scope}.conflict_untitled`) };
  return (
    <div
      role="status"
      data-conflict-notice={notice.kind}
      className={cx(
        "flex items-start gap-3 rounded-block-sm border border-line bg-surface-1 px-4 py-3 text-text",
        className,
      )}
    >
      <Icon aria-hidden="true" size={18} className="mt-0.5 shrink-0" />
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <p className="m-0 font-semibold text-ui-sm">{t(`${scope}.conflict_label`)}</p>
        <p className="m-0 text-text-2 text-ui-sm">{t(`${scope}.conflict_${notice.kind}`, vars)}</p>
        <NoticeLink notice={notice} />
      </div>
      <button
        type="button"
        onClick={() => onDismiss(notice.id)}
        aria-label={t(`${scope}.conflict_dismiss`)}
        data-conflict-dismiss=""
        className="flex size-11 shrink-0 items-center justify-center rounded-full border-0 bg-transparent text-text-2 hover:bg-surface-2 hover:text-text"
      >
        <X aria-hidden="true" size={16} />
      </button>
    </div>
  );
}

/** Every notice matching `filter`, stacked (nothing when there are none). */
export function ConflictNotices({ filter, className }: { filter?: ConflictFilter; className?: string }) {
  const { notices, dismiss } = useConflictNotices(filter);
  if (notices.length === 0) return null;
  return (
    <div className={cx("flex flex-col gap-2", className)} data-conflict-notices="">
      {notices.map((n) => (
        <ConflictNotice key={n.id} notice={n} onDismiss={(id) => void dismiss(id)} />
      ))}
    </div>
  );
}

// ── Resolving a note conflict ──

type T = (key: string, vars?: Readonly<Record<string, string>>) => string;

const TITLE_MAX = 200;

/** The title of a conflict copy: "Conflict copy: {title}" (ui_copy), within the API's 200 characters. */
export function conflictCopyTitle(t: T, title: string | null | undefined): string {
  const base = (title ?? "").trim();
  const out = base ? t("notes.conflict_copy_title", { title: base }) : t("notes.conflict_copy_title_untitled");
  return out.length > TITLE_MAX ? `${out.slice(0, TITLE_MAX - 1)}…` : out;
}

interface NotesCacheEntry {
  data: { items: NoteOut[] };
  fromCache: boolean;
}

function isListEntry(v: unknown): v is NotesCacheEntry {
  return Boolean(v && typeof v === "object" && Array.isArray((v as NotesCacheEntry).data?.items));
}

/** Put the server's copy of a note into every cached Notes list and single-note query of this user. */
export function adoptNote(qc: QueryClient, userId: string, current: NoteOut): void {
  noteSeen(current.id, current.edited_at);
  qc.setQueriesData<unknown>({ queryKey: ["notes", userId] }, (old: unknown) => {
    if (isListEntry(old)) {
      if (!old.data.items.some((n) => n.id === current.id)) return old;
      return {
        ...old,
        data: { ...old.data, items: old.data.items.map((n) => (n.id === current.id ? current : n)) },
      };
    }
    if (old && typeof old === "object" && (old as NoteOut).id === current.id) return current;
    return old;
  });
  bumpConflictRevision(current.id);
}

function hasText(fields: Record<string, unknown>): boolean {
  return "body" in fields || "title" in fields;
}

/**
 * The handler `flushOps` calls on a note conflict: adopt the server's copy; keep the student's typed version as a
 * free "Conflict copy" note (a page note's copy cannot be a second note on its section); tell the student.
 */
export function noteConflictHandler(userId: string, qc: QueryClient, t: T): ConflictHandler {
  return async (local: NoteOp, current: NoteOut) => {
    adoptNote(qc, userId, current);
    const base = {
      user: userId,
      noteId: current.id,
      anchorType: current.anchor_type,
      anchorId: current.anchor_id,
      notebookId: current.notebook_id,
      title: current.title ?? current.source?.section_heading ?? null,
    };
    if (current.kind === "highlight") return; // a colour or a delete: the server's copy stands
    if (local.deleted) {
      await addConflictNotice({ ...base, kind: "note_delete_kept" });
      return;
    }
    if (!hasText(local.fields) || current.canvas !== "paper") {
      await addConflictNotice({ ...base, kind: "note_kept" });
      return;
    }
    const copyId = crypto.randomUUID();
    const body = "body" in local.fields ? local.fields.body : current.body;
    const title = "title" in local.fields ? (local.fields.title as string | null) : current.title;
    await queueCreate(userId, copyId, {
      kind: "note",
      anchor_type: "free",
      title: conflictCopyTitle(t, title ?? current.source?.section_heading ?? null),
      body: body ?? null,
      ...(current.notebook_id && current.kind === "note" ? { notebook_id: current.notebook_id } : {}),
    });
    await addConflictNotice({ ...base, kind: "note_copy", copyId });
  };
}

/** A stable conflict handler for the signed-in student's flushes (worded with the current ui_copy). */
export function useNoteConflictHandler(): ConflictHandler | undefined {
  const user = useUser();
  const qc = useQueryClient();
  const t = useUiText();
  const tRef = useRef(t);
  tRef.current = t;
  return useMemo(() => (user ? noteConflictHandler(user, qc, (k, v) => tRef.current(k, v)) : undefined), [user, qc]);
}
