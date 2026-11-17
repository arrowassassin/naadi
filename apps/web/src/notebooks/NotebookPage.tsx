import { type NoteOut, uuidv7 } from "@naadi/shared";
import { Button, cx, ErrorState, OfflineBanner, Skeleton } from "@naadi/ui";
import { Link, Navigate, useNavigate } from "@tanstack/react-router";
import {
  ArrowDown,
  ArrowLeft,
  ArrowUp,
  ChevronLeft,
  ChevronRight,
  FileDown,
  FilePlus2,
  Link2,
  PenLine,
  Presentation,
  Trash2,
} from "lucide-react";
import { Fragment, type ReactNode, useState } from "react";
import { ApiError } from "../auth/apiError";
import { useUiText } from "../copy/useUiText";
import { useOnlineStatus } from "../health/useOnlineStatus";
import { ConflictNotices, useConflictRevision } from "../notes/conflicts";
import { deliverPdf, exportNotebookToPdf } from "../pdf";
import { errorKey, useNotebook, useNotebookSync } from "./api";
import { ConfirmDialog } from "./dialogs";
import { NotebooksShelf } from "./NotebooksShelf";
import { PageInk } from "./PageInk";
import { clampPageNumber, movePageBy, pageAfterDelete, pagePaper } from "./pageOps";
import { PAPERS } from "./paper";
import { NotebookSaveStatus } from "./SaveStatus";
import { WhiteboardPage } from "./whiteboard/WhiteboardPage";

const chip =
  "inline-flex min-h-11 shrink-0 items-center rounded-pill px-3.5 text-[0.8125rem] transition-colors duration-(--duration-control) ease-out";
const iconBtn =
  "inline-flex size-11 shrink-0 items-center justify-center rounded-full border border-line-control text-text hover:bg-surface-2 disabled:cursor-not-allowed disabled:border-dashed disabled:border-line-dashed disabled:text-text-muted disabled:hover:bg-transparent";

/** "Linked: Chapter 8 · Loop diuretics" for an anchored page, linking to where it is anchored. */
function LinkedChip({ page }: { page: NoteOut }) {
  const t = useUiText();
  const s = page.source;
  if (!s) return null;
  const cls =
    "inline-flex min-h-11 max-w-full items-center gap-1.5 rounded-pill bg-ground px-4 text-paper text-ui-sm no-underline hover:bg-surface-2";
  const icon = <Link2 aria-hidden="true" size={14} className="shrink-0" />;
  if (s.type === "section" && s.chapter_id && s.section_id) {
    const text = s.section_heading
      ? t("notebooks.page.linked_section", { chapter: s.chapter_title ?? "", section: s.section_heading })
      : t("notebooks.page.linked_chapter", { chapter: s.chapter_title ?? "" });
    return (
      <Link
        to="/learn/$chapterId/$sectionId"
        params={{ chapterId: s.chapter_id, sectionId: s.section_id }}
        className={cls}
        data-linked-chip=""
      >
        {icon}
        <span className="truncate">{text}</span>
      </Link>
    );
  }
  if (s.type === "question" && s.question_id) {
    return (
      <Link
        to="/practice/question/$questionId"
        params={{ questionId: s.question_id }}
        className={cls}
        data-linked-chip=""
      >
        {icon}
        <span className="truncate">
          {s.question_number
            ? t("notebooks.page.linked_question", { number: String(s.question_number) })
            : t("notebooks.page.linked_question_plain")}
        </span>
      </Link>
    );
  }
  return s.title ? (
    <span className={cls} data-linked-chip="">
      {icon}
      <span className="truncate">{t("notebooks.page.linked_other", { title: s.title })}</span>
    </span>
  ) : null;
}

/**
 * `/notes/notebooks/$id/$page` (design/Notebook.dc.html): the notebook's page N of M on US Letter paper with
 * persisted ink, or a whiteboard; paper choice per page, add / reorder / delete pages, export the notebook as a PDF.
 * The shelf sits beside the page on wide screens, as on the board.
 */
/** Remounts its children when a conflict replaced the page's content with the server's copy (notes/conflicts.tsx). */
function ConflictKeyed({ id, children }: { id: string; children: ReactNode }) {
  const revision = useConflictRevision(id);
  return <Fragment key={revision}>{children}</Fragment>;
}

export function NotebookPage({ id, page: rawPage }: { id: string; page: string }) {
  const t = useUiText();
  const navigate = useNavigate();
  const online = useOnlineStatus();
  const sync = useNotebookSync();
  const { query, detail, fromDevice } = useNotebook(id);
  const [ink, setInk] = useState(true);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [exporting, setExporting] = useState<"idle" | "busy" | "error">("idle");

  const shelf = (
    <aside className="hidden w-[380px] shrink-0 min-[1120px]:block" aria-label={t("notebooks.shelf_label")}>
      <NotebooksShelf />
    </aside>
  );
  const back = (
    <Link
      to="/notes/notebooks"
      className="inline-flex min-h-11 items-center gap-1.5 self-start rounded-pill border border-line-control px-4 text-text text-ui-sm no-underline hover:bg-surface-2"
    >
      <ArrowLeft aria-hidden="true" size={16} />
      {t("notebooks.back")}
    </Link>
  );

  if (detail === undefined) {
    if (query.isError && !(query.error instanceof ApiError && query.error.status === 404)) {
      const offline = !online || errorKey(query.error) === "notebooks.error.unreachable";
      return (
        <div className="flex flex-col gap-4" data-notebook-page="error">
          {back}
          {offline ? (
            <OfflineBanner message={t("notebooks.page.offline_none")} />
          ) : (
            <ErrorState
              title={t("notebooks.page.error_title")}
              message={t(errorKey(query.error))}
              onRetry={() => void query.refetch()}
              retryLabel={t("notebooks.retry")}
              retrying={query.isFetching}
            />
          )}
        </div>
      );
    }
    return (
      <div className="flex gap-5" aria-busy="true" data-notebook-page="loading">
        {shelf}
        <div className="flex min-w-0 flex-1 flex-col gap-3">
          <span className="sr-only">{t("notebooks.page.loading")}</span>
          <Skeleton className="h-11 w-64 rounded-pill" />
          <Skeleton className="aspect-[612/792] w-full max-w-[880px] rounded-[18px]" />
        </div>
      </div>
    );
  }
  if (detail === null) {
    return (
      <div className="flex flex-col gap-4" data-notebook-page="missing">
        {back}
        <ErrorState title={t("notebooks.page.missing_title")} message={t("notebooks.error.notebook_not_found")} />
      </div>
    );
  }

  const pages = detail.pages;
  const count = pages.length;
  const number = clampPageNumber(rawPage, count);
  if (number !== null && String(number) !== rawPage) {
    // Out of range: wait for a refetch in flight (a page just added elsewhere), then go to the nearest page.
    if (query.isFetching)
      return (
        <div className="flex flex-col gap-3" aria-busy="true" data-notebook-page="loading">
          <span className="sr-only">{t("notebooks.page.loading")}</span>
          <Skeleton className="aspect-[612/792] w-full max-w-[880px] rounded-[18px]" />
        </div>
      );
    return <Navigate to="/notes/notebooks/$id/$page" params={{ id, page: String(number) }} replace />;
  }
  const current = number !== null ? (pages[number - 1] ?? null) : null;
  const ids = pages.map((p) => p.id);
  const go = (n: number) => void navigate({ to: "/notes/notebooks/$id/$page", params: { id, page: String(n) } });

  const addPage = async (canvas: "paper" | "whiteboard") => {
    if (!sync) return;
    const pageId = uuidv7();
    const position = number ?? 0;
    await sync.add({
      kind: "createPage",
      id: pageId,
      notebookId: id,
      body: {
        id: pageId,
        canvas,
        position,
        ...(canvas === "whiteboard"
          ? { body: { type: "excalidraw", version: 2, source: "naadi", elements: [], appState: {}, files: {} } }
          : {}),
      },
    });
    go(position + 1);
  };

  const move = async (delta: -1 | 1) => {
    if (!sync || !current || number === null) return;
    const next = movePageBy(ids, current.id, delta);
    await sync.add({ kind: "reorder", notebookId: id, pageIds: next });
    go(next.indexOf(current.id) + 1);
  };

  const remove = async () => {
    if (!sync || !current) return;
    setConfirmDelete(false);
    const after = pageAfterDelete(pages, current.id);
    await sync.add({ kind: "deleteNote", id: current.id, notebookId: id });
    if (after !== null) go(after);
  };

  const exportPdf = async () => {
    setExporting("busy");
    try {
      const result = await exportNotebookToPdf(detail, t);
      await deliverPdf(result);
      setExporting("idle");
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") setExporting("idle");
      else setExporting("error");
    }
  };

  const paper = pagePaper(current, detail.paper);
  const position = number !== null ? t("notebooks.page.position", { page: String(number), count: String(count) }) : "";
  const pageLabel = number !== null ? t("notebooks.page.surface", { title: detail.title, page: String(number) }) : "";

  return (
    <div className="flex gap-5" data-notebook-page="ready" data-notebook-id={id}>
      {shelf}
      <section className="flex min-w-0 flex-1 flex-col gap-3" aria-labelledby="notebook-page-h">
        <div className="flex flex-wrap items-center gap-2">
          <Link
            to="/notes/notebooks"
            aria-label={t("notebooks.back")}
            title={t("notebooks.back")}
            className="inline-flex size-11 items-center justify-center rounded-full border border-line-control text-text hover:bg-surface-2 min-[1120px]:hidden"
          >
            <ArrowLeft aria-hidden="true" size={18} />
          </Link>
          <h1
            id="notebook-page-h"
            className="m-0 mr-auto min-w-0 font-display text-[1.25rem] leading-tight font-bold break-words"
          >
            {detail.title}
            {position ? (
              <>
                <span aria-hidden="true"> · </span>
                <span className="font-semibold" data-page-position="">
                  {position}
                </span>
              </>
            ) : null}
          </h1>
          {sync ? (
            <NotebookSaveStatus sync={sync} notebookId={id} quietWhenSaved={current?.canvas !== "whiteboard"} />
          ) : null}
        </div>

        {fromDevice || !online ? <OfflineBanner message={t("notebooks.page.offline")} /> : null}
        <ConflictNotices filter={{ scope: "notebooks", notebookId: id }} />

        {current === null ? (
          <div className="flex flex-col items-start gap-3 rounded-block border border-line p-6" data-notebook-empty="">
            <p className="m-0 font-semibold text-text">{t("notebooks.page.empty_title")}</p>
            <p className="m-0 text-text-2">{t("notebooks.page.empty_body")}</p>
            <Button variant="primary" icon={<FilePlus2 size={16} />} onClick={() => void addPage("paper")}>
              {t("notebooks.page.add")}
            </Button>
          </div>
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-2">
              {current.canvas === "paper" ? (
                <fieldset className="m-0 flex flex-wrap items-center gap-1.5 border-0 p-0" data-paper-choice="">
                  <legend className="sr-only">{t("notebooks.page.paper_group")}</legend>
                  {PAPERS.map((p) => (
                    <button
                      key={p}
                      type="button"
                      aria-pressed={paper === p}
                      onClick={() =>
                        void sync?.add({ kind: "patchNote", id: current.id, notebookId: id, patch: { paper: p } })
                      }
                      className={cx(
                        chip,
                        paper === p
                          ? "bg-paper font-semibold text-ground"
                          : "border border-line-control text-text hover:bg-surface-2",
                      )}
                    >
                      {t(`notebooks.paper.${p}`)}
                    </button>
                  ))}
                </fieldset>
              ) : (
                <span className="inline-flex min-h-11 items-center gap-1.5 text-text-2 text-ui-sm">
                  <Presentation aria-hidden="true" size={16} />
                  {t("whiteboard.label")}
                </span>
              )}
              <span className="flex-1" />
              {current.canvas === "paper" && !ink ? (
                <Button
                  variant="secondary"
                  icon={<PenLine size={16} />}
                  onClick={() => setInk(true)}
                  className="min-h-11"
                >
                  {t("notebooks.page.write")}
                </Button>
              ) : null}
              <LinkedChip page={current} />
            </div>

            {current.canvas === "whiteboard" && sync ? (
              <ConflictKeyed key={current.id} id={current.id}>
                <WhiteboardPage page={current} notebookId={id} sync={sync} label={pageLabel} />
              </ConflictKeyed>
            ) : (
              <PageInk
                key={current.id}
                noteId={current.id}
                paper={paper}
                on={ink}
                onToggle={setInk}
                label={pageLabel}
                offline={fromDevice}
                pending={
                  sync?.queue.some(
                    (q) =>
                      (q.op.kind === "createPage" && q.op.id === current.id) ||
                      (q.op.kind === "createNotebook" && q.op.body.first_page_id === current.id),
                  ) ?? false
                }
                className="w-full max-w-[880px]"
              />
            )}

            <nav aria-label={t("notebooks.page.pages_nav")} className="flex flex-col gap-3 pb-4">
              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  className={iconBtn}
                  aria-label={t("notebooks.page.prev")}
                  title={t("notebooks.page.prev")}
                  disabled={number === null || number <= 1}
                  onClick={() => number && go(number - 1)}
                >
                  <ChevronLeft aria-hidden="true" size={18} />
                </button>
                <button
                  type="button"
                  className={iconBtn}
                  aria-label={t("notebooks.page.next")}
                  title={t("notebooks.page.next")}
                  disabled={number === null || number >= count}
                  onClick={() => number && go(number + 1)}
                >
                  <ChevronRight aria-hidden="true" size={18} />
                </button>
                <span className="mx-1 h-6 w-px bg-line-strong" aria-hidden="true" />
                <Button
                  variant="secondary"
                  icon={<FilePlus2 size={16} />}
                  onClick={() => void addPage("paper")}
                  className="min-h-11"
                  data-add-page=""
                >
                  {t("notebooks.page.add")}
                </Button>
                <Button
                  variant="secondary"
                  icon={<Presentation size={16} />}
                  onClick={() => void addPage("whiteboard")}
                  className="min-h-11"
                  data-add-whiteboard=""
                >
                  {t("notebooks.page.add_whiteboard")}
                </Button>
                <button
                  type="button"
                  className={iconBtn}
                  aria-label={t("notebooks.page.move_earlier")}
                  title={t("notebooks.page.move_earlier")}
                  disabled={number === null || number <= 1}
                  onClick={() => void move(-1)}
                >
                  <ArrowUp aria-hidden="true" size={18} />
                </button>
                <button
                  type="button"
                  className={iconBtn}
                  aria-label={t("notebooks.page.move_later")}
                  title={t("notebooks.page.move_later")}
                  disabled={number === null || number >= count}
                  onClick={() => void move(1)}
                >
                  <ArrowDown aria-hidden="true" size={18} />
                </button>
                <button
                  type="button"
                  className={iconBtn}
                  aria-label={t("notebooks.page.delete")}
                  title={t("notebooks.page.delete")}
                  onClick={() => setConfirmDelete(true)}
                  data-delete-page=""
                >
                  <Trash2 aria-hidden="true" size={18} />
                </button>
                <span className="flex-1" />
                <Button
                  variant="secondary"
                  icon={<FileDown size={16} />}
                  onClick={() => void exportPdf()}
                  disabled={exporting === "busy"}
                  className="min-h-11"
                  data-export-pdf=""
                >
                  {exporting === "busy" ? t("pdf.exporting") : t("pdf.export")}
                </Button>
              </div>
              {exporting === "error" ? (
                <p role="alert" className="m-0 text-amber text-ui-sm">
                  {t(online ? "pdf.error" : "pdf.error_offline")}
                </p>
              ) : null}
              <ol
                className="m-0 flex list-none gap-1.5 overflow-x-auto p-0 pb-1 [scrollbar-width:thin]"
                data-page-strip=""
              >
                {pages.map((p, i) => (
                  <li key={p.id} className="shrink-0">
                    <Link
                      to="/notes/notebooks/$id/$page"
                      params={{ id, page: String(i + 1) }}
                      aria-current={i + 1 === number ? "page" : undefined}
                      aria-label={t(p.canvas === "whiteboard" ? "notebooks.page.go_whiteboard" : "notebooks.page.go", {
                        page: String(i + 1),
                      })}
                      className={cx(
                        "inline-flex min-h-11 min-w-11 items-center justify-center gap-1 rounded-[12px] px-2 font-mono text-[0.8125rem] no-underline",
                        i + 1 === number
                          ? "bg-paper font-semibold text-ground"
                          : "border border-line-control text-text hover:bg-surface-2",
                      )}
                    >
                      {p.canvas === "whiteboard" ? <Presentation aria-hidden="true" size={12} /> : null}
                      {i + 1}
                    </Link>
                  </li>
                ))}
              </ol>
            </nav>
          </>
        )}
      </section>
      <ConfirmDialog
        open={confirmDelete}
        title={t("notebooks.page.delete_title", { page: String(number ?? "") })}
        body={t(
          current?.canvas === "whiteboard" ? "notebooks.page.delete_body_whiteboard" : "notebooks.page.delete_body",
        )}
        confirm={t("notebooks.page.delete_confirm")}
        onClose={() => setConfirmDelete(false)}
        onConfirm={() => void remove()}
      />
    </div>
  );
}
