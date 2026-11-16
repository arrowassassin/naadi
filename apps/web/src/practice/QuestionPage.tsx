import type { BlockDetail, QuestionView } from "@naadi/shared";
import { Button, Chip, cx, ErrorState, Skeleton } from "@naadi/ui";
import { Link, useNavigate } from "@tanstack/react-router";
import { BookOpen, Check, ChevronLeft, ChevronRight, Flag, FlaskConical, PenLine, TriangleAlert } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../auth/apiError";
import { SaveAsFlashcard } from "../cards/hooks";
import { SafeHtml } from "../content/SafeHtml";
import { ExamPage } from "../exam/ExamPage";
import { InkCanvas } from "../ink/InkCanvas";
import { ReportIssueDialog } from "../learn/ReportIssueDialog";
import {
  isUnreachable,
  useBlock,
  useBlockQuestion,
  useFlag,
  usePrefetchQuestion,
  useSendAttempt,
  useUserId,
} from "./api";
import { useCopy } from "./copy";
import { percent, splitWhyNot } from "./format";
import { isTypingTarget, keyAction } from "./keys";
import { LabValuesSheet } from "./LabValuesSheet";
import { enqueueAttempt, flushQueue, type QueuedAttempt, queuedFor } from "./outbox";

const ROW =
  "flex w-full min-h-[68px] items-center gap-4 rounded-block border p-3 pr-5 text-[1.0625rem] box-border transition-colors duration-(--duration-control) ease-out";
const DOT = "flex size-11 shrink-0 items-center justify-center rounded-full font-display font-bold text-[1.125rem]";

function Loading() {
  const t = useCopy();
  return (
    <section aria-busy="true" className="flex flex-col gap-6 pt-6">
      <p role="status" className="sr-only">
        {t("practice.loading_question")}
      </p>
      <Skeleton className="h-6 w-1/3" />
      <Skeleton className="h-40 rounded-block" />
      {[0, 1, 2, 3, 4].map((i) => (
        <Skeleton key={i} className="h-[68px] rounded-block" />
      ))}
    </section>
  );
}

/** The text of trusted-but-unrendered HTML (DOMParser runs no scripts and loads nothing). */
function htmlText(html: string): string {
  return new DOMParser().parseFromString(html, "text/html").body.textContent?.trim() ?? "";
}

function Explanation({ view, correct }: { view: QuestionView; correct: boolean | null }) {
  const t = useCopy();
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
      {correct === null ? null : (
        <span className="sr-only">{correct ? t("practice.correct") : t("practice.incorrect")}</span>
      )}
      {/* Segment 10: the explanation exists only after the student's graded attempt. */}
      <SaveAsFlashcard
        questionId={view.question.id}
        graded
        prefill={{ front: htmlText(view.question.stem_html), back: ex.educational_objective ?? "" }}
        className="self-start"
      />
    </section>
  );
}

interface BodyProps {
  blockId: string;
  n: number;
  view: QuestionView;
  fromCache: boolean;
  block: BlockDetail | undefined;
}

function QuestionBody({ blockId, n, view, fromCache, block }: BodyProps) {
  const t = useCopy();
  const nav = useNavigate();
  const userId = useUserId();
  const attempts = useSendAttempt();
  const flag = useFlag(blockId);
  const prefetch = usePrefetchQuestion();
  const headingRef = useRef<HTMLHeadingElement>(null);
  const shownAt = useRef(Date.now());
  const qid = view.question.id;

  const [choice, setChoice] = useState<string | null>(null);
  const [pending, setPending] = useState<QueuedAttempt | null>(() =>
    userId ? (queuedFor(userId, blockId, qid) ?? null) : null,
  );
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [labsOpen, setLabsOpen] = useState(false);
  const [reportOpen, setReportOpen] = useState(false);
  const [inkOn, setInkOn] = useState(false);

  const answered = view.attempt !== null;
  const chosen = view.attempt?.chosen ?? pending?.body.chosen ?? choice;
  const locked = answered || pending !== null;
  const revealed = view.explanation !== null;
  const correctLetter = view.explanation?.answer ?? null;
  const total = view.total;
  const isLast = n >= total;
  const tutor = view.mode === "tutor";
  const finished = block?.questions.every((q) => q.answered) && answered;

  useEffect(() => {
    headingRef.current?.focus({ preventScroll: true });
    // Read ahead so Next is instant and an offline block keeps going.
    if (n < total) prefetch(blockId, n + 1);
  }, [blockId, n, total, prefetch]);

  // Answers saved while offline are sent as soon as the connection returns (and once on arrival).
  useEffect(() => {
    if (!userId) return;
    const run = () =>
      void flushQueue(userId, attempts.send, isUnreachable, (b, out) => attempts.applied(b, out)).then((sent) => {
        if (sent > 0) setPending(queuedFor(userId, blockId, qid) ?? null);
      });
    run();
    window.addEventListener("online", run);
    return () => window.removeEventListener("online", run);
  }, [userId, blockId, qid, attempts]);

  const submit = useCallback(async () => {
    if (!choice || locked || submitting || !userId) return;
    setSubmitting(true);
    setError(null);
    const body = {
      question_id: qid,
      chosen: choice,
      seconds: Math.max(0, Math.round((Date.now() - shownAt.current) / 1000)),
      flagged: view.flagged,
    };
    try {
      attempts.applied(blockId, await attempts.send(blockId, body));
    } catch (err) {
      if (isUnreachable(err)) {
        enqueueAttempt(userId, blockId, body);
        setPending({ blockId, body, queuedAt: Date.now() });
      } else {
        setError(err instanceof Error ? err.message : String(err));
      }
    } finally {
      setSubmitting(false);
    }
  }, [attempts, blockId, choice, locked, qid, submitting, userId, view.flagged]);

  const toggleFlag = useCallback(() => {
    flag.mutate({ questionId: qid, flagged: !view.flagged });
  }, [flag, qid, view.flagged]);

  const go = useCallback(
    (to: number) => {
      if (to >= 1 && to <= total) void nav({ to: "/practice/blocks/$blockId/$n", params: { blockId, n: to } });
    },
    [nav, blockId, total],
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || isTypingTarget(e.target)) return;
      const action = keyAction(e, view.question.options.length);
      if (!action) return;
      if (action.type === "choose") {
        const option = view.question.options[action.index];
        if (!option || locked) return;
        setChoice(option.label);
      } else if (action.type === "flag") {
        toggleFlag();
      } else if (action.type === "next") {
        if (!isLast) go(n + 1);
      } else if (action.type === "submit") {
        void submit();
      }
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [go, isLast, locked, n, submit, toggleFlag, view.question.options]);

  const pct = Math.round((100 * n) / total);
  const lesson = view.lesson;

  return (
    <div className="flex flex-col gap-6 pt-4 pb-40 tablet:gap-8 tablet:pt-6">
      <header className="sticky top-[calc(var(--shell-top,0px)+8px)] z-10 flex flex-wrap items-center gap-3 rounded-[28px] border border-line bg-bar/90 py-2 pr-2 pl-5 backdrop-blur-[18px]">
        <Link to="/practice" className="flex min-h-11 items-center text-text-2 text-ui-sm no-underline hover:text-text">
          {t("practice.exit")}
        </Link>
        <span className="font-mono text-[0.8125rem]" aria-hidden="true">
          {n} / {total}
        </span>
        <div
          role="img"
          aria-label={t("practice.progress", { n, total })}
          className="h-1.5 min-w-24 flex-1 rounded-[3px] bg-line"
        >
          <div className="h-1.5 rounded-[3px] bg-paper" style={{ width: `${pct}%` }} />
        </div>
        {block?.config.weak_spot ? <Chip tone="meta">{t("practice.weak_block")}</Chip> : null}
        <span className="flex items-center gap-1.5">
          <button
            type="button"
            aria-label={t("practice.previous")}
            disabled={n <= 1}
            onClick={() => go(n - 1)}
            className="flex size-11 items-center justify-center rounded-full border border-line-control bg-control text-text disabled:opacity-40"
          >
            <ChevronLeft aria-hidden="true" size={18} />
          </button>
          <button
            type="button"
            aria-label={t("practice.next_question")}
            disabled={isLast}
            onClick={() => go(n + 1)}
            className="flex size-11 items-center justify-center rounded-full border border-line-control bg-control text-text disabled:opacity-40"
          >
            <ChevronRight aria-hidden="true" size={18} />
          </button>
        </span>
      </header>

      {fromCache ? (
        <p role="status" className="m-0 rounded-block border border-line-control px-4 py-3 text-text-2 text-ui-sm">
          {t("practice.offline_saved")}
        </p>
      ) : null}

      <InkCanvas enabled={inkOn} surfaceType="question" theme="dark" label={t("practice.ink")} className="rounded-tile">
        <div className="flex flex-col gap-6 tablet:gap-8">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
            {view.question.topics.length ? (
              <span className="font-mono text-label text-text-3 uppercase">{view.question.topics.join(" · ")}</span>
            ) : null}
            {lesson ? (
              <Link
                to="/learn/$chapterId"
                params={{ chapterId: lesson.chapter_id }}
                className="inline-flex min-h-11 items-center gap-1.5 rounded-pill border border-line-control px-3.5 text-[0.8125rem] text-text no-underline hover:bg-surface-2 hover:text-text"
              >
                <BookOpen aria-hidden="true" size={15} />
                {t("practice.lesson", { title: lesson.title })}
              </Link>
            ) : null}
          </div>

          <h1 ref={headingRef} tabIndex={-1} className="sr-only">
            {t("practice.progress", { n, total })}
          </h1>
          <SafeHtml html={view.question.stem_html} className="flex flex-col gap-3 text-reading text-text [&_p]:m-0" />

          <fieldset className="m-0 flex min-w-0 flex-col gap-2.5 border-0 p-0">
            <legend className="sr-only">{t("practice.options_label")}</legend>
            {view.question.options.map((o) => {
              const mine = chosen === o.label;
              const isCorrect = revealed && correctLetter === o.label;
              const wrong = revealed && mine && !isCorrect;
              const tag = isCorrect
                ? mine
                  ? `${t("practice.correct")} · ${t("practice.your_answer")}`
                  : t("practice.correct_answer")
                : wrong
                  ? `${t("practice.incorrect")} · ${t("practice.your_answer")}`
                  : locked && mine
                    ? t("practice.your_answer")
                    : "";
              return (
                <label
                  key={o.label}
                  className={cx(
                    ROW,
                    "has-[:focus-visible]:outline has-[:focus-visible]:outline-(--focus-ring-width) has-[:focus-visible]:outline-offset-(--focus-ring-offset) has-[:focus-visible]:outline-violet",
                    locked ? "cursor-default" : "cursor-pointer",
                    isCorrect
                      ? "border-lime bg-lime text-lime-ink"
                      : wrong
                        ? "border-2 border-amber bg-surface-1 text-text"
                        : mine
                          ? "border-paper bg-paper text-ground"
                          : cx("border-line-soft bg-surface-1 text-text", !locked && "hover:border-line-control"),
                  )}
                >
                  <input
                    type="radio"
                    name={`answer-${qid}`}
                    value={o.label}
                    checked={mine}
                    disabled={locked}
                    onChange={() => setChoice(o.label)}
                    className="sr-only"
                  />
                  <span
                    aria-hidden="true"
                    className={cx(
                      DOT,
                      isCorrect
                        ? "bg-lime-ink text-lime"
                        : wrong
                          ? "bg-amber text-amber-ink"
                          : mine
                            ? "bg-ground text-text"
                            : "bg-surface-2 text-text",
                    )}
                  >
                    {o.label}
                  </span>
                  <span className="sr-only">{`${o.label}. `}</span>
                  <SafeHtml html={o.html} className="min-w-0 flex-1 text-left [&_p]:m-0" />
                  {tag ? (
                    <span className="flex shrink-0 items-center gap-1.5 font-mono text-label-sm tracking-[0.06em] uppercase">
                      {isCorrect ? <Check aria-hidden="true" size={14} /> : null}
                      {wrong ? <TriangleAlert aria-hidden="true" size={14} /> : null}
                      {tag}
                    </span>
                  ) : null}
                </label>
              );
            })}
          </fieldset>
        </div>
      </InkCanvas>

      {inkOn ? null : <p className="m-0 hidden text-text-3 text-ui-sm laptop:block">{t("practice.keys_hint")}</p>}

      {pending && !answered ? (
        <p role="status" className="m-0 rounded-block border border-line-control px-4 py-3 text-text-2">
          {t("practice.offline_saved")}
        </p>
      ) : null}
      {error ? (
        <p role="alert" className="m-0 rounded-block border border-amber px-4 py-3 text-amber">
          {error}
        </p>
      ) : null}

      {revealed ? (
        <p
          role="status"
          className={cx(
            "m-0 flex items-center gap-2 font-semibold text-[1.125rem]",
            view.attempt?.correct ? "text-text" : "text-amber",
          )}
        >
          {view.attempt?.correct ? (
            <Check aria-hidden="true" size={20} />
          ) : (
            <TriangleAlert aria-hidden="true" size={20} />
          )}
          {view.attempt?.correct
            ? t("practice.correct")
            : t("practice.result_incorrect", { answer: correctLetter ?? "" })}
        </p>
      ) : null}

      <Explanation view={view} correct={view.attempt?.correct ?? null} />

      {finished && tutor ? (
        <section className="flex flex-col gap-3 rounded-tile border border-line-soft bg-surface-1 p-6">
          <h2 className="m-0 font-bold font-display text-[1.5rem] tracking-[-0.02em]">
            {t("practice.block_done_title")}
          </h2>
          <p className="m-0 text-text-2">
            {t("practice.block_done_body", {
              total,
              correct: block?.correct ?? 0,
              score: percent(block?.correct ?? 0, total),
            })}
          </p>
        </section>
      ) : null}

      <div className="flex justify-start">
        <button
          type="button"
          onClick={() => setReportOpen(true)}
          className="min-h-11 rounded-pill border border-line-control bg-transparent px-4 text-[0.8125rem] text-text-2 hover:bg-surface-2"
        >
          {t("practice.report")}
        </button>
      </div>

      <div className="fixed inset-x-0 bottom-[calc(var(--shell-bottom,0px)+12px)] z-10 mx-auto flex w-[calc(100%-32px)] max-w-[780px] items-center gap-2 rounded-[32px] border border-line-strong bg-bar/90 p-2 backdrop-blur-[22px]">
        <Button
          variant="secondary"
          aria-pressed={view.flagged}
          onClick={toggleFlag}
          icon={<Flag size={16} fill={view.flagged ? "currentColor" : "none"} />}
          className="min-h-12 px-4 whitespace-nowrap"
        >
          {view.flagged ? t("practice.flagged") : t("practice.flag")}
        </Button>
        <Button
          variant="secondary"
          onClick={() => setLabsOpen(true)}
          icon={<FlaskConical size={16} />}
          className="min-h-12 px-4 whitespace-nowrap"
        >
          {t("practice.labs")}
        </Button>
        <span className="hidden tablet:inline-flex">
          <Button
            variant="secondary"
            aria-pressed={inkOn}
            onClick={() => setInkOn((v) => !v)}
            icon={<PenLine size={16} />}
            className="min-h-12 px-4 whitespace-nowrap"
          >
            {t("practice.ink")}
          </Button>
        </span>
        <span className="flex-1" />
        {locked ? (
          isLast ? (
            <Link
              to="/practice"
              className="inline-flex min-h-12 items-center justify-center rounded-pill bg-lime px-6 font-semibold text-[0.9375rem] text-lime-ink no-underline hover:bg-lime-hover hover:text-lime-ink"
            >
              {t("practice.finish")}
            </Link>
          ) : (
            <Button variant="primary" onClick={() => go(n + 1)} className="px-6">
              {t("practice.next")}
            </Button>
          )
        ) : (
          <Button variant="primary" disabled={!choice || submitting} onClick={() => void submit()} className="px-6">
            {t("practice.submit")}
          </Button>
        )}
      </div>

      <LabValuesSheet open={labsOpen} onClose={() => setLabsOpen(false)} />
      <ReportIssueDialog
        open={reportOpen}
        onClose={() => setReportOpen(false)}
        refType="question"
        refId={qid}
        what={t("practice.report_what")}
      />
    </div>
  );
}

export function QuestionPage({ blockId, n }: { blockId: string; n: number }) {
  const t = useCopy();
  const q = useBlockQuestion(blockId, n);
  const block = useBlock(blockId);

  if (q.isPending && !q.data) return <Loading />;
  if (!q.data) {
    const err = q.error;
    const gone = err instanceof ApiError && err.status === 404;
    return (
      <section className="flex flex-col gap-6 pt-8">
        <ErrorState
          title={gone ? t("practice.question_gone_title") : t("practice.question_error_title")}
          message={
            err instanceof ApiError && err.status === null
              ? t("practice.offline_question")
              : (err?.message ?? t("practice.generic_error"))
          }
          requestId={err instanceof ApiError ? err.requestId : null}
          onRetry={gone ? undefined : () => void q.refetch()}
          retryLabel={t("practice.retry")}
          retrying={q.isFetching}
        />
        <p className="m-0">
          <Link to="/practice">{t("practice.back_to_practice")}</Link>
        </p>
      </section>
    );
  }

  const view = q.data.data;
  // Timed and exam blocks run on the exam view (segment 09), which reads the server's clock.
  if (view.mode !== "tutor") return <ExamPage blockId={blockId} n={n} />;
  return (
    <QuestionBody
      key={`${blockId}:${n}`}
      blockId={blockId}
      n={n}
      view={view}
      fromCache={q.data.fromCache}
      block={block.data}
    />
  );
}
