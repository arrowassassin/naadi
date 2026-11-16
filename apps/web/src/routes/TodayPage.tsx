import { ErrorState, Skeleton, Tile, TileLabel } from "@naadi/ui";
import { Link } from "@tanstack/react-router";
import { ArrowRight } from "lucide-react";
import { useMe } from "../auth/api";
import { ApiError } from "../auth/apiError";
import { useUiText } from "../copy/useUiText";
import { dayLabel, dayPart, daysUntil, formatDate, safeZone } from "./time";

export const CTA_LINK =
  "inline-flex min-h-14 items-center justify-center gap-2 rounded-pill bg-lime px-7 text-[1rem] font-semibold text-lime-ink no-underline transition-colors duration-(--duration-control) ease-out hover:bg-lime-hover hover:text-lime-ink";

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

export function TodayPage() {
  const t = useUiText();
  const me = useMe();
  const now = new Date();

  if (me.isPending && !me.data) {
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

  if (!me.data) {
    const err = me.error;
    return (
      <section aria-labelledby="today-h" className="flex flex-col gap-6 pt-10">
        <h1 id="today-h" className="m-0 font-display text-headline">
          {t("today.heading")}
        </h1>
        <ErrorState
          title={t("today.load_failed_title")}
          message={err?.message ?? t("common.error_fallback")}
          requestId={err instanceof ApiError ? err.requestId : null}
          onRetry={() => void me.refetch()}
          retrying={me.isFetching}
        />
      </section>
    );
  }

  const { profile } = me.data;
  const tz = safeZone(profile.timezone);
  const exam = t(`exam.${profile.exam_target}`);
  const name = profile.display_name;
  const part = dayPart(now, tz);
  const hello = name ? t(`today.greeting_${part}_name`, { name }) : t(`today.greeting_${part}`);

  return (
    <section aria-labelledby="today-h" className="flex flex-col gap-8 pt-6 tablet:pt-12" data-state="populated">
      <div className="flex flex-col gap-5">
        <span className="font-mono text-label text-text-3 uppercase">
          {t("today.date_line", { day: dayLabel(now, tz), exam })}
        </span>
        {profile.exam_date ? (
          <>
            <h1 id="today-h" className="m-0 font-display text-lead text-text">
              {hello}.
            </h1>
            <Countdown days={daysUntil(profile.exam_date, now, tz)} exam={exam} date={profile.exam_date} />
          </>
        ) : (
          <h1
            id="today-h"
            className="m-0 font-display font-extrabold text-[clamp(3.5rem,1.5rem+8vw,9rem)] leading-[0.9] tracking-[-0.05em]"
          >
            {hello}
            <span className="text-lime">.</span>
          </h1>
        )}
      </div>

      {!name ? (
        <div>
          <Link to="/settings/account" className={CTA_LINK}>
            {t("today.add_name")}
            <ArrowRight aria-hidden="true" size={18} />
          </Link>
        </div>
      ) : null}

      <div className="grid gap-4 tablet:grid-cols-2">
        <Tile as="article" aria-labelledby="today-exam" className="flex flex-col gap-3">
          <TileLabel>{t("today.exam_label")}</TileLabel>
          <h2 id="today-exam" className="m-0 font-display font-bold text-[1.75rem] leading-tight">
            {t("today.exam_name", { exam })}
          </h2>
          <p className="m-0 text-text-2">
            {profile.exam_date
              ? t("today.exam_date", { date: formatDate(profile.exam_date) })
              : t("today.no_exam_date")}
          </p>
        </Tile>
        <Tile as="article" aria-labelledby="today-tz" className="flex flex-col gap-3">
          <TileLabel>{t("today.tz_label")}</TileLabel>
          <h2 id="today-tz" className="m-0 font-display font-bold text-[1.75rem] leading-tight break-words">
            {profile.timezone.replaceAll("_", " ")}
          </h2>
          <p className="m-0 text-text-2">
            {t("today.change_tz_before")}{" "}
            <Link to="/settings/account" className="text-text underline underline-offset-4 hover:text-text">
              {t("today.change_tz_link")}
            </Link>
            {t("today.change_tz_after")}
          </p>
        </Tile>
      </div>
    </section>
  );
}
