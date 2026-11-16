import type { BlockResults, QuestionView, ResultQuestion } from "@naadi/shared";
import { Button, Chip, cx, ErrorState, Skeleton } from "@naadi/ui";
import { Link, useNavigate } from "@tanstack/react-router";
import { Check, ChevronLeft, ChevronRight, Flag, Minus, TriangleAlert } from "lucide-react";
import { useCallback, useEffect, useRef } from "react";
import { ApiError } from "../../auth/apiError";
import { SafeHtml } from "../../content/SafeHtml";
import { useCopy } from "../../practice/copy";
import { splitWhyNot } from "../../practice/format";
import { isTypingTarget } from "../../practice/keys";
import { isFinal, useBlockResults, useReviewQuestion } from "./api";
import {
  filterCounts,
  filterList,
  isReviewFilter,
  landingFor,
  neighbours,
  ownsArrowKeys,
  REVIEW_FILTERS,
  type ReviewFilter,
  reviewKey,
  type Translate,
} from "./model";
import { ResultsNotFinished } from "./ResultsPage";

export type { ReviewFilter };

export interface ReviewSearch {
  filter: ReviewFilter;
}

/** `?filter=` of the review route: one of all, missed, flagged, changed (anything else is all). */
export function parseReviewSearch(s: Record<string, unknown>): ReviewSearch {
  return { filter: isReviewFilter(s.filter) ? s.filter : "all" };
}

const ROW =
  "box-border flex w-full min-h-[68px] items-center gap-4 rounded-block border p-3 pr-5 text-[1.0625rem] text-text";
const DOT = "flex size-11 shrink-0 items-center justify-center rounded-full font-display font-bold text-[1.125rem]";
const CHIP =
  "inline-flex min-h-11 items-center gap-2 rounded-pill px-4 text-[0.875rem] no-underline transition-colors duration-(--duration-control) ease-out";

function Loading() {
  const t = useCopy();
  return (
    <section aria-busy="true" className="flex flex-col gap-6 pt-6">
      <h1 className="sr-only">{t("results.review_title")}</h1>
      <p role="status" className="sr-only">
        {t("results.review_loading")}
      </p>
      <Skeleton className="h-14 rounded-[28px]" />
      <Skeleton className="h-11 w-2/3 rounded-pill" />
      <Skeleton className="h-40 rounded-block" />
      {[0, 1, 2, 3, 4].map((i) => (
        <Skeleton key={i} className="h-[68px] rounded-block" />
      ))}
    </section>
  );
}

function Problem({ title, message, blockId, err, retry }: ProblemProps) {
  const t = useCopy();
  return (
    <section className="flex flex-col gap-6 pt-8">
      <h1 className="sr-only">{t("results.review_title")}</h1>
      <ErrorState
        title={title}
        message={message}
        requestId={err instanceof ApiError ? err.requestId : null}
        onRetry={retry}
        retryLabel={t("results.retry")}
      />
      <p className="m-0 flex flex-wrap gap-x-6 gap-y-2">
        <Link to="/practice/blocks/$blockId/results" params={{ blockId }} className="inline-flex min-h-11 items-center">
          {t("results.review_back")}
        </Link>
        <Link to="/practice" className="inline-flex min-h-11 items-center">
          {t("practice.back_to_practice")}
        </Link>
      </p>
    </section>
  );
}

interface ProblemProps {
  title: string;
  message: string;
  blockId: string;
  err?: unknown;
  retry?: () => void;
}

function errorMessage(t: Translate, err: unknown): string {
  if (err instanceof ApiError && err.status === null) return t("results.offline");
  if (err instanceof ApiError && err.code === "not_timed") return t("results.not_timed");
  if (err instanceof ApiError && err.status === 404) return t("results.gone_body");
  return err instanceof Error ? err.message : t("results.error_title");
}

function Explanation({ view, t }: { view: QuestionView; t: Translate }) {
  const ex = view.explanation;
  if (!ex) return null;
  const rows = splitWhyNot(ex.why_not_html);
  return (
    <section
      aria-label={t("practice.explanation_label")}
      className="flex flex-col gap-4 rounded-tile border border-line-soft bg-surface-1 p-6 tablet:p-8"
    >
      <h2 className="m-0 font-bold font-display text-[clamp(1.5rem,1.1rem+1.4vw,2rem)] tracking-[-0.02em]">
        {t("practice.why_it_is", { answer: ex.answer })}
      </h2>
      {ex.explanation_html ? (
        <SafeHtml html={ex.explanation_html} className="text-reading text-text-2 [&_p]:m-0 [&_p+p]:mt-3" />
      ) : null}
      {rows ? (
        <div className="flex flex-col">
          <h3 className="m-0 pb-2 font-mono text-label text-text-3 uppercase">{t("practice.why_not")}</h3>
          {rows.map((r) => (
            <div key={r.label} className="grid grid-cols-[2.5rem_1fr] gap-4 border-line-soft border-t py-3.5">
              <span className="font-mono text-label text-text-2 uppercase">{r.label}</span>
              <span className="text-text-2">{r.text}</span>
            </div>
          ))}
        </div>
      ) : ex.why_not_html ? (
        <SafeHtml html={ex.why_not_html} className="text-text-2 [&_p]:m-0" />
      ) : null}
      {ex.educational_objective ? (
        <p className="m-0 border-line-soft border-t pt-4 text-text-2">
          <span className="font-mono text-label text-text-3 uppercase">{t("practice.objective")}</span>
          <br />
          {ex.educational_objective}
        </p>
      ) : null}
    </section>
  );
}

/** The options with the student's choice and the right answer, each marked by a word and an icon. */
function Options({ view, t }: { view: QuestionView; t: Translate }) {
  const chosen = view.attempt?.chosen ?? null;
  const answer = view.explanation?.answer ?? null;
  return (
    <ol aria-label={t("practice.options_label")} className="m-0 flex list-none flex-col gap-2.5 p-0">
      {view.question.options.map((o) => {
        const mine = chosen === o.label;
        const right = answer === o.label;
        const wrong = mine && !right;
        const tag = right
          ? mine
            ? `${t("practice.correct")} · ${t("practice.your_answer")}`
            : t("practice.correct_answer")
          : wrong
            ? `${t("practice.incorrect")} · ${t("practice.your_answer")}`
            : "";
        return (
          <li
            key={o.label}
            data-option={o.label}
            data-state={right ? "correct" : wrong ? "wrong" : undefined}
            className={cx(
              ROW,
              right && mine
                ? "border-2 border-paper bg-paper text-ground"
                : right
                  ? "border-2 border-paper bg-surface-1"
                  : wrong
                    ? "border-2 border-amber bg-surface-1"
                    : "border-line-soft bg-surface-1",
            )}
          >
            <span
              aria-hidden="true"
              className={cx(
                DOT,
                right && mine
                  ? "bg-ground text-text"
                  : right
                    ? "bg-paper text-ground"
                    : wrong
                      ? "bg-amber text-[#201200]"
                      : "bg-surface-2 text-text",
              )}
            >
              {o.label}
            </span>
            <span className="sr-only">{`${o.label}. `}</span>
            <SafeHtml html={o.html} className="min-w-0 flex-1 text-left [&_p]:m-0" />
            {tag ? (
              <span
                className={cx(
                  "flex shrink-0 items-center gap-1.5 font-mono text-label-sm tracking-[0.06em] uppercase",
                  wrong && "text-amber",
                )}
              >
                {right ? <Check aria-hidden="true" size={14} /> : <TriangleAlert aria-hidden="true" size={14} />}
                {tag}
              </span>
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}

function StatusLine({ rq, t }: { rq: ResultQuestion | undefined; t: Translate }) {
  if (!rq) return null;
  return (
    <p className="m-0 flex flex-wrap items-center gap-2">
      {rq.outcome === "correct" ? (
        <Chip tone="done" icon={<Check size={13} />}>
          {t("practice.correct")}
        </Chip>
      ) : rq.outcome === "omitted" ? (
        <Chip tone="weak" icon={<Minus size={13} />}>
          {t("results.review_omitted")}
        </Chip>
      ) : (
        <Chip tone="weak" icon={<TriangleAlert size={13} />}>
          {t("practice.incorrect")}
        </Chip>
      )}
      {rq.flagged ? (
        <Chip tone="meta" icon={<Flag size={12} />}>
          {t("practice.flagged")}
        </Chip>
      ) : null}
      {rq.change ? (
        <Chip tone={rq.change === "right_to_wrong" ? "weak" : "meta"}>{t(`results.change.${rq.change}`)}</Chip>
      ) : null}
      {rq.seconds !== null ? <Chip tone="meta">{t("results.review_seconds", { seconds: rq.seconds })}</Chip> : null}
    </p>
  );
}

function FilterChips({ r, n, filter, t }: { r: BlockResults; n: number; filter: ReviewFilter; t: Translate }) {
  const counts = filterCounts(r.questions);
  return (
    <nav aria-label={t("results.review_filter_label")} className="flex flex-wrap gap-2">
      {REVIEW_FILTERS.map((f) => {
        const to = landingFor(filterList(r.questions, f), n);
        const label = t(`results.filter_${f}`);
        const on = f === filter;
        const count = <span className="font-mono text-label-sm opacity-75">{counts[f]}</span>;
        if (to === null) {
          return (
            <span
              key={f}
              aria-disabled="true"
              className={cx(CHIP, "border border-line-dashed border-dashed text-text-muted")}
            >
              {label}
              {count}
              <span className="sr-only">{t("results.filter_empty")}</span>
            </span>
          );
        }
        return (
          <Link
            key={f}
            to="/practice/blocks/$blockId/review/$n"
            params={{ blockId: r.block_id, n: to }}
            search={{ filter: f }}
            replace
            aria-current={on ? "page" : undefined}
            className={cx(
              CHIP,
              on
                ? "border border-paper bg-paper font-semibold text-ground hover:text-ground"
                : "border border-line-control text-text hover:bg-surface-2 hover:text-text",
            )}
          >
            {label}
            {count}
          </Link>
        );
      })}
    </nav>
  );
}

interface BodyProps {
  blockId: string;
  n: number;
  filter: ReviewFilter;
  r: BlockResults;
  view: QuestionView;
}

function ReviewBody({ blockId, n, filter, r, view }: BodyProps) {
  const t = useCopy();
  const nav = useNavigate();
  const headingRef = useRef<HTMLHeadingElement>(null);
  const list = filterList(r.questions, filter);
  const { prev, next, index } = neighbours(list, n);
  const rq = r.questions.find((q) => q.n === n);
  const total = r.questions.length;

  useEffect(() => {
    headingRef.current?.focus({ preventScroll: true });
  }, []);

  const go = useCallback(
    (to: number | null) => {
      if (to === null) return;
      void nav({ to: "/practice/blocks/$blockId/review/$n", params: { blockId, n: to }, search: { filter } });
    },
    [nav, blockId, filter],
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || isTypingTarget(e.target)) return;
      const action = reviewKey(e);
      if (!action) return;
      if (e.key.startsWith("Arrow") && ownsArrowKeys(e.target)) return;
      const to = action === "next" ? next : prev;
      if (to === null) return;
      e.preventDefault();
      go(to);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [go, next, prev]);

  const position =
    index === null
      ? t("results.review_outside", { filter: t(`results.filter_${filter}`).toLowerCase() })
      : t("results.review_position", { index, count: list.length });

  return (
    <div className="flex flex-col gap-6 pt-4 pb-40 tablet:gap-8 tablet:pt-6">
      <header className="sticky top-[calc(var(--shell-top,0px)+8px)] z-10 flex flex-wrap items-center gap-x-4 gap-y-1 rounded-[28px] border border-line bg-bar/90 py-2 pr-2 pl-5 backdrop-blur-[18px]">
        <Link
          to="/practice/blocks/$blockId/results"
          params={{ blockId }}
          className="flex min-h-11 items-center gap-1 text-text-2 text-ui-sm no-underline hover:text-text"
        >
          <ChevronLeft aria-hidden="true" size={16} />
          {t("results.review_back")}
        </Link>
        <span className="font-mono text-[0.8125rem]">{t("practice.progress", { n, total })}</span>
        <span className="ml-auto pr-3 font-mono text-[0.8125rem] text-text-3" data-testid="review-position">
          {position}
        </span>
      </header>

      <FilterChips r={r} n={n} filter={filter} t={t} />

      <h1 ref={headingRef} tabIndex={-1} className="sr-only">
        {t("results.review_heading", { n, total })}
      </h1>

      <StatusLine rq={rq} t={t} />

      {view.question.topics.length ? (
        <span className="font-mono text-label text-text-3 uppercase">{view.question.topics.join(" · ")}</span>
      ) : null}
      <SafeHtml html={view.question.stem_html} className="flex flex-col gap-3 text-reading text-text [&_p]:m-0" />
      <Options view={view} t={t} />
      {view.attempt?.chosen ? null : <p className="m-0 text-text-2">{t("results.review_unanswered")}</p>}

      <Explanation view={view} t={t} />

      <p className="m-0 hidden text-text-3 text-ui-sm laptop:block">{t("results.review_keys")}</p>

      <div className="fixed inset-x-0 bottom-[calc(var(--shell-bottom,0px)+12px)] z-10 mx-auto flex w-[calc(100%-32px)] max-w-[780px] items-center gap-2 rounded-[32px] border border-line-strong bg-bar/90 p-2 backdrop-blur-[22px]">
        <Button
          variant="secondary"
          disabled={prev === null}
          onClick={() => go(prev)}
          icon={<ChevronLeft size={16} />}
          className="min-h-12 px-4 whitespace-nowrap"
        >
          {t("results.review_prev")}
        </Button>
        <span className="flex-1" />
        {next !== null ? (
          <Button
            variant="primary"
            onClick={() => go(next)}
            className="min-h-[52px] px-6 whitespace-nowrap"
            icon={<ChevronRight size={16} />}
          >
            {t("results.review_next")}
          </Button>
        ) : (
          <Link
            to="/practice/blocks/$blockId/results"
            params={{ blockId }}
            className="inline-flex min-h-[52px] items-center justify-center rounded-pill bg-lime px-6 font-semibold text-[0.9375rem] text-lime-ink no-underline hover:bg-lime-hover hover:text-lime-ink"
          >
            {t("results.review_done")}
          </Link>
        )}
      </div>
    </div>
  );
}

/**
 * `/practice/blocks/$blockId/review/$n?filter=`: a finished timed/exam block's question `n` with the
 * student's answer, the right one and the explanation; previous/next move within the filtered list.
 */
export function ReviewPage({ blockId, n, filter }: { blockId: string; n: number; filter: ReviewFilter }) {
  const t = useCopy();
  const results = useBlockResults(blockId);
  const total = results.data?.questions.length ?? null;
  const inBlock = total === null || (n >= 1 && n <= total);
  const q = useReviewQuestion(blockId, n, inBlock);

  if (results.error) {
    const err = results.error;
    if (err instanceof ApiError && err.code === "not_finished") return <ResultsNotFinished blockId={blockId} />;
    return (
      <Problem
        title={t("results.error_title")}
        message={errorMessage(t, err)}
        blockId={blockId}
        err={err}
        retry={isFinal(err) ? undefined : () => void results.refetch()}
      />
    );
  }
  if (!inBlock) {
    return (
      <Problem title={t("results.review_missing_title")} message={t("results.review_missing")} blockId={blockId} />
    );
  }
  if (!results.data || (!q.data && !q.error)) return <Loading />;
  if (!q.data) {
    const err = q.error;
    if (err instanceof ApiError && err.status === 404) {
      return (
        <Problem title={t("results.review_missing_title")} message={t("results.review_missing")} blockId={blockId} />
      );
    }
    return (
      <Problem
        title={t("practice.question_error_title")}
        message={errorMessage(t, err)}
        blockId={blockId}
        err={err}
        retry={isFinal(err) ? undefined : () => void q.refetch()}
      />
    );
  }
  const view = q.data;
  if (view.explanation === null) {
    // Answers unlock only once the whole session is finished (the server sends none before).
    return <ResultsNotFinished blockId={blockId} />;
  }
  return <ReviewBody key={`${blockId}:${n}`} blockId={blockId} n={n} filter={filter} r={results.data} view={view} />;
}
