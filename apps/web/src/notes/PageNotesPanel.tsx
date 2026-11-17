import { Button, cx, ErrorState, Skeleton } from "@naadi/ui";
import { PenLine } from "lucide-react";
import { type Ref, useCallback, useEffect, useId, useImperativeHandle, useRef, useState } from "react";
import { ApiError } from "../auth/apiError";
import { useUiText } from "../copy/useUiText";
import { useOnlineStatus } from "../health/useOnlineStatus";
import { lazyWithFallback } from "../shell/lazyPage";
import {
  beaconNotes,
  errorKey,
  type Note,
  useNotes,
  useNotesSync,
  useNoteWriter,
  usePendingOps,
  useUserId,
} from "./api";
import { ConflictNotices, useConflictRevision } from "./conflicts";
import { EMPTY_DOC, isEmptyDoc } from "./doc";
import { NoteInk } from "./NoteInk";
import { PAPER_NOTICE } from "./paperNotice";
import { NoteSaveStatus } from "./SaveStatus";

/**
 * TipTap and ProseMirror (about 130 KB gzipped) are their own chunk: the Reader renders the section first and loads
 * the editor beside it. A quote sent before the editor exists waits in state and is inserted by the editor once it is attached.
 */
const NoteEditor = lazyWithFallback(() => import("./NoteEditor").then((m) => m.NoteEditor), {
  fallback: (
    <div aria-busy="true" className="flex flex-col gap-3">
      <Skeleton className="h-24 w-full rounded-block-sm bg-ground/10" />
    </div>
  ),
  unavailable: "inline",
});

export type PanelMode = "type" | "write" | "both";
const MODES: PanelMode[] = ["type", "write", "both"];
const MODE_KEY = "naadi.page-notes.mode.v1";
/** Typing is saved after this much quiet (and when the panel closes). */
export const SAVE_DEBOUNCE_MS = 700;

function readMode(): PanelMode {
  try {
    const v = localStorage.getItem(MODE_KEY);
    return v === "write" || v === "both" || v === "type" ? v : "type";
  } catch {
    return "type";
  }
}

function writeMode(m: PanelMode): void {
  try {
    localStorage.setItem(MODE_KEY, m);
  } catch {
    // private mode: the tab is remembered for this page only
  }
}

export interface PageNotesHandle {
  /** Add a quote from the section to the end of the note (the selection toolbar's Note) and focus it. */
  addQuote(quote: string): void;
}

export interface PageNotesPanelProps {
  sectionId: string;
  sectionHeading: string;
  /** Ink on in the panel (the Reader hides its page bar and turns section ink off). */
  onInkChange: (on: boolean) => void;
  /** Called once the panel is mounted and `ref` is set. */
  onMounted?: () => void;
  ref?: Ref<PageNotesHandle>;
  className?: string;
}

/**
 * The Reader's page-notes panel (Chapter / Tablet / PhoneChapter boards): the one note a student keeps per section
 * (`kind 'note'`, anchored to the section), typed (TipTap), handwritten (the `notebook_page` ink layer keyed by the
 * note id) or both. The note is made on the first edit; typing is saved after a pause through the offline queue.
 */
export function PageNotesPanel({
  sectionId,
  sectionHeading,
  onInkChange,
  onMounted,
  ref,
  className,
}: PageNotesPanelProps) {
  const t = useUiText();
  const ids = useId();
  const online = useOnlineStatus();
  const list = useNotes({ kind: "note", anchor_type: "section", anchor_id: sectionId, limit: 1 });
  const writer = useNoteWriter();
  useNotesSync();
  const ops = usePendingOps();
  const [mode, setMode] = useState<PanelMode>(readMode);
  const [inkOn, setInkOn] = useState(false);
  const note: Note | undefined = list.items[0];
  // The note id: the stored one, or one made here on the first edit.
  const [madeId, setMadeId] = useState<string | null>(null);
  const noteId = note?.id ?? madeId;
  // A newer copy adopted after a conflict replaces the stale text in the editor (instead of being saved over it).
  const conflictRevision = useConflictRevision(noteId);
  // Read synchronously by the editor's change handler (two keystrokes before a re-render must not make two notes).
  const idRef = useRef<string | null>(noteId);
  idRef.current = noteId ?? idRef.current;
  // A quote waiting for the editor (it is a lazy chunk, and the panel may be opened by the very tap that quotes).
  const [quote, setQuote] = useState<{ id: number; text: string } | null>(null);
  const timer = useRef(0);
  const latest = useRef<Record<string, unknown> | null>(null);
  // Typed but not yet handed to the queue (the save waits for a pause): shown as "Saving…".
  const [dirty, setDirty] = useState(false);

  const inkChange = useCallback(
    (on: boolean) => {
      setInkOn(on);
      onInkChange(on);
    },
    [onInkChange],
  );

  const save = useCallback(() => {
    window.clearTimeout(timer.current);
    const body = latest.current;
    const id = idRef.current;
    if (!body || !id) return;
    latest.current = null;
    setDirty(false);
    writer.update(id, { body });
  }, [writer]);

  const ensureNote = useCallback(
    (body: Record<string, unknown>): string => {
      if (idRef.current) return idRef.current;
      const id = writer.create({ kind: "note", anchor_type: "section", anchor_id: sectionId, body });
      idRef.current = id;
      setMadeId(id);
      return id;
    },
    [writer, sectionId],
  );

  const onChange = useCallback(
    (doc: Record<string, unknown>) => {
      if (!idRef.current) {
        // A note is made by its first word (or quote), never by opening the panel.
        if (!isEmptyDoc(doc)) ensureNote(doc);
        return;
      }
      latest.current = doc;
      setDirty(true);
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(save, SAVE_DEBOUNCE_MS);
    },
    [ensureNote, save],
  );

  // Save what is typed when the panel closes or the section changes.
  const saveRef = useRef(save);
  saveRef.current = save;
  useEffect(() => () => saveRef.current(), []);
  const userId = useUserId();
  const userRef = useRef(userId);
  userRef.current = userId;
  useEffect(() => {
    const flush = (e?: Event) => {
      saveRef.current();
      // Leaving the page: what was just typed goes out now (keepalive), not only into the device queue.
      if (e?.type === "pagehide") beaconNotes(userRef.current);
    };
    window.addEventListener("pagehide", flush);
    document.addEventListener("visibilitychange", flush);
    return () => {
      window.removeEventListener("pagehide", flush);
      document.removeEventListener("visibilitychange", flush);
    };
  }, []);

  const pick = useCallback(
    (m: PanelMode) => {
      setMode(m);
      writeMode(m);
      if (m === "type") inkChange(false);
    },
    [inkChange],
  );

  useImperativeHandle(
    ref,
    () => ({
      addQuote(text: string) {
        if (mode === "write") pick("both");
        setQuote((q) => ({ id: (q?.id ?? 0) + 1, text }));
      },
    }),
    [mode, pick],
  );
  const quoteInserted = useCallback((id: number) => setQuote((q) => (q && q.id === id ? null : q)), []);

  const mountedRef = useRef(onMounted);
  mountedRef.current = onMounted;
  useEffect(() => mountedRef.current?.(), []);

  const pendingCreate = noteId ? ops.some((o) => o.id === noteId && o.create) : false;
  const showType = mode !== "write";
  const showWrite = mode !== "type";

  return (
    <section
      aria-labelledby={`${ids}-h`}
      data-page-notes=""
      className={cx("flex flex-col gap-4 bg-paper text-ground", className)}
    >
      <div className="flex flex-wrap items-center gap-3">
        <div className="mr-auto flex min-w-0 flex-col">
          <h2 id={`${ids}-h`} className="m-0 font-display font-extrabold text-[1.5rem] tracking-[-0.02em]">
            {t("notes.panel_title")}
          </h2>
          <span className="truncate text-paper-text-2 text-ui-sm">
            {t("notes.panel_pinned", { section: sectionHeading })}
          </span>
        </div>
        <fieldset className="m-0 flex rounded-pill border-0 bg-ground/10 p-1">
          <legend className="sr-only">{t("notes.panel_mode")}</legend>
          {MODES.map((m) => (
            <button
              key={m}
              type="button"
              aria-pressed={mode === m}
              onClick={() => pick(m)}
              className={cx(
                "min-h-11 rounded-pill border-0 px-3.5 text-[0.8125rem]",
                mode === m ? "bg-ground font-semibold text-paper" : "bg-transparent text-ground",
              )}
            >
              {t(`notes.panel_mode_${m}`)}
            </button>
          ))}
        </fieldset>
      </div>

      {list.isPending && !list.data ? (
        <div aria-busy="true" className="flex flex-col gap-3">
          <p role="status" className="sr-only">
            {t("notes.panel_loading")}
          </p>
          <Skeleton className="h-5 w-3/4 bg-ground/10" />
          <Skeleton className="h-24 w-full rounded-block-sm bg-ground/10" />
        </div>
      ) : list.isError && !list.data ? (
        <ErrorState
          title={t("notes.panel_error")}
          message={t(errorKey(list.error))}
          requestId={list.error instanceof ApiError ? list.error.requestId : null}
          onRetry={() => void list.refetch()}
          retrying={list.isFetching}
          retryLabel={t("notes.retry")}
          className="bg-surface-1 text-text"
        />
      ) : (
        <>
          {list.fromCache ? (
            <p role="status" className="m-0 text-paper-text-2 text-ui-sm">
              {t("notes.saved_copy")}
            </p>
          ) : null}
          {showType ? (
            <div className="flex flex-col gap-2">
              {mode === "both" ? (
                <span className="font-mono text-label-sm text-paper-text-2 uppercase tracking-[0.12em]">
                  {t("notes.panel_typed")}
                </span>
              ) : null}
              <div className={PAPER_NOTICE}>
                <NoteEditor
                  // One editor per note; a note made here keeps the editor it was typed in.
                  key={`${madeId ? "local" : (note?.id ?? "local")}:${conflictRevision}`}
                  noteId={noteId ?? sectionId}
                  initial={note?.body ?? EMPTY_DOC}
                  onChange={onChange}
                  label={t("notes.panel_editor", { section: sectionHeading })}
                  placeholder={t("notes.panel_placeholder")}
                  quote={quote}
                  onQuoteInserted={quoteInserted}
                  compact
                />
              </div>
            </div>
          ) : null}
          {showWrite ? (
            <div className="flex flex-col gap-2">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-mono text-label-sm text-paper-text-2 uppercase tracking-[0.12em]">
                  {t("notes.panel_handwritten")}
                </span>
                {noteId && !pendingCreate && !inkOn ? (
                  <Button
                    variant="secondary"
                    icon={<PenLine size={16} />}
                    onClick={() => inkChange(true)}
                    className="min-h-11 border-ground! text-ground! hover:bg-ground/10!"
                  >
                    {t("notes.panel_ink_start")}
                  </Button>
                ) : null}
              </div>
              {noteId && !pendingCreate ? (
                <NoteInk noteId={noteId} on={inkOn} onToggle={inkChange} offline={list.fromCache} />
              ) : noteId ? (
                <p
                  role="status"
                  className="m-0 rounded-block-sm border border-ground/20 border-dashed p-4 text-paper-text-2 text-ui-sm"
                >
                  {online ? t("notes.panel_ink_preparing") : t("notes.panel_ink_offline")}
                </p>
              ) : (
                <div className="flex flex-col items-start gap-3 rounded-block-sm border border-ground/20 border-dashed p-4">
                  <p className="m-0 text-paper-text-2 text-ui-sm">{t("notes.panel_ink_empty")}</p>
                  <Button
                    variant="secondary"
                    icon={<PenLine size={16} />}
                    onClick={() => ensureNote({ ...EMPTY_DOC })}
                    className="min-h-11 border-ground! text-ground! hover:bg-ground/10!"
                  >
                    {t("notes.panel_ink_begin")}
                  </Button>
                </div>
              )}
            </div>
          ) : null}
          <div className={PAPER_NOTICE}>
            <ConflictNotices filter={{ scope: "notes", anchorId: sectionId }} />
          </div>
          {noteId ? <NoteSaveStatus ids={[noteId]} tone="paper" dirty={dirty} /> : null}
        </>
      )}
    </section>
  );
}
