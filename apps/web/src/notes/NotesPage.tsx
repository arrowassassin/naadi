import type { SearchHit } from "@naadi/shared";
import { Button, cx, ErrorState, Skeleton } from "@naadi/ui";
import { Link, useNavigate, useRouter } from "@tanstack/react-router";
import { NotebookPen, Plus, Search as SearchIcon, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { ApiError } from "../auth/apiError";
import { useUiText } from "../copy/useUiText";
import { useOnlineStatus } from "../health/useOnlineStatus";
import { NotebooksShelf } from "../notebooks/NotebooksShelf";
import { useLocalSearch } from "../search/api";
import { resolveTarget } from "../search/targets";
import { errorKey, type Note, useNote, useNotes, useNotesSync, useNoteWriter } from "./api";
import { ExportHighlightsPdf } from "./ExportPdf";
import { EMPTY_DOC } from "./extensions";
import { editedLabel, noteTitle, sourceLabel } from "./format";
import { type AnchorFilter, type HighlightFilters, HighlightsRail } from "./HighlightsRail";
import { NoteSheet } from "./NoteSheet";
import type { NotesTab } from "./types";

export interface NotesSearch {
  tab?: NotesTab;
  /** The open note. */
  note?: string;
  color?: "lime" | "violet" | "amber";
  anchor?: AnchorFilter;
  q?: string;
}

const SEARCH_DEBOUNCE_MS = 250;
const MIN_QUERY = 2;

function NoteRow({ note, open, onOpen }: { note: Note; open: boolean; onOpen: () => void }) {
  const t = useUiText();
  const source = sourceLabel(t, note);
  return (
    <li>
      <button
        type="button"
        onClick={onOpen}
        aria-current={open ? "true" : undefined}
        data-note-row={note.id}
        className={cx(
          "flex min-h-12 w-full flex-col items-start gap-0.5 rounded-block-sm border-0 px-4 py-2.5 text-left",
          open ? "bg-surface-2 font-semibold text-text" : "bg-transparent text-text-2 hover:bg-surface-1",
        )}
      >
        <span className="w-full truncate text-[0.9375rem]">{noteTitle(t, note)}</span>
        <span className="w-full truncate font-normal text-text-3 text-ui-sm">
          {[source, editedLabel(t, note.updated_at)].filter(Boolean).join(t("notes.separator"))}
        </span>
      </button>
    </li>
  );
}

/** Search results for the box (S07 local search scoped to notes and to handwriting). */
function SearchResults({ q, onOpen }: { q: string; onOpen: (id: string) => void }) {
  const t = useUiText();
  const online = useOnlineStatus();
  const router = useRouter();
  const notes = useLocalSearch(q, "notes", online);
  const ink = useLocalSearch(q, "handwriting", online);
  if (!online) return <p className="m-0 text-text-2 text-ui-sm">{t("notes.search_offline")}</p>;
  if ((notes.isPending && !notes.data) || (ink.isPending && !ink.data)) {
    return (
      <div aria-busy="true" className="flex flex-col gap-2">
        <p role="status" className="sr-only">
          {t("notes.search_loading")}
        </p>
        <Skeleton className="h-14 rounded-inner" />
        <Skeleton className="h-14 rounded-inner" />
      </div>
    );
  }
  if (notes.isError && ink.isError) {
    return (
      <ErrorState
        title={t("notes.search_error")}
        message={t(errorKey(notes.error))}
        onRetry={() => {
          void notes.refetch();
          void ink.refetch();
        }}
        retryLabel={t("notes.retry")}
      />
    );
  }
  const hits: SearchHit[] = [...(notes.data?.hits ?? []), ...(ink.data?.hits ?? [])];
  if (hits.length === 0) {
    return (
      <p role="status" className="m-0 text-text-2 text-ui-sm" data-notes-search-empty="">
        {t("notes.search_empty", { query: q })}
      </p>
    );
  }
  return (
    <ul aria-label={t("notes.search_results")} className="m-0 flex list-none flex-col gap-1 p-0">
      {hits.map((h) => {
        const snippet = h.snippet.map((s) => s.text).join("");
        const body = (
          <>
            <span
              className={cx(
                "w-full truncate",
                h.type === "ink" ? "font-ink text-[1.375rem] leading-tight" : "text-[0.9375rem]",
              )}
            >
              {h.title}
            </span>
            <span className="w-full truncate text-text-3 text-ui-sm">
              {h.type === "ink" ? t("notes.search_kind_ink") : t("notes.search_kind_note")}
              {snippet ? `${t("notes.separator")}${snippet}` : ""}
            </span>
          </>
        );
        const cls =
          "flex min-h-12 w-full flex-col items-start gap-0.5 rounded-block-sm border-0 bg-transparent px-4 py-2.5 text-left text-text no-underline hover:bg-surface-1 hover:text-text";
        const noteId = h.target.note_id;
        if (h.type === "note" && noteId && h.target.kind !== "page") {
          return (
            <li key={h.id}>
              <button type="button" className={cls} onClick={() => onOpen(noteId)} data-search-hit={h.type}>
                {body}
              </button>
            </li>
          );
        }
        const dest = resolveTarget(h.target, router.routesByPath as Record<string, unknown>);
        return (
          <li key={h.id}>
            {dest ? (
              <Link to={dest.to} params={dest.params} search={dest.search} className={cls} data-search-hit={h.type}>
                {body}
              </Link>
            ) : (
              <div className={cls}>{body}</div>
            )}
          </li>
        );
      })}
    </ul>
  );
}

/**
 * `/notes` (Notes board): the student's notes with counts, the note sheet, recent highlights with filters and a
 * Detached group, search over notes and handwriting, Export PDF; and the Notebooks tab (WEB-B's shelf). Every
 * write goes through the offline queue: offline the page shows the device copy and edits "will sync".
 */
export function NotesPage({ search }: { search: NotesSearch }) {
  const t = useUiText();
  const navigate = useNavigate();
  const tab: NotesTab = search.tab === "notebooks" ? "notebooks" : "notes";
  const [text, setText] = useState(search.q ?? "");
  const writer = useNoteWriter();
  useNotesSync();
  const list = useNotes({ kind: "note", limit: 200 }, { enabled: tab === "notes" });

  const go = (patch: Partial<NotesSearch>, replace = true) =>
    void navigate({ to: "/notes", search: { ...search, ...patch }, replace });

  useEffect(() => {
    document.title = t("notes.document_title");
  }, [t]);

  // The box commits to the URL after a pause (back button, shared links).
  // biome-ignore lint/correctness/useExhaustiveDependencies: only the typed text schedules a commit
  useEffect(() => {
    const q = text.trim();
    if (q === (search.q ?? "")) return;
    const id = window.setTimeout(() => go({ q: q || undefined }), SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(id);
  }, [text]);

  const notes = list.items;
  const listed = useMemo(() => notes.find((n) => n.id === search.note), [notes, search.note]);
  // A note outside the list (a search hit, an older note) or a notebook page (opened in its notebook).
  const single = useNote(search.note, tab === "notes" && Boolean(list.data) && !listed);
  const open = listed ?? (single.data?.kind === "note" ? single.data : undefined);
  const page = single.data?.kind === "page" ? single.data : undefined;
  useEffect(() => {
    if (page?.notebook_id) {
      void navigate({
        to: "/notes/notebooks/$id/$page",
        params: { id: page.notebook_id, page: String(page.sort + 1) },
        replace: true,
      });
    }
  }, [page, navigate]);
  const counts = list.counts;
  const query = (search.q ?? "").trim();
  const searching = query.length >= MIN_QUERY;

  const newNote = () => {
    const id = writer.create({ kind: "note", anchor_type: "free", body: { ...EMPTY_DOC } });
    go({ note: id, q: undefined }, false);
    setText("");
  };

  const highlightFilters: HighlightFilters = { color: search.color, anchor: search.anchor };
  const placeholder = counts
    ? t("notes.search_placeholder_counts", { notes: String(counts.notes), highlights: String(counts.highlights) })
    : t("notes.search_placeholder");

  const tabs = (
    <fieldset className="m-0 flex w-fit rounded-pill border border-line bg-surface-1 p-1">
      <legend className="sr-only">{t("notes.tabs")}</legend>
      {(["notes", "notebooks"] as const).map((k) => (
        <button
          key={k}
          type="button"
          aria-pressed={tab === k}
          onClick={() => go({ tab: k === "notes" ? undefined : k, note: undefined })}
          className={cx(
            "min-h-11 rounded-pill border-0 px-5 text-[0.875rem]",
            tab === k ? "bg-paper font-semibold text-ground" : "bg-transparent text-text-2 hover:text-text",
          )}
        >
          {t(`notes.tab_${k}`)}
        </button>
      ))}
    </fieldset>
  );

  const header = (
    <header className="flex flex-wrap items-end justify-between gap-6">
      <h1 className="m-0 font-display font-extrabold text-[clamp(3rem,1.6rem+6vw,8.5rem)] leading-[0.88] tracking-[-0.05em]">
        {t("notes.heading_1")}
        <br />
        <span className="text-line-dashed">{t("notes.heading_2")}</span>
      </h1>
      {tab === "notes" ? (
        <search className="flex min-h-15 w-full max-w-[30rem] flex-[1_1_20rem] items-center gap-3 rounded-pill border border-line bg-surface-1 px-5">
          <SearchIcon aria-hidden="true" size={20} className="shrink-0 text-text-3" />
          <label htmlFor="notes-q" className="sr-only">
            {t("notes.search_label")}
          </label>
          <input
            id="notes-q"
            type="search"
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape" && text) {
                e.preventDefault();
                setText("");
              }
            }}
            placeholder={placeholder}
            autoComplete="off"
            enterKeyHint="search"
            className="min-h-12 min-w-0 flex-1 appearance-none border-0 bg-transparent text-[1rem] text-text outline-none placeholder:text-text-3 [&::-webkit-search-cancel-button]:hidden"
          />
          {text ? (
            <button
              type="button"
              onClick={() => setText("")}
              aria-label={t("notes.search_clear")}
              className="flex size-11 shrink-0 items-center justify-center rounded-full border-0 bg-control text-text"
            >
              <X aria-hidden="true" size={16} />
            </button>
          ) : null}
        </search>
      ) : null}
    </header>
  );

  if (tab === "notebooks") {
    return (
      <div className="flex flex-col gap-8 pt-8 pb-10 tablet:pt-14" data-notes-page="notebooks">
        {header}
        {tabs}
        <NotebooksShelf />
      </div>
    );
  }

  const listColumn = (
    <nav aria-label={t("notes.list_label")} className="flex min-w-0 flex-col gap-1.5">
      {/* The view's one lime action (Notes board). */}
      <Button variant="primary" icon={<Plus size={18} />} onClick={newNote} className="mb-2 min-h-13 w-full">
        {t("notes.new_note")}
      </Button>
      {searching ? (
        <SearchResults q={query} onOpen={(id) => go({ note: id }, false)} />
      ) : list.isPending && !list.data ? (
        <div aria-busy="true" className="flex flex-col gap-2">
          <p role="status" className="sr-only">
            {t("notes.loading")}
          </p>
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-12 rounded-block-sm" />
          ))}
        </div>
      ) : list.isError && !list.data ? (
        <ErrorState
          title={t("notes.error_title")}
          message={t(errorKey(list.error))}
          requestId={list.error instanceof ApiError ? list.error.requestId : null}
          onRetry={() => void list.refetch()}
          retrying={list.isFetching}
          retryLabel={t("notes.retry")}
        />
      ) : (
        <>
          <p className="m-0 flex items-center justify-between px-4 pb-1 font-mono text-label-sm text-text-3 uppercase tracking-[0.12em]">
            <span>{t("notes.all_notes")}</span>
            <span data-notes-count="">{notes.length}</span>
          </p>
          {list.fromCache ? (
            <p role="status" className="m-0 px-4 text-text-3 text-ui-sm">
              {t("notes.saved_copy")}
            </p>
          ) : null}
          {notes.length === 0 ? (
            <div className="flex flex-col gap-2 px-4 py-3" data-notes-empty="">
              <p className="m-0 font-bold font-display text-[1.375rem]">{t("notes.empty_title")}</p>
              <p className="m-0 text-text-2 text-ui-sm">{t("notes.empty_body")}</p>
            </div>
          ) : (
            <ul className="m-0 flex list-none flex-col gap-0.5 p-0">
              {notes.map((n) => (
                <NoteRow key={n.id} note={n} open={n.id === search.note} onOpen={() => go({ note: n.id }, false)} />
              ))}
            </ul>
          )}
        </>
      )}
    </nav>
  );

  const sheet = open ? (
    <NoteSheet
      key={open.id}
      note={open}
      onBack={() => go({ note: undefined })}
      onDeleted={() => go({ note: undefined })}
    />
  ) : (
    <div className="hidden min-h-[24rem] flex-col items-center justify-center gap-3 rounded-tile border border-line-soft border-dashed p-10 text-center text-text-2 tablet:flex">
      <NotebookPen aria-hidden="true" size={28} className="text-text-3" />
      <p className="m-0 max-w-[22rem]">{search.note && list.data ? t("notes.note_missing") : t("notes.pick_note")}</p>
    </div>
  );

  const rail = (
    <div className="flex min-w-0 flex-col gap-4">
      <HighlightsRail filters={highlightFilters} onFilters={(f) => go({ color: f.color, anchor: f.anchor })} />
      <ExportHighlightsPdf filters={highlightFilters} />
    </div>
  );

  return (
    <div className="flex flex-col gap-8 pt-8 pb-10 tablet:pt-14" data-notes-page="notes">
      {header}
      {tabs}
      <div
        className={cx(
          "grid grid-cols-[minmax(0,1fr)] gap-5",
          "tablet:grid-cols-[minmax(13rem,16rem)_minmax(0,1fr)] laptop:grid-cols-[minmax(14rem,18rem)_minmax(0,1fr)_minmax(18rem,24rem)]",
        )}
      >
        {/* Phone: the list, or the open note (with a back button). */}
        <div className={cx(open && "hidden tablet:block")}>{listColumn}</div>
        <div className={cx("min-w-0", !open && "hidden tablet:block")}>{sheet}</div>
        <div className="tablet:col-span-2 laptop:col-span-1">{rail}</div>
      </div>
    </div>
  );
}
