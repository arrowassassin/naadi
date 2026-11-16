import type { CourseChapter, CourseSystem, CoursesResponse } from "@naadi/shared";
import { Button, Chip, cx, ErrorState, Skeleton } from "@naadi/ui";
import { Link, useRouter } from "@tanstack/react-router";
import { ArrowRight, FileText } from "lucide-react";
import { ApiError } from "../auth/apiError";
import { CTA_LINK } from "../routes/TodayPage";
import { useCourses } from "./api";
import { coverageLine, percent, plural, statusWord } from "./format";

export interface CoursesSearch {
  exam?: string;
  system?: string;
}

function ProgressBar({
  read,
  total,
  label,
  inverse,
}: {
  read: number;
  total: number;
  label: string;
  inverse?: boolean;
}) {
  const pct = percent(read, total);
  return (
    <span className="flex items-center gap-2.5">
      <span
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
        aria-valuetext={`${pct}% read, ${read} of ${total} sections`}
        className={cx("h-1.5 flex-1 overflow-hidden rounded-[3px]", inverse ? "bg-ground/20" : "bg-line")}
      >
        <span
          className={cx("block h-full rounded-[3px]", inverse ? "bg-ground" : "bg-paper")}
          style={{ width: `${pct}%` }}
        />
      </span>
      <span className="font-mono text-label-sm">{pct}% read</span>
    </span>
  );
}

function SystemTile({ s, selected, exam }: { s: CourseSystem; selected: boolean; exam: string }) {
  return (
    <li className="flex">
      <Link
        to="/learn"
        search={{ exam, system: selected ? undefined : s.id }}
        aria-current={selected ? "page" : undefined}
        activeOptions={{ exact: true, includeSearch: true }}
        className={cx(
          "flex min-h-40 w-full flex-col justify-between gap-4 rounded-tile-sm border p-5 no-underline",
          "transition-colors duration-(--duration-control) ease-out",
          selected
            ? "border-paper bg-paper text-ground hover:text-ground"
            : "border-line-soft bg-surface-1 text-text hover:border-line-control hover:text-text",
        )}
      >
        <span className="flex justify-between gap-2 font-mono text-label-sm tracking-[0.08em] uppercase opacity-80">
          <span>{plural(s.chapter_ids.length, "chapter")}</span>
          <span>{selected ? "Showing" : s.sections_read === 0 ? "Not started" : ""}</span>
        </span>
        <span className="font-bold font-display text-[1.375rem] leading-[1.1] tracking-[-0.02em] [overflow-wrap:anywhere]">
          {s.label}
        </span>
        <ProgressBar read={s.sections_read} total={s.sections_total} label={`${s.label} progress`} inverse={selected} />
      </Link>
    </li>
  );
}

function ChapterRow({ c }: { c: CourseChapter }) {
  const own = c.kind !== "chapter";
  return (
    <li className="border-line-soft border-t first:border-t-0">
      <Link
        to="/learn/$chapterId"
        params={{ chapterId: c.id }}
        className="grid min-h-[72px] grid-cols-[2.75rem_minmax(0,1fr)] items-center gap-x-4 gap-y-2 py-3 text-text no-underline hover:text-text tablet:grid-cols-[2.75rem_minmax(0,1fr)_auto]"
      >
        <span className="font-mono text-[0.8125rem] text-text-3">
          {own ? <FileText aria-label="Your file" size={18} /> : String(c.number ?? "").padStart(2, "0")}
        </span>
        <span className="flex min-w-0 flex-col gap-1">
          <span className="font-semibold text-[1.0625rem] leading-snug">{c.title}</span>
          <span className="text-text-3 text-ui-sm">{coverageLine(c.coverage)}</span>
          <span className="max-w-72">
            <ProgressBar
              read={c.progress.sections_read}
              total={c.progress.sections_total}
              label={`${c.title} progress`}
            />
          </span>
        </span>
        <span className="col-start-2 flex flex-wrap gap-1.5 tablet:col-start-3 tablet:justify-end">
          <Chip tone="meta" srPrefix="Format">
            {own ? "Your file" : c.format === "read_watch" ? "Read + watch" : "Read"}
          </Chip>
          {c.coverage.questions ? <Chip tone="meta">{plural(c.coverage.questions, "question")}</Chip> : null}
          <Chip tone={c.progress.status === "done" ? "done" : "planned"} srPrefix="Status">
            {statusWord(c.progress)}
          </Chip>
        </span>
      </Link>
    </li>
  );
}

function Loading() {
  return (
    <section aria-labelledby="courses-h" aria-busy="true" className="flex flex-col gap-8 pt-6 tablet:pt-10">
      <h1 id="courses-h" className="sr-only">
        Courses
      </h1>
      <p role="status" className="sr-only">
        Loading your courses…
      </p>
      <Skeleton className="h-[clamp(5rem,12vw,9rem)] w-2/3 rounded-block" />
      <div className="grid grid-cols-[repeat(auto-fill,minmax(220px,1fr))] gap-4">
        {[0, 1, 2, 3, 4, 5].map((i) => (
          <Skeleton key={i} className="h-40 rounded-tile-sm" />
        ))}
      </div>
      <Skeleton className="h-72 rounded-tile" />
    </section>
  );
}

function Headline() {
  return (
    <h1
      id="courses-h"
      className="m-0 font-display font-extrabold text-[clamp(3.5rem,1.5rem+8vw,8.5rem)] leading-[0.88] tracking-[-0.05em]"
    >
      Everything<span className="text-lime">,</span>
      <br />
      <span className="text-text-muted">mapped.</span>
    </h1>
  );
}

export function CoursesPage({ search }: { search: CoursesSearch }) {
  const q = useCourses(search);
  const router = useRouter();
  const routes = router.routesByPath as Record<string, unknown>;

  if (q.isPending && !q.data) return <Loading />;
  if (!q.data) {
    const err = q.error;
    return (
      <section aria-labelledby="courses-h" className="flex flex-col gap-8 pt-6 tablet:pt-10">
        <Headline />
        <ErrorState
          title="Your courses could not be loaded"
          message={err?.message ?? "Something went wrong."}
          requestId={err instanceof ApiError ? err.requestId : null}
          onRetry={() => void q.refetch()}
          retrying={q.isFetching}
        />
      </section>
    );
  }

  const data: CoursesResponse = q.data.data;
  const exam = data.exam;
  const shared = data.chapters.filter((c) => c.kind === "chapter");
  const own = data.chapters.filter((c) => c.kind !== "chapter");
  const selected = data.systems.find((s) => s.id === data.system) ?? null;
  const next = data.next_up;
  const ctaVerb = next?.reason === "start" ? "Start" : next?.reason === "next" ? "Next" : "Continue";

  return (
    <section aria-labelledby="courses-h" className="flex flex-col gap-8 pt-6 pb-8 tablet:gap-10 tablet:pt-10">
      <header className="flex flex-wrap items-end justify-between gap-6">
        <Headline />
        {data.exams.length > 1 ? (
          <fieldset className="m-0 flex flex-wrap gap-2 border-0 p-0">
            <legend className="sr-only">Exam</legend>
            {data.exams.map((e) => (
              <Link
                key={e.id}
                to="/learn"
                search={{ exam: e.id }}
                aria-current={e.id === exam ? "page" : undefined}
                activeOptions={{ exact: true, includeSearch: true }}
                className={cx(
                  "inline-flex min-h-12 items-center rounded-pill px-5 text-[0.875rem] no-underline",
                  e.id === exam
                    ? "bg-paper font-semibold text-ground hover:text-ground"
                    : "border border-line-control text-text hover:bg-surface-2 hover:text-text",
                )}
              >
                {e.label}
              </Link>
            ))}
          </fieldset>
        ) : null}
      </header>

      {q.data.fromCache ? (
        <p role="status" className="m-0 text-text-2">
          Showing the copy saved on this device; progress updates when you are back online.
        </p>
      ) : null}

      {next ? (
        <div className="flex flex-col gap-2">
          <Link
            to="/learn/$chapterId/$sectionId"
            params={{ chapterId: next.chapter_id, sectionId: next.section_id }}
            className={cx(CTA_LINK, "w-fit max-w-full")}
          >
            <span className="truncate">
              {ctaVerb}: {next.chapter_title}
            </span>
            <ArrowRight aria-hidden="true" size={18} className="shrink-0" />
          </Link>
          <span className="pl-2 text-text-3 text-ui-sm">{next.section_heading}</span>
        </div>
      ) : null}

      {data.systems.length ? (
        <section aria-labelledby="systems-h" className="flex flex-col gap-4">
          <div className="flex flex-wrap items-baseline justify-between gap-3">
            <h2 id="systems-h" className="m-0 font-mono text-label text-text-3 uppercase">
              Systems
            </h2>
            {selected ? (
              <Link
                to="/learn"
                search={{ exam: exam ?? undefined }}
                activeOptions={{ exact: true, includeSearch: true }}
                className="min-h-11 py-3 text-ui-sm"
              >
                Show all systems
              </Link>
            ) : null}
          </div>
          <ul className="m-0 grid list-none grid-cols-[repeat(auto-fill,minmax(min(100%,220px),1fr))] gap-3 p-0 tablet:gap-4">
            {data.systems.map((s) => (
              <SystemTile key={s.id} s={s} selected={s.id === data.system} exam={exam ?? ""} />
            ))}
          </ul>
        </section>
      ) : null}

      <section
        aria-labelledby="chapters-h"
        className="flex flex-col gap-5 rounded-tile-sm border border-line-soft bg-surface-1 p-5 tablet:rounded-tile tablet:p-9"
      >
        <div className="flex flex-wrap items-end justify-between gap-3">
          <h2
            id="chapters-h"
            className="m-0 font-display font-extrabold text-[clamp(2rem,1.2rem+3vw,4rem)] leading-[0.95] tracking-[-0.04em]"
          >
            {selected ? selected.label : "All chapters"}
          </h2>
          <span className="text-text-3 text-ui-sm">
            {plural(shared.length, "chapter")}
            {selected ? ` · ${plural(selected.coverage.questions, "question")} on this system` : ""}
          </span>
        </div>
        {shared.length ? (
          <ol className="m-0 flex list-none flex-col p-0">
            {shared.map((c) => (
              <ChapterRow key={c.id} c={c} />
            ))}
          </ol>
        ) : (
          <div className="flex flex-col items-start gap-4 py-6">
            <p className="m-0 text-text-2">
              {data.system ? "No chapters cover this system yet." : "There are no chapters for this exam yet."}
            </p>
            {data.system ? (
              <Button
                variant="secondary"
                onClick={() => void router.navigate({ to: "/learn", search: { exam: exam ?? undefined } })}
              >
                Show all systems
              </Button>
            ) : null}
          </div>
        )}
      </section>

      {own.length ? (
        <section aria-labelledby="own-h" className="flex flex-col gap-4">
          <h2 id="own-h" className="m-0 font-bold font-display text-[1.75rem] tracking-[-0.02em]">
            Your files
          </h2>
          <ol className="m-0 flex list-none flex-col rounded-tile-sm border border-line-soft bg-surface-1 px-5 p-0">
            {own.map((c) => (
              <ChapterRow key={c.id} c={c} />
            ))}
          </ol>
        </section>
      ) : null}

      {/* Mock exams open in Practice; listed only once that route exists in this build (no dead links). */}
      {data.mock_exams.length && "/practice" in routes ? (
        <section aria-labelledby="mocks-h" className="flex flex-col gap-4">
          <h2 id="mocks-h" className="m-0 font-bold font-display text-[1.75rem] tracking-[-0.02em]">
            Mock exams
          </h2>
          <ul className="m-0 flex list-none flex-col gap-2 p-0">
            {data.mock_exams.map((m) => (
              <li key={m.id}>
                <a href={`/practice?mock=${encodeURIComponent(m.id)}`} className="flex min-h-12 flex-col py-2">
                  <span className="font-semibold">{m.title}</span>
                  <span className="text-text-3 text-ui-sm">{plural(m.question_count, "question")}</span>
                </a>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </section>
  );
}
