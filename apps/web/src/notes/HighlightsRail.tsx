import { cx, ErrorState, Skeleton } from "@naadi/ui";
import { Link } from "@tanstack/react-router";
import { BookOpen, CircleHelp, Trash2, Unlink } from "lucide-react";
import { useMemo } from "react";
import { ApiError } from "../auth/apiError";
import { useUiText } from "../copy/useUiText";
import { HIGHLIGHT_COLORS, type HighlightColor, isHighlightColor } from "../highlights/anchor";
import { HighlightWord, SWATCH_CLASS } from "../highlights/Mark";
import { errorKey, type Note, useNotes, useNoteWriter, usePendingOps } from "./api";
import { isDetached, sourceLabel } from "./format";

export type AnchorFilter = "section" | "question" | "detached";

export interface HighlightFilters {
  color?: HighlightColor;
  anchor?: AnchorFilter;
}

/** The chip under a highlight: where it is (a link to that page), or "Detached" with its icon. */
function SourceChip({ note, detached }: { note: Note; detached: boolean }) {
  const t = useUiText();
  const s = note.source;
  const label = sourceLabel(t, note);
  const chip =
    "inline-flex min-h-8 max-w-full items-center gap-1.5 rounded-pill border border-line-chip px-2.5 text-text-3 text-ui-sm no-underline";
  if (detached) {
    return (
      <span className={chip} data-detached="">
        <Unlink aria-hidden="true" size={13} className="shrink-0" />
        {t("highlights.detached")}
      </span>
    );
  }
  if (s?.type === "section" && s.chapter_id && s.section_id) {
    return (
      <Link
        to="/learn/$chapterId/$sectionId"
        params={{ chapterId: s.chapter_id, sectionId: s.section_id }}
        className={cx(chip, "hover:text-text")}
      >
        <BookOpen aria-hidden="true" size={13} className="shrink-0" />
        <span className="truncate">{label}</span>
      </Link>
    );
  }
  if (s?.type === "question" && s.block_id && s.question_number) {
    return (
      <Link
        to="/practice/blocks/$blockId/$n"
        params={{ blockId: s.block_id, n: s.question_number }}
        className={cx(chip, "hover:text-text")}
      >
        <CircleHelp aria-hidden="true" size={13} className="shrink-0" />
        <span className="truncate">{label}</span>
      </Link>
    );
  }
  return label ? <span className={chip}>{label}</span> : null;
}

function FilterChip({
  pressed,
  onClick,
  children,
  count,
}: {
  pressed: boolean;
  onClick: () => void;
  children: React.ReactNode;
  count?: number;
}) {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      onClick={onClick}
      className={cx(
        "inline-flex min-h-11 items-center gap-1.5 rounded-pill px-3.5 text-[0.8125rem]",
        pressed
          ? "border-0 bg-paper font-semibold text-ground"
          : "border border-line-control bg-transparent text-text hover:bg-surface-2",
      )}
    >
      {children}
      {count !== undefined ? <span className="font-mono text-label-sm opacity-70">{count}</span> : null}
    </button>
  );
}

/**
 * Recent highlights (Notes board, right rail): each with its colour bar AND its word, the quote, and a source chip
 * that links back to the section or the answered question. Filter by colour and by anchor; Detached highlights
 * (their text changed and the quote is gone) are a group of their own, never dropped.
 */
export function HighlightsRail({
  filters,
  onFilters,
  className,
}: {
  filters: HighlightFilters;
  onFilters: (f: HighlightFilters) => void;
  className?: string;
}) {
  const t = useUiText();
  const ops = usePendingOps();
  const writer = useNoteWriter();
  const anchorType = filters.anchor === "section" || filters.anchor === "question" ? filters.anchor : undefined;
  const list = useNotes({ kind: "highlight", color: filters.color, anchor_type: anchorType, limit: 100 });
  const local = useMemo(() => new Set(ops.filter((o) => o.create).map((o) => o.id)), [ops]);
  const { attached, detached } = useMemo(() => {
    const a: Note[] = [];
    const d: Note[] = [];
    for (const n of list.items) (isDetached(n, local.has(n.id)) ? d : a).push(n);
    return { attached: a, detached: d };
  }, [list.items, local]);
  const shown = filters.anchor === "detached" ? [] : attached;
  const counts = list.counts;

  const set = (patch: Partial<HighlightFilters>) => onFilters({ ...filters, ...patch });
  const row = (n: Note, isDet: boolean) => {
    const color = isHighlightColor(n.highlight_color) ? n.highlight_color : "lime";
    const quote = (n.anchor_pos as { quote?: string } | null)?.quote ?? n.excerpt;
    return (
      <li key={n.id} data-highlight-row={color} className="flex gap-3">
        <span aria-hidden="true" className={cx("w-1.5 shrink-0 rounded-[3px]", SWATCH_CLASS[color])} />
        <div className="flex min-w-0 flex-1 flex-col gap-1.5">
          <HighlightWord color={color} className="font-mono text-label-sm text-text-3 uppercase tracking-[0.1em]" />
          <span className="text-[0.875rem] text-text [overflow-wrap:anywhere]">{quote}</span>
          <div className="flex flex-wrap items-center gap-2">
            <SourceChip note={n} detached={isDet} />
          </div>
        </div>
        <button
          type="button"
          onClick={() => writer.remove(n.id)}
          aria-label={t("highlights.remove_named", { quote: quote.slice(0, 60) })}
          className="flex size-11 shrink-0 items-center justify-center rounded-pill border-0 bg-transparent text-text-3 hover:bg-surface-2 hover:text-text"
        >
          <Trash2 aria-hidden="true" size={16} />
        </button>
      </li>
    );
  };

  return (
    <section
      aria-labelledby="hl-h"
      data-highlights-rail=""
      className={cx("flex flex-col gap-4 rounded-tile border border-line-soft bg-surface-1 p-6", className)}
    >
      <h2 id="hl-h" className="m-0 font-bold font-display text-[1.375rem]">
        {t("highlights.recent")}
      </h2>
      <fieldset className="m-0 flex flex-wrap gap-2 border-0 p-0">
        <legend className="sr-only">{t("highlights.filter_color")}</legend>
        <FilterChip pressed={!filters.color} onClick={() => set({ color: undefined })}>
          {t("highlights.filter_all_colors")}
        </FilterChip>
        {HIGHLIGHT_COLORS.map((c) => (
          <FilterChip
            key={c}
            pressed={filters.color === c}
            onClick={() => set({ color: filters.color === c ? undefined : c })}
            count={counts?.by_color[c]}
          >
            <HighlightWord color={c} />
          </FilterChip>
        ))}
      </fieldset>
      <fieldset className="m-0 flex flex-wrap gap-2 border-0 p-0">
        <legend className="sr-only">{t("highlights.filter_anchor")}</legend>
        <FilterChip pressed={!filters.anchor} onClick={() => set({ anchor: undefined })}>
          {t("highlights.filter_all_places")}
        </FilterChip>
        <FilterChip
          pressed={filters.anchor === "section"}
          onClick={() => set({ anchor: filters.anchor === "section" ? undefined : "section" })}
          count={counts?.by_anchor.section}
        >
          <BookOpen aria-hidden="true" size={14} />
          {t("highlights.filter_sections")}
        </FilterChip>
        <FilterChip
          pressed={filters.anchor === "question"}
          onClick={() => set({ anchor: filters.anchor === "question" ? undefined : "question" })}
          count={counts?.by_anchor.question}
        >
          <CircleHelp aria-hidden="true" size={14} />
          {t("highlights.filter_questions")}
        </FilterChip>
        <FilterChip
          pressed={filters.anchor === "detached"}
          onClick={() => set({ anchor: filters.anchor === "detached" ? undefined : "detached" })}
        >
          <Unlink aria-hidden="true" size={14} />
          {t("highlights.detached")}
        </FilterChip>
      </fieldset>

      {list.isPending && !list.data ? (
        <div aria-busy="true" className="flex flex-col gap-3">
          <p role="status" className="sr-only">
            {t("highlights.loading")}
          </p>
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-16 rounded-inner" />
          ))}
        </div>
      ) : list.isError && !list.data ? (
        <ErrorState
          title={t("highlights.error")}
          message={t(errorKey(list.error))}
          requestId={list.error instanceof ApiError ? list.error.requestId : null}
          onRetry={() => void list.refetch()}
          retrying={list.isFetching}
          retryLabel={t("notes.retry")}
        />
      ) : (
        <>
          {list.fromCache ? (
            <p role="status" className="m-0 text-text-3 text-ui-sm">
              {t("notes.saved_copy")}
            </p>
          ) : null}
          {shown.length === 0 && (filters.anchor === "detached" ? detached.length === 0 : true) ? (
            <p className="m-0 text-text-2 text-ui-sm" data-highlights-empty="">
              {filters.color || filters.anchor ? t("highlights.empty_filtered") : t("highlights.empty")}
            </p>
          ) : null}
          {shown.length > 0 ? (
            <ul aria-label={t("highlights.recent")} className="m-0 flex list-none flex-col gap-4 p-0">
              {shown.map((n) => row(n, false))}
            </ul>
          ) : null}
          {detached.length > 0 ? (
            <section
              aria-labelledby="hl-detached-h"
              data-detached-group=""
              className="flex flex-col gap-3 border-line-soft border-t pt-4"
            >
              <h3
                id="hl-detached-h"
                className="m-0 flex items-center gap-2 font-mono font-medium text-label text-text-3 uppercase"
              >
                <Unlink aria-hidden="true" size={14} />
                {t("highlights.detached_group", { n: String(detached.length) })}
              </h3>
              <p className="m-0 text-text-3 text-ui-sm">{t("highlights.detached_body")}</p>
              <ul className="m-0 flex list-none flex-col gap-4 p-0">{detached.map((n) => row(n, true))}</ul>
            </section>
          ) : null}
        </>
      )}
    </section>
  );
}
