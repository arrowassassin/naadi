import type { ChapterDetail, ReadingPosition } from "@naadi/shared";
import { Button, Chip, cx, ErrorState, Skeleton } from "@naadi/ui";
import { Link, useNavigate } from "@tanstack/react-router";
import { ArrowLeft, ArrowRight, ChevronLeft, ChevronRight, Flag, ListOrdered } from "lucide-react";
import {
  type ReactNode,
  type RefObject,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import { ApiError } from "../auth/apiError";
import { ContentRenderer } from "../content/ContentRenderer";
import type { TextLookup, UnknownContent } from "../content/types";
import { useUiText } from "../copy/useUiText";
import { useShellBottomSlot } from "../shell/chrome";
import { useChapter, usePositions } from "./api";
import { ContentsSheet } from "./ContentsSheet";
import { count, minutesLabel } from "./format";
import { ReportIssueDialog } from "./ReportIssueDialog";
import { useReadingPosition } from "./useReadingPosition";
import "./reader.css";

function logUnknown(u: UnknownContent) {
  // The API logs the same finding server-side with the request id; this keeps it visible in devtools.
  console.warn(`naadi: unknown content ${u.kind} "${u.type}" at ${u.path}`);
}

/**
 * Arrow keys turn the page only while nothing else wants them: focus on the page itself (body, <main>) or on
 * the article, never in a scrollable table, a field, a dialog, a video or any other control.
 */
export function pageOwnsArrows(active: Element | null, article: Element | null): boolean {
  if (!active || active === document.body || active === document.documentElement) return true;
  if (active === article) return true;
  return active instanceof HTMLElement && active.id === "main" && active.hasAttribute("data-shell-content");
}

function ReaderSkeleton({ t }: { t: TextLookup }) {
  return (
    <div aria-busy="true" className="flex flex-col gap-6 pt-4">
      <p role="status" className="sr-only">
        {t("learn.reader_loading")}
      </p>
      <h1 className="sr-only">{t("learn.reader_chapter")}</h1>
      <Skeleton className="h-14 w-full max-w-2xl rounded-pill" />
      <div className="flex flex-col gap-5 tablet:rounded-tile tablet:border tablet:border-line-soft tablet:bg-surface-1 tablet:p-11">
        <Skeleton className="h-4 w-48" />
        <Skeleton className="h-12 w-3/4 rounded-block" />
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-4 w-full" />
        ))}
        <Skeleton className="h-56 w-full rounded-block" />
      </div>
    </div>
  );
}

function PageDots({ index, total, t }: { index: number; total: number; t: TextLookup }) {
  if (total > 40) return null;
  return (
    <div
      role="img"
      aria-label={t("learn.page_dots", { n: String(index + 1), total: String(total) })}
      className="flex gap-1 tablet:hidden"
    >
      {Array.from({ length: total }, (_, i) => (
        <span
          // biome-ignore lint/suspicious/noArrayIndexKey: fixed-length positional indicator
          key={i}
          className={cx("h-1 flex-1 rounded-[2px]", i < index ? "bg-paper" : i === index ? "bg-text-2" : "bg-line")}
        />
      ))}
    </div>
  );
}

interface ReaderProps {
  chapterId: string;
  sectionId: string;
}

export function ReaderPage({ chapterId, sectionId }: ReaderProps) {
  const chapter = useChapter(chapterId);
  const positions = usePositions(chapterId);
  const t = useUiText();
  if (chapter.isPending && !chapter.data) return <ReaderSkeleton t={t} />;
  if (!chapter.data) {
    const err = chapter.error;
    const missing = err instanceof ApiError && err.status === 404;
    return (
      <section aria-labelledby="reader-h" className="flex flex-col gap-6 pt-6">
        <h1 id="reader-h" className="m-0 font-display text-headline">
          {missing ? t("learn.reader_not_found") : t("learn.reader_chapter")}
        </h1>
        <ErrorState
          title={missing ? t("learn.reader_unavailable") : t("learn.reader_load_failed")}
          message={err?.message ?? t("learn.error_generic")}
          requestId={err instanceof ApiError ? err.requestId : null}
          onRetry={missing ? undefined : () => void chapter.refetch()}
          retrying={chapter.isFetching}
          retryLabel={t("learn.retry")}
        />
        <Link to="/learn" className="min-h-11 py-3">
          {t("learn.back_to_courses")}
        </Link>
      </section>
    );
  }
  return (
    <Reader
      key={chapterId}
      detail={chapter.data.data}
      fromCache={chapter.data.fromCache}
      sectionId={sectionId}
      positions={positions.data?.data.sections}
      positionsLoaded={!positions.isPending}
      t={t}
    />
  );
}

/** Publishes the sticky page bar's height as --reader-bar (scroll-padding-bottom), 0 when it is not in the page. */
function useReaderBarVar(ref: RefObject<HTMLElement | null>, inPage: boolean): void {
  useLayoutEffect(() => {
    const root = document.documentElement;
    const el = ref.current;
    if (!inPage || !el) {
      root.style.setProperty("--reader-bar", "0px");
      return;
    }
    const apply = () =>
      root.style.setProperty("--reader-bar", `${Math.round(el.getBoundingClientRect().height + 12)}px`);
    apply();
    const ro = new ResizeObserver(apply);
    ro.observe(el);
    return () => {
      ro.disconnect();
      root.style.setProperty("--reader-bar", "0px");
    };
  }, [ref, inPage]);
}

const NEXT_PILL =
  "flex min-h-12 min-w-12 max-w-[60%] shrink-0 items-center justify-center gap-2 whitespace-nowrap rounded-pill bg-lime px-4 font-semibold text-lime-ink no-underline hover:bg-lime-hover hover:text-lime-ink";

function Reader({
  detail,
  fromCache,
  sectionId,
  positions,
  positionsLoaded,
  t,
}: {
  detail: ChapterDetail;
  fromCache: boolean;
  sectionId: string;
  positions: ReadingPosition[] | undefined;
  positionsLoaded: boolean;
  t: TextLookup;
}) {
  const navigate = useNavigate();
  const slot = useShellBottomSlot();
  const index = detail.sections.findIndex((s) => s.id === sectionId);
  const section = index >= 0 ? detail.sections[index] : undefined;
  const prev = index > 0 ? detail.sections[index - 1] : undefined;
  const next = index >= 0 && index < detail.sections.length - 1 ? detail.sections[index + 1] : undefined;
  const rootRef = useRef<HTMLDivElement>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const articleRef = useRef<HTMLElement>(null);
  const barRef = useRef<HTMLElement>(null);
  const [contentsOpen, setContentsOpen] = useState(false);
  const [reportOpen, setReportOpen] = useState(false);
  const total = detail.sections.length;
  useReaderBarVar(barRef, slot === null);

  const byId = useMemo(() => new Map((positions ?? []).map((p) => [p.section_id, p])), [positions]);
  const saved = positionsLoaded ? (byId.get(sectionId) ?? null) : undefined;
  const { flush, idle } = useReadingPosition({
    chapterId: detail.id,
    sectionId,
    root: rootRef,
    end: endRef,
    saved,
    ready: Boolean(section),
    enabled: !fromCache,
  });

  const go = useCallback(
    (sid: string) => {
      flush();
      void navigate({ to: "/learn/$chapterId/$sectionId", params: { chapterId: detail.id, sectionId: sid } });
    },
    [flush, navigate, detail.id],
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey || e.shiftKey) return;
      if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
      if (!pageOwnsArrows(document.activeElement, articleRef.current)) return;
      const target = e.key === "ArrowRight" ? next : prev;
      if (!target) return;
      e.preventDefault();
      go(target.id);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [go, next, prev]);

  useEffect(() => {
    if (section) document.title = t("learn.document_title", { section: section.heading, chapter: detail.title });
  }, [section, detail.title, t]);

  if (!section) {
    const first = detail.sections[0];
    return (
      <section aria-labelledby="reader-h" className="flex flex-col gap-6 pt-6">
        <h1 id="reader-h" className="m-0 font-display text-headline">
          {detail.title}
        </h1>
        <ErrorState
          title={t("learn.section_missing")}
          message={first ? t("learn.section_moved") : t("learn.chapter_empty")}
        />
        {first ? (
          <Link to="/learn/$chapterId/$sectionId" params={{ chapterId: detail.id, sectionId: first.id }}>
            {t("learn.first_section")}
          </Link>
        ) : (
          <Link to="/learn">{t("learn.back_to_courses")}</Link>
        )}
      </section>
    );
  }

  const systemLabel = detail.systems[0]?.label;
  const where = detail.number
    ? t("learn.where_chapter", { n: String(detail.number), total: String(detail.chapter_count) })
    : t("learn.your_file");
  const whereShort = detail.number
    ? t("learn.where_chapter_short", { n: String(detail.number) })
    : t("learn.your_file");
  const read = (positions ?? []).filter((p) => p.completed).length;
  const cov = detail.coverage;
  const pos = { n: String(index + 1), total: String(total) };
  const label = [systemLabel, where].filter(Boolean).join(t("learn.separator"));
  const phoneLabel = [systemLabel, whereShort, t("learn.section_of", pos)].filter(Boolean).join(t("learn.separator"));

  const nextLink: ReactNode = next ? (
    <Link
      to="/learn/$chapterId/$sectionId"
      params={{ chapterId: detail.id, sectionId: next.id }}
      onClick={() => flush()}
      aria-label={t("learn.next_section", { heading: next.heading })}
      className={NEXT_PILL}
    >
      <span className="min-w-0 truncate">
        <span className="hidden tablet:inline">{next.heading}</span>
        <span className="tablet:hidden">{t("learn.next_short")}</span>
      </span>
      <ChevronRight aria-hidden="true" size={20} className="shrink-0" />
    </Link>
  ) : detail.next_chapter ? (
    <Link
      to="/learn/$chapterId"
      params={{ chapterId: detail.next_chapter.id }}
      onClick={() => flush()}
      aria-label={t("learn.next_chapter_named", { title: detail.next_chapter.title })}
      className={NEXT_PILL}
    >
      <span className="min-w-0 truncate">{t("learn.next_chapter")}</span>
      <ArrowRight aria-hidden="true" size={18} className="shrink-0" />
    </Link>
  ) : (
    <Link to="/learn" onClick={() => flush()} className={NEXT_PILL}>
      <span className="min-w-0 truncate">{t("learn.back_to_courses")}</span>
    </Link>
  );

  const barRow = (
    <div className="flex items-center gap-2">
      {prev ? (
        <Link
          to="/learn/$chapterId/$sectionId"
          params={{ chapterId: detail.id, sectionId: prev.id }}
          onClick={() => flush()}
          aria-label={t("learn.prev_section", { heading: prev.heading })}
          className="flex size-12 shrink-0 items-center justify-center rounded-pill bg-control text-text hover:text-text"
        >
          <ChevronLeft aria-hidden="true" size={20} />
        </Link>
      ) : (
        <span aria-hidden="true" className="size-12 shrink-0" />
      )}
      <span className="min-w-0 flex-1 truncate px-2 text-center text-text-2 text-ui-sm">
        {t("learn.position", pos)}
        <span className="hidden tablet:inline">
          {t("learn.separator")}
          {section.heading}
        </span>
      </span>
      {nextLink}
    </div>
  );

  return (
    <div className="flex flex-col gap-4 pt-1 tablet:gap-5 tablet:pt-2" data-position-idle={idle || undefined}>
      {/* Phone: one mono line (PhoneChapter board) */}
      <header className="flex items-center gap-2 tablet:hidden">
        <Link
          to="/learn"
          aria-label={t("learn.back_to_courses")}
          className="flex size-11 shrink-0 items-center justify-center rounded-pill text-text-2 hover:text-text"
        >
          <ArrowLeft aria-hidden="true" size={18} />
        </Link>
        <p className="m-0 min-w-0 flex-1 truncate font-mono text-label-sm tracking-[0.1em] text-text-3 uppercase">
          <span className="sr-only">{detail.title}</span>
          {phoneLabel}
        </p>
        <button
          type="button"
          onClick={() => setContentsOpen(true)}
          aria-haspopup="dialog"
          aria-label={t("learn.contents")}
          className="flex size-11 shrink-0 items-center justify-center rounded-pill border border-line-control text-text"
        >
          <ListOrdered aria-hidden="true" size={18} />
        </button>
      </header>

      {/* Tablet and laptop: chapter bar (Chapter / Lesson boards) */}
      <header className="hidden flex-wrap items-center gap-3 tablet:flex">
        <Link
          to="/learn"
          aria-label={t("learn.back_to_courses")}
          className="flex size-12 shrink-0 items-center justify-center rounded-pill border border-line bg-bar text-text hover:text-text"
        >
          <ArrowLeft aria-hidden="true" size={18} />
        </Link>
        <div className="mr-auto flex min-w-0 flex-1 flex-col">
          <span className="truncate font-mono text-label-sm tracking-[0.12em] text-text-3 uppercase">{label}</span>
          <span className="font-bold font-display text-[1.25rem] leading-tight">{detail.title}</span>
        </div>
        <ul aria-label={t("learn.chapter_has")} className="m-0 flex list-none flex-wrap gap-1.5 p-0">
          <li>
            <Chip tone="meta">{t("learn.chip_read", { sections: count(t, "learn.count_section", total) })}</Chip>
          </li>
          <li>
            <Chip tone="meta">{cov.videos ? count(t, "learn.count_video", cov.videos) : t("learn.no_video")}</Chip>
          </li>
          {cov.questions ? (
            <li>
              <Chip tone="meta">{t("learn.chip_questions", { n: String(cov.questions) })}</Chip>
            </li>
          ) : null}
          {cov.cards ? (
            <li>
              <Chip tone="meta">{t("learn.chip_cards", { n: String(cov.cards) })}</Chip>
            </li>
          ) : null}
        </ul>
        <Button
          variant="secondary"
          onClick={() => setContentsOpen(true)}
          icon={<ListOrdered size={16} />}
          aria-haspopup="dialog"
        >
          {t("learn.contents")}
        </Button>
      </header>

      {fromCache ? (
        <p role="status" className="m-0 text-text-2">
          {t("learn.reader_saved_copy")}
        </p>
      ) : null}

      <PageDots index={index} total={total} t={t} />

      {/* Reading page: full-bleed text on phone, a card from tablet up. The selection toolbar (Highlight / Note /
          Card / Ask, segments 13/14) anchors to this article; ink (12b) mounts per section inside it. */}
      <article
        ref={articleRef}
        tabIndex={-1}
        aria-labelledby="section-h"
        className="relative flex flex-col gap-6 pb-6 outline-none tablet:rounded-tile tablet:border tablet:border-line-soft tablet:bg-surface-1 tablet:px-11 tablet:pt-9 tablet:pb-8"
      >
        <div className="hidden flex-wrap items-baseline justify-between gap-2 font-mono text-label-sm text-text-3 tablet:flex">
          <span className="tracking-[0.12em] uppercase">
            {t("learn.section_label", { n: String(index + 1), heading: section.heading })}
          </span>
          <span>
            {t("learn.section_meta", {
              ...pos,
              minutes: minutesLabel(t, section.reading_minutes),
              read: String(read),
            })}
          </span>
        </div>
        <h1
          id="section-h"
          className="m-0 font-display font-extrabold text-[clamp(2.25rem,1.4rem+2.6vw,3.25rem)] leading-[0.98] tracking-[-0.04em]"
        >
          {section.heading}
        </h1>
        <div className="mx-auto w-full max-w-[46rem]">
          {section.body ? (
            <div ref={rootRef} className="contents">
              <ContentRenderer
                key={section.id}
                doc={section.body}
                sectionId={section.id}
                media={detail.media}
                questions={detail.questions}
                onUnknown={logUnknown}
                text={t}
              />
            </div>
          ) : (
            <p className="m-0 text-text-2">{t("learn.section_empty")}</p>
          )}
        </div>
        <div ref={endRef} aria-hidden="true" className="h-px" />
        <div className="mx-auto flex w-full max-w-[46rem] flex-wrap items-center justify-between gap-3 border-line-soft border-t pt-5">
          <span className="text-text-3 text-ui-sm">{t("content.report_issue_prompt")}</span>
          <Button
            variant="secondary"
            icon={<Flag size={16} />}
            onClick={() => setReportOpen(true)}
            aria-haspopup="dialog"
          >
            {t("content.report_issue")}
          </Button>
        </div>
      </article>

      {/* Page bar: previous · where · next (the one lime action). On phone it joins the shell's bottom bar. */}
      {slot ? (
        createPortal(<nav aria-label={t("learn.sections_nav")}>{barRow}</nav>, slot)
      ) : (
        <nav
          ref={barRef}
          aria-label={t("learn.sections_nav")}
          data-bleed=""
          className="sticky bottom-[calc(var(--shell-bottom,0px)+12px)] z-10 mx-auto w-full max-w-[min(var(--content-max),calc(100%-2*var(--gutter)))] rounded-pill border border-line-strong bg-bar/90 p-1.5 backdrop-blur-[18px]"
        >
          {barRow}
        </nav>
      )}

      <ContentsSheet
        open={contentsOpen}
        onClose={() => setContentsOpen(false)}
        detail={detail}
        currentId={section.id}
        positions={byId}
        onPick={(sid) => {
          setContentsOpen(false);
          go(sid);
        }}
      />
      <ReportIssueDialog
        open={reportOpen}
        onClose={() => setReportOpen(false)}
        refType="section"
        refId={section.id}
        what={t("learn.quoted", { text: section.heading })}
      />
    </div>
  );
}
