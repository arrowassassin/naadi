import type { EmbeddedQuestion, MediaOut } from "@naadi/shared";
import { cx, Highlight, type HighlightKind } from "@naadi/ui";
import { AlertTriangle, ArrowLeftRight, Gem, OctagonAlert } from "lucide-react";
import { createElement, type ReactNode, useEffect, useMemo, useRef } from "react";
import { Figure, PdfLink, Video } from "./MediaNodes";
import { QuestionEmbed } from "./QuestionEmbed";
import { MathBlock, MermaidBlock } from "./RichNodes";
import {
  type ContentEnv,
  type ContentMark,
  type ContentNode,
  int,
  isNode,
  type RenderMode,
  str,
  type UnknownContent,
} from "./types";

/*
 * <ContentRenderer> maps every node of the agreed TipTap schema to a component. One renderer serves every
 * reading surface (reader, later the admin preview and the assistant canvas).
 *
 * Unknown node or mark types: in dev a visible error card names the type; in prod the node is skipped,
 * its text (if any) is kept, and `onUnknown` reports it (the API also logs it server-side with the request
 * id). It is never dropped silently.
 *
 * Every top-level block carries `data-block-index`, the anchor the reader uses for reading positions.
 */

export interface ContentRendererProps {
  doc: unknown;
  sectionId: string;
  media?: Record<string, MediaOut>;
  questions?: Record<string, EmbeddedQuestion>;
  mode?: RenderMode;
  headingBase?: 2 | 3;
  onUnknown?: (u: UnknownContent) => void;
  className?: string;
}

const CALLOUTS = {
  pearl: { label: "Pearl", Icon: Gem, frame: "border-line-strong bg-surface-2", tone: "text-text" },
  trap: { label: "Trap", Icon: OctagonAlert, frame: "border-amber/70 bg-surface-2", tone: "text-amber" },
  bridge: { label: "Bridge", Icon: ArrowLeftRight, frame: "border-line-control bg-surface-1", tone: "text-text-2" },
} as const;

const HIGHLIGHT_MEANING: Record<string, HighlightKind> = {
  key_fact: "key-fact",
  formula: "formula",
  mistake: "mistake",
};
const HIGHLIGHT_COLOR: Record<string, HighlightKind> = { lime: "key-fact", violet: "formula", amber: "mistake" };

const ORDERED_TYPES = new Set(["1", "a", "A", "i", "I"]);

function UnknownCard({ type, path, kind }: { type: string; path: string; kind: "node" | "mark" }) {
  return (
    <div
      role="note"
      data-unknown-node={type}
      className="flex items-start gap-3 rounded-block border border-amber bg-surface-2 px-4 py-3 text-text"
    >
      <AlertTriangle aria-hidden="true" size={18} className="mt-0.5 shrink-0 text-amber" />
      <span className="flex flex-col gap-1">
        <span className="font-semibold">
          Unknown content {kind} “{type}”
        </span>
        <span className="font-mono text-label-sm text-text-2">
          {path} · not in the content schema; shown in development only
        </span>
      </span>
    </div>
  );
}

class Walker {
  private reported = new Set<string>();
  constructor(private env: ContentEnv) {}

  report(kind: "node" | "mark", type: string, path: string): void {
    const k = `${kind}:${type}`;
    if (this.reported.has(k)) return;
    this.reported.add(k);
    this.env.onUnknown?.({ kind, type, path });
  }

  children(node: ContentNode, path: string): ReactNode[] {
    return (node.content ?? []).map((c, i) => this.node(c, `${path}.${i}`, i));
  }

  text(node: ContentNode, path: string, key: number): ReactNode {
    let out: ReactNode = node.text ?? "";
    // Marks apply innermost-first in TipTap order; wrap from the last to the first.
    const marks = [...(node.marks ?? [])].reverse();
    for (const [i, m] of marks.entries()) out = this.mark(m, out, path, `${key}-${i}`);
    return out === node.text ? out : <span key={key}>{out}</span>;
  }

  mark(m: ContentMark, inner: ReactNode, path: string, key: string): ReactNode {
    switch (m.type) {
      case "bold":
        return <strong key={key}>{inner}</strong>;
      case "italic":
        return <em key={key}>{inner}</em>;
      case "subscript":
        return <sub key={key}>{inner}</sub>;
      case "superscript":
        return <sup key={key}>{inner}</sup>;
      case "highlight": {
        const kind =
          HIGHLIGHT_MEANING[String(m.attrs?.meaning)] ?? HIGHLIGHT_COLOR[String(m.attrs?.color)] ?? "key-fact";
        return (
          <Highlight key={key} kind={kind}>
            {inner}
          </Highlight>
        );
      }
      default:
        this.report("mark", m.type, path);
        if (this.env.mode === "dev") {
          return (
            <span key={key} data-unknown-mark={m.type} className="underline decoration-amber decoration-wavy">
              {inner}
            </span>
          );
        }
        return inner;
    }
  }

  heading(node: ContentNode, path: string, key: number): ReactNode {
    const level = int(node.attrs?.level, 1, 6) ?? 3;
    const shifted = Math.min(6, this.env.headingBase + Math.max(0, level - this.env.minHeading));
    const size =
      shifted <= 2 ? "text-[1.5rem] tablet:text-[1.75rem]" : shifted === 3 ? "text-[1.25rem]" : "text-[1.0625rem]";
    return createElement(
      `h${shifted}`,
      { key, className: cx("m-0 font-display font-bold leading-tight tracking-[-0.015em] text-text", size) },
      ...this.children(node, path),
    );
  }

  node(node: unknown, path: string, key: number): ReactNode {
    if (!isNode(node)) {
      this.report("node", "<invalid>", path);
      return this.env.mode === "dev" ? <UnknownCard key={key} type="<invalid>" path={path} kind="node" /> : null;
    }
    const env = this.env;
    switch (node.type) {
      case "text":
        return this.text(node, path, key);
      case "hardBreak":
        return <br key={key} />;
      case "paragraph":
        return (
          <p key={key} className="m-0">
            {this.children(node, path)}
          </p>
        );
      case "heading":
        return this.heading(node, path, key);
      case "bulletList":
        return (
          <ul key={key} className="m-0 flex list-disc flex-col gap-2 pl-6 marker:text-text-3">
            {this.children(node, path)}
          </ul>
        );
      case "orderedList": {
        const start = int(node.attrs?.start, 0, 100000) ?? 1;
        const t = str(node.attrs?.type);
        return (
          <ol
            key={key}
            start={start === 1 ? undefined : start}
            type={t && ORDERED_TYPES.has(t) ? (t as "1" | "a" | "A" | "i" | "I") : undefined}
            className="m-0 flex list-decimal flex-col gap-2 pl-6 marker:text-text-3"
          >
            {this.children(node, path)}
          </ol>
        );
      }
      case "listItem":
        return (
          <li key={key} className="pl-1 [&>p+*]:mt-2">
            {this.children(node, path)}
          </li>
        );
      case "table":
        return this.table(node, path, key);
      case "tableRow":
        return <tr key={key}>{this.children(node, path)}</tr>;
      case "tableHeader":
      case "tableCell": {
        const colSpan = int(node.attrs?.colspan, 2, 50) ?? undefined;
        const rowSpan = int(node.attrs?.rowspan, 2, 500) ?? undefined;
        const Tag = node.type === "tableHeader" ? "th" : "td";
        const align = str(node.attrs?.align);
        return (
          <Tag
            key={key}
            colSpan={colSpan}
            rowSpan={rowSpan}
            scope={Tag === "th" ? "col" : undefined}
            className={cx(
              "border-line border-t px-3 py-2.5 align-top [&>p+p]:mt-2",
              align === "center" ? "text-center" : align === "right" ? "text-right" : "text-left",
              Tag === "th"
                ? "bg-surface-2 font-mono text-label-sm font-medium tracking-[0.06em] text-text-2 uppercase"
                : "text-text-2",
            )}
          >
            {this.children(node, path)}
          </Tag>
        );
      }
      case "callout": {
        const variant = str(node.attrs?.variant) as keyof typeof CALLOUTS | null;
        const c = variant && variant in CALLOUTS ? CALLOUTS[variant] : CALLOUTS.pearl;
        return (
          <aside
            key={key}
            data-node="callout"
            data-variant={variant ?? "pearl"}
            aria-label={c.label}
            className={cx("flex flex-col gap-3 rounded-block border p-4 tablet:p-5", c.frame)}
          >
            <span className={cx("flex items-center gap-2 font-mono text-label uppercase", c.tone)}>
              <c.Icon aria-hidden="true" size={16} />
              {c.label}
            </span>
            <div className="flex flex-col gap-3">{this.children(node, path)}</div>
          </aside>
        );
      }
      case "figure":
        return <Figure key={key} mediaId={str(node.attrs?.mediaId)} env={env} />;
      case "video":
        return (
          <Video
            key={key}
            mediaId={str(node.attrs?.mediaId)}
            startS={int(node.attrs?.startS, 0, 86400)}
            endS={int(node.attrs?.endS, 1, 86400)}
            env={env}
          />
        );
      case "pdf":
        return (
          <PdfLink key={key} mediaId={str(node.attrs?.mediaId)} page={int(node.attrs?.page, 1, 100000)} env={env} />
        );
      case "questionEmbed":
        return <QuestionEmbed key={key} questionId={str(node.attrs?.questionId)} env={env} />;
      case "mermaid":
        return <MermaidBlock key={key} code={str(node.attrs?.code) ?? ""} env={env} />;
      case "math":
        return <MathBlock key={key} latex={str(node.attrs?.latex) ?? ""} env={env} />;
      case "doc":
        return <div key={key}>{this.children(node, path)}</div>;
      default:
        this.report("node", node.type, path);
        if (env.mode === "dev") return <UnknownCard key={key} type={node.type} path={path} kind="node" />;
        // prod: skip the node, keep any text a student would otherwise lose
        return node.content?.length ? <div key={key}>{this.children(node, path)}</div> : null;
    }
  }

  table(node: ContentNode, path: string, key: number): ReactNode {
    const highYield = node.attrs?.highYield === true;
    const first = node.content?.[0];
    const headerRow = first?.content?.length && first.content.every((c) => c.type === "tableHeader");
    const rows = this.children(node, path);
    return (
      <div key={key} data-node="table" className="flex flex-col gap-2">
        {highYield ? (
          <span className="font-mono text-label-sm tracking-[0.12em] text-text-3 uppercase">High-yield table</span>
        ) : null}
        {/* Scrolls sideways on phone; focusable so keyboard users can scroll it too. */}
        <section
          // biome-ignore lint/a11y/noNoninteractiveTabindex: a scrollable region must be focusable (WCAG 2.1.1)
          tabIndex={0}
          aria-label={highYield ? "High-yield table" : "Table"}
          className="overflow-x-auto rounded-block border border-line [-webkit-overflow-scrolling:touch]"
        >
          <table className="w-full min-w-[32rem] border-collapse text-[0.9375rem] leading-normal tablet:min-w-0">
            {headerRow ? (
              <>
                <thead>{rows[0]}</thead>
                <tbody>{rows.slice(1)}</tbody>
              </>
            ) : (
              <tbody>{rows}</tbody>
            )}
          </table>
        </section>
      </div>
    );
  }
}

function minHeadingLevel(doc: ContentNode): number {
  let min = 6;
  const stack: ContentNode[] = [doc];
  while (stack.length) {
    const n = stack.pop();
    if (!n) continue;
    if (n.type === "heading") min = Math.min(min, int(n.attrs?.level, 1, 6) ?? 6);
    for (const c of n.content ?? []) if (isNode(c)) stack.push(c);
  }
  return min;
}

export function ContentRenderer({
  doc,
  sectionId,
  media = {},
  questions = {},
  mode = import.meta.env.DEV ? "dev" : "prod",
  headingBase = 2,
  onUnknown,
  className,
}: ContentRendererProps) {
  const unknownRef = useRef(onUnknown);
  unknownRef.current = onUnknown;
  const blocks = useMemo(() => {
    const unknown: UnknownContent[] = [];
    if (!isNode(doc) || doc.type !== "doc") {
      return { error: true as const, nodes: [] as ReactNode[], unknown };
    }
    const env: ContentEnv = {
      media,
      questions,
      mode,
      sectionId,
      headingBase,
      minHeading: minHeadingLevel(doc),
      onUnknown: (u) => unknown.push(u),
      embedOrigin: typeof window === "undefined" ? "" : window.location.origin,
    };
    const w = new Walker(env);
    const nodes = (doc.content ?? []).map((c, i) => (
      // biome-ignore lint/suspicious/noArrayIndexKey: blocks are positional; the index is the reading-position anchor
      <div key={`${sectionId}-${i}`} data-block-index={i} className="scroll-mt-(--reader-anchor,96px)">
        {w.node(c, `${sectionId} › block ${i}`, i)}
      </div>
    ));
    return { error: false as const, nodes, unknown };
  }, [doc, media, questions, mode, sectionId, headingBase]);

  // Report after render, never during it.
  useEffect(() => {
    for (const u of blocks.unknown) unknownRef.current?.(u);
  }, [blocks]);

  if (blocks.error) {
    return mode === "dev" ? <UnknownCard type="<not a doc>" path={sectionId} kind="node" /> : null;
  }
  return (
    <div
      data-content-root=""
      data-section-id={sectionId}
      className={cx(
        "flex flex-col gap-5 text-reading text-text-2 [&_strong]:font-semibold [&_strong]:text-text",
        className,
      )}
    >
      {blocks.nodes}
    </div>
  );
}
