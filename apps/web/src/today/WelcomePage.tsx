import type { OnboardingState, PlanPreview } from "@naadi/shared";
import { cx, ErrorState, Skeleton } from "@naadi/ui";
import { useNavigate } from "@tanstack/react-router";
import { Check } from "lucide-react";
import { type ReactNode, useEffect, useId, useState } from "react";
import { ApiError } from "../auth/apiError";
import { useUiText } from "../copy/useUiText";
import { formatDate } from "../routes/time";
import { Wordmark } from "../shell/Logo";
import { errorCode, useCreateDiagnostic, useOnboarding, usePlanPreview, useSaveOnboarding } from "./api";
import type { T } from "./format";

const DAYS = [1, 2, 3, 4, 5, 6, 7] as const;
type Material = OnboardingState["materials"][number];

const PILL =
  "inline-flex min-h-11 items-center gap-2 rounded-pill px-4 text-ui transition-colors duration-(--duration-control) ease-out";
const PILL_ON = "border border-transparent bg-paper text-ground font-semibold";
const PILL_OFF = "border border-line-control bg-transparent text-text hover:border-text-3";
const FIELD =
  "min-h-14 w-full rounded-swatch border border-line-control bg-ground px-4 text-[1.0625rem] text-text placeholder:text-text-3";

function Toggle({
  on,
  onToggle,
  children,
  big,
}: {
  on: boolean;
  onToggle: () => void;
  children: string;
  big?: boolean;
}) {
  return (
    <button
      type="button"
      aria-pressed={on}
      onClick={onToggle}
      className={cx(PILL, on ? PILL_ON : PILL_OFF, big && "min-h-14 px-6 text-[1.0625rem]")}
    >
      {on ? <Check aria-hidden="true" size={16} /> : null}
      {children}
    </button>
  );
}

function addDays(iso: string, n: number): string {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

function daysBetween(from: string, to: string): number {
  return Math.round((Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / 86_400_000);
}

/** The device's IANA time zone, sent with the first onboarding so "today" and the plan's dates are the student's. */
function deviceTimeZone(): string | undefined {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || undefined;
  } catch {
    return undefined;
  }
}

function previewText(
  t: T,
  p: PlanPreview | undefined,
  dateOk: boolean,
  span: number,
): { big: string | null; line: string } {
  if (!dateOk) return { big: null, line: t("onboarding.preview_pending") };
  if (!p) return { big: null, line: t("onboarding.preview_loading") };
  if (!p.ok || !p.summary) {
    if (p.problem === "exam_date_not_after_start") return { big: null, line: t("onboarding.preview_past") };
    if (p.problem === "too_many_days")
      return { big: null, line: t("onboarding.preview_too_far", { days: String(span), max: String(p.max_days) }) };
    return {
      big: null,
      line: t("onboarding.preview_short", { available: String(p.available_days), needed: String(p.needed_days) }),
    };
  }
  const s = p.summary;
  return {
    big: String(s.weeks),
    line: t(s.weeks === 1 ? "onboarding.preview_unit_one" : "onboarding.preview_unit", {
      days: t(`onboarding.per_week_${s.days_per_week}`),
    }),
  };
}

function Steps({ done, diagnostic }: { done: Record<string, boolean>; diagnostic: boolean }) {
  const t = useUiText();
  const steps = ["goal", "schedule", "materials", ...(diagnostic ? ["diagnostic"] : []), "plan"];
  return (
    <ol aria-label={t("onboarding.steps_label")} className="m-0 flex list-none flex-wrap gap-2 p-0">
      {steps.map((s, i) => (
        <li
          key={s}
          data-step={s}
          data-done={done[s] || undefined}
          className={cx(
            "flex min-h-11 items-center gap-2 rounded-pill px-4 text-ui-sm",
            done[s] ? "bg-surface-2 text-text" : "border border-line-chip text-text-2",
          )}
        >
          {done[s] ? <Check aria-hidden="true" size={14} /> : <span className="font-mono">{i + 1}</span>}
          <span>{t(`onboarding.step_${s}`)}</span>
          {done[s] ? <span className="sr-only">{t("onboarding.step_done")}</span> : null}
        </li>
      ))}
    </ol>
  );
}

function Form({ state }: { state: OnboardingState }) {
  const t = useUiText();
  const nav = useNavigate();
  const ids = useId();
  const v = state.values;
  const [exam, setExam] = useState<OnboardingState["exams"][number]["id"]>(v.exam);
  const [date, setDate] = useState(v.exam_date ?? "");
  const [school, setSchool] = useState(v.medical_school ?? "");
  const [hours, setHours] = useState(v.daily_hours);
  const [days, setDays] = useState<number[]>(v.study_days);
  const [materials, setMaterials] = useState<Material[]>(v.materials);
  const [stage, setStage] = useState<"idle" | "saving" | "diagnostic">("idle");
  const save = useSaveOnboarding();
  const diag = useCreateDiagnostic();
  const minDate = addDays(state.today, 1);
  const maxDate = addDays(state.today, state.max_days);
  const dateOk = /^\d{4}-\d{2}-\d{2}$/.test(date) && date >= minDate;
  // Only a first onboarding takes the device's zone; later edits keep the one saved in Settings.
  const [timezone] = useState(() => (state.completed_at === null ? deviceTimeZone() : undefined));

  // Debounced, so dragging the hours slider asks the server once it settles.
  const [input, setInput] = useState<{
    exam: string;
    exam_date: string;
    daily_hours: number;
    study_days: number[];
    tz?: string;
  } | null>(null);
  useEffect(() => {
    const id = window.setTimeout(
      () =>
        setInput(
          dateOk && days.length ? { exam, exam_date: date, daily_hours: hours, study_days: days, tz: timezone } : null,
        ),
      200,
    );
    return () => window.clearTimeout(id);
  }, [exam, date, hours, days, dateOk, timezone]);
  const preview = usePlanPreview(input);
  const text = previewText(
    t,
    input ? preview.data : undefined,
    dateOk && days.length > 0,
    dateOk ? daysBetween(state.today, date) : 0,
  );
  const ready = dateOk && days.length > 0 && preview.data?.ok === true && !preview.isPlaceholderData;
  const done = {
    goal: dateOk,
    schedule: days.length > 0,
    materials: materials.length > 0,
    diagnostic: false,
    plan: state.completed_at !== null,
  };

  const toggleDay = (d: number) =>
    setDays((xs) => (xs.includes(d) ? xs.filter((x) => x !== d) : [...xs, d].sort((a, b) => a - b)));
  const toggleMaterial = (m: Material) =>
    setMaterials((xs) => {
      if (xs.includes(m)) return xs.filter((x) => x !== m);
      // "Nothing yet" excludes everything else, and the other way round.
      return m === "nothing" ? ["nothing"] : [...xs.filter((x) => x !== "nothing"), m];
    });

  const submit = (then: "diagnostic" | "today") => {
    setStage("saving");
    save.mutate(
      {
        exam,
        exam_date: date,
        medical_school: school.trim() || null,
        daily_hours: hours,
        study_days: days,
        materials,
        ...(timezone ? { timezone } : {}),
      },
      {
        onSuccess: () => {
          if (then === "today") {
            void nav({ to: "/", replace: true });
            return;
          }
          setStage("diagnostic");
          diag.mutate(undefined, {
            onSuccess: (d) =>
              void nav({ to: "/practice/blocks/$blockId/$n", params: { blockId: d.block_id, n: 1 }, replace: true }),
            onError: () => setStage("idle"),
          });
        },
        onError: () => setStage("idle"),
      },
    );
  };

  const saveCode = save.error ? errorCode(save.error) : null;
  const saveError = !save.error
    ? null
    : saveCode === "too_few_study_days"
      ? t("onboarding.save_too_short")
      : saveCode === "too_many_days"
        ? t("onboarding.save_too_far", { max: String(state.max_days) })
        : t("onboarding.save_failed");
  const busy = stage !== "idle";

  return (
    <div className="flex flex-col gap-10">
      <header className="flex flex-wrap items-center justify-between gap-4">
        <Wordmark className="text-[1.375rem]" size={32} />
        <Steps done={done} diagnostic={state.diagnostic.available} />
      </header>
      <h1 className="m-0 font-display font-extrabold text-[clamp(3.5rem,1.5rem+8vw,9.25rem)] leading-[0.88] tracking-[-0.05em]">
        {t("onboarding.heading")}
        <span className="text-lime">?</span>
      </h1>
      <div className="flex flex-wrap items-stretch gap-5">
        <form
          className="flex min-w-0 flex-[2_1_32rem] flex-col gap-7 rounded-tile-sm border border-line-soft bg-surface-1 p-6 tablet:rounded-tile tablet:p-9"
          onSubmit={(e) => {
            e.preventDefault();
            if (ready && !busy) submit(state.diagnostic.available ? "diagnostic" : "today");
          }}
          aria-describedby={`${ids}-preview`}
        >
          <fieldset className="m-0 flex flex-col gap-3 border-0 p-0">
            <legend className="mb-3 p-0 font-mono text-label text-text-3 uppercase">
              {t("onboarding.exam_legend")}
            </legend>
            <div className="flex flex-wrap gap-2">
              {state.exams.map((e) => (
                <Toggle key={e.id} big on={exam === e.id} onToggle={() => setExam(e.id)}>
                  {t(`exam.${e.id}`)}
                </Toggle>
              ))}
            </div>
          </fieldset>
          <div className="grid gap-5 tablet:grid-cols-2">
            <label className="flex flex-col gap-2 text-ui text-text-2">
              {t("onboarding.date_label")}
              <input
                type="date"
                required
                min={minDate}
                max={maxDate}
                value={date}
                onChange={(e) => setDate(e.target.value)}
                className={FIELD}
                aria-invalid={date !== "" && (!dateOk || date > maxDate) ? true : undefined}
              />
            </label>
            <label className="flex flex-col gap-2 text-ui text-text-2">
              <span>
                {t("onboarding.school_label")} <span className="text-text-3">{t("onboarding.optional")}</span>
              </span>
              <input
                type="text"
                maxLength={120}
                autoComplete="organization"
                value={school}
                onChange={(e) => setSchool(e.target.value)}
                className={FIELD}
              />
            </label>
          </div>
          <div className="flex flex-col gap-3 text-ui text-text-2">
            <span className="flex items-baseline justify-between gap-3">
              <label htmlFor={`${ids}-hours`} id={`${ids}-hours-label`}>
                {t("onboarding.hours_label")}
              </label>
              <output
                htmlFor={`${ids}-hours`}
                className="font-display font-bold text-[1.375rem] text-text"
                aria-live="polite"
              >
                {t("onboarding.hours_value", { h: String(hours) })}
              </output>
            </span>
            <input
              id={`${ids}-hours`}
              type="range"
              min={state.hours.min}
              max={state.hours.max}
              step={state.hours.step}
              value={hours}
              onChange={(e) => setHours(Number(e.target.value))}
              aria-valuetext={t("onboarding.hours_value", { h: String(hours) })}
              className="h-11 w-full accent-[var(--color-paper)]"
            />
          </div>
          <fieldset className="m-0 border-0 p-0">
            <legend className="mb-3 p-0 text-ui text-text-2">{t("onboarding.days_legend")}</legend>
            <div className="flex flex-wrap gap-2">
              {DAYS.map((d) => (
                <Toggle key={d} on={days.includes(d)} onToggle={() => toggleDay(d)}>
                  {t(`onboarding.day_${d}`)}
                </Toggle>
              ))}
            </div>
          </fieldset>
          <fieldset className="m-0 border-0 p-0">
            <legend className="mb-3 p-0 text-ui text-text-2">{t("onboarding.materials_legend")}</legend>
            <div className="flex flex-wrap gap-2">
              {state.materials.map((m) => (
                <Toggle key={m} on={materials.includes(m)} onToggle={() => toggleMaterial(m)}>
                  {t(`onboarding.material_${m}`)}
                </Toggle>
              ))}
            </div>
          </fieldset>
        </form>

        <aside
          aria-labelledby={`${ids}-plan`}
          className="flex min-w-0 flex-[1_1_20rem] flex-col justify-between gap-7 rounded-tile-sm bg-lime p-6 text-lime-ink tablet:rounded-tile tablet:p-9"
        >
          <h2 id={`${ids}-plan`} className="m-0 font-mono text-label uppercase tracking-[0.12em]">
            {t("onboarding.preview_label")}
          </h2>
          <div
            id={`${ids}-preview`}
            className="flex flex-col gap-1"
            aria-live="polite"
            data-preview={text.big ?? "none"}
          >
            {text.big ? (
              <span className="font-display font-extrabold text-[clamp(5rem,4rem+4vw,7.5rem)] leading-[0.85] tracking-[-0.06em]">
                {text.big}
              </span>
            ) : null}
            <span
              className={cx(
                "font-display font-bold tracking-[-0.02em]",
                text.big ? "text-[1.75rem]" : "text-[1.25rem] leading-snug",
              )}
            >
              {text.line}
            </span>
            {ready && preview.data?.summary ? (
              <span className="mt-2 text-ui">
                {t("onboarding.preview_detail", {
                  days: String(preview.data.summary.study_days),
                  first: formatDate(preview.data.summary.first_day),
                  last: formatDate(preview.data.summary.last_day),
                  assessments: String(preview.data.summary.assessments),
                })}
              </span>
            ) : null}
          </div>
          <p className="m-0 font-medium text-[1rem]">
            {state.diagnostic.available
              ? t("onboarding.next_diagnostic", { n: String(state.diagnostic.questions) })
              : t("onboarding.next_plan", { date: formatDate(state.today) })}
          </p>
          <div className="flex flex-col gap-3">
            <button
              type="button"
              disabled={!ready || busy}
              aria-busy={busy || undefined}
              onClick={() => submit(state.diagnostic.available ? "diagnostic" : "today")}
              className={cx(
                "inline-flex min-h-[3.75rem] w-full items-center justify-center rounded-pill px-7 font-semibold text-[1.0625rem]",
                "transition-colors duration-(--duration-control) ease-out",
                "bg-lime-ink text-lime hover:text-lime-hover",
                "disabled:cursor-not-allowed disabled:border disabled:border-lime-ink disabled:border-dashed disabled:bg-transparent disabled:text-lime-ink",
              )}
              data-cta="primary"
            >
              {busy
                ? t(stage === "diagnostic" ? "onboarding.starting_diagnostic" : "onboarding.saving")
                : t(state.diagnostic.available ? "onboarding.cta_diagnostic" : "onboarding.cta_start")}
            </button>
            {state.diagnostic.available ? (
              <button
                type="button"
                disabled={!ready || busy}
                onClick={() => submit("today")}
                className="min-h-12 rounded-pill border border-lime-ink/40 px-5 text-ui font-semibold text-lime-ink hover:bg-lime-hover disabled:opacity-60"
              >
                {t("onboarding.cta_later")}
              </button>
            ) : null}
            {!ready && dateOk && days.length > 0 && preview.isFetching ? (
              <span className="text-ui-sm">{t("onboarding.preview_loading")}</span>
            ) : null}
            {saveError ? (
              <p role="alert" className="m-0 text-ui font-semibold">
                {saveError}
              </p>
            ) : null}
            {diag.isError ? (
              <p role="alert" className="m-0 text-ui font-semibold">
                {t("onboarding.diagnostic_failed")}
              </p>
            ) : null}
          </div>
        </aside>
      </div>
    </div>
  );
}

export function WelcomePage() {
  const t = useUiText();
  const state = useOnboarding();
  let body: ReactNode;
  if (state.isPending) {
    body = (
      <div aria-busy="true" className="flex flex-col gap-6">
        <p role="status" className="sr-only">
          {t("onboarding.loading")}
        </p>
        <Skeleton className="h-11 w-64" />
        <Skeleton className="h-[clamp(3.5rem,10vw,9rem)] w-3/4 rounded-block" />
        <Skeleton className="h-96 rounded-tile" />
      </div>
    );
  } else if (!state.data) {
    body = (
      <div className="flex flex-col gap-8">
        <Wordmark className="text-[1.375rem]" size={32} />
        <h1 className="m-0 font-display text-headline">{t("onboarding.heading")}</h1>
        <ErrorState
          title={t("onboarding.load_failed_title")}
          message={t(
            state.error instanceof ApiError && state.error.status === null
              ? "today.error_unreachable"
              : "today.error_generic",
          )}
          requestId={state.error instanceof ApiError ? state.error.requestId : null}
          onRetry={() => void state.refetch()}
          retrying={state.isFetching}
        />
      </div>
    );
  } else {
    body = <Form state={state.data} />;
  }
  return (
    <main
      id="main"
      tabIndex={-1}
      className="mx-auto box-border flex min-h-dvh max-w-(--content-max) flex-col px-(--gutter) pt-6 pb-12"
      data-page="welcome"
    >
      {body}
    </main>
  );
}
