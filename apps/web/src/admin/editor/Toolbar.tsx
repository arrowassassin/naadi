import { CALLOUT_VARIANTS, HIGHLIGHT_COLORS, HIGHLIGHT_MEANING_BY_COLOR } from "@naadi/shared/content";
import { cx } from "@naadi/ui";
import { type Editor, useEditorState } from "@tiptap/react";
import {
  Bold,
  Columns3,
  FileText,
  Image,
  Italic,
  List,
  ListChecks,
  ListOrdered,
  type LucideIcon,
  MessageSquareQuote,
  Network,
  Play,
  Redo2,
  Rows3,
  Sigma,
  Subscript,
  Superscript,
  Table,
  Trash2,
  Undo2,
} from "lucide-react";
import { type ReactNode, useId } from "react";
import { useT } from "../components/ui";

export const CALLOUT_KEYS: Record<string, string> = {
  pearl: "admin.editor.callout.pearl",
  trap: "admin.editor.callout.trap",
  bridge: "admin.editor.callout.bridge",
};
export const HIGHLIGHT_KEYS: Record<string, string> = {
  key_fact: "admin.editor.highlight.key_fact",
  formula: "admin.editor.highlight.formula",
  mistake: "admin.editor.highlight.mistake",
};

function Tool({
  label,
  Icon,
  onClick,
  pressed,
  disabled,
  children,
}: {
  label: string;
  Icon?: LucideIcon;
  onClick: () => void;
  pressed?: boolean;
  disabled?: boolean;
  children?: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={children ? undefined : label}
      title={label}
      aria-pressed={pressed}
      disabled={disabled}
      // Keep the editor's selection: the command applies to what is selected.
      onMouseDown={(e) => e.preventDefault()}
      onClick={onClick}
      className={cx(
        "flex min-h-11 min-w-11 shrink-0 items-center justify-center gap-1.5 rounded-pill border px-2.5 text-ui-sm",
        "disabled:cursor-not-allowed disabled:border-dashed disabled:border-line-dashed disabled:text-text-muted",
        pressed ? "border-paper bg-paper text-ground" : "border-transparent text-text hover:bg-surface-2",
      )}
    >
      {Icon ? <Icon aria-hidden="true" size={18} /> : null}
      {children}
    </button>
  );
}

function Sep() {
  return <span aria-hidden="true" className="mx-1 hidden h-6 w-px bg-line-strong tablet:block" />;
}

const SELECT =
  "min-h-11 rounded-pill border border-line-control bg-ground px-3 text-ui-sm text-text disabled:text-text-muted";

/** The editor toolbar: exactly the nodes and marks ContentRenderer renders (shared content schema). */
export function Toolbar({
  editor,
  onInsertAtom,
  controls,
}: {
  editor: Editor | null;
  onInsertAtom: (type: string) => void;
  controls: string;
}) {
  const t = useT();
  const ids = useId();
  const s = useEditorState({
    editor,
    selector: ({ editor: e }) => {
      if (!e) return null;
      const level = [2, 3, 4].find((l) => e.isActive("heading", { level: l }));
      const callout = CALLOUT_VARIANTS.find((v) => e.isActive("callout", { variant: v })) ?? null;
      const hl = HIGHLIGHT_COLORS.find((c) => e.isActive("highlight", { color: c })) ?? null;
      return {
        block: level ? `h${level}` : "p",
        bold: e.isActive("bold"),
        italic: e.isActive("italic"),
        sub: e.isActive("subscript"),
        sup: e.isActive("superscript"),
        bullet: e.isActive("bulletList"),
        ordered: e.isActive("orderedList"),
        table: e.isActive("table"),
        highYield: e.isActive("table", { highYield: true }),
        callout,
        highlight: hl,
        canUndo: e.can().undo(),
        canRedo: e.can().redo(),
      };
    },
  });
  const run = () => editor?.chain().focus();
  const off = !editor?.isEditable;

  return (
    <div
      role="toolbar"
      aria-label={t("admin.editor.toolbar")}
      aria-controls={controls}
      className="flex flex-wrap items-center gap-1 rounded-block-sm border border-line-soft bg-surface-1 p-1.5"
    >
      <label htmlFor={`${ids}-b`} className="sr-only">
        {t("admin.editor.text_style")}
      </label>
      <select
        id={`${ids}-b`}
        className={SELECT}
        disabled={off}
        value={s?.block ?? "p"}
        onChange={(e) => {
          const v = e.target.value;
          if (v === "p") run()?.setParagraph().run();
          else
            run()
              ?.setHeading({ level: Number(v.slice(1)) as 2 | 3 | 4 })
              .run();
        }}
      >
        <option value="p">{t("admin.editor.paragraph")}</option>
        <option value="h2">{t("admin.editor.heading", { level: "2" })}</option>
        <option value="h3">{t("admin.editor.heading", { level: "3" })}</option>
        <option value="h4">{t("admin.editor.heading", { level: "4" })}</option>
      </select>
      <Sep />
      <Tool
        label={t("admin.editor.bold")}
        Icon={Bold}
        pressed={s?.bold}
        disabled={off}
        onClick={() => run()?.toggleBold().run()}
      />
      <Tool
        label={t("admin.editor.italic")}
        Icon={Italic}
        pressed={s?.italic}
        disabled={off}
        onClick={() => run()?.toggleItalic().run()}
      />
      <Tool
        label={t("admin.editor.subscript")}
        Icon={Subscript}
        pressed={s?.sub}
        disabled={off}
        onClick={() => run()?.toggleMark("subscript").run()}
      />
      <Tool
        label={t("admin.editor.superscript")}
        Icon={Superscript}
        pressed={s?.sup}
        disabled={off}
        onClick={() => run()?.toggleMark("superscript").run()}
      />
      <label htmlFor={`${ids}-h`} className="sr-only">
        {t("admin.editor.highlight")}
      </label>
      <select
        id={`${ids}-h`}
        className={SELECT}
        disabled={off}
        value={s?.highlight ?? ""}
        onChange={(e) => {
          const c = e.target.value as (typeof HIGHLIGHT_COLORS)[number] | "";
          if (!c) run()?.unsetMark("highlight").run();
          else run()?.setMark("highlight", { color: c, meaning: HIGHLIGHT_MEANING_BY_COLOR[c] }).run();
        }}
      >
        <option value="">{t("admin.editor.highlight_none")}</option>
        {HIGHLIGHT_COLORS.map((c) => (
          <option key={c} value={c}>
            {t(HIGHLIGHT_KEYS[HIGHLIGHT_MEANING_BY_COLOR[c]] ?? c)}
          </option>
        ))}
      </select>
      <Sep />
      <Tool
        label={t("admin.editor.bullets")}
        Icon={List}
        pressed={s?.bullet}
        disabled={off}
        onClick={() => run()?.toggleBulletList().run()}
      />
      <Tool
        label={t("admin.editor.numbers")}
        Icon={ListOrdered}
        pressed={s?.ordered}
        disabled={off}
        onClick={() => run()?.toggleOrderedList().run()}
      />
      <Sep />
      <label htmlFor={`${ids}-c`} className="sr-only">
        {t("admin.editor.callout")}
      </label>
      <span className="flex items-center gap-1">
        <MessageSquareQuote aria-hidden="true" size={18} className="ml-1 text-text-2" />
        <select
          id={`${ids}-c`}
          className={SELECT}
          disabled={off}
          value={s?.callout ?? ""}
          onChange={(e) => {
            const v = e.target.value;
            if (!v) run()?.lift("callout").run();
            else if (s?.callout) run()?.updateAttributes("callout", { variant: v }).run();
            else run()?.wrapIn("callout", { variant: v }).run();
          }}
        >
          <option value="">{t(s?.callout ? "admin.editor.callout_remove" : "admin.editor.callout")}</option>
          {CALLOUT_VARIANTS.map((v) => (
            <option key={v} value={v}>
              {t(CALLOUT_KEYS[v] ?? v)}
            </option>
          ))}
        </select>
      </span>
      <Sep />
      <Tool
        label={t("admin.editor.table_insert")}
        Icon={Table}
        disabled={off || s?.table}
        onClick={() => run()?.insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run()}
      />
      {s?.table ? (
        <>
          <Tool label={t("admin.editor.table_row")} Icon={Rows3} onClick={() => run()?.addRowAfter().run()} />
          <Tool label={t("admin.editor.table_col")} Icon={Columns3} onClick={() => run()?.addColumnAfter().run()} />
          <Tool label={t("admin.editor.table_row_delete")} onClick={() => run()?.deleteRow().run()}>
            {t("admin.editor.table_row_delete_short")}
          </Tool>
          <Tool label={t("admin.editor.table_col_delete")} onClick={() => run()?.deleteColumn().run()}>
            {t("admin.editor.table_col_delete_short")}
          </Tool>
          <Tool
            label={t("admin.editor.table_high_yield")}
            pressed={s.highYield}
            onClick={() => run()?.updateAttributes("table", { highYield: !s.highYield }).run()}
          >
            {t("admin.editor.table_high_yield")}
          </Tool>
          <Tool label={t("admin.editor.table_delete")} Icon={Trash2} onClick={() => run()?.deleteTable().run()} />
        </>
      ) : null}
      <Sep />
      <Tool
        label={t("admin.editor.insert_figure")}
        Icon={Image}
        disabled={off}
        onClick={() => onInsertAtom("figure")}
      />
      <Tool label={t("admin.editor.insert_video")} Icon={Play} disabled={off} onClick={() => onInsertAtom("video")} />
      <Tool label={t("admin.editor.insert_pdf")} Icon={FileText} disabled={off} onClick={() => onInsertAtom("pdf")} />
      <Tool
        label={t("admin.editor.insert_question")}
        Icon={ListChecks}
        disabled={off}
        onClick={() => onInsertAtom("questionEmbed")}
      />
      <Tool
        label={t("admin.editor.insert_mermaid")}
        Icon={Network}
        disabled={off}
        onClick={() => onInsertAtom("mermaid")}
      />
      <Tool label={t("admin.editor.insert_math")} Icon={Sigma} disabled={off} onClick={() => onInsertAtom("math")} />
      <Sep />
      <Tool
        label={t("admin.editor.undo")}
        Icon={Undo2}
        disabled={off || !s?.canUndo}
        onClick={() => run()?.undo().run()}
      />
      <Tool
        label={t("admin.editor.redo")}
        Icon={Redo2}
        disabled={off || !s?.canRedo}
        onClick={() => run()?.redo().run()}
      />
    </div>
  );
}
