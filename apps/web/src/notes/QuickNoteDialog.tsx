import { Button } from "@naadi/ui";
import { X } from "lucide-react";
import { type FormEvent, useEffect, useId, useRef, useState } from "react";
import { useUiText } from "../copy/useUiText";
import { useNoteWriter } from "./api";

export const MAX_QUICK_NOTE = 4000;

/** A TipTap doc: the quote as a blockquote, then the student's text (a blank line starts a paragraph). */
export function quickNoteDoc(quote: string, text: string): Record<string, unknown> {
  const paragraphs = text
    .split(/\n\s*\n/)
    .map((p) => p.trim())
    .filter(Boolean)
    .map((p) => ({ type: "paragraph", content: [{ type: "text", text: p }] }));
  return {
    type: "doc",
    content: [
      { type: "blockquote", content: [{ type: "paragraph", content: [{ type: "text", text: quote }] }] },
      ...(paragraphs.length ? paragraphs : [{ type: "paragraph" }]),
    ],
  };
}

/**
 * "Note" from the selection toolbar where there is no page-notes panel (a question's explanation, after answering):
 * a short note with the selected text quoted, saved to Notes anchored to the question (offline: it waits and syncs).
 */
export function QuickNoteDialog({
  open,
  quote,
  anchorType,
  anchorId,
  onClose,
}: {
  open: boolean;
  quote: string;
  anchorType: "question" | "section";
  anchorId: string;
  onClose: () => void;
}) {
  const t = useUiText();
  const ref = useRef<HTMLDialogElement>(null);
  const ids = useId();
  const writer = useNoteWriter();
  const [text, setText] = useState("");

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) {
      setText("");
      d.showModal();
    } else if (!open && d.open) d.close();
  }, [open]);

  function submit(e: FormEvent) {
    e.preventDefault();
    writer.create({ kind: "note", anchor_type: anchorType, anchor_id: anchorId, body: quickNoteDoc(quote, text) });
    onClose();
  }

  return (
    <dialog
      ref={ref}
      aria-labelledby={`${ids}-h`}
      onClose={onClose}
      className="m-auto max-h-[calc(100dvh-32px)] w-[min(100%-32px,36rem)] overflow-y-auto rounded-tile-sm border-0 bg-paper p-0 text-ground backdrop:bg-ground/70"
    >
      <form onSubmit={submit} className="flex flex-col gap-4 p-6">
        <div className="flex items-start justify-between gap-4">
          <h2 id={`${ids}-h`} className="m-0 font-display font-extrabold text-[1.5rem]">
            {t("notes.quick_title")}
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label={t("notes.cancel")}
            className="flex size-11 shrink-0 items-center justify-center rounded-full border border-ground/30 bg-transparent text-ground"
          >
            <X aria-hidden="true" size={18} />
          </button>
        </div>
        <blockquote className="m-0 border-ground border-l-[3px] pl-3 text-paper-text-2">{quote}</blockquote>
        <label htmlFor={`${ids}-text`} className="font-mono text-label text-paper-text-2 uppercase">
          {t("notes.quick_label")}
        </label>
        <textarea
          id={`${ids}-text`}
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={4}
          maxLength={MAX_QUICK_NOTE}
          className="w-full rounded-block-sm border border-ground/30 bg-transparent px-4 py-3 text-[1rem] text-ground placeholder:text-paper-text-2 focus:border-ground focus:outline-none"
        />
        <div className="flex flex-wrap gap-2">
          <Button type="submit" variant="strong" className="bg-ground! text-paper! hover:bg-ground!">
            {t("notes.quick_save")}
          </Button>
          <Button variant="secondary" onClick={onClose} className="border-ground! text-ground! hover:bg-ground/10!">
            {t("notes.cancel")}
          </Button>
        </div>
      </form>
    </dialog>
  );
}
