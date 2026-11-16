import type { ChapterDetail, ReadingPosition } from "@naadi/shared";
import { Button, Chip, cx, ErrorState, Skeleton } from "@naadi/ui";
import { Link, useNavigate } from "@tanstack/react-router";
import { ArrowLeft, ArrowRight, ChevronLeft, ChevronRight, Flag, ListOrdered } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "../auth/apiError";
import { ContentRenderer } from "../content/ContentRenderer";
import type { UnknownContent } from "../content/types";
import { useChapter, usePositions } from "./api";
import { ContentsSheet } from "./ContentsSheet";
import { minutesLabel, plural } from "./format";
import { ReportIssueDialog } from "./ReportIssueDialog";
import { useReadingPosition } from "./useReadingPosition";

function isEditable(t: EventTarget | null): boolean {
  if (!(t instanceof HTMLElement)) return false;
  return t.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName) || t.closest("dialog") !== null;
}

function logUnknown(u: UnknownContent) {
  // The API logs the same finding server-side with the request id; this keeps it visible in devtools.
  console.warn(`naadi: unknown content ${u.kind} "${u.type}" at ${u.path}`);
}

function ReaderSkeleton() {
  return (
    <div aria-busy="true" className="flex flex-col gap-6 pt-4">
      <p role="status" className="sr-only">
        Loading the chapter…
      </p>
      <h1 className="sr-only">Chapter</h1>
      <Skeleton className="h-14 w-full max-w-2xl rounded-pill" />
      <div className="flex flex-col gap-5 rounded-tile-sm border border-line-soft bg-surface-1 p-6 tablet:rounded-tile tablet:p-11">
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

function PageDots({ index, total }: { index: number; total: number }) {
  if (total > 40) return null;
  return (
    <div role="img" aria-label={`Section ${index + 1} of ${total}`} className="flex gap-1 tablet:hidden">
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
  if (chapter.isPending && !chapter.data) return <ReaderSkeleton />;
  if (!chapter.data) {
    const err = chapter.error;
    const missing = err instanceof ApiError && err.status === 404;
    return (
      <section aria-labelledby="reader-h" className="flex flex-col gap-6 pt-6">
        <h1 id="reader-h" className="m-0 font-display text-headline">
          {missing ? "Chapter not found" : "Chapter"}
        </h1>
        <ErrorState
          title={missing ? "This chapter is not available" : "The chapter could not be loaded"}
          message={err?.message ?? "Something went wrong."}
          requestId={err instanceof ApiError ? err.requestId : null}
          onRetry={missing ? undefined : () => void chapter.refetch()}
          retrying={chapter.isFetching}
        />
        <Link to="/learn" className="min-h-11 py-3">
          Back to courses
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
    />
  );
}

function Reader({
  detail,
  fromCache,
  sectionId,
  positions,
  positionsLoaded,
}: {
  detail: ChapterDetail;
  fromCache: boolean;
  sectionId: string;
  positions: ReadingPosition[] | undefined;
  positionsLoaded: boolean;
}) {
  const navigate = useNavigate();
  const index = detail.sections.findIndex((s) => s.id === sectionId);
  const section = index >= 0 ? detail.sections[index] : undefined;
  const prev = index > 0 ? detail.sections[index - 1] : undefined;
  const next = index >= 0 && index < detail.sections.length - 1 ? detail.sections[index + 1] : undefined;
  const rootRef = useRef<HTMLDivElement>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const [contentsOpen, setContentsOpen] = useState(false);
  const [reportOpen, setReportOpen] = useState(false);
  const total = detail.sections.length;

  const byId = useMemo(() => new Map((positions ?? []).map((p) => [p.section_id, p])), [positions]);
  const saved = positionsLoaded ? (byId.get(sectionId) ?? null) : undefined;
  const { flush } = useReadingPosition({
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
      if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey || e.shiftKey || isEditable(e.target)) return;
      if (e.key === "ArrowRight" && next) {
        e.preventDefault();
        go(next.id);
      } else if (e.key === "ArrowLeft" && prev) {
        e.preventDefault();
        go(prev.id);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [go, next, prev]);

  useEffect(() => {
    if (section) document.title = `${section.heading} · ${detail.title} · Naadi`;
  }, [section, detail.title]);

  if (!section) {
    const first = detail.sections[0];
    return (
      <section aria-labelledby="reader-h" className="flex flex-col gap-6 pt-6">
        <h1 id="reader-h" className="m-0 font-display text-headline">
          {detail.title}
        </h1>
        <ErrorState
          title="This section is not part of the chapter"
          message={first ? "It may have been moved or removed." : "This chapter has no sections yet."}
        />
        {first ? (
          <Link to="/learn/$chapterId/$sectionId" params={{ chapterId: detail.id, sectionId: first.id }}>
            Go to the first section
          </Link>
        ) : (
          <Link to="/learn">Back to courses</Link>
        )}
      </section>
    );
  }

  const systemLabel = detail.systems[0]?.label;
  const where = detail.number ? `Chapter ${detail.number} of ${detail.chapter_count}` : "Your file";
  const read = (positions ?? []).filter((p) => p.completed).length;
  const cov = detail.coverage;

  return (
    <div className="flex flex-col gap-4 pt-2 tablet:gap-5">
      {/* Chapter bar */}
      <header className="flex flex-wrap items-center gap-3">
        <Link
          to="/learn"
          aria-label="Back to courses"
          className="flex size-12 shrink-0 items-center justify-center rounded-pill border border-line bg-bar text-text hover:text-text"
        >
          <ArrowLeft aria-hidden="true" size={18} />
        </Link>
        <div className="mr-auto flex min-w-0 flex-1 flex-col">
          <span className="truncate font-mono text-label-sm tracking-[0.12em] text-text-3 uppercase">
            {systemLabel ? `${systemLabel} · ` : ""}
            {where}
          </span>
          <span className="font-bold font-display text-[1.125rem] leading-tight tablet:text-[1.25rem]">
            {detail.title}
          </span>
        </div>
        <ul aria-label="What this chapter has" className="m-0 hidden list-none flex-wrap gap-1.5 p-0 tablet:flex">
          <li>
            <Chip tone="meta">{`Read · ${plural(total, "section")}`}</Chip>
          </li>
          <li>
            <Chip tone="meta">{cov.videos ? plural(cov.videos, "video") : "No video"}</Chip>
          </li>
          {cov.questions ? (
            <li>
              <Chip tone="meta">{`Questions · ${cov.questions}`}</Chip>
            </li>
          ) : null}
          {cov.cards ? (
            <li>
              <Chip tone="meta">{`Cards · ${cov.cards}`}</Chip>
            </li>
          ) : null}
        </ul>
        <Button
          variant="secondary"
          onClick={() => setContentsOpen(true)}
          icon={<ListOrdered size={16} />}
          aria-haspopup="dialog"
        >
          Contents
        </Button>
      </header>

      {fromCache ? (
        <p role="status" className="m-0 text-text-2">
          Reading the copy saved on this device. Your place is saved again when you are back online.
        </p>
      ) : null}

      <PageDots index={index} total={total} />

      {/* Reading page. The selection toolbar (Highlight / Note / Card / Ask, segments 13/14) anchors to this
          article; ink (12b) mounts per section inside it. */}
      <article
        aria-labelledby="section-h"
        className="relative flex flex-col gap-6 rounded-tile-sm border border-line-soft bg-surface-1 px-5 pt-6 pb-8 tablet:rounded-tile tablet:px-11 tablet:pt-9"
      >
        <div className="flex flex-wrap items-baseline justify-between gap-2 font-mono text-label-sm text-text-3">
          <span className="tracking-[0.12em] uppercase">
            Section {index + 1} · {section.heading}
          </span>
          <span>
            {index + 1} / {total} · {minutesLabel(section.reading_minutes)} · {read} of {total} read
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
              />
            </div>
          ) : (
            <p className="m-0 text-text-2">This section has no text yet.</p>
          )}
        </div>
        <div ref={endRef} aria-hidden="true" className="h-px" />
        <div className="mx-auto flex w-full max-w-[46rem] flex-wrap items-center justify-between gap-3 border-line-soft border-t pt-5">
          <span className="text-text-3 text-ui-sm">Something wrong or unclear in this section?</span>
          <Button
            variant="secondary"
            icon={<Flag size={16} />}
            onClick={() => setReportOpen(true)}
            aria-haspopup="dialog"
          >
            Report an issue
          </Button>
        </div>
      </article>

      {/* Page bar: previous · where · next (the one lime action). */}
      <nav
        aria-label="Sections"
        data-bleed=""
        className="sticky bottom-[calc(var(--shell-bottom,0px)+12px)] z-10 mx-auto w-full max-w-[min(var(--content-max),calc(100%-2*var(--gutter)))]"
      >
        <div className="flex items-center gap-2 rounded-pill border border-line-strong bg-bar/90 p-1.5 backdrop-blur-[18px]">
          {prev ? (
            <Link
              to="/learn/$chapterId/$sectionId"
              params={{ chapterId: detail.id, sectionId: prev.id }}
              onClick={() => flush()}
              aria-label={`Previous section: ${prev.heading}`}
              className="flex size-12 shrink-0 items-center justify-center rounded-pill bg-control text-text hover:text-text"
            >
              <ChevronLeft aria-hidden="true" size={20} />
            </Link>
          ) : (
            <span aria-hidden="true" className="size-12 shrink-0" />
          )}
          <span className="min-w-0 flex-1 truncate px-2 text-center text-text-2 text-ui-sm">
            {index + 1} of {total}
            <span className="hidden tablet:inline"> · {section.heading}</span>
          </span>
          {next ? (
            <Link
              to="/learn/$chapterId/$sectionId"
              params={{ chapterId: detail.id, sectionId: next.id }}
              onClick={() => flush()}
              className="flex min-h-12 min-w-12 max-w-[60%] shrink-0 items-center justify-center gap-2 rounded-pill bg-lime px-4 font-semibold text-lime-ink no-underline hover:bg-lime-hover hover:text-lime-ink"
            >
              <span className="truncate">
                <span className="sr-only tablet:not-sr-only">Next: </span>
                <span className="hidden tablet:inline">{next.heading}</span>
                <span className="tablet:hidden">Next</span>
              </span>
              <ChevronRight aria-hidden="true" size={20} className="shrink-0" />
            </Link>
          ) : detail.next_chapter ? (
            <Link
              to="/learn/$chapterId"
              params={{ chapterId: detail.next_chapter.id }}
              onClick={() => flush()}
              className="flex min-h-12 max-w-[60%] shrink-0 items-center justify-center gap-2 rounded-pill bg-lime px-4 font-semibold text-lime-ink no-underline hover:bg-lime-hover hover:text-lime-ink"
            >
              <span className="truncate">Next chapter</span>
              <ArrowRight aria-hidden="true" size={18} className="shrink-0" />
            </Link>
          ) : (
            <Link
              to="/learn"
              onClick={() => flush()}
              className="flex min-h-12 shrink-0 items-center justify-center gap-2 rounded-pill bg-lime px-4 font-semibold text-lime-ink no-underline hover:bg-lime-hover hover:text-lime-ink"
            >
              Back to courses
            </Link>
          )}
        </div>
      </nav>

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
        what={`“${section.heading}”`}
      />
    </div>
  );
}
