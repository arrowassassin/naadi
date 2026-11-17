import { Button, cx } from "@naadi/ui";
import { Link } from "@tanstack/react-router";
import { ArrowLeft, BookOpen, Sparkles, Trash2 } from "lucide-react";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { askAssistant, useAssistantAvailable } from "../assistant/ask";
import { useRegisterPageContext } from "../assistant/pageContext";
import { useUiText } from "../copy/useUiText";
import { beaconNotes, type Note, useNoteWriter, useUserId } from "./api";
import { ExportNotePdf } from "./ExportPdf";
import { EMPTY_DOC } from "./extensions";
import { editedLabel, sourceLabel } from "./format";
import { NoteEditor } from "./NoteEditor";
import { SAVE_DEBOUNCE_MS } from "./PageNotesPanel";
import { NoteSaveStatus } from "./SaveStatus";

/**
 * The note sheet (Notes board, centre): a paper page with the note's title, where it is from, the TipTap editor
 * and Export PDF. Edits save after a short pause through the offline queue. Summarise / Turn into flashcards /
 * Quiz me hand the note to the assistant (segment 14) and show only while it can answer (feature on, no exam);
 * flashcards end in its confirm chip, and a quiz stays in the chat (never stored as questions).
 */
export function NoteSheet({ note, onBack, onDeleted }: { note: Note; onBack?: () => void; onDeleted: () => void }) {
  const t = useUiText();
  const ids = useId();
  const assistantOn = useAssistantAvailable();
  useRegisterPageContext({ type: "note", id: note.id, title: note.title ?? undefined });
  const writer = useNoteWriter();
  const [title, setTitle] = useState(note.title ?? "");
  const [confirm, setConfirm] = useState(false);
  const pending = useRef<Record<string, unknown>>({});
  const [dirty, setDirty] = useState(false);
  const timer = useRef(0);
  const live = useRef<Record<string, unknown> | null>(null);

  const flush = useCallback(() => {
    window.clearTimeout(timer.current);
    const patch = pending.current;
    pending.current = {};
    setDirty(false);
    if (Object.keys(patch).length) writer.update(note.id, patch);
  }, [writer, note.id]);

  const queue = useCallback(
    (patch: Record<string, unknown>) => {
      pending.current = { ...pending.current, ...patch };
      setDirty(true);
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(flush, SAVE_DEBOUNCE_MS);
    },
    [flush],
  );

  const flushRef = useRef(flush);
  flushRef.current = flush;
  const userId = useUserId();
  const userRef = useRef(userId);
  userRef.current = userId;
  useEffect(() => {
    const out = (e?: Event) => {
      flushRef.current();
      if (e?.type === "pagehide") beaconNotes(userRef.current);
    };
    window.addEventListener("pagehide", out);
    document.addEventListener("visibilitychange", out);
    return () => {
      window.removeEventListener("pagehide", out);
      document.removeEventListener("visibilitychange", out);
      out();
    };
  }, []);

  const source = sourceLabel(t, note);
  const s = note.source;
  const meta = [source, editedLabel(t, note.updated_at)].filter(Boolean).join(t("notes.separator"));
  const onChange = useCallback(
    (doc: Record<string, unknown>) => {
      live.current = doc;
      queue({ body: doc });
    },
    [queue],
  );

  return (
    <article
      aria-labelledby={`${ids}-title`}
      data-note-sheet={note.id}
      className="flex min-w-0 flex-col gap-5 rounded-tile bg-paper p-[clamp(20px,4vw,56px)] text-ground"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        {onBack ? (
          <button
            type="button"
            onClick={() => {
              flush();
              onBack();
            }}
            aria-label={t("notes.back_to_list")}
            className="flex size-11 items-center tablet:hidden justify-center rounded-pill border border-ground/30 bg-transparent text-ground"
          >
            <ArrowLeft aria-hidden="true" size={18} />
          </button>
        ) : null}
        <span className="mr-auto min-w-0 font-mono text-label-sm text-paper-text-2 uppercase tracking-[0.12em]">
          {meta}
        </span>
        <NoteSaveStatus ids={[note.id]} tone="paper" dirty={dirty} />
      </div>
      <label htmlFor={`${ids}-title`} className="sr-only">
        {t("notes.title_label")}
      </label>
      <input
        id={`${ids}-title`}
        value={title}
        maxLength={200}
        placeholder={note.source?.section_heading ?? t("notes.title_placeholder")}
        onChange={(e) => {
          setTitle(e.target.value);
          queue({ title: e.target.value.trim() ? e.target.value : null });
        }}
        className="w-full border-0 bg-transparent p-0 font-display font-extrabold text-[clamp(2rem,1.2rem+3vw,4rem)] text-ground leading-[0.95] tracking-[-0.04em] outline-none placeholder:text-paper-text-2/60 focus-visible:underline focus-visible:decoration-2"
      />
      {s?.type === "section" && s.chapter_id && s.section_id ? (
        <Link
          to="/learn/$chapterId/$sectionId"
          params={{ chapterId: s.chapter_id, sectionId: s.section_id }}
          className="inline-flex min-h-11 w-fit items-center gap-2 rounded-pill border border-ground/30 px-4 text-ground text-ui-sm no-underline hover:text-ground"
        >
          <BookOpen aria-hidden="true" size={15} />
          {t("notes.open_in_chapter")}
        </Link>
      ) : null}
      {note.body_omitted ? (
        <p className="m-0 text-paper-text-2">{t("notes.body_omitted")}</p>
      ) : (
        <NoteEditor
          key={note.id}
          noteId={note.id}
          initial={note.body ?? EMPTY_DOC}
          onChange={onChange}
          label={t("notes.editor_label")}
          placeholder={t("notes.editor_placeholder")}
        />
      )}
      {assistantOn ? (
        <div className="flex flex-wrap items-center gap-2" data-note-assistant="">
          {(
            [
              ["summarise", "agent.chip.summarise_note"],
              ["flashcards", "agent.chip.flashcards"],
              ["quiz", "agent.chip.quiz_me"],
            ] as const
          ).map(([kind, key]) => (
            <Button
              key={kind}
              variant="assistant"
              icon={<Sparkles size={16} />}
              onClick={() => {
                flush();
                askAssistant({ kind, context: { type: "note", id: note.id } });
              }}
            >
              {t(key)}
            </Button>
          ))}
        </div>
      ) : null}
      <div className={cx("flex flex-wrap items-center gap-2 border-ground/15 border-t pt-4")}>
        <ExportNotePdf note={note} body={() => live.current ?? note.body} title={title} />
        {confirm ? (
          <span role="alert" className="flex flex-wrap items-center gap-2">
            <span className="text-ui-sm">{t("notes.delete_confirm")}</span>
            <Button
              variant="secondary"
              onClick={() => {
                window.clearTimeout(timer.current);
                pending.current = {};
                writer.remove(note.id);
                onDeleted();
              }}
              className="border-ground! text-ground! hover:bg-ground/10!"
            >
              {t("notes.delete_yes")}
            </Button>
            <Button
              variant="secondary"
              onClick={() => setConfirm(false)}
              className="border-ground! text-ground! hover:bg-ground/10!"
            >
              {t("notes.cancel")}
            </Button>
          </span>
        ) : (
          <Button
            variant="secondary"
            icon={<Trash2 size={16} />}
            onClick={() => setConfirm(true)}
            className="ml-auto border-ground/40! text-ground! hover:bg-ground/10!"
          >
            {t("notes.delete")}
          </Button>
        )}
      </div>
    </article>
  );
}
