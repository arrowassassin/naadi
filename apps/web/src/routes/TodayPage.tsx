import type {
  CoachSuggestionOut,
  PathwayStageOut,
  PlanItemOut,
  ReadinessOut,
  SystemsOut,
  TodayOut,
} from "@naadi/shared";
import { Button, Chip, cx, ErrorState, Skeleton, Tile, TileLabel } from "@naadi/ui";
import { Link, Navigate, useNavigate } from "@tanstack/react-router";
import { ArrowRight, Check, Circle, ExternalLink } from "lucide-react";
import { useId, useState } from "react";
import { useMe } from "../auth/api";
import { ApiError } from "../auth/apiError";
import { useUiText } from "../copy/useUiText";
import { useCreateBlock, useCreateWeakSpotBlock } from "../practice/api";
import { type CachedToday, useAcceptSuggestion, useDismissSuggestion, useItemStatus, useToday } from "../today/api";
import {
  type Action,
  actionLabel,
  duration,
  FACT_KEYS,
  factValue,
  itemAction,
  itemTitle,
  primaryAction,
  suggestionText,
  type T,
} from "../today/format";
import { dayLabel, dayPart, formatDate, safeZone } from "./time";

export const CTA_LINK =
  "inline-flex min-h-14 items-center justify-center gap-2 rounded-pill bg-lime px-7 text-[1rem] font-semibold text-lime-ink no-underline transition-colors duration-(--duration-control) ease-out hover:bg-lime-hover hover:text-lime-ink";

const SECONDARY_LINK =
  "inline-flex min-h-12 items-center justify-center gap-2 rounded-pill border border-line-control px-5 text-[0.875rem] text-text no-underline transition-colors duration-(--duration-control) ease-out hover:border-text-3 hover:bg-surface-2 hover:text-text";

function errorMessage(t: T, err: unknown): string {
  if (err instanceof ApiError && err.status === null) return t("today.error_unreachable");
  return t("today.error_generic");
}

function Countdown({ days, exam, date }: { days: number; exam: string; date: string }) {
  const t = useUiText();
  const headline =
    days > 1
      ? t("today.days_left", { days: String(days) })
      : days === 1
        ? t("today.one_day_left")
        : days === 0
          ? t("today.exam_day")
          : t("today.exam_passed");
  return (
    <div className="flex flex-col gap-3">
      <p className="m-0 font-display font-extrabold text-[clamp(4.5rem,2rem+12vw,13.5rem)] leading-[0.86] tracking-[-0.055em]">
        {headline}
        <span className="text-lime">.</span>
      </p>
      <p className="m-0 text-reading text-text-2">
        {t(days >= 0 ? "today.until_exam" : "today.exam_date_passed", { exam, date: formatDate(date) })}
      </p>
    </div>
  );
}

function Loading() {
  const t = useUiText();
  return (
    <section aria-labelledby="today-h" aria-busy="true" className="flex flex-col gap-6 pt-10">
      <h1 id="today-h" className="sr-only">
        {t("today.heading")}
      </h1>
      <p role="status" className="sr-only">
        {t("today.loading")}
      </p>
      <Skeleton className="h-4 w-40" />
      <Skeleton className="h-[clamp(4rem,10vw,9rem)] w-3/4 rounded-block" />
      <div className="grid gap-4 tablet:grid-cols-2">
        <Skeleton className="h-36 rounded-tile" />
        <Skeleton className="h-36 rounded-tile" />
      </div>
    </section>
  );
}

/** Runs an action: links navigate, block actions build the block on the server first. */
function useRunAction() {
  const nav = useNavigate();
  const block = useCreateBlock();
  const weak = useCreateWeakSpotBlock();
  const [failed, setFailed] = useState(false);
  const pending = block.isPending || weak.isPending;
  const open = (id: string, n: number) => void nav({ to: "/practice/blocks/$blockId/$n", params: { blockId: id, n } });
  const run = (a: Action) => {
    setFailed(false);
    switch (a.kind) {
      case "resume_block":
        open(a.blockId, a.n);
        return;
      case "resume_reading":
        void nav({ to: "/learn/$chapterId/$sectionId", params: { chapterId: a.chapterId, sectionId: a.sectionId } });
        return;
      case "start_reading":
        void nav({ to: "/learn/$chapterId", params: { chapterId: a.chapterId } });
        return;
      case "practice":
        void nav({ to: "/practice" });
        return;
      case "cards":
        void nav({ to: "/cards" });
        return;
      case "weak_spot":
        weak.mutate(
          { exam: a.exam, count: a.count, mode: "tutor" },
          { onSuccess: (r) => open(r.block.id, r.block.next_n), onError: () => setFailed(true) },
        );
        return;
      case "block": {
        const make = (status: "unused" | "incorrect" | "all") =>
          block.mutate(
            { exam: a.exam, count: a.count, mode: "tutor", status },
            {
              onSuccess: (r) => open(r.id, r.next_n),
              // Fewer unused questions than the plan asks for: fall back to the whole pool.
              onError: (e) =>
                e instanceof ApiError && e.status === 422 && status === "unused" ? make("all") : setFailed(true),
            },
          );
        make(a.status);
        return;
      }
    }
  };
  return { run, pending, failed };
}

function PrimaryCta({ today }: { today: TodayOut }) {
  const t = useUiText();
  const action = primaryAction(today);
  const { run, pending, failed } = useRunAction();
  return (
    <div className="flex flex-col gap-3">
      <div>
        <Button
          variant="primary"
          size="cta"
          onClick={() => run(action)}
          disabled={pending}
          aria-busy={pending || undefined}
          data-action={action.kind}
          icon={<ArrowRight size={18} />}
        >
          {pending ? t("today.cta_working") : actionLabel(t, action)}
        </Button>
      </div>
      {failed ? (
        <p role="alert" className="m-0 text-ui text-amber">
          {t("today.cta_failed")}
        </p>
      ) : null}
    </div>
  );
}

function ResumeTile({ today }: { today: TodayOut }) {
  const t = useUiText();
  const r = today.resume;
  if (!r) return null;
  return (
    <Tile as="article" aria-labelledby="today-resume" className="flex flex-col gap-2">
      <TileLabel>{t("today.resume_label")}</TileLabel>
      {r.kind === "reading" ? (
        <>
          <h2 id="today-resume" className="m-0 font-display font-bold text-[1.5rem] leading-tight">
            {r.section_heading}
          </h2>
          <p className="m-0 text-text-2">
            {t("today.resume_reading", { chapter: r.chapter_title, n: String(r.section_n) })}
          </p>
        </>
      ) : (
        <>
          <h2 id="today-resume" className="m-0 font-display font-bold text-[1.5rem] leading-tight">
            {t(r.diagnostic ? "today.resume_diagnostic_title" : `today.resume_block_${r.mode}`)}
          </h2>
          <p className="m-0 text-text-2">
            {t("today.resume_block", { answered: String(r.answered), total: String(r.total) })}
          </p>
        </>
      )}
    </Tile>
  );
}

function statusChip(t: T, item: PlanItemOut, isNow: boolean) {
  if (item.status === "done")
    return (
      <Chip tone="done" icon={<Check size={12} />}>
        {t("today.item_done")}
      </Chip>
    );
  if (item.status === "skipped") return <Chip tone="planned">{t("today.item_skipped")}</Chip>;
  // Outlined, not lime: the view's one lime is the primary action button (CLAUDE.md colour roles).
  if (isNow) return <Chip tone="current">{t("today.item_now")}</Chip>;
  return <Chip tone="planned">{t("today.item_planned")}</Chip>;
}

function FlowItem({ item, today, isNow }: { item: PlanItemOut; today: TodayOut; isNow: boolean }) {
  const t = useUiText();
  const status = useItemStatus();
  const action = itemAction(item, today);
  const { run, pending } = useRunAction();
  const done = item.status === "done";
  const title = itemTitle(t, item);
  const outside = item.ref_type === "external";
  return (
    <li
      className={cx(
        "flex flex-col gap-3 border-line-soft border-b py-4 last:border-b-0 tablet:flex-row tablet:items-start tablet:gap-4",
        isNow && "rounded-block-sm bg-surface-2 px-4 tablet:-mx-4",
      )}
      data-item-kind={item.kind}
      data-now={isNow || undefined}
    >
      <button
        type="button"
        onClick={() => status.mutate({ id: item.id, status: done ? "planned" : "done" })}
        aria-pressed={done}
        aria-label={t(done ? "today.mark_undone" : "today.mark_done", { title })}
        className="inline-flex size-11 shrink-0 items-center justify-center rounded-pill border border-line-control text-text hover:bg-surface-2"
      >
        {done ? (
          <Check aria-hidden="true" size={18} />
        ) : (
          <Circle aria-hidden="true" size={18} className="text-text-3" />
        )}
      </button>
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-label-sm text-text-3 uppercase">{t(`today.kind_${item.kind}`)}</span>
          {statusChip(t, item, isNow)}
          {outside ? (
            <Chip tone="meta" icon={<ExternalLink size={11} />}>
              {t("today.item_outside")}
            </Chip>
          ) : null}
          {item.origin === "assistant" ? <Chip tone="assistant">{t("today.item_from_coach")}</Chip> : null}
        </div>
        <p className={cx("m-0 break-words text-ui text-text", done && "text-text-3 line-through")}>{title}</p>
        <p className="m-0 flex flex-wrap gap-x-3 text-ui-sm text-text-3">
          {item.target_minutes ? <span>{duration(t, item.target_minutes)}</span> : null}
          {item.target_count && item.kind === "questions" ? (
            <span>{t("today.item_count", { n: String(item.target_count) })}</span>
          ) : null}
        </p>
      </div>
      {action && !done && !isNow ? (
        <Button variant="secondary" onClick={() => run(action)} disabled={pending} className="self-start">
          {actionLabel(t, action)}
        </Button>
      ) : null}
    </li>
  );
}

function Flow({ today }: { today: TodayOut }) {
  const t = useUiText();
  const plan = today.plan;
  if (!plan) return null;
  const items = plan.items;
  return (
    <Tile as="section" aria-labelledby="today-flow" className="flex flex-col gap-2">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="today-flow" className="m-0 font-display font-bold text-[1.5rem]">
          {t("today.flow_label")}
        </h2>
        {plan.day?.target_hours ? (
          <span className="font-mono text-label text-text-3 uppercase">
            {duration(t, Math.round(plan.day.target_hours * 60))}
          </span>
        ) : null}
      </div>
      {items.length === 0 ? (
        <p className="m-0 text-text-2">
          {plan.next_day ? t("today.flow_empty_next", { date: formatDate(plan.next_day) }) : t("today.flow_empty")}
        </p>
      ) : (
        <ol className="m-0 list-none p-0">
          {items.map((i) => (
            <FlowItem key={i.id} item={i} today={today} isNow={i.id === plan.now_item} />
          ))}
        </ol>
      )}
    </Tile>
  );
}

function Ring({ score }: { score: number }) {
  const r = 52;
  const c = 2 * Math.PI * r;
  return (
    <svg viewBox="0 0 120 120" className="size-32 shrink-0" aria-hidden="true">
      <circle cx="60" cy="60" r={r} fill="none" stroke="var(--color-line)" strokeWidth="10" />
      <circle
        cx="60"
        cy="60"
        r={r}
        fill="none"
        stroke="var(--color-paper)"
        strokeWidth="10"
        strokeLinecap="round"
        strokeDasharray={`${(c * score) / 100} ${c}`}
        transform="rotate(-90 60 60)"
      />
      <text
        x="60"
        y="60"
        textAnchor="middle"
        dominantBaseline="central"
        fill="var(--color-text)"
        fontFamily="var(--font-display)"
        fontWeight="800"
        fontSize="34"
      >
        {score}
      </text>
    </svg>
  );
}

function Readiness({ r }: { r: ReadinessOut }) {
  const t = useUiText();
  return (
    <Tile as="section" aria-labelledby="today-ready" className="flex flex-col gap-4">
      <h2 id="today-ready" className="m-0">
        <TileLabel>{t("today.readiness_label")}</TileLabel>
      </h2>
      {r.score !== null ? (
        <div className="flex flex-wrap items-center gap-5">
          <div role="img" aria-label={t("today.readiness_aria", { score: String(r.score) })}>
            <Ring score={r.score} />
          </div>
          <p className="m-0 min-w-0 flex-1 basis-48 text-text-2">
            {t("today.readiness_caption", { coverage: String(r.coverage), days: String(r.days) })}
          </p>
        </div>
      ) : (
        <p className="m-0 text-text-2" data-readiness="pending">
          {t("today.readiness_pending", {
            needed: String(r.needed_answered),
            needed_coverage: String(r.needed_coverage),
            answered: String(r.answered),
            coverage: String(r.coverage),
          })}
        </p>
      )}
    </Tile>
  );
}

function Systems({ s }: { s: SystemsOut }) {
  const t = useUiText();
  return (
    <Tile as="section" aria-labelledby="today-systems" className="flex flex-col gap-4">
      <div className="flex flex-col gap-1">
        <h2 id="today-systems" className="m-0 font-display font-bold text-[1.5rem]">
          {t("today.systems_label")}
        </h2>
        <p className="m-0 text-ui-sm text-text-3">{t("today.systems_caption", { days: String(s.window_days) })}</p>
      </div>
      <ul className="m-0 grid list-none gap-x-8 gap-y-4 p-0 laptop:grid-cols-2">
        {s.items.map((x) => (
          <li key={x.id} className="flex flex-col gap-2" data-system={x.id} data-state={x.state}>
            <div className="flex items-baseline justify-between gap-3">
              <span className="min-w-0 break-words text-ui text-text">{x.label}</span>
              <span className="shrink-0 text-ui-sm">
                {x.percent === null ? (
                  <span className="text-text-3">{t("today.system_insufficient")}</span>
                ) : (
                  <span className={cx("font-semibold", x.state === "weak" ? "text-amber" : "text-text")}>
                    {t("today.system_percent", { pct: String(x.percent) })}
                    {x.state === "weak" ? <span className="ml-2 font-normal">{t("today.system_weak")}</span> : null}
                  </span>
                )}
              </span>
            </div>
            <div
              className={cx(
                "h-2 w-full overflow-hidden rounded-pill",
                x.percent === null ? "border border-dashed border-line-dashed" : "bg-surface-2",
              )}
              aria-hidden="true"
            >
              {x.percent !== null ? (
                <div
                  className={cx("h-full rounded-pill", x.state === "weak" ? "bg-amber" : "bg-text-2")}
                  style={{ width: `${x.percent}%` }}
                />
              ) : null}
            </div>
            <span className="sr-only">
              {t("today.system_attempts", { correct: String(x.correct), attempts: String(x.attempts) })}
            </span>
          </li>
        ))}
      </ul>
    </Tile>
  );
}

function Cards({ today }: { today: TodayOut }) {
  const t = useUiText();
  const c = today.cards;
  return (
    <Tile as="section" aria-labelledby="today-cards" className="flex flex-col gap-3">
      <h2 id="today-cards" className="m-0">
        <TileLabel>{t("today.cards_label")}</TileLabel>
      </h2>
      <p className="m-0 font-display font-extrabold text-[3.5rem] leading-none tracking-[-0.04em]">{c.due}</p>
      <p className="m-0 text-text-2">{t(c.due > 0 ? "today.cards_caption" : "today.cards_none")}</p>
      {c.reviewable && c.due > 0 ? (
        <Link to="/cards" className={cx(SECONDARY_LINK, "self-start")}>
          {t("today.cards_review")}
        </Link>
      ) : null}
    </Tile>
  );
}

function Suggestion({ s, assistant }: { s: CoachSuggestionOut; assistant: boolean }) {
  const t = useUiText();
  const accept = useAcceptSuggestion();
  const dismiss = useDismissSuggestion();
  const [why, setWhy] = useState(false);
  const whyId = useId();
  const { title, body } = suggestionText(t, s);
  const facts = FACT_KEYS.filter((k) => s.facts[k] !== undefined && s.facts[k] !== null);
  const busy = accept.isPending || dismiss.isPending;
  return (
    <Tile variant="assistant" as="article" aria-labelledby={`${whyId}-h`} className="flex flex-col gap-4">
      <TileLabel variant="assistant">{t("today.coach_label")}</TileLabel>
      <h3 id={`${whyId}-h`} className="m-0 font-display font-bold text-[1.375rem] text-text leading-tight">
        {title}
      </h3>
      <p className="m-0 text-text-2">{body}</p>
      {why ? (
        <dl id={whyId} className="m-0 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-ui-sm">
          {facts.map((k) => (
            <div key={k} className="contents">
              <dt className="text-violet-text">{t(`today.fact_${k}`)}</dt>
              <dd className="m-0 break-words text-text">{factValue(t, k, s.facts[k])}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      <div className="flex flex-wrap gap-2">
        <Button variant="assistant" onClick={() => accept.mutate(s.id)} disabled={busy}>
          {t("today.coach_add")}
        </Button>
        <Button variant="secondary" onClick={() => setWhy((v) => !v)} aria-expanded={why} aria-controls={whyId}>
          {t(why ? "today.coach_hide_why" : "today.coach_why")}
        </Button>
        {assistant ? (
          <Button
            variant="secondary"
            onClick={() =>
              window.dispatchEvent(
                new CustomEvent("naadi:ask", { detail: { kind: "coach", id: s.id, facts: s.facts } }),
              )
            }
          >
            {t("today.coach_ask")}
          </Button>
        ) : null}
        <Button variant="secondary" onClick={() => dismiss.mutate(s.id)} disabled={busy}>
          {t("today.coach_dismiss")}
        </Button>
      </div>
      {accept.isError || dismiss.isError ? (
        <p role="alert" className="m-0 text-ui text-amber">
          {t("today.coach_failed")}
        </p>
      ) : null}
    </Tile>
  );
}

function Pathway({ stages }: { stages: PathwayStageOut[] }) {
  const t = useUiText();
  if (stages.length === 0) return null;
  return (
    <Tile as="section" aria-labelledby="today-pathway" className="flex flex-col gap-4">
      <h2 id="today-pathway" className="m-0">
        <TileLabel>{t("today.pathway_label")}</TileLabel>
      </h2>
      <ol className="m-0 flex list-none flex-wrap gap-2 p-0">
        {stages.map((s, i) => (
          <li key={s.key} className="flex items-center gap-2 rounded-pill border border-line-chip px-3 py-2 text-ui-sm">
            <span className="font-mono text-text-3">{i + 1}</span>
            <span className="text-text">{s.title ?? s.key}</span>
            <span className="text-text-3">
              {t("today.pathway_stage", { done: String(s.done), total: String(s.total) })}
            </span>
          </li>
        ))}
      </ol>
    </Tile>
  );
}

function focusLine(t: T, today: TodayOut): string | null {
  const plan = today.plan;
  if (!plan) return null;
  if (plan.day?.topic && plan.items.some((i) => i.kind === "rest")) {
    return t("today.focus_rest", { topic: plan.day.topic });
  }
  if (plan.day?.topic) {
    const open = plan.items.filter((i) => i.status !== "done" && i.status !== "skipped").length;
    if (plan.items.length > 0 && open === 0) return t("today.focus_done");
    return t("today.focus_topic", { topic: plan.day.topic });
  }
  if (plan.items.length > 0) return null;
  return plan.next_day ? t("today.focus_off", { date: formatDate(plan.next_day) }) : null;
}

function Populated({ cached }: { cached: CachedToday }) {
  const t = useUiText();
  const me = useMe();
  const now = new Date();
  const today = cached.data;
  const tz = safeZone(me.data?.profile.timezone);
  const exam = t(`exam.${today.exam}`);
  const name = me.data?.profile.display_name ?? null;
  const part = dayPart(now, tz);
  const hello = name ? t(`today.greeting_${part}_name`, { name }) : t(`today.greeting_${part}`);
  const focus = focusLine(t, today);
  const assistant = me.data?.features.assistant === true;
  return (
    <section
      aria-labelledby="today-h"
      className="flex flex-col gap-8 pt-6 tablet:pt-12"
      data-state="populated"
      data-from-cache={cached.fromCache || undefined}
    >
      <div className="flex flex-col gap-5">
        <span className="font-mono text-label text-text-3 uppercase">
          {today.exam_date
            ? t("today.date_line_exam", { day: dayLabel(now, tz), exam, date: formatDate(today.exam_date) })
            : t("today.date_line", { day: dayLabel(now, tz), exam })}
        </span>
        <h1 id="today-h" className="m-0 font-display text-lead text-text">
          {hello}.
        </h1>
        {today.exam_date && today.days_left !== null ? (
          <Countdown days={today.days_left} exam={exam} date={today.exam_date} />
        ) : null}
        {focus ? <p className="m-0 max-w-[48rem] text-lead text-text-2">{focus}</p> : null}
        {cached.fromCache ? <p className="m-0 text-ui text-text-3">{t("today.offline_note")}</p> : null}
        <PrimaryCta today={today} />
      </div>

      <div className="grid gap-4 laptop:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <div className="flex min-w-0 flex-col gap-4">
          <ResumeTile today={today} />
          <Flow today={today} />
        </div>
        <div className="flex min-w-0 flex-col gap-4">
          <Readiness r={today.readiness} />
          <Cards today={today} />
          {today.coach.length > 0 ? (
            <section aria-labelledby="today-coach" className="flex flex-col gap-4">
              <h2 id="today-coach" className="sr-only">
                {t("today.coach_label")}
              </h2>
              {today.coach.map((s) => (
                <Suggestion key={s.id} s={s} assistant={assistant} />
              ))}
            </section>
          ) : null}
        </div>
      </div>

      <Systems s={today.systems} />
      {today.pathway ? <Pathway stages={today.pathway} /> : null}
    </section>
  );
}

export function TodayPage() {
  const t = useUiText();
  const today = useToday();

  if (today.isPending) return <Loading />;
  if (!today.data) {
    return (
      <section aria-labelledby="today-h" className="flex flex-col gap-6 pt-10">
        <h1 id="today-h" className="m-0 font-display text-headline">
          {t("today.heading")}
        </h1>
        <ErrorState
          title={t("today.today_failed_title")}
          message={errorMessage(t, today.error)}
          requestId={today.error instanceof ApiError ? today.error.requestId : null}
          onRetry={() => void today.refetch()}
          retrying={today.isFetching}
        />
      </section>
    );
  }
  if (!today.data.data.onboarding_complete) {
    // Only a fresh answer decides: a cached copy from before onboarding must not bounce the student back.
    return today.isFetching || today.data.fromCache ? <Loading /> : <Navigate to="/welcome" replace />;
  }
  return <Populated cached={today.data} />;
}
