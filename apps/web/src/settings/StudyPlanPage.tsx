import type { OnboardingState } from "@naadi/shared";
import { Button, ErrorState, Skeleton, Tile } from "@naadi/ui";
import { Link } from "@tanstack/react-router";
import { Check } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { ApiError } from "../auth/apiError";
import { useUiText } from "../copy/useUiText";
import { useOnlineStatus } from "../health/useOnlineStatus";
import { formatDate } from "../routes/time";
import { errorCode, useOnboarding, usePlanPreview, useSaveOnboarding } from "../today/api";
import { SettingsPage } from "./SettingsPage";
import { FIELD, Toggle } from "./ui";

const DAYS = [1, 2, 3, 4, 5, 6, 7] as const;

function addDays(iso: string, n: number): string {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

function spanDays(from: string, to: string): number {
  return Math.round((Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / 86_400_000);
}

const sameDays = (a: readonly number[], b: readonly number[]) => a.length === b.length && a.every((d, i) => d === b[i]);

function PlanForm({ state }: { state: OnboardingState }) {
  const t = useUiText();
  const online = useOnlineStatus();
  const ids = useId();
  const v = state.values;
  const [exam, setExam] = useState<OnboardingState["exams"][number]["id"]>(v.exam);
  const [date, setDate] = useState(v.exam_date ?? "");
  const [hours, setHours] = useState(v.daily_hours);
  const [days, setDays] = useState<number[]>(v.study_days);
  const [saved, setSaved] = useState(false);
  const save = useSaveOnboarding();
  const minDate = addDays(state.today, 1);
  const maxDate = addDays(state.today, state.max_days);
  const dateOk = /^\d{4}-\d{2}-\d{2}$/.test(date) && date >= minDate && date <= maxDate;
  const dirty =
    exam !== v.exam || date !== (v.exam_date ?? "") || hours !== v.daily_hours || !sameDays(days, v.study_days);

  // Debounced, so dragging the hours slider asks the server once it settles.
  const [input, setInput] = useState<{
    exam: string;
    exam_date: string;
    daily_hours: number;
    study_days: number[];
  } | null>(null);
  useEffect(() => {
    const id = window.setTimeout(
      () => setInput(dateOk && days.length ? { exam, exam_date: date, daily_hours: hours, study_days: days } : null),
      200,
    );
    return () => window.clearTimeout(id);
  }, [exam, date, hours, days, dateOk]);
  const preview = usePlanPreview(input);
  const ready = dateOk && days.length > 0 && preview.data?.ok === true && !preview.isPlaceholderData;

  const toggleDay = (d: number) => {
    setSaved(false);
    setDays((xs) => (xs.includes(d) ? xs.filter((x) => x !== d) : [...xs, d].sort((a, b) => a - b)));
  };
  const submit = () => {
    if (!dirty || !ready) return;
    setSaved(false);
    save.mutate(
      {
        exam,
        exam_date: date,
        daily_hours: hours,
        study_days: days,
        materials: state.values.materials,
        medical_school: state.values.medical_school ?? null,
      },
      { onSuccess: () => setSaved(true) },
    );
  };

  const code = save.error ? errorCode(save.error) : null;
  const saveError = !save.error
    ? null
    : code === "too_few_study_days"
      ? t("onboarding.save_too_short")
      : code === "too_many_days"
        ? t("onboarding.save_too_far", { max: String(state.max_days) })
        : t("onboarding.save_failed");

  let line: string;
  if (!dateOk) line = t("settings.plan_pick_date");
  else if (days.length === 0) line = t("settings.plan_pick_days");
  else if (!preview.data || preview.isPlaceholderData) line = t("onboarding.preview_loading");
  else if (!preview.data.ok || !preview.data.summary) {
    const p = preview.data;
    line =
      p.problem === "too_many_days"
        ? t("onboarding.preview_too_far", { days: String(spanDays(state.today, date)), max: String(p.max_days) })
        : p.problem === "exam_date_not_after_start"
          ? t("onboarding.preview_past")
          : t("onboarding.preview_short", { available: String(p.available_days), needed: String(p.needed_days) });
  } else {
    const s = preview.data.summary;
    line = t("settings.plan_summary", {
      weeks: String(s.weeks),
      days: String(s.study_days),
      first: formatDate(s.first_day),
      last: formatDate(s.last_day),
    });
  }

  return (
    <form
      noValidate
      className="grid items-start gap-5 laptop:grid-cols-[2fr_1fr]"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
      aria-busy={save.isPending}
    >
      <Tile as="section" aria-labelledby={`${ids}-h`} className="flex flex-col gap-7">
        <h2 id={`${ids}-h`} className="m-0 font-display font-bold text-[1.5rem]">
          {t("settings.plan_heading")}
        </h2>
        <p className="m-0 text-reading text-text-2">{t("settings.plan_intro")}</p>
        <fieldset className="m-0 flex flex-col gap-3 border-0 p-0">
          <legend className="mb-3 p-0 text-ui text-text-2">{t("onboarding.exam_legend")}</legend>
          <div className="flex flex-wrap gap-2">
            {state.exams.map((e) => (
              <Toggle
                key={e.id}
                on={exam === e.id}
                onToggle={() => {
                  setExam(e.id);
                  setSaved(false);
                }}
              >
                {t(`exam.${e.id}`)}
              </Toggle>
            ))}
          </div>
        </fieldset>
        <label className="flex max-w-sm flex-col gap-2 text-ui text-text-2">
          {t("onboarding.date_label")}
          <input
            type="date"
            required
            min={minDate}
            max={maxDate}
            value={date}
            onChange={(e) => {
              setDate(e.target.value);
              setSaved(false);
            }}
            className={FIELD}
            aria-invalid={date !== "" && !dateOk ? true : undefined}
          />
        </label>
        <div className="flex max-w-xl flex-col gap-3 text-ui text-text-2">
          <span className="flex items-baseline justify-between gap-3">
            <label htmlFor={`${ids}-hours`}>{t("onboarding.hours_label")}</label>
            <output htmlFor={`${ids}-hours`} className="font-display font-bold text-[1.375rem] text-text">
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
            onChange={(e) => {
              setHours(Number(e.target.value));
              setSaved(false);
            }}
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
      </Tile>
      <Tile as="aside" aria-labelledby={`${ids}-s`} className="flex flex-col gap-5">
        <h2 id={`${ids}-s`} className="m-0 font-display font-bold text-[1.5rem]">
          {t("settings.plan_summary_heading")}
        </h2>
        <p className="m-0 text-reading text-text-2" aria-live="polite" data-plan-summary="">
          {line}
        </p>
        {saveError ? (
          <p role="alert" className="m-0 font-semibold text-amber">
            {saveError}
          </p>
        ) : null}
        <div className="flex flex-wrap items-center gap-4">
          <Button type="submit" variant="primary" size="cta" disabled={!dirty || !ready || save.isPending || !online}>
            {t(save.isPending ? "account.saving" : "account.save")}
          </Button>
          {saved && !dirty ? (
            <span role="status" className="inline-flex items-center gap-2 text-text-2">
              <Check aria-hidden="true" size={18} /> {t("settings.plan_saved")}
            </span>
          ) : null}
        </div>
        {!online ? <p className="m-0 text-text-3">{t("account.offline_save")}</p> : null}
      </Tile>
    </form>
  );
}

/** Settings -> Study plan: the answers behind the plan, saved through the onboarding API (which rebuilds the plan). */
export function StudyPlanPage() {
  const t = useUiText();
  const state = useOnboarding();
  return (
    <SettingsPage group="plan" titleKey="settings.plan_title" headingId="plan-settings-h">
      {state.isPending ? (
        <div aria-busy="true" className="flex flex-col gap-4">
          <p role="status" className="sr-only">
            {t("settings.plan_loading")}
          </p>
          <Skeleton className="h-96 rounded-tile" />
        </div>
      ) : !state.data ? (
        <ErrorState
          title={t("settings.plan_load_failed_title")}
          message={t(
            state.error instanceof ApiError && state.error.status === null
              ? "today.error_unreachable"
              : "today.error_generic",
          )}
          requestId={state.error instanceof ApiError ? state.error.requestId : null}
          retryLabel={t("settings.retry")}
          onRetry={() => void state.refetch()}
          retrying={state.isFetching}
        />
      ) : state.data.completed_at === null ? (
        <Tile as="section" className="flex flex-col items-start gap-4" data-plan-not-set="">
          <p className="m-0 text-reading text-text-2">{t("settings.plan_not_set")}</p>
          <Link
            to="/welcome"
            className="inline-flex min-h-14 items-center rounded-pill bg-lime px-7 font-semibold text-lime-ink no-underline hover:bg-lime-hover hover:text-lime-ink"
          >
            {t("settings.plan_setup")}
          </Link>
        </Tile>
      ) : (
        <PlanForm state={state.data} />
      )}
    </SettingsPage>
  );
}
