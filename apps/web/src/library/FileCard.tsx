import { Button, Chip, type ChipTone, cx } from "@naadi/ui";
import { Link } from "@tanstack/react-router";
import {
  BookOpen,
  CircleAlert,
  CircleCheck,
  CircleHelp,
  ClipboardPaste,
  Clock,
  FileArchive,
  FileAudio,
  FileSpreadsheet,
  FileText,
  FileVideo,
  Globe,
  Image,
  Layers,
  Link as LinkIcon,
  LoaderCircle,
  Lock,
  type LucideIcon,
  Presentation,
  RotateCw,
  ShieldCheck,
  Trash2,
  Undo2,
  Upload,
} from "lucide-react";
import { type ReactNode, useEffect, useId, useRef } from "react";
import { useUiText } from "../copy/useUiText";
import { ingestErrorKey, requestErrorKey, useDeleteItem, useRetryItem, useUndoPlacement } from "./api";
import { type Item, isMoving, metaLine, type StatusCode } from "./model";

const KIND_ICON: Record<string, LucideIcon> = {
  pdf: FileText,
  docx: FileText,
  rtf: FileText,
  txt: FileText,
  md: FileText,
  pptx: Presentation,
  xlsx: FileSpreadsheet,
  csv: FileSpreadsheet,
  html: Globe,
  epub: BookOpen,
  image: Image,
  audio: FileAudio,
  video: FileVideo,
  link: LinkIcon,
  anki: Layers,
  zip: FileArchive,
  paste: ClipboardPaste,
};

const STATUS: Record<StatusCode, { icon: ReactNode; tone: ChipTone }> = {
  uploading: { icon: <Upload size={14} />, tone: "done" },
  upload_failed: { icon: <CircleAlert size={14} />, tone: "critical" },
  upload_incomplete: { icon: <CircleAlert size={14} />, tone: "critical" },
  received: { icon: <Clock size={14} />, tone: "done" },
  reading: { icon: <LoaderCircle size={14} className="motion-safe:animate-spin" />, tone: "done" },
  needs_you: { icon: <CircleHelp size={14} />, tone: "assistant" },
  personal: { icon: <ShieldCheck size={14} />, tone: "assistant" },
  placing: { icon: <LoaderCircle size={14} className="motion-safe:animate-spin" />, tone: "done" },
  ready: { icon: <CircleCheck size={14} />, tone: "done" },
  failed: { icon: <CircleAlert size={14} />, tone: "critical" },
  vaulted: { icon: <Lock size={14} />, tone: "done" },
};

export function StatusChip({ code }: { code: StatusCode }) {
  const t = useUiText();
  const s = STATUS[code];
  return (
    <Chip tone={s.tone} icon={s.icon} srPrefix={t("library.status_label")}>
      {t(`library.status.${code}`)}
    </Chip>
  );
}

/** Outline buttons on paper: dark ink instead of the dark-ground outline colours. */
const PAPER_BUTTON = "min-h-11 px-4 border-ground/40! text-ground! hover:bg-ground/10!";

/** The "Your file" mark on a paper card (the Chip's meta tone is made for the dark ground). */
export function YourFileMark() {
  const t = useUiText();
  return (
    <span className="inline-flex items-center gap-1.5 rounded-pill border border-ground/40 px-2.5 py-[5px] font-mono text-[0.6875rem] text-ground leading-[1.2]">
      <FileText aria-hidden="true" size={12} />
      {t("learn.your_file")}
    </span>
  );
}

const ACTION_LINK =
  "inline-flex min-h-11 items-center justify-center gap-2 rounded-pill bg-ground px-4 text-[0.8125rem] font-semibold text-paper no-underline hover:bg-surface-2 hover:text-paper";

function DeleteDialog({ item, open, onClose }: { item: Item; open: boolean; onClose: () => void }) {
  const t = useUiText();
  const ref = useRef<HTMLDialogElement>(null);
  const id = useId();
  const remove = useDeleteItem();
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) {
      remove.reset();
      if (typeof d.showModal === "function") d.showModal();
      else d.setAttribute("open", "");
    } else if (!open && d.open) d.close();
  }, [open, remove.reset]);
  async function confirm() {
    if (remove.isPending) return;
    try {
      await remove.mutateAsync(item.id);
      onClose();
    } catch {
      // worded below
    }
  }
  return (
    <dialog
      ref={ref}
      aria-labelledby={`${id}-title`}
      onClose={onClose}
      className="m-auto w-[min(100%-32px,480px)] rounded-tile-sm border border-line-strong bg-surface-1 p-0 text-text backdrop:bg-ground/70 backdrop:backdrop-blur-sm"
    >
      {open ? (
        <div className="flex flex-col gap-4 p-6">
          <h2 id={`${id}-title`} className="m-0 font-display text-lead [overflow-wrap:anywhere]">
            {t("library.delete_title", { name: item.name ?? t("library.untitled") })}
          </h2>
          <p className="m-0 text-text-2">{item.placed ? t("library.delete_body_placed") : t("library.delete_body")}</p>
          {remove.error ? (
            <p role="alert" className="m-0 text-amber text-ui-sm">
              {t(requestErrorKey(remove.error))}
            </p>
          ) : null}
          <div className="flex flex-wrap justify-end gap-3">
            <Button variant="secondary" onClick={onClose} disabled={remove.isPending}>
              {t("library.delete_cancel")}
            </Button>
            <Button variant="strong" onClick={() => void confirm()} disabled={remove.isPending}>
              {t("library.delete_confirm")}
            </Button>
          </div>
        </div>
      ) : null}
    </dialog>
  );
}

/**
 * One of the student's files, links or pastes (paper: the student's own material). The status is a word with an
 * icon, never a colour alone; while it moves, a progress bar with its value. Ready files open their private
 * chapter; PDFs also open in the PDF viewer.
 */
export function FileCard({
  item,
  code,
  deleting,
  onDelete,
  onCloseDelete,
}: {
  item: Item;
  code: StatusCode;
  deleting: boolean;
  onDelete: () => void;
  onCloseDelete: () => void;
}) {
  const t = useUiText();
  const id = useId();
  const retry = useRetryItem();
  const undo = useUndoPlacement();
  const moving = isMoving(code) || code === "uploading";
  const name = item.name ?? t("library.untitled");
  const failure =
    code === "failed" ? item.error : code === "upload_failed" || code === "upload_incomplete" ? code : null;
  // Only a file that was read (or is being placed) opens in the viewer: not a refused, failed or vaulted one.
  const pdf =
    item.input_kind === "pdf" &&
    item.upload_id &&
    (code === "ready" || code === "needs_you" || code === "placing" || code === "reading");
  const actionError = retry.error ?? undo.error;
  const KindIcon = KIND_ICON[item.input_kind ?? ""] ?? FileText;
  return (
    <li className="flex">
      <article
        aria-labelledby={`${id}-name`}
        data-file-card=""
        data-status={code}
        data-item={item.id}
        className="flex w-full min-w-0 flex-col gap-3 rounded-tile-sm border border-paper bg-paper p-5 text-ground"
      >
        <div className="flex items-start justify-between gap-3">
          <span
            aria-hidden="true"
            className="flex size-11 shrink-0 items-center justify-center rounded-inner bg-ground text-paper"
          >
            <KindIcon size={20} />
          </span>
          <StatusChip code={code} />
        </div>
        <div className="flex min-w-0 flex-col gap-0.5">
          <h3 id={`${id}-name`} className="m-0 font-semibold text-[1rem] leading-snug [overflow-wrap:anywhere]">
            {name}
          </h3>
          <span className="text-paper-text-2 text-ui-sm">{metaLine(t, item)}</span>
          {item.placed?.chapter_id || item.chapter_id ? (
            <span className="mt-1">
              <YourFileMark />
            </span>
          ) : null}
        </div>
        {moving ? (
          <span className="flex items-center gap-2.5">
            <span
              role="progressbar"
              aria-label={t("library.progress_label", { name })}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={item.progress}
              aria-valuetext={t("library.progress_value", {
                pct: String(item.progress),
                status: t(`library.status.${code}`),
              })}
              className="h-1.5 flex-1 overflow-hidden rounded-[3px] bg-ground/15"
            >
              <span
                className="block h-full rounded-[3px] bg-ground transition-[width] duration-(--duration-control)"
                style={{ width: `${Math.max(2, item.progress)}%` }}
              />
            </span>
            <span className="font-mono text-label-sm">
              {t("library.progress_percent", { pct: String(item.progress) })}
            </span>
          </span>
        ) : null}
        {failure ? (
          <p className="m-0 flex items-start gap-2 text-ui-sm" data-failure={failure}>
            <CircleAlert aria-hidden="true" size={16} className="mt-0.5 shrink-0" />
            {t(ingestErrorKey(failure))}
          </p>
        ) : null}
        {code === "vaulted" ? <p className="m-0 text-paper-text-2 text-ui-sm">{t("library.vaulted_note")}</p> : null}
        {actionError ? (
          <p role="alert" className="m-0 text-ui-sm">
            {t(requestErrorKey(actionError))}
          </p>
        ) : null}
        <div className="mt-auto flex flex-wrap gap-2">
          {code === "ready" && (item.placed?.chapter_id || item.chapter_id) ? (
            <Link
              to="/learn/$chapterId"
              params={{ chapterId: (item.placed?.chapter_id ?? item.chapter_id) as string }}
              className={ACTION_LINK}
            >
              <BookOpen aria-hidden="true" size={16} />
              {t("library.open_chapter")}
            </Link>
          ) : null}
          {pdf ? (
            <Link
              to="/learn/library/pdf/$uploadId"
              params={{ uploadId: item.upload_id as string }}
              search={{ page: undefined }}
              className={ACTION_LINK}
            >
              <FileText aria-hidden="true" size={16} />
              {t("library.open_pdf")}
            </Link>
          ) : null}
          {code === "failed" && item.retryable ? (
            <Button
              variant="secondary"
              className={PAPER_BUTTON}
              icon={<RotateCw size={16} />}
              disabled={retry.isPending}
              onClick={() => retry.mutate(item.id)}
            >
              {t("library.retry")}
            </Button>
          ) : null}
          {code === "ready" && item.placed?.can_undo ? (
            <Button
              variant="secondary"
              className={PAPER_BUTTON}
              icon={<Undo2 size={16} />}
              disabled={undo.isPending}
              onClick={() => undo.mutate(item.id)}
            >
              {t("library.undo")}
            </Button>
          ) : null}
          {code !== "placing" && code !== "uploading" ? (
            <Button
              variant="secondary"
              className={PAPER_BUTTON}
              icon={<Trash2 size={16} />}
              aria-haspopup="dialog"
              onClick={onDelete}
              aria-label={t("library.delete_label", { name })}
            >
              {t("library.delete")}
            </Button>
          ) : null}
        </div>
        <DeleteDialog item={item} open={deleting} onClose={onCloseDelete} />
      </article>
    </li>
  );
}

export function cardGridClass(): string {
  return cx("m-0 grid list-none gap-4 p-0", "grid-cols-[repeat(auto-fill,minmax(min(100%,260px),1fr))]");
}
