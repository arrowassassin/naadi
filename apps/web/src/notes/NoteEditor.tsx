import { cx } from "@naadi/ui";
import { type Editor, EditorContent, useEditor, useEditorState } from "@tiptap/react";
import { Bold, Heading2, ImagePlus, Italic, List, ListOrdered } from "lucide-react";
import { type ReactNode, useEffect, useId, useRef, useState } from "react";
import { useUiText } from "../copy/useUiText";
import { HIGHLIGHT_COLORS, type HighlightColor } from "../highlights/anchor";
import { SWATCH_CLASS, WORD_KEY } from "../highlights/Mark";
import { EMPTY_DOC, noteExtensions } from "./extensions";
import { stripPastedHtml } from "./pasteHtml";
import { IMAGE_TYPES, imagesAvailable, type UploadFailure, uploadNoteImage } from "./storage";
import "./notes.css";

export interface NoteEditorProps {
  /** The note id (images are stored under it). Key the component by note to start a fresh editor. */
  noteId: string;
  /** The stored TipTap JSON (read once per note id; later edits come from the editor itself). */
  initial: unknown;
  onChange: (doc: Record<string, unknown>) => void;
  /** Accessible name of the writing area. */
  label: string;
  placeholder: string;
  editable?: boolean;
  /** Smaller type and toolbar (the Reader's page-notes panel). */
  compact?: boolean;
  /** The editor once it exists (insert a quote, focus). */
  onEditor?: (editor: Editor | null) => void;
  /**
   * A quote to add at the end of the note as a block quote (the selection toolbar's Note). It is inserted once the
   * editor exists and is attached to the page, however late that is, then `onQuoteInserted(id)` is called.
   */
  quote?: { id: number; text: string } | null;
  onQuoteInserted?: (id: number) => void;
  className?: string;
}

function validDoc(v: unknown): Record<string, unknown> {
  return v && typeof v === "object" && (v as { type?: unknown }).type === "doc"
    ? (v as Record<string, unknown>)
    : { ...EMPTY_DOC };
}

function ToolButton({
  label,
  pressed,
  onClick,
  children,
  wide,
  disabled,
}: {
  label: string;
  pressed?: boolean;
  onClick: () => void;
  children: ReactNode;
  wide?: boolean;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      aria-label={wide ? undefined : label}
      aria-pressed={pressed}
      disabled={disabled}
      // Keep the editor's selection: formatting applies to what is selected.
      onMouseDown={(e) => e.preventDefault()}
      onClick={onClick}
      className={cx(
        "flex min-h-11 min-w-11 shrink-0 items-center justify-center gap-1.5 rounded-pill border-0 px-2.5 text-[0.8125rem]",
        "focus-visible:outline-2 focus-visible:outline-ground disabled:opacity-40",
        pressed ? "bg-ground text-paper" : "bg-transparent text-ground hover:bg-ground/10",
      )}
    >
      {children}
    </button>
  );
}

const UPLOAD_KEY: Record<UploadFailure, string> = {
  type: "notes.image_error_type",
  size: "notes.image_error_size",
  unavailable: "notes.image_error_unavailable",
  failed: "notes.image_error_failed",
};

/**
 * The note sheet's editor (TipTap 3.34.0) on paper: headings, lists, bold, italic, the three highlight colours
 * (each with its word) and images from the student's uploads. Never logs or sends the body anywhere but `onChange`.
 */
export function NoteEditor({
  noteId,
  initial,
  onChange,
  label,
  placeholder,
  editable = true,
  compact,
  onEditor,
  quote,
  onQuoteInserted,
  className,
}: NoteEditorProps) {
  const t = useUiText();
  const ids = useId();
  const changeRef = useRef(onChange);
  const lastJson = useRef<string | null>(null);
  changeRef.current = onChange;
  const fileRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const images = imagesAvailable();
  const idRef = useRef(noteId);
  idRef.current = noteId;

  const editor = useEditor(
    {
      extensions: noteExtensions(placeholder, images),
      content: validDoc(initial),
      editable,
      // The production CSP allows no inline <style>: the editor's base CSS is in notes.css.
      injectCSS: false,
      editorProps: {
        // Copied pages carry style attributes: strip them before parsing, so the strict CSP logs nothing.
        transformPastedHTML: stripPastedHtml,
        attributes: {
          "aria-label": label,
          "aria-multiline": "true",
          role: "textbox",
          "data-note-editor": "",
          class: cx(
            "note-prose min-h-[8rem] text-ground outline-none",
            compact ? "text-[1rem] leading-relaxed" : "text-[1.1875rem] leading-[1.6]",
          ),
        },
      },
      onCreate: ({ editor: e }) => {
        lastJson.current = JSON.stringify(e.getJSON());
      },
      // Only real changes reach the caller (a selection or a normalisation is not an edit).
      onUpdate: ({ editor: e }) => {
        const doc = e.getJSON() as Record<string, unknown>;
        const json = JSON.stringify(doc);
        if (json === lastJson.current) return;
        lastJson.current = json;
        changeRef.current(doc);
      },
    },
    // One editor per mount: callers key the component by note.
    [],
  );

  useEffect(() => {
    onEditor?.(editor);
    return () => onEditor?.(null);
  }, [editor, onEditor]);

  const insertedRef = useRef(onQuoteInserted);
  insertedRef.current = onQuoteInserted;
  useEffect(() => {
    if (!editor || !quote) return;
    let frame = 0;
    let tries = 0;
    const insert = () => {
      if (editor.isDestroyed) return;
      editor
        .chain()
        .focus("end")
        .insertContent([
          { type: "blockquote", content: [{ type: "paragraph", content: [{ type: "text", text: quote.text }] }] },
          { type: "paragraph" },
        ])
        .run();
      insertedRef.current?.(quote.id);
    };
    // A fresh editor's view is attached to the page a frame later: insert once it is (bounded retries).
    const attempt = () => {
      if (editor.isDestroyed) return;
      if (editor.view.dom.isConnected || tries++ >= 60) insert();
      else frame = window.requestAnimationFrame(attempt);
    };
    attempt();
    return () => window.cancelAnimationFrame(frame);
  }, [editor, quote]);

  useEffect(() => {
    // false: changing editability is not an edit (no update event, nothing saved).
    if (editor && editor.isEditable !== editable) editor.setEditable(editable, false);
  }, [editor, editable]);

  const active = useEditorState({
    editor,
    selector: ({ editor: e }) =>
      e
        ? {
            h2: e.isActive("heading", { level: 2 }),
            bullet: e.isActive("bulletList"),
            ordered: e.isActive("orderedList"),
            bold: e.isActive("bold"),
            italic: e.isActive("italic"),
            color: (HIGHLIGHT_COLORS.find((c) => e.isActive("highlight", { color: c })) ??
              null) as HighlightColor | null,
          }
        : null,
  });

  async function addImage(file: File) {
    if (!editor) return;
    setUploadError(null);
    if (!(IMAGE_TYPES as readonly string[]).includes(file.type)) {
      setUploadError(t(UPLOAD_KEY.type));
      return;
    }
    setUploading(true);
    const out = await uploadNoteImage(idRef.current, file);
    setUploading(false);
    if ("error" in out) {
      setUploadError(t(UPLOAD_KEY[out.error]));
      return;
    }
    editor
      .chain()
      .focus()
      .insertContent({ type: "image", attrs: { path: out.path } })
      .run();
  }

  const chain = () => editor?.chain().focus();

  return (
    <div className={cx("flex flex-col gap-3", className)}>
      {editable ? (
        <div
          role="toolbar"
          aria-label={t("notes.format_toolbar")}
          aria-controls={`${ids}-ed`}
          className="flex flex-wrap items-center gap-1 rounded-block-sm bg-ground/5 p-1"
        >
          <ToolButton
            label={t("notes.format_heading")}
            pressed={active?.h2}
            onClick={() => chain()?.toggleHeading({ level: 2 }).run()}
          >
            <Heading2 aria-hidden="true" size={18} />
          </ToolButton>
          <ToolButton
            label={t("notes.format_bullets")}
            pressed={active?.bullet}
            onClick={() => chain()?.toggleBulletList().run()}
          >
            <List aria-hidden="true" size={18} />
          </ToolButton>
          <ToolButton
            label={t("notes.format_numbers")}
            pressed={active?.ordered}
            onClick={() => chain()?.toggleOrderedList().run()}
          >
            <ListOrdered aria-hidden="true" size={18} />
          </ToolButton>
          <ToolButton label={t("notes.format_bold")} pressed={active?.bold} onClick={() => chain()?.toggleBold().run()}>
            <Bold aria-hidden="true" size={18} />
          </ToolButton>
          <ToolButton
            label={t("notes.format_italic")}
            pressed={active?.italic}
            onClick={() => chain()?.toggleItalic().run()}
          >
            <Italic aria-hidden="true" size={18} />
          </ToolButton>
          <span aria-hidden="true" className="mx-1 h-6 w-px bg-ground/20" />
          {HIGHLIGHT_COLORS.map((c) => (
            <ToolButton
              key={c}
              wide
              label={t(WORD_KEY[c])}
              pressed={active?.color === c}
              onClick={() => chain()?.toggleHighlight({ color: c }).run()}
            >
              <span
                aria-hidden="true"
                className={cx("size-5 shrink-0 rounded-full border-2 border-ground", SWATCH_CLASS[c])}
              />
              <span className={compact ? "text-[0.75rem]" : undefined}>{t(WORD_KEY[c])}</span>
            </ToolButton>
          ))}
          {images ? (
            <>
              <span aria-hidden="true" className="mx-1 h-6 w-px bg-ground/20" />
              <ToolButton
                label={uploading ? t("notes.image_uploading") : t("notes.image_add")}
                disabled={uploading}
                onClick={() => fileRef.current?.click()}
              >
                <ImagePlus aria-hidden="true" size={18} />
              </ToolButton>
              <input
                ref={fileRef}
                type="file"
                accept={IMAGE_TYPES.join(",")}
                tabIndex={-1}
                aria-hidden="true"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  e.target.value = "";
                  if (f) void addImage(f);
                }}
              />
            </>
          ) : null}
        </div>
      ) : null}
      {uploadError ? (
        <p role="alert" className="m-0 text-paper-text-2 text-ui-sm">
          {uploadError}
        </p>
      ) : null}
      <EditorContent
        className={compact ? "flex flex-1 flex-col [&>.note-prose]:flex-1" : undefined}
        editor={editor}
        id={`${ids}-ed`}
      />
    </div>
  );
}
