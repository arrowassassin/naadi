import { cx } from "@naadi/ui";
import { type NodeViewProps, NodeViewWrapper } from "@tiptap/react";
import { FileText, Image, ListChecks, type LucideIcon, Network, Pencil, Play, Sigma, Trash2 } from "lucide-react";
import { createContext, useContext } from "react";
/** What a card shows of a media asset or question (editor payload rows, or full rows picked in a dialog). */
export interface MediaLookup {
  id: string;
  kind: string;
  title: string | null;
  caption?: string | null;
}
export interface QuestionLookup {
  id: string;
  stem_text?: string;
}

import { fmtSeconds, type T, useT } from "../components/ui";

/** What the atom cards need from the editor page: lookups for titles, and the attribute dialog opener. */
export interface EditorEnv {
  media: ReadonlyMap<string, MediaLookup>;
  questions: ReadonlyMap<string, QuestionLookup>;
  /** Open the attribute dialog for the node at `pos` (an existing node). */
  editNode: (type: string, attrs: Record<string, unknown>, pos: number) => void;
}

export const EditorEnvContext = createContext<EditorEnv | null>(null);

export const NODE_LOOK: Record<string, { key: string; Icon: LucideIcon }> = {
  figure: { key: "admin.editor.node.figure", Icon: Image },
  video: { key: "admin.editor.node.video", Icon: Play },
  pdf: { key: "admin.editor.node.pdf", Icon: FileText },
  questionEmbed: { key: "admin.editor.node.question", Icon: ListChecks },
  mermaid: { key: "admin.editor.node.mermaid", Icon: Network },
  math: { key: "admin.editor.node.math", Icon: Sigma },
};

function str(v: unknown): string {
  return typeof v === "string" ? v : "";
}

function summary(type: string, attrs: Readonly<Record<string, unknown>>, env: EditorEnv | null, t: T) {
  const mediaId = str(attrs.mediaId);
  const m = mediaId ? env?.media.get(mediaId) : undefined;
  switch (type) {
    case "figure":
      return { main: m?.title ?? m?.caption ?? mediaId, sub: mediaId, missing: !m };
    case "video": {
      const start = typeof attrs.startS === "number" ? fmtSeconds(attrs.startS) : "";
      const end = typeof attrs.endS === "number" ? fmtSeconds(attrs.endS) : "";
      const range = start || end ? t("admin.editor.range", { start: start || "0:00", end: end || "…" }) : "";
      return { main: m?.title ?? mediaId, sub: [mediaId, range].filter(Boolean).join(" · "), missing: !m };
    }
    case "pdf": {
      const page = typeof attrs.page === "number" ? t("admin.editor.page_n", { page: String(attrs.page) }) : "";
      return { main: m?.title ?? mediaId, sub: [mediaId, page].filter(Boolean).join(" · "), missing: !m };
    }
    case "questionEmbed": {
      const qid = str(attrs.questionId);
      const q = env?.questions.get(qid);
      return { main: q?.stem_text ?? qid, sub: qid, missing: !q };
    }
    case "mermaid":
      return { main: str(attrs.code).split("\n")[0] ?? "", sub: "", missing: false };
    case "math":
      return { main: str(attrs.latex), sub: "", missing: false };
    default:
      return { main: type, sub: "", missing: false };
  }
}

/** An atom block in the editor: a card naming the node and its target, with Edit and Remove. */
export function AtomNodeView({ node, selected, getPos, deleteNode, editor }: NodeViewProps) {
  const t = useT();
  const env = useContext(EditorEnvContext);
  const type = node.type.name;
  const look = NODE_LOOK[type] ?? { key: "admin.editor.node.unknown", Icon: Pencil };
  const attrs = node.attrs as Record<string, unknown>;
  const s = summary(type, attrs, env, t);
  const editable = editor.isEditable;
  return (
    <NodeViewWrapper
      data-atom={type}
      contentEditable={false}
      className={cx(
        "admin-atom my-3 flex flex-col gap-3 rounded-block border bg-surface-2 p-4 text-text tablet:flex-row tablet:items-center",
        selected ? "border-paper" : "border-line-strong",
      )}
    >
      <span className="flex min-w-0 flex-1 items-start gap-3">
        <look.Icon aria-hidden="true" size={20} className="mt-0.5 shrink-0 text-text-2" />
        <span className="flex min-w-0 flex-col gap-1">
          <span className="font-mono text-label-sm text-text-3 uppercase">{t(look.key)}</span>
          <span
            className={cx("break-words font-semibold", (type === "mermaid" || type === "math") && "font-mono text-ui")}
          >
            {s.main || t("admin.editor.empty_attr")}
          </span>
          {s.sub ? <span className="break-all font-mono text-label-sm text-text-3">{s.sub}</span> : null}
          {s.missing ? <span className="text-ui-sm text-amber">{t("admin.editor.target_unknown")}</span> : null}
        </span>
      </span>
      {editable ? (
        <span className="flex shrink-0 gap-2">
          <button
            type="button"
            className="inline-flex min-h-11 items-center gap-2 rounded-pill border border-line-control px-4 text-ui-sm text-text"
            onClick={() => {
              const pos = typeof getPos === "function" ? getPos() : undefined;
              if (typeof pos === "number") env?.editNode(type, attrs, pos);
            }}
          >
            <Pencil aria-hidden="true" size={14} />
            {t("admin.action.edit")}
            <span className="sr-only">{t(look.key)}</span>
          </button>
          <button
            type="button"
            aria-label={t("admin.editor.remove_node", { node: t(look.key) })}
            className="inline-flex size-11 items-center justify-center rounded-pill border border-line-control text-text"
            onClick={() => deleteNode()}
          >
            <Trash2 aria-hidden="true" size={16} />
          </button>
        </span>
      ) : null}
    </NodeViewWrapper>
  );
}
