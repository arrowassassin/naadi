import type { NotebookOut } from "@naadi/shared";
import { Button, cx } from "@naadi/ui";
import { Check } from "lucide-react";
import { type ReactNode, useEffect, useId, useRef, useState } from "react";
import { useUiText } from "../copy/useUiText";
import { COVERS, type Cover, coverClass, coverKey } from "./covers";
import { PaperBackground } from "./PaperBackground";
import { PAPERS, type Paper } from "./paper";

/** A modal <dialog> opened while `open`; Escape and the backdrop close it through `onClose`. */
function Modal({
  open,
  onClose,
  labelledBy,
  describedBy,
  children,
  className,
}: {
  open: boolean;
  onClose: () => void;
  labelledBy: string;
  describedBy?: string;
  children: ReactNode;
  className?: string;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog
      ref={ref}
      aria-labelledby={labelledBy}
      aria-describedby={describedBy}
      onClose={onClose}
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      className={cx(
        "m-auto max-h-[calc(100dvh-32px)] w-[min(520px,calc(100vw-32px))] overflow-y-auto rounded-block border border-line-strong bg-surface-1 p-6 text-text backdrop:bg-ground/70",
        className,
      )}
    >
      {open ? children : null}
    </dialog>
  );
}

export function ConfirmDialog({
  open,
  title,
  body,
  confirm,
  busy,
  onConfirm,
  onClose,
}: {
  open: boolean;
  title: string;
  body: string;
  confirm: string;
  busy?: boolean;
  onConfirm: () => void;
  onClose: () => void;
}) {
  const t = useUiText();
  const id = useId();
  return (
    <Modal open={open} onClose={onClose} labelledBy={`${id}-h`} describedBy={`${id}-d`}>
      <h2 id={`${id}-h`} className="m-0 font-display text-[1.375rem] font-bold">
        {title}
      </h2>
      <p id={`${id}-d`} className="mt-2 mb-5 text-text-2">
        {body}
      </p>
      <div className="flex flex-wrap justify-end gap-2">
        <Button variant="secondary" onClick={onClose} autoFocus>
          {t("notebooks.cancel")}
        </Button>
        <Button variant="strong" onClick={onConfirm} disabled={busy} data-confirm="">
          {confirm}
        </Button>
      </div>
    </Modal>
  );
}

export interface NotebookFormValue {
  title: string;
  cover: Cover;
  paper: Paper;
}

/** New notebook (title, cover, paper) or rename (title, cover). The create button is the dialog's lime action. */
export function NotebookFormDialog({
  open,
  mode,
  initial,
  onSubmit,
  onClose,
}: {
  open: boolean;
  mode: "create" | "edit";
  initial?: Pick<NotebookOut, "title" | "cover" | "paper">;
  onSubmit: (value: NotebookFormValue) => void;
  onClose: () => void;
}) {
  const t = useUiText();
  const id = useId();
  const [title, setTitle] = useState("");
  const [cover, setCover] = useState<Cover>("lime");
  const [paper, setPaper] = useState<Paper>("ruled");
  const [touched, setTouched] = useState(false);

  useEffect(() => {
    if (!open) return;
    setTitle(initial?.title ?? "");
    setCover((initial?.cover as Cover | null) ?? "lime");
    setPaper(initial?.paper ?? "ruled");
    setTouched(false);
  }, [open, initial?.title, initial?.cover, initial?.paper]);

  const clean = title.replace(/\s+/g, " ").trim();
  const invalid = clean.length === 0;
  const tooLong = clean.length > 200;

  return (
    <Modal open={open} onClose={onClose} labelledBy={`${id}-h`}>
      <form
        className="flex flex-col gap-5"
        onSubmit={(e) => {
          e.preventDefault();
          setTouched(true);
          if (invalid || tooLong) return;
          onSubmit({ title: clean, cover, paper });
        }}
        noValidate
      >
        <h2 id={`${id}-h`} className="m-0 font-display text-[1.375rem] font-bold">
          {t(mode === "create" ? "notebooks.dialog_new" : "notebooks.dialog_edit")}
        </h2>
        <label className="flex flex-col gap-2">
          <span className="font-semibold text-ui">{t("notebooks.field_title")}</span>
          <input
            name="title"
            // biome-ignore lint/a11y/noAutofocus: the dialog's only text field; focus moves into the modal
            autoFocus
            value={title}
            maxLength={220}
            onChange={(e) => setTitle(e.target.value)}
            aria-invalid={touched && (invalid || tooLong) ? true : undefined}
            aria-describedby={touched && (invalid || tooLong) ? `${id}-err` : undefined}
            className="min-h-12 rounded-pill border border-line-control bg-ground px-5 text-text placeholder:text-text-3"
            placeholder={t("notebooks.field_title_hint")}
          />
          {touched && (invalid || tooLong) ? (
            <span id={`${id}-err`} className="text-amber text-ui-sm">
              {t(invalid ? "notebooks.field_required" : "notebooks.field_too_long")}
            </span>
          ) : null}
        </label>
        <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
          <legend className="mb-2 p-0 font-semibold text-ui">{t("notebooks.field_cover")}</legend>
          <div className="flex flex-wrap gap-2">
            {COVERS.map((c) => (
              <label key={c} className="relative">
                <input
                  type="radio"
                  name="cover"
                  value={c}
                  checked={cover === c}
                  onChange={() => setCover(c)}
                  className="peer sr-only"
                />
                <span
                  className={cx(
                    "flex size-11 items-center justify-center rounded-full border-2 border-line-control peer-checked:border-paper peer-focus-visible:outline-3 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-violet",
                    coverClass(c).swatch,
                  )}
                >
                  {cover === c ? <Check aria-hidden="true" size={18} /> : null}
                </span>
                <span className="sr-only">{t(coverKey(c))}</span>
              </label>
            ))}
          </div>
          <span className="text-text-2 text-ui-sm" aria-hidden="true">
            {t(coverKey(cover))}
          </span>
        </fieldset>
        {mode === "create" ? (
          <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
            <legend className="mb-2 p-0 font-semibold text-ui">{t("notebooks.field_paper")}</legend>
            <div className="grid grid-cols-5 gap-2">
              {PAPERS.map((p) => (
                <label key={p} className="relative flex flex-col items-center gap-1.5">
                  <input
                    type="radio"
                    name="paper"
                    value={p}
                    checked={paper === p}
                    onChange={() => setPaper(p)}
                    className="peer sr-only"
                  />
                  <span className="block w-full overflow-hidden rounded-[6px] border-2 border-line-control bg-paper peer-checked:border-paper peer-checked:outline-2 peer-checked:outline-offset-2 peer-checked:outline-paper peer-focus-visible:outline-3 peer-focus-visible:outline-violet">
                    <PaperBackground paper={p} thumb />
                  </span>
                  <span className="text-center text-text text-ui-sm">{t(`notebooks.paper.${p}`)}</span>
                </label>
              ))}
            </div>
          </fieldset>
        ) : null}
        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="secondary" onClick={onClose}>
            {t("notebooks.cancel")}
          </Button>
          <Button type="submit" variant="primary">
            {t(mode === "create" ? "notebooks.create" : "notebooks.save")}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
