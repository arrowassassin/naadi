import { Button, ErrorState, Skeleton } from "@naadi/ui";
import { Check, MessageSquarePlus, Pencil, Search, Trash2, X } from "lucide-react";
import { type FormEvent, useEffect, useId, useState } from "react";
import { ApiError } from "../auth/apiError";
import { useUiText } from "../copy/useUiText";
import { useDeleteThread, useRenameThread, useThreads } from "./api";
import type { ThreadItem } from "./protocol";
import { formatWhen } from "./time";

const INPUT =
  "min-h-12 w-full rounded-pill border border-line-control bg-ground px-4 text-text text-ui placeholder:text-text-3 focus-visible:border-violet";
const ICON_BTN =
  "flex size-11 shrink-0 items-center justify-center rounded-pill text-text-2 hover:bg-surface-2 hover:text-text disabled:cursor-not-allowed disabled:text-text-muted";

function useDebounced(value: string, ms: number): string {
  const [v, setV] = useState(value);
  useEffect(() => {
    const id = window.setTimeout(() => setV(value), ms);
    return () => window.clearTimeout(id);
  }, [value, ms]);
  return v;
}

function Row({
  thread,
  current,
  onOpen,
  onDeleted,
}: {
  thread: ThreadItem;
  current: boolean;
  onOpen: () => void;
  onDeleted: () => void;
}) {
  const t = useUiText();
  const rename = useRenameThread();
  const del = useDeleteThread();
  const [mode, setMode] = useState<"view" | "rename" | "delete">("view");
  const [title, setTitle] = useState(thread.title);
  const inputId = useId();
  const when = formatWhen(thread.last_message_at ?? thread.created_at);

  const save = (e: FormEvent) => {
    e.preventDefault();
    const next = title.trim();
    if (!next || next === thread.title) {
      setMode("view");
      return;
    }
    rename.mutate({ id: thread.id, title: next.slice(0, 80) }, { onSuccess: () => setMode("view") });
  };

  if (mode === "rename") {
    return (
      <li className="rounded-block border border-line p-2">
        <form onSubmit={save} className="flex items-center gap-1">
          <label htmlFor={inputId} className="sr-only">
            {t("agent.threads.rename_label")}
          </label>
          <input
            id={inputId}
            value={title}
            maxLength={80}
            onChange={(e) => setTitle(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                e.stopPropagation();
                setMode("view");
              }
            }}
            // biome-ignore lint/a11y/noAutofocus: the student just asked to rename this row
            autoFocus
            className={INPUT}
          />
          <button
            type="submit"
            aria-label={t("agent.threads.rename_save")}
            disabled={rename.isPending}
            className={ICON_BTN}
          >
            <Check aria-hidden="true" size={18} />
          </button>
          <button
            type="button"
            aria-label={t("agent.threads.rename_cancel")}
            onClick={() => setMode("view")}
            className={ICON_BTN}
          >
            <X aria-hidden="true" size={18} />
          </button>
        </form>
        {rename.isError ? (
          <p role="alert" className="m-0 px-2 pt-1 text-amber text-ui-sm">
            {t("agent.threads.rename_failed")}
          </p>
        ) : null}
      </li>
    );
  }

  if (mode === "delete") {
    return (
      <li className="flex flex-col gap-2 rounded-block border border-amber/60 p-3" data-thread-delete="">
        <p className="m-0 text-text">{t("agent.threads.delete_confirm", { title: thread.title })}</p>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="secondary"
            className="border-amber text-amber"
            icon={<Trash2 size={16} />}
            disabled={del.isPending}
            // mutateAsync: the row unmounts once the list refetches, so a per-call onSuccess would never run.
            onClick={() => void del.mutateAsync(thread.id).then(onDeleted, () => undefined)}
          >
            {t("agent.threads.delete")}
          </Button>
          <Button variant="secondary" disabled={del.isPending} onClick={() => setMode("view")}>
            {t("agent.threads.keep")}
          </Button>
        </div>
        {del.isError ? (
          <p role="alert" className="m-0 text-amber text-ui-sm">
            {t("agent.threads.delete_failed")}
          </p>
        ) : null}
      </li>
    );
  }

  return (
    <li className="flex items-center gap-1 rounded-block hover:bg-surface-1" data-thread-row={thread.id}>
      <button
        type="button"
        onClick={onOpen}
        disabled={!thread.aegra_thread_id}
        aria-current={current ? "true" : undefined}
        className="flex min-h-12 min-w-0 flex-1 flex-col items-start justify-center rounded-block px-3 py-2 text-left disabled:cursor-not-allowed"
      >
        <span className="w-full truncate font-semibold text-text">{thread.title}</span>
        <span className="text-text-3 text-ui-sm">{current ? t("agent.threads.current", { when }) : when}</span>
      </button>
      <button
        type="button"
        onClick={() => setMode("rename")}
        aria-label={t("agent.threads.rename", { title: thread.title })}
        className={ICON_BTN}
      >
        <Pencil aria-hidden="true" size={16} />
      </button>
      <button
        type="button"
        onClick={() => setMode("delete")}
        aria-label={t("agent.threads.delete_named", { title: thread.title })}
        className={ICON_BTN}
      >
        <Trash2 aria-hidden="true" size={16} />
      </button>
    </li>
  );
}

export function ThreadList({
  currentThreadId,
  onOpen,
  onNew,
}: {
  currentThreadId: string | null;
  onOpen: (aegraThreadId: string) => void;
  onNew: () => void;
}) {
  const t = useUiText();
  const [q, setQ] = useState("");
  const query = useDebounced(q, 250);
  const threads = useThreads(query);
  const searchId = useId();
  const items = threads.data?.items ?? [];

  return (
    <section
      aria-label={t("agent.threads.title")}
      className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-4 py-4 tablet:px-6"
      data-thread-list=""
    >
      <Button variant="assistant" icon={<MessageSquarePlus size={18} />} onClick={onNew} className="self-start">
        {t("agent.threads.new")}
      </Button>
      <div className="relative">
        <label htmlFor={searchId} className="sr-only">
          {t("agent.threads.search")}
        </label>
        <Search
          aria-hidden="true"
          size={16}
          className="pointer-events-none absolute top-1/2 left-4 -translate-y-1/2 text-text-3"
        />
        <input
          id={searchId}
          type="search"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder={t("agent.threads.search")}
          className={`${INPUT} pl-10`}
        />
      </div>
      {threads.isPending && !threads.data ? (
        <div className="flex flex-col gap-2" aria-busy="true">
          <Skeleton className="h-14 w-full rounded-block" />
          <Skeleton className="h-14 w-full rounded-block" />
        </div>
      ) : threads.isError && !threads.data ? (
        <ErrorState
          title={t("agent.threads.load_failed")}
          message={
            threads.error instanceof ApiError && threads.error.status === 409
              ? t("agent.exam_mode")
              : t("agent.error.generic")
          }
          requestId={threads.error instanceof ApiError ? threads.error.requestId : null}
          onRetry={() => void threads.refetch()}
          retryLabel={t("agent.error.retry")}
        />
      ) : items.length === 0 ? (
        <p className="m-0 text-text-2">{t(query ? "agent.threads.no_match" : "agent.threads.empty")}</p>
      ) : (
        <ul className="m-0 flex list-none flex-col gap-1 p-0">
          {items.map((th) => (
            <Row
              key={th.id}
              thread={th}
              current={th.aegra_thread_id !== null && th.aegra_thread_id === currentThreadId}
              onOpen={() => th.aegra_thread_id && onOpen(th.aegra_thread_id)}
              onDeleted={() => {
                if (th.aegra_thread_id === currentThreadId) onNew();
              }}
            />
          ))}
        </ul>
      )}
    </section>
  );
}
