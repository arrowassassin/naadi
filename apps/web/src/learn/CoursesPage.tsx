import type { CourseChapter, CourseSystem, CoursesResponse } from "@naadi/shared";
import { Button, Chip, cx, ErrorState, Skeleton } from "@naadi/ui";
import { Link, useRouter } from "@tanstack/react-router";
import { ArrowRight, FileText } from "lucide-react";
import { Fragment } from "react";
import { ApiError } from "../auth/apiError";
import type { TextLookup } from "../content/types";
import { useUiText } from "../copy/useUiText";
import { CTA_LINK } from "../routes/TodayPage";
import { useCourses } from "./api";
import { count, coverageLine, percent, statusWord } from "./format";

export interface CoursesSearch {
  exam?: string;
  system?: string;
}

function ProgressBar({
  read,
  total,
  label,
  inverse,
  t,
}: {
  read: number;
  total: number;
  label: string;
  inverse?: boolean;
  t: TextLookup;
}) {
  const pct = percent(read, total);
  const vars = { pct: String(pct), read: String(read), total: String(total) };
  return (
    <span className="flex items-center gap-2.5">
      <span
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
        aria-valuetext={t("learn.progress_value", vars)}
        className={cx("h-1.5 flex-1 overflow-hidden rounded-[3px]", inverse ? "bg-ground/20" : "bg-line")}
      >
        <span
          className={cx("block h-full rounded-[3px]", inverse ? "bg-ground" : "bg-paper")}
          style={{ width: `${pct}%` }}
        />
      </span>
      <span className="font-mono text-label-sm">{t("learn.progress_percent", vars)}</span>
    </span>
  );
}

/** System names like "Respiratory & Renal/Urinary Systems" may break after a slash, never mid-word. */
function BreakAfterSlash({ text }: { text: string }) {
  const parts = text.split("/");
  return (
    <>
      {parts.map((p, i) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: fixed split of one label
        <Fragment key={i}>
          {p}
          {i < parts.length - 1 ? (
            <>
              /<wbr />
            </>
          ) : null}
        </Fragment>
      ))}
    </>
  );
}

function SystemTile({ s, selected, exam, t }: { s: CourseSystem; selected: boolean; exam: string; t: TextLookup }) {
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
          <span>{count(t, "learn.count_chapter", s.chapter_ids.length)}</span>
          <span>{selected ? t("learn.tile_showing") : s.sections_read === 0 ? t("learn.status_not_started") : ""}</span>
        </span>
        <span className="font-bold font-display text-[1.375rem] leading-[1.1] tracking-[-0.02em] [overflow-wrap:break-word]">
          <BreakAfterSlash text={s.label} />
        </span>
        <ProgressBar
          read={s.sections_read}
          total={s.sections_total}
          label={t("learn.progress_label", { name: s.label })}
          inverse={selected}
          t={t}
        />
      </Link>
    </li>
  );
}

function ChapterRow({ c, t }: { c: CourseChapter; t: TextLookup }) {
  const own = c.kind !== "chapter";
  return (
    <li className="border-line-soft border-t first:border-t-0">
      <Link
        to="/learn/$chapterId"
        params={{ chapterId: c.id }}
        className="grid min-h-[72px] grid-cols-[2.75rem_minmax(0,1fr)] items-center gap-x-4 gap-y-2 py-3 text-text no-underline hover:text-text tablet:grid-cols-[2.75rem_minmax(0,1fr)_auto]"
      >
        <span className="font-mono text-[0.8125rem] text-text-3">
          {own ? <FileText aria-label={t("learn.your_file")} size={18} /> : String(c.number ?? "").padStart(2, "0")}
        </span>
        <span className="flex min-w-0 flex-col gap-1">
          <span className="font-semibold text-[1.0625rem] leading-snug">{c.title}</span>
          <span className="text-text-3 text-ui-sm">{coverageLine(t, c.coverage)}</span>
          <span className="max-w-72">
            <ProgressBar
              read={c.progress.sections_read}
              total={c.progress.sections_total}
              label={t("learn.progress_label", { name: c.title })}
              t={t}
            />
          </span>
        </span>
        <span className="col-start-2 flex flex-wrap gap-1.5 tablet:col-start-3 tablet:justify-end">
          <Chip tone="meta" srPrefix={t("learn.format_label")}>
            {own
              ? t("learn.your_file")
              : c.format === "read_watch"
                ? t("learn.format_read_watch")
                : t("learn.format_read")}
          </Chip>
          {c.coverage.questions ? (
            <Chip tone="meta">{count(t, "learn.count_question", c.coverage.questions)}</Chip>
          ) : null}
          <Chip tone={c.progress.status === "done" ? "done" : "planned"} srPrefix={t("learn.status_label")}>
            {statusWord(t, c.progress)}
          </Chip>
        </span>
      </Link>
    </li>
  );
}

function Loading({ t }: { t: TextLookup }) {
  return (
    <section aria-labelledby="courses-h" aria-busy="true" className="flex flex-col gap-8 pt-6 tablet:pt-10">
      <h1 id="courses-h" className="sr-only">
        {t("learn.title")}
      </h1>
      <p role="status" className="sr-only">
        {t("learn.loading")}
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

function Headline({ t }: { t: TextLookup }) {
  return (
    <h1
      id="courses-h"
      className="m-0 font-display font-extrabold text-[clamp(3.5rem,1.5rem+8vw,8.5rem)] leading-[0.88] tracking-[-0.05em]"
    >
      {t("learn.headline_first")}
      <span className="text-lime">{t("learn.headline_mark")}</span>
      <br />
      <span className="text-text-muted">{t("learn.headline_second")}</span>
    </h1>
  );
}

const CTA_KEY = { start: "learn.cta_start", next: "learn.cta_next", continue: "learn.cta_continue" } as const;

export function CoursesPage({ search }: { search: CoursesSearch }) {
  const q = useCourses(search);
  const t = useUiText();
  const router = useRouter();
  const routes = router.routesByPath as Record<string, unknown>;

  if (q.isPending && !q.data) return <Loading t={t} />;
  if (!q.data) {
    const err = q.error;
    return (
      <section aria-labelledby="courses-h" className="flex flex-col gap-8 pt-6 tablet:pt-10">
        <Headline t={t} />
        <ErrorState
          title={t("learn.load_failed")}
          message={err?.message ?? t("learn.error_generic")}
          requestId={err instanceof ApiError ? err.requestId : null}
          onRetry={() => void q.refetch()}
          retrying={q.isFetching}
          retryLabel={t("learn.retry")}
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

  return (
    <section aria-labelledby="courses-h" className="flex flex-col gap-8 pt-6 pb-8 tablet:gap-10 tablet:pt-10">
      <header className="flex flex-wrap items-end justify-between gap-6">
        <Headline t={t} />
        {data.exams.length > 1 ? (
          <fieldset className="m-0 flex flex-wrap gap-2 border-0 p-0">
            <legend className="sr-only">{t("learn.exam_group")}</legend>
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
          {t("learn.saved_copy")}
        </p>
      ) : null}

      {next ? (
        <div className="flex flex-col gap-2">
          <Link
            to="/learn/$chapterId/$sectionId"
            params={{ chapterId: next.chapter_id, sectionId: next.section_id }}
            className={cx(CTA_LINK, "w-fit max-w-full")}
          >
            <span className="truncate">{t(CTA_KEY[next.reason], { title: next.chapter_title })}</span>
            <ArrowRight aria-hidden="true" size={18} className="shrink-0" />
          </Link>
          <span className="pl-2 text-text-3 text-ui-sm">{next.section_heading}</span>
        </div>
      ) : null}

      {data.systems.length ? (
        <section aria-labelledby="systems-h" className="flex flex-col gap-4">
          <div className="flex flex-wrap items-baseline justify-between gap-3">
            <h2 id="systems-h" className="m-0 font-mono text-label text-text-3 uppercase">
              {t("learn.systems")}
            </h2>
            {selected ? (
              <Link
                to="/learn"
                search={{ exam: exam ?? undefined }}
                activeOptions={{ exact: true, includeSearch: true }}
                className="min-h-11 py-3 text-ui-sm"
              >
                {t("learn.show_all_systems")}
              </Link>
            ) : null}
          </div>
          <ul className="m-0 grid list-none grid-cols-[repeat(auto-fill,minmax(min(100%,220px),1fr))] gap-3 p-0 tablet:gap-4">
            {data.systems.map((s) => (
              <SystemTile key={s.id} s={s} selected={s.id === data.system} exam={exam ?? ""} t={t} />
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
            {selected ? selected.label : t("learn.all_chapters")}
          </h2>
          <span className="text-text-3 text-ui-sm">
            {count(t, "learn.count_chapter", shared.length)}
            {selected
              ? t("learn.separator") + count(t, "learn.count_system_question", selected.coverage.questions)
              : ""}
          </span>
        </div>
        {shared.length ? (
          <ol className="m-0 flex list-none flex-col p-0">
            {shared.map((c) => (
              <ChapterRow key={c.id} c={c} t={t} />
            ))}
          </ol>
        ) : (
          <div className="flex flex-col items-start gap-4 py-6">
            <p className="m-0 text-text-2">{data.system ? t("learn.empty_system") : t("learn.empty_exam")}</p>
            {data.system ? (
              <Button
                variant="secondary"
                onClick={() => void router.navigate({ to: "/learn", search: { exam: exam ?? undefined } })}
              >
                {t("learn.show_all_systems")}
              </Button>
            ) : null}
          </div>
        )}
      </section>

      {own.length ? (
        <section aria-labelledby="own-h" className="flex flex-col gap-4">
          <h2 id="own-h" className="m-0 font-bold font-display text-[1.75rem] tracking-[-0.02em]">
            {t("learn.your_files")}
          </h2>
          <ol className="m-0 flex list-none flex-col rounded-tile-sm border border-line-soft bg-surface-1 p-0 px-5">
            {own.map((c) => (
              <ChapterRow key={c.id} c={c} t={t} />
            ))}
          </ol>
        </section>
      ) : null}

      {/* Mock exams open in Practice; listed only once that route exists in this build (no dead links). */}
      {data.mock_exams.length && "/practice" in routes ? (
        <section aria-labelledby="mocks-h" className="flex flex-col gap-4">
          <h2 id="mocks-h" className="m-0 font-bold font-display text-[1.75rem] tracking-[-0.02em]">
            {t("learn.mock_exams")}
          </h2>
          <ul className="m-0 flex list-none flex-col gap-2 p-0">
            {data.mock_exams.map((m) => (
              <li key={m.id}>
                <a href="/practice" className="flex min-h-12 flex-col py-2">
                  <span className="font-semibold">{m.title}</span>
                  <span className="text-text-3 text-ui-sm">{count(t, "learn.count_question", m.question_count)}</span>
                </a>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </section>
  );
}
