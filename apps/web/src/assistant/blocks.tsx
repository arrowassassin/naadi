import type { MediaOut } from "@naadi/shared";
import { BarChart, Button, cx, LineChart } from "@naadi/ui";
import { Link, useNavigate } from "@tanstack/react-router";
import {
  AlertTriangle,
  BookOpen,
  Clock,
  ExternalLink,
  Globe,
  Info,
  Layers,
  NotebookPen,
  PanelRight,
  ShieldAlert,
} from "lucide-react";
import { useMemo } from "react";
import { useAuth } from "../auth/session";
import { getCanvasKind, openInCanvas, zCanvasRef } from "../canvas/registry";
import { ContentRenderer } from "../content/ContentRenderer";
import { MermaidBlock } from "../content/RichNodes";
import type { ContentEnv } from "../content/types";
import { useUiText } from "../copy/useUiText";
import { FileVideo } from "../videos/FileVideo";
import type { AssistantAskKind } from "./ask";
import { useKnownCopy } from "./copy";
import { allowedUrl, imagePrefixes } from "./mediaHosts";
import type { Block, BlockOf, ChartData, Citation, Notice, TableData, VideoData } from "./protocol";
import { formatClock } from "./time";

/*
 * Answer blocks (System board: "Assistant answer blocks"), all on the assistant's violet framing. Every word is
 * ui_copy; the server's structured fields (label_key, layer, kind) pick the wording. Model-written titles and
 * labels render as text, never as HTML.
 */

type T = ReturnType<typeof useUiText>;

const LABEL = "font-mono text-label-sm tracking-[0.1em] text-text-3 uppercase";
const FRAME = "flex flex-col gap-3 rounded-block border border-violet-line bg-ground/60 p-4";

/** A server-sent label: its ui_copy key when the key is known, else the plain label, else the key. */
export function useLabel(): (key: string | null, label: string | null, fallback: string) => string {
  const copy = useKnownCopy();
  return (key, label, fallback) => (key ? copy(key) : null) ?? label ?? fallback;
}

function useContentEnv(): ContentEnv {
  const t = useUiText();
  return useMemo(
    () => ({
      media: {},
      questions: {},
      mode: "prod",
      sectionId: "assistant",
      headingBase: 3,
      minHeading: 1,
      embedOrigin: window.location.origin,
      text: (k, v) => t(k, v),
    }),
    [t],
  );
}

export function FlowchartBlock({ block }: { block: BlockOf<"flowchart"> }) {
  const t = useUiText();
  const env = useContentEnv();
  return (
    <section aria-label={t("agent.block.flowchart")} className="flex flex-col gap-2" data-block="flowchart">
      <span className={LABEL}>{t("agent.block.flowchart")}</span>
      <MermaidBlock code={block.data.mermaid} env={env} />
    </section>
  );
}

export function ImageBlock({ block }: { block: BlockOf<"image"> }) {
  const t = useUiText();
  const auth = useAuth();
  const supabase = auth.status === "signed-in" || auth.status === "signed-out" ? auth.config.supabase_url : null;
  const origin = window.location.origin;
  const d = block.data;
  if (!allowedUrl(d.url, imagePrefixes(origin, supabase), origin)) return null;
  const credit = [d.credit, d.license].filter(Boolean).join(" · ");
  return (
    <figure className="m-0 flex flex-col gap-2" data-block="image">
      <img
        src={d.url}
        alt={d.title}
        width={d.width ?? undefined}
        height={d.height ?? undefined}
        loading="lazy"
        decoding="async"
        className="h-auto max-h-80 w-full rounded-swatch bg-paper object-contain"
      />
      <figcaption className="flex flex-col gap-1 text-text-2 text-ui-sm">
        <span className="font-semibold text-text">{d.title}</span>
        {credit ? (
          <span className="font-mono text-label-sm text-text-3">{t("agent.block.image_credit", { credit })}</span>
        ) : null}
        {d.link && allowedUrl(d.link, ["https://"], origin) ? (
          <a
            href={d.link}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex min-h-11 items-center gap-1 text-violet-text"
          >
            {t("agent.block.image_source")} <ExternalLink aria-hidden="true" size={14} />
          </a>
        ) : null}
      </figcaption>
    </figure>
  );
}

/**
 * A library video: YouTube through the existing click-to-load node (official IFrame player via the player page),
 * an open-licence Commons file in the browser's player. A catalogue video links to its place on the Videos page.
 */
export function VideoBlock({ block }: { block: BlockOf<"video"> }) {
  const t = useUiText();
  const d = block.data;
  return (
    <section aria-label={t("agent.block.video")} className="flex flex-col gap-2" data-block="video">
      <span className={LABEL}>{t("agent.block.video")}</span>
      {d.provider === "commons" ? (
        <FileVideo
          mediaId={d.media_id}
          src={d.file_url}
          title={d.title}
          durationS={d.duration_s}
          attribution={d.attribution}
          licence={d.licence}
          pageUrl={d.page_url}
        />
      ) : (
        <YouTubeNode d={d} id={block.id} />
      )}
      {d.step && d.subject ? (
        <Link
          to="/videos"
          search={{ v: d.media_id, vs: { step: d.step, subject: d.subject } }}
          className="inline-flex min-h-11 items-center gap-1 self-start text-violet-text"
        >
          {t("agent.block.video_open")}
        </Link>
      ) : null}
    </section>
  );
}

function YouTubeNode({ d, id }: { d: Extract<VideoData, { provider: "youtube" }>; id: string }) {
  const t = useUiText();
  const media: Record<string, MediaOut> = {
    [d.media_id]: {
      id: d.media_id,
      kind: "video_youtube",
      youtube_id: d.video_id,
      title: d.title,
      caption: null,
      attribution: null,
      duration_s: null,
      embeddable: true,
      height: null,
      width: null,
      source: null,
      url: null,
    },
  };
  const doc = {
    type: "doc",
    content: [{ type: "video", attrs: { mediaId: d.media_id, startS: d.start_s ?? undefined } }],
  };
  return (
    <ContentRenderer doc={doc} sectionId={`assistant-${id}`} media={media} text={t} mode="prod" className="text-ui!" />
  );
}

function layerWord(t: T, c: Citation): string {
  if (c.layer === "web") return t("agent.source.layer_web", { source: c.source });
  return t(c.layer === "course" ? "agent.source.layer_course" : "agent.source.layer_notes");
}

const LAYER_ICON = { course: BookOpen, notes: NotebookPen, web: Globe } as const;

const UPLOAD_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

/** A page of the student's own PDF (S15 `pdf_page`): its upload id and page number, when well formed. */
function pdfPage(c: Citation): { uploadId: string; page: number } | null {
  const target = c.target ?? {};
  const page = Number(target.page);
  if (target.type !== "pdf_page" || !UPLOAD_ID.test(target.upload_id ?? "") || !Number.isInteger(page) || page < 1)
    return null;
  return { uploadId: target.upload_id as string, page };
}

function citationTitle(t: T, c: Citation): string {
  const pdf = pdfPage(c);
  if (!pdf) return c.title;
  return c.title
    ? t("agent.source.pdf_page", { name: c.title, page: String(pdf.page) })
    : t("agent.source.pdf_page_untitled", { page: String(pdf.page) });
}

function citationHref(c: Citation): { to: string; external: boolean } | null {
  if (c.url && allowedUrl(c.url, ["https://"], window.location.origin)) return { to: c.url, external: true };
  const pdf = pdfPage(c);
  if (pdf) return { to: `/learn/library/pdf/${pdf.uploadId}?page=${pdf.page}`, external: false };
  const target = c.target ?? {};
  if (target.chapter_id && target.section_id) {
    return {
      to: `/learn/${encodeURIComponent(target.chapter_id)}/${encodeURIComponent(target.section_id)}`,
      external: false,
    };
  }
  return null;
}

export function SourcesBlock({ items }: { items: readonly Citation[] }) {
  const t = useUiText();
  if (!items.length) return null;
  const web = items.some((c) => c.layer === "web");
  return (
    <section aria-label={t("agent.block.sources")} className="flex flex-wrap items-center gap-2" data-block="sources">
      <span className="text-text-3 text-ui-sm">
        {t(
          web
            ? items.length === 1
              ? "agent.block.sources_web_one"
              : "agent.block.sources_web_other"
            : items.length === 1
              ? "agent.block.sources_count_one"
              : "agent.block.sources_count_other",
          { count: String(items.length) },
        )}
      </span>
      <ul className="m-0 flex list-none flex-wrap gap-2 p-0">
        {items.map((c) => {
          const href = citationHref(c);
          const Icon = LAYER_ICON[c.layer];
          const body = (
            <>
              <Icon aria-hidden="true" size={14} className="shrink-0" />
              {c.n ? <span className="font-mono text-text-3">{c.n}</span> : null}
              <span className="max-w-[16rem] truncate">{citationTitle(t, c)}</span>
              <span className="sr-only">{`(${layerWord(t, c)})`}</span>
            </>
          );
          const cls =
            "inline-flex min-h-11 items-center gap-1.5 rounded-pill border border-line-chip px-3 text-text-2 text-ui-sm no-underline hover:border-text-3 hover:text-text";
          return (
            <li key={c.id} id={`cite-${c.id}`} title={layerWord(t, c)}>
              {href?.external ? (
                <a href={href.to} target="_blank" rel="noopener noreferrer" className={cls}>
                  {body}
                </a>
              ) : href ? (
                <a href={href.to} className={cls}>
                  {body}
                </a>
              ) : (
                <span className={cls}>{body}</span>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

export interface ActionHandlers {
  ask: (text: string, kind: AssistantAskKind) => void;
}

/** A stable key: an action is identified by what it does. */
function actionKey(a: { action: string; label_key: string; payload: Record<string, unknown> }): string {
  return `${a.action}:${a.label_key}:${JSON.stringify(a.payload)}`;
}

const ASK_KINDS = new Set<AssistantAskKind>(["hint", "explain", "ask", "summarise", "flashcards", "quiz"]);

export function ActionsBlock({
  block,
  handlers,
  disabled,
}: {
  block: BlockOf<"actions">;
  handlers: ActionHandlers;
  disabled: boolean;
}) {
  const copy = useKnownCopy();
  const navigate = useNavigate();
  const buttons = block.data.items.flatMap((a) => {
    const label = copy(a.label_key);
    if (!label) return []; // a label this build cannot word is not shown (no button without a name)
    if (a.action === "open_block") {
      const blockId = a.payload.block_id;
      const n = a.payload.n;
      if (typeof blockId !== "string" || typeof n !== "number" || !Number.isInteger(n) || n < 1) return [];
      return [
        <Button
          key={actionKey(a)}
          variant="strong"
          onClick={() => void navigate({ to: "/practice/blocks/$blockId/$n", params: { blockId, n } })}
        >
          {label}
        </Button>,
      ];
    }
    if (a.action === "open_library") {
      return [
        <Button key={actionKey(a)} variant="secondary" onClick={() => void navigate({ to: "/learn/library" })}>
          {label}
        </Button>,
      ];
    }
    if (a.action === "open_canvas") {
      const ref = zCanvasRef.safeParse(a.payload.canvas ?? a.payload);
      if (!ref.success || !getCanvasKind(ref.data.kind)) return [];
      return [
        <Button
          key={actionKey(a)}
          variant="secondary"
          className="border-violet-line text-violet-text"
          onClick={() => openInCanvas(ref.data)}
        >
          {label}
        </Button>,
      ];
    }
    const kind =
      typeof a.payload.kind === "string" && ASK_KINDS.has(a.payload.kind as AssistantAskKind)
        ? (a.payload.kind as AssistantAskKind)
        : "ask";
    return [
      <Button
        key={actionKey(a)}
        variant="secondary"
        className="border-violet-line text-violet-text"
        disabled={disabled}
        onClick={() => handlers.ask(label, kind)}
      >
        {label}
      </Button>,
    ];
  });
  if (!buttons.length) return null;
  return (
    <div className="flex flex-wrap gap-2" data-block="actions">
      {buttons}
    </div>
  );
}

export function CardPreviewBlock({ block }: { block: BlockOf<"card_preview"> }) {
  const t = useUiText();
  const cards = block.data.cards;
  return (
    <section
      aria-label={t("agent.block.cards", { count: String(cards.length) })}
      className={FRAME}
      data-block="card_preview"
    >
      <span className={LABEL}>{t("agent.block.cards", { count: String(cards.length) })}</span>
      <ol className="m-0 flex list-none flex-col gap-2 p-0">
        {cards.map((c, i) => (
          // biome-ignore lint/suspicious/noArrayIndexKey: cards are positional
          <li key={i} className="flex flex-col gap-1 rounded-inner bg-surface-2 p-3 text-ui">
            <span className="font-semibold text-text">{c.front}</span>
            <span className="text-text-2">{c.back}</span>
          </li>
        ))}
      </ol>
      <Link to="/cards" className="inline-flex min-h-11 items-center gap-1 self-start text-violet-text">
        <Layers aria-hidden="true" size={16} /> {t("agent.block.cards_open")}
      </Link>
    </section>
  );
}

export function cellText(v: string | number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return typeof v === "number" ? new Intl.NumberFormat().format(v) : v;
}

export function DataTableView({ data, caption }: { data: TableData; caption: string }) {
  const label = useLabel();
  return (
    // biome-ignore lint/a11y/noNoninteractiveTabindex: a scrollable region must be focusable (WCAG 2.1.1)
    <section tabIndex={0} aria-label={caption} className="overflow-x-auto rounded-inner border border-line">
      <table className="w-full border-collapse text-left text-ui-sm">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            {data.columns.map((c) => (
              <th
                key={c.key}
                scope="col"
                className="border-line border-b bg-surface-2 px-3 py-2 font-mono font-medium text-label-sm text-text-2 uppercase"
              >
                {label(c.label_key, c.label, c.key)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.rows.map((r, i) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: rows are positional
            <tr key={i}>
              {data.columns.map((c, j) =>
                j === 0 ? (
                  <th
                    key={c.key}
                    scope="row"
                    className="border-line-soft border-t px-3 py-2 text-left font-normal text-text"
                  >
                    {cellText(r[c.key])}
                  </th>
                ) : (
                  <td key={c.key} className="border-line-soft border-t px-3 py-2 text-text-2 tabular-nums">
                    {cellText(r[c.key])}
                  </td>
                ),
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

export function TableBlock({ block }: { block: BlockOf<"table"> }) {
  const t = useUiText();
  return (
    <section aria-label={t("agent.block.table")} className="flex flex-col gap-2" data-block="table">
      <span className={LABEL}>{t("agent.block.table")}</span>
      <DataTableView data={block.data} caption={t("agent.block.table")} />
    </section>
  );
}

export function useValueFormat(unit: ChartData["unit"]): (v: number) => string {
  const t = useUiText();
  const nf = new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 });
  return (v) => {
    if (unit === "percent") return t("agent.unit.percent", { value: nf.format(v) });
    if (unit === "seconds") return t("agent.unit.seconds", { value: nf.format(v) });
    return nf.format(v);
  };
}

export function ChartView({ data, title }: { data: ChartData; title: string }) {
  const t = useUiText();
  const label = useLabel();
  const format = useValueFormat(data.unit);
  const Chart = data.chart === "bar" ? BarChart : LineChart;
  const xColumn = data.x;
  return (
    <Chart
      title={title}
      x={xColumn}
      xLabel={label(`agent.analytics.${xColumn}`, null, xColumn)}
      series={data.series.map((s) => ({ key: s.key, label: label(s.label_key, s.label, s.key) }))}
      rows={data.rows}
      formatValue={format}
      max={data.unit === "percent" ? 100 : undefined}
      labels={{
        showTable: t("agent.chart.show_table"),
        hideTable: t("agent.chart.hide_table"),
        noData: t("agent.chart.no_data"),
      }}
    />
  );
}

export function ChartBlock({ block }: { block: BlockOf<"chart"> }) {
  const t = useUiText();
  const title = t(block.data.chart === "bar" ? "agent.block.chart_bar" : "agent.block.chart_line");
  return (
    <section aria-label={title} className="flex flex-col gap-2" data-block="chart">
      <span className={LABEL}>{title}</span>
      <ChartView data={block.data} title={title} />
    </section>
  );
}

export function CanvasChip({ block }: { block: BlockOf<"canvas"> }) {
  const t = useUiText();
  const copy = useKnownCopy();
  const ref = block.data;
  const kind = getCanvasKind(ref.kind);
  if (!kind) return null;
  const title = (ref.title_key ? copy(ref.title_key) : null) ?? ref.title ?? t(kind.label_key);
  return (
    <button
      type="button"
      onClick={() => openInCanvas(ref)}
      data-block="canvas"
      className="inline-flex min-h-12 items-center gap-2 self-start rounded-pill border border-violet-line bg-violet-ground px-4 text-left text-ui text-violet-text hover:border-violet"
    >
      <PanelRight aria-hidden="true" size={18} className="shrink-0" />
      <span className="flex min-w-0 flex-col">
        <span className="font-mono text-label-sm uppercase">{t("agent.block.canvas")}</span>
        <span className="truncate font-semibold text-text">{title}</span>
      </span>
    </button>
  );
}

export function noticeText(t: T, n: Notice): string {
  switch (n.kind) {
    case "disclaimer":
      return t("agent.notice.disclaimer");
    case "budget_exhausted":
      return t("agent.quota_reset", { time: formatClock(n.reset_at) });
    case "rate_limited":
      return t("agent.notice.rate_limited_until", { time: formatClock(n.reset_at) });
    case "unavailable":
      return t("agent.unavailable");
    case "turn_cap":
      return t("agent.notice.turn_cap");
    case "exam_mode":
      return t("agent.exam_mode");
  }
}

const NOTICE_CHIP: Record<Notice["kind"], string> = {
  disclaimer: "agent.notice.chip_disclaimer",
  budget_exhausted: "agent.notice.chip_quota",
  rate_limited: "agent.notice.chip_busy",
  unavailable: "agent.notice.chip_unavailable",
  turn_cap: "agent.notice.chip_partial",
  exam_mode: "agent.notice.chip_exam",
};

export function NoticeBlock({ notice }: { notice: Notice }) {
  const t = useUiText();
  const Icon =
    notice.kind === "disclaimer"
      ? Info
      : notice.kind === "budget_exhausted" || notice.kind === "rate_limited"
        ? Clock
        : notice.kind === "exam_mode"
          ? ShieldAlert
          : AlertTriangle;
  const warn = notice.kind !== "disclaimer";
  return (
    <p
      role={warn ? "status" : "note"}
      data-notice={notice.kind}
      className={cx(
        "m-0 flex items-start gap-2 rounded-inner border px-3 py-2 text-ui-sm",
        warn ? "border-amber/60 text-text" : "border-line text-text-2",
      )}
    >
      <Icon aria-hidden="true" size={16} className={cx("mt-0.5 shrink-0", warn ? "text-amber" : "text-text-3")} />
      <span className="flex flex-wrap gap-x-1.5">
        <span className="font-semibold">{t(NOTICE_CHIP[notice.kind])}</span>
        <span>{noticeText(t, notice)}</span>
      </span>
    </p>
  );
}

/** One block, by type. Sources and notices of a message are rendered by the message (see Thread.tsx). */
export function BlockView({ block, handlers, busy }: { block: Block; handlers: ActionHandlers; busy: boolean }) {
  switch (block.type) {
    case "flowchart":
      return <FlowchartBlock block={block} />;
    case "image":
      return <ImageBlock block={block} />;
    case "video":
      return <VideoBlock block={block} />;
    case "sources":
      return <SourcesBlock items={block.data.items} />;
    case "actions":
      return <ActionsBlock block={block} handlers={handlers} disabled={busy} />;
    case "card_preview":
      return <CardPreviewBlock block={block} />;
    case "table":
      return <TableBlock block={block} />;
    case "chart":
      return <ChartBlock block={block} />;
    case "canvas":
      return <CanvasChip block={block} />;
    case "notice":
      return <NoticeBlock notice={block.data} />;
  }
}
