import { type NotebookOut, uuidv7 } from "@naadi/shared";
import { cx, ErrorState, OfflineBanner, Skeleton } from "@naadi/ui";
import { Link, useNavigate } from "@tanstack/react-router";
import { BookPlus, MoreHorizontal, Pencil, Trash2 } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { useUiText } from "../copy/useUiText";
import { useOnlineStatus } from "../health/useOnlineStatus";
import { ConflictNotices } from "../notes/conflicts";
import { errorKey, useNotebookSync, useNotebooks } from "./api";
import { coverClass } from "./covers";
import { ConfirmDialog, NotebookFormDialog } from "./dialogs";
import { NotebookSaveStatus } from "./SaveStatus";

/** "23 pages" / "1 page" from ui_copy. */
export function usePagesLabel() {
  const t = useUiText();
  return (n: number) => (n === 1 ? t("notebooks.pages_one") : t("notebooks.pages", { count: n.toLocaleString() }));
}

function CoverMenu({ nb, onRename, onDelete }: { nb: NotebookOut; onRename: () => void; onDelete: () => void }) {
  const t = useUiText();
  const [open, setOpen] = useState(false);
  const id = useId();
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: Event) => {
      if (!box.current?.contains(e.target as Node)) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("pointerdown", close);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);
  const item =
    "flex min-h-11 w-full items-center gap-2 rounded-[12px] px-3 text-left text-text text-ui hover:bg-surface-2 focus-visible:bg-surface-2";
  return (
    <div ref={box} className="absolute top-2 right-2">
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        aria-label={t("notebooks.menu", { title: nb.title })}
        onClick={() => setOpen((o) => !o)}
        className="inline-flex size-11 items-center justify-center rounded-full bg-ground/80 text-text hover:bg-ground"
      >
        <MoreHorizontal aria-hidden="true" size={18} />
      </button>
      {open ? (
        <div
          id={id}
          role="menu"
          aria-label={t("notebooks.menu", { title: nb.title })}
          className="absolute top-12 right-0 z-20 flex w-48 flex-col gap-0.5 rounded-[16px] border border-line-strong bg-surface-1 p-1.5 shadow-lg"
        >
          <button
            type="button"
            role="menuitem"
            className={item}
            onClick={() => {
              setOpen(false);
              onRename();
            }}
          >
            <Pencil aria-hidden="true" size={16} />
            {t("notebooks.rename")}
          </button>
          <button
            type="button"
            role="menuitem"
            className={cx(item, "text-amber")}
            onClick={() => {
              setOpen(false);
              onDelete();
            }}
          >
            <Trash2 aria-hidden="true" size={16} />
            {t("notebooks.delete")}
          </button>
        </div>
      ) : null}
    </div>
  );
}

const coverBase =
  "flex aspect-[4/5] min-h-[176px] flex-col justify-between rounded-[10px_24px_24px_10px] border-l-[10px] p-4 no-underline transition-transform duration-(--duration-control) ease-out hover:-translate-y-0.5 focus-visible:outline-3 focus-visible:outline-offset-3 focus-visible:outline-violet";

/**
 * The notebook shelf (design/Notebook.dc.html): covers in their token colour with the page count, a dashed
 * "New notebook" tile (title, cover, paper), rename and delete with a confirmation, and every state: loading,
 * empty, error, offline (the device copy, edits queued with "will sync"). WEB-A shows it as the Notes page's
 * Notebooks tab; `/notes/notebooks` shows it alone.
 */
export function NotebooksShelf({ className, headingLevel = 2 }: { className?: string; headingLevel?: 1 | 2 }) {
  const t = useUiText();
  const pages = usePagesLabel();
  const online = useOnlineStatus();
  const navigate = useNavigate();
  const sync = useNotebookSync();
  const { query, items, fromDevice } = useNotebooks();
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<NotebookOut | null>(null);
  const [deleting, setDeleting] = useState<NotebookOut | null>(null);
  const H = headingLevel === 1 ? "h1" : "h2";

  const create = async (v: { title: string; cover: NotebookOut["cover"]; paper: NotebookOut["paper"] }) => {
    if (!sync) return;
    const id = uuidv7();
    const firstPage = uuidv7();
    setCreating(false);
    await sync.add({
      kind: "createNotebook",
      id,
      body: { id, first_page_id: firstPage, title: v.title, cover: v.cover, paper: v.paper },
    });
    void navigate({ to: "/notes/notebooks/$id/$page", params: { id, page: "1" } });
  };

  const header = (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <H className="m-0 font-display text-headline">{t("notebooks.title")}</H>
      {sync ? <NotebookSaveStatus sync={sync} notebookId={null} /> : null}
      <ConflictNotices filter={{ scope: "notebooks" }} className="w-full" />
    </div>
  );

  let body: React.ReactNode;
  if (!items && (query.isPending || !sync?.ready)) {
    body = (
      <div aria-busy="true" className="grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-4">
        <span className="sr-only">{t("notebooks.loading")}</span>
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} className="aspect-[4/5] min-h-[176px] w-full rounded-[10px_24px_24px_10px]" />
        ))}
      </div>
    );
  } else if (!items) {
    const offline = !online || errorKey(query.error) === "notebooks.error.unreachable";
    body = offline ? (
      <OfflineBanner message={t("notebooks.offline_none")} />
    ) : (
      <ErrorState
        title={t("notebooks.error_title")}
        message={t(errorKey(query.error))}
        requestId={(query.error as { requestId?: string | null } | null)?.requestId ?? null}
        onRetry={() => void query.refetch()}
        retryLabel={t("notebooks.retry")}
        retrying={query.isFetching}
      />
    );
  } else {
    body = (
      <>
        {fromDevice || !online ? <OfflineBanner message={t("notebooks.offline")} /> : null}
        {items.length === 0 ? (
          <div className="flex flex-col gap-1" data-notebooks-empty="">
            <p className="m-0 font-semibold text-text">{t("notebooks.empty_title")}</p>
            <p className="m-0 text-text-2">{t("notebooks.empty_body")}</p>
          </div>
        ) : null}
        <ul
          className="m-0 grid list-none grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-4 p-0"
          data-notebook-shelf=""
        >
          {items.map((nb) => (
            <li key={nb.id} className="relative" data-notebook={nb.id}>
              <Link
                to="/notes/notebooks/$id/$page"
                params={{ id: nb.id, page: "1" }}
                className={cx(coverBase, coverClass(nb.cover).cover)}
              >
                <span className="font-mono text-[0.6875rem] tracking-[0.1em] uppercase opacity-80">
                  {pages(nb.page_count)}
                </span>
                <span className="font-display text-[1.625rem] leading-[0.98] font-extrabold tracking-[-0.03em] break-words">
                  {nb.title}
                </span>
              </Link>
              <CoverMenu nb={nb} onRename={() => setEditing(nb)} onDelete={() => setDeleting(nb)} />
            </li>
          ))}
          <li>
            <button
              type="button"
              onClick={() => setCreating(true)}
              className={cx(
                coverBase,
                "w-full border-2 border-dashed border-line-dashed bg-transparent text-left text-text-2 hover:border-text-3 hover:text-text",
              )}
              data-new-notebook=""
            >
              <BookPlus aria-hidden="true" size={22} />
              <span className="flex flex-col gap-1">
                <span className="font-display text-[1.375rem] leading-none font-extrabold tracking-[-0.02em] text-text">
                  {t("notebooks.new")}
                </span>
                <span className="text-ui-sm">{t("notebooks.new_papers")}</span>
              </span>
            </button>
          </li>
        </ul>
      </>
    );
  }

  return (
    <section className={cx("flex flex-col gap-5", className)} aria-label={t("notebooks.title")} data-notebooks="">
      {header}
      {body}
      <NotebookFormDialog
        open={creating}
        mode="create"
        onClose={() => setCreating(false)}
        onSubmit={(v) => void create(v)}
      />
      <NotebookFormDialog
        open={editing !== null}
        mode="edit"
        initial={editing ?? undefined}
        onClose={() => setEditing(null)}
        onSubmit={(v) => {
          const nb = editing;
          setEditing(null);
          if (nb && sync)
            void sync.add({ kind: "patchNotebook", id: nb.id, patch: { title: v.title, cover: v.cover } });
        }}
      />
      <ConfirmDialog
        open={deleting !== null}
        title={t("notebooks.delete_title", { title: deleting?.title ?? "" })}
        body={deleting ? t("notebooks.delete_body", { pages: pages(deleting.page_count) }) : ""}
        confirm={t("notebooks.delete_confirm")}
        onClose={() => setDeleting(null)}
        onConfirm={() => {
          const nb = deleting;
          setDeleting(null);
          if (nb && sync) void sync.add({ kind: "deleteNotebook", id: nb.id });
        }}
      />
    </section>
  );
}
