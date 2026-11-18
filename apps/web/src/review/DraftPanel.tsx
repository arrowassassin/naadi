import type { AuthoringRunIn } from "@naadi/shared";
import { Button, Chip, ErrorState, Skeleton, Tile } from "@naadi/ui";
import { Link } from "@tanstack/react-router";
import { ChevronDown, ChevronRight, Loader2, Sparkles } from "lucide-react";
import { type FormEvent, useEffect, useId, useMemo, useState } from "react";
import { isRunning, requestIdOf, reviewErrorKey, useGaps, useRuns, useStartRun, useTaxonomy } from "./api";
import { useReviewCopy } from "./copy";
import { FIELD, LABEL } from "./ui";

type Exam = "step1" | "step2ck";

/** "Draft new items": a taxonomy cell (a topic from the coverage gaps or a search), difficulty and count. Starting a
 * run is assistant work, so its button is violet; the hub's one lime CTA is "Review next". */
export function DraftPanel({ defaultOpen = false }: { defaultOpen?: boolean }) {
  const t = useReviewCopy();
  const ids = useId();
  const [open, setOpen] = useState(defaultOpen);
  const [exam, setExam] = useState<Exam>("step1");
  const [search, setSearch] = useState("");
  const [topic, setTopic] = useState("");
  const [second, setSecond] = useState("");
  const [competency, setCompetency] = useState("");
  const [difficulty, setDifficulty] = useState<"easy" | "medium" | "hard">("medium");
  const [count, setCount] = useState(1);
  const [objective, setObjective] = useState("");
  const gaps = useGaps(open);
  const tax = useTaxonomy(exam, open);
  const runs = useRuns(true);
  const start = useStartRun();
  const running = isRunning(runs.data);

  const gapList = useMemo(() => (gaps.data?.items ?? []).filter((g) => g.exam === exam), [gaps.data, exam]);
  const q = search.trim().toLowerCase();
  const gapOptions = gapList.filter((g) => !q || g.label.toLowerCase().includes(q));
  const gapIds = new Set(gapList.map((g) => g.topic_id));
  const otherOptions = (tax.data?.topics ?? []).filter(
    (n) => !gapIds.has(n.id) && (!q || n.label.toLowerCase().includes(q)),
  );
  const target = gaps.data?.min_published ?? 0;

  // A topic of another exam, or one the search hides, is no longer a valid choice.
  // biome-ignore lint/correctness/useExhaustiveDependencies: reset the dependent picks when the exam changes
  useEffect(() => {
    setTopic("");
    setSecond("");
    setCompetency("");
  }, [exam]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!topic || start.isPending || running) return;
    const body: AuthoringRunIn = {
      exam,
      topic_id: topic,
      difficulty,
      count,
      ...(second ? { second_id: second } : {}),
      ...(competency ? { competency_id: competency } : {}),
      ...(objective.trim() ? { objective: objective.trim() } : {}),
    };
    try {
      await start.mutateAsync(body);
    } catch {
      // shown below
    }
  }

  const disabledWhy = running ? t("review.draft_running") : !topic ? t("review.draft_pick_topic") : null;

  return (
    <Tile as="section" aria-labelledby={`${ids}-h`} className="flex flex-col gap-5" data-testid="draft-panel">
      <h2 id={`${ids}-h`} className="m-0">
        <button
          type="button"
          aria-expanded={open}
          aria-controls={`${ids}-body`}
          onClick={() => setOpen((o) => !o)}
          className="flex min-h-11 w-full items-center gap-3 border-0 bg-transparent p-0 text-left font-bold font-display text-[1.375rem] text-text tracking-[-0.02em]"
        >
          {open ? <ChevronDown aria-hidden="true" size={20} /> : <ChevronRight aria-hidden="true" size={20} />}
          {t("review.draft_panel_title")}
          {running ? (
            <Chip tone="assistant" icon={<Loader2 size={12} className="animate-spin" />}>
              {t("review.run_running")}
            </Chip>
          ) : null}
        </button>
      </h2>
      <div id={`${ids}-body`} hidden={!open} className="flex flex-col gap-6">
        <p className="m-0 text-text-2 text-ui-sm">{t("review.draft_panel_intro")}</p>
        <form onSubmit={submit} className="flex flex-col gap-5">
          <div className="grid grid-cols-1 gap-5 tablet:grid-cols-2">
            <div className="flex flex-col gap-2">
              <label htmlFor={`${ids}-exam`} className={LABEL}>
                {t("review.draft_exam")}
              </label>
              <select
                id={`${ids}-exam`}
                value={exam}
                onChange={(e) => setExam(e.target.value as Exam)}
                className={FIELD}
              >
                <option value="step1">{t("review.exam_step1")}</option>
                <option value="step2ck">{t("review.exam_step2ck")}</option>
              </select>
            </div>
            <div className="flex flex-col gap-2">
              <label htmlFor={`${ids}-search`} className={LABEL}>
                {t("review.draft_search")}
              </label>
              <input
                id={`${ids}-search`}
                type="search"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className={FIELD}
                autoComplete="off"
              />
            </div>
          </div>
          <div className="flex flex-col gap-2">
            <label htmlFor={`${ids}-topic`} className={LABEL}>
              {t("review.draft_topic")}
            </label>
            {gaps.isPending || tax.isPending ? <Skeleton className="h-12 w-full rounded-block" /> : null}
            {gaps.isError || tax.isError ? (
              <ErrorState
                title={t("review.draft_topics_error")}
                message={t(reviewErrorKey(gaps.error ?? tax.error))}
                requestId={requestIdOf(gaps.error ?? tax.error)}
                onRetry={() => {
                  void gaps.refetch();
                  void tax.refetch();
                }}
                retryLabel={t("review.retry")}
              />
            ) : null}
            {gaps.data && tax.data ? (
              <select
                id={`${ids}-topic`}
                value={topic}
                onChange={(e) => setTopic(e.target.value)}
                className={FIELD}
                data-testid="draft-topic"
                required
              >
                <option value="">{t("review.draft_pick")}</option>
                {gapOptions.length ? (
                  <optgroup label={t("review.draft_gaps_group")}>
                    {gapOptions.map((g) => (
                      <option key={g.topic_id} value={g.topic_id}>
                        {t("review.draft_gap_option", { label: g.label, published: g.published, target })}
                      </option>
                    ))}
                  </optgroup>
                ) : null}
                {otherOptions.length ? (
                  <optgroup label={t("review.draft_all_group")}>
                    {otherOptions.map((n) => (
                      <option key={n.id} value={n.id}>
                        {n.label}
                      </option>
                    ))}
                  </optgroup>
                ) : null}
              </select>
            ) : null}
            {gaps.data && tax.data && gapOptions.length + otherOptions.length === 0 ? (
              <p role="status" className="m-0 text-text-2 text-ui-sm">
                {t("review.draft_no_topics")}
              </p>
            ) : null}
          </div>
          <div className="grid grid-cols-1 gap-5 tablet:grid-cols-2">
            <div className="flex flex-col gap-2">
              <label htmlFor={`${ids}-second`} className={LABEL}>
                {t(exam === "step1" ? "review.draft_discipline" : "review.draft_clinical")}
              </label>
              <select id={`${ids}-second`} value={second} onChange={(e) => setSecond(e.target.value)} className={FIELD}>
                <option value="">{t("review.draft_auto")}</option>
                {(tax.data?.seconds ?? []).map((n) => (
                  <option key={n.id} value={n.id}>
                    {n.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="flex flex-col gap-2">
              <label htmlFor={`${ids}-comp`} className={LABEL}>
                {t("review.draft_competency")}
              </label>
              <select
                id={`${ids}-comp`}
                value={competency}
                onChange={(e) => setCompetency(e.target.value)}
                className={FIELD}
              >
                <option value="">{t("review.draft_auto")}</option>
                {(tax.data?.competencies ?? []).map((n) => (
                  <option key={n.id} value={n.id}>
                    {n.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="flex flex-col gap-2">
              <label htmlFor={`${ids}-diff`} className={LABEL}>
                {t("review.draft_difficulty")}
              </label>
              <select
                id={`${ids}-diff`}
                value={difficulty}
                onChange={(e) => setDifficulty(e.target.value as "easy" | "medium" | "hard")}
                className={FIELD}
              >
                <option value="easy">{t("review.difficulty_easy")}</option>
                <option value="medium">{t("review.difficulty_medium")}</option>
                <option value="hard">{t("review.difficulty_hard")}</option>
              </select>
            </div>
            <div className="flex flex-col gap-2">
              <label htmlFor={`${ids}-count`} className={LABEL}>
                {t("review.draft_count")}
              </label>
              <select
                id={`${ids}-count`}
                value={count}
                onChange={(e) => setCount(Number(e.target.value))}
                className={FIELD}
              >
                <option value={1}>1</option>
                <option value={2}>2</option>
                <option value={3}>3</option>
              </select>
            </div>
          </div>
          <div className="flex flex-col gap-2">
            <label htmlFor={`${ids}-obj`} className={LABEL}>
              {t("review.draft_objective")}
            </label>
            <input
              id={`${ids}-obj`}
              value={objective}
              onChange={(e) => setObjective(e.target.value)}
              maxLength={300}
              className={FIELD}
            />
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <Button
              type="submit"
              variant="assistant"
              icon={start.isPending ? <Loader2 size={16} className="animate-spin" /> : <Sparkles size={16} />}
              disabled={disabledWhy !== null || start.isPending}
              aria-describedby={`${ids}-why`}
            >
              {t("review.draft_start")}
            </Button>
            <p id={`${ids}-why`} className="m-0 text-text-2 text-ui-sm">
              {start.isPending ? t("review.draft_starting") : (disabledWhy ?? t("review.draft_hint"))}
            </p>
          </div>
          {start.error ? (
            <p role="alert" className="m-0 rounded-block border border-amber/60 bg-surface-2 px-4 py-3">
              {t(reviewErrorKey(start.error))}
              {requestIdOf(start.error) ? (
                <span className="block font-mono text-label-sm text-text-3">{requestIdOf(start.error)}</span>
              ) : null}
            </p>
          ) : null}
        </form>
        {open ? <RunsList /> : null}
      </div>
    </Tile>
  );
}

const OUTCOME_KNOWN = new Set([
  "in_review",
  "needs_independent_check",
  "discarded_lint",
  "discarded_solver",
  "no_excerpts",
  "budget",
  "failed",
]);

function outcomeKey(o: string): string {
  return OUTCOME_KNOWN.has(o) ? `review.outcome.${o}` : "review.outcome.unknown";
}

/** Active and recent runs: status in words, outcomes as a list, polled while one is running. */
function RunsList() {
  const t = useReviewCopy();
  const runs = useRuns(true);
  const step1 = useTaxonomy("step1", true);
  const step2 = useTaxonomy("step2ck", true);
  const labels = useMemo(() => {
    const m = new Map<string, string>();
    for (const n of [...(step1.data?.topics ?? []), ...(step2.data?.topics ?? [])]) m.set(n.id, n.label);
    return m;
  }, [step1.data, step2.data]);
  if (runs.isPending) return <Skeleton className="h-16 w-full rounded-block" />;
  if (runs.isError) {
    return (
      <ErrorState
        title={t("review.runs_error")}
        message={t(reviewErrorKey(runs.error))}
        requestId={requestIdOf(runs.error)}
        onRetry={() => void runs.refetch()}
        retryLabel={t("review.retry")}
      />
    );
  }
  const { active, items } = runs.data;
  if (active.length === 0 && items.length === 0) {
    return (
      <p className="m-0 text-text-3 text-ui-sm" data-testid="runs-empty">
        {t("review.runs_empty")}
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-3 border-line-soft border-t pt-5" data-testid="runs">
      <h3 className={`${LABEL} m-0`}>{t("review.runs_title")}</h3>
      <ul className="m-0 flex list-none flex-col gap-2 p-0" aria-live="polite">
        {active.slice(0, 3).map((j) => (
          <li
            key={j.id}
            data-testid="run-job"
            data-status={j.status}
            className="flex flex-col gap-1 rounded-block border border-violet-line bg-violet-ground px-4 py-3"
          >
            <span className="flex flex-wrap items-center gap-2">
              <Chip
                tone="assistant"
                icon={j.status === "running" ? <Loader2 size={12} className="animate-spin" /> : undefined}
              >
                {t(`review.run_${j.status}`)}
              </Chip>
              <span className="text-ui-sm">{t("review.run_items", { n: j.count })}</span>
            </span>
            {j.outcomes.length ? (
              <span className="text-text-2 text-ui-sm">{j.outcomes.map((o) => t(outcomeKey(o))).join(" · ")}</span>
            ) : null}
            {j.error ? (
              <span className="text-amber text-ui-sm">{t("review.run_failed_detail", { code: j.error })}</span>
            ) : null}
          </li>
        ))}
        {items.slice(0, 12).map((r) => {
          const topic = String(r.cell.topic ?? "");
          return (
            <li
              key={r.id}
              data-testid="run-row"
              data-run-id={r.id}
              className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-block border border-line-soft bg-surface-2 px-4 py-3 text-ui-sm"
            >
              <span className="max-w-full rounded-pill border border-line-chip px-2.5 py-1 font-mono text-[0.6875rem] text-text-2">
                {t(outcomeKey(r.outcome))}
              </span>
              <span className="min-w-0 flex-1 text-text-2">{labels.get(topic) ?? topic}</span>
              <span className="font-mono text-label-sm text-text-3">{t("review.run_calls", { n: r.llm_calls })}</span>
              {r.question_id ? (
                <Link
                  to="/admin/review/$itemId"
                  params={{ itemId: r.question_id }}
                  className="inline-flex min-h-11 items-center text-ui-sm"
                >
                  {t("review.run_open")}
                </Link>
              ) : null}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
