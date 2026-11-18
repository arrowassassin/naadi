import type { RebalanceOut, WeekItemOut, WeekOut } from "@naadi/shared";
import { Button, Chip, ErrorState, Skeleton, Tile, TileLabel } from "@naadi/ui";
import { Link } from "@tanstack/react-router";
import { ArrowRight, CalendarSync, ChevronLeft, ChevronRight, RefreshCw } from "lucide-react";
import { useState, useSyncExternalStore } from "react";
import { useMe } from "../auth/api";
import { ApiError } from "../auth/apiError";
import { useUiText } from "../copy/useUiText";
import { useOnlineStatus } from "../health/useOnlineStatus";
import { useUserId } from "../practice/api";
import { duration, itemTitle } from "../today/format";
import { useRunAction } from "../today/useRunAction";
import { useDecideRebalance, useFlushPlanOutbox, usePlanWeek, useSetItemStatus, useStartRebalance } from "./api";
import { examName, hours, range } from "./format";
import { ItemSheet } from "./ItemSheet";
import { itemLink } from "./itemLink";
import { Milestones } from "./Milestones";
import { usePendingIds } from "./outbox";
import { RebalanceCard } from "./RebalanceCard";
import { DayList, WeekGrid, WeekStrip } from "./WeekViews";

const LAPTOP = "(min-width: 68.75rem)"; // the `laptop` breakpoint of tokens.css

function useIsLaptop(): boolean {
  return useSyncExternalStore(
    (l) => {
      const m = window.matchMedia(LAPTOP);
      m.addEventListener("change", l);
      return () => m.removeEventListener("change", l);
    },
    () => window.matchMedia(LAPTOP).matches,
    () => false,
  );
}

type Notice =
  | { kind: "applied"; moved: number; dropped: number }
  | { kind: "declined" | "stale" | "nothing" | "failed" | "decide_failed" };

function Loading() {
  const t = useUiText();
  return (
    <section aria-labelledby="plan-h" aria-busy="true" className="flex flex-col gap-6 pt-10">
      <h1 id="plan-h" className="sr-only">
        {t("planner.heading")}
      </h1>
      <p role="status" className="sr-only">
        {t("planner.loading")}
      </p>
      <Skeleton className="h-4 w-40" />
      <Skeleton className="h-24 rounded-tile" />
      <Skeleton className="h-96 rounded-tile" />
    </section>
  );
}

function Empty() {
  const t = useUiText();
  return (
    <section aria-labelledby="plan-h" className="flex flex-col gap-6 pt-10">
      <h1 id="plan-h" className="m-0 font-display font-bold text-headline">
        {t("planner.heading")}
      </h1>
      <Tile as="article" className="flex flex-col gap-3">
        <h2 className="m-0 font-display font-bold text-[1.5rem] leading-tight">{t("planner.empty_title")}</h2>
        <p className="m-0 text-text-2">{t("planner.empty_body")}</p>
        <div>
          <Link
            to="/welcome"
            className="inline-flex min-h-12 items-center rounded-pill border border-line-control px-6 font-semibold text-text no-underline hover:bg-surface-2"
          >
            {t("planner.empty_cta")}
          </Link>
        </div>
      </Tile>
    </section>
  );
}

/** The screen's one lime action: today's next item. */
function NextUp({
  week,
  onOpen,
  linkCtx,
}: {
  week: WeekOut;
  onOpen: (i: WeekItemOut) => void;
  linkCtx: { exam: string; cardsOn: boolean };
}) {
  const t = useUiText();
  const { run, pending, failed } = useRunAction();
  const today = week.days.find((d) => d.is_today);
  if (!today) return null;
  const item = week.next_item;
  if (!item) {
    return (
      <p className="m-0 text-ui text-text-2" data-testid="cta-none">
        {t(today.items.length > 0 ? "planner.cta_all_done" : "planner.cta_none")}
      </p>
    );
  }
  const action = itemLink(item, linkCtx);
  return (
    <Tile as="section" aria-labelledby="next-up-h" className="flex flex-col gap-4 tablet:flex-row tablet:items-center">
      <div className="flex min-w-0 flex-1 flex-col gap-1.5">
        <TileLabel>
          <span id="next-up-h">{t("planner.cta_label")}</span>
        </TileLabel>
        <p className="m-0 break-words font-display font-bold text-[1.375rem] leading-tight">{itemTitle(t, item)}</p>
        {item.target_minutes ? <p className="m-0 text-ui text-text-3">{duration(t, item.target_minutes)}</p> : null}
        {failed ? (
          <p role="alert" className="m-0 text-ui text-amber">
            {t("today.cta_failed")}
          </p>
        ) : null}
      </div>
      <div>
        <Button
          variant="primary"
          size="cta"
          disabled={pending}
          data-testid="cta-start"
          icon={<ArrowRight size={18} />}
          onClick={() => (action ? run(action) : onOpen(item))}
        >
          {t("planner.cta_start")}
        </Button>
      </div>
    </Tile>
  );
}

function Header({
  week,
  online,
  onPrev,
  onNext,
  onToday,
  busy,
  onRebalance,
}: {
  week: WeekOut;
  online: boolean;
  onPrev: (() => void) | null;
  onNext: (() => void) | null;
  onToday: (() => void) | null;
  busy: boolean;
  onRebalance: () => void;
}) {
  const t = useUiText();
  const days = week.days_to_exam;
  return (
    <header className="flex flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex min-w-0 flex-col gap-1">
          <TileLabel>{t("planner.week_of", { week: String(week.week), weeks: String(week.weeks) })}</TileLabel>
          <h1 id="plan-h" className="m-0 font-display font-bold text-headline">
            {t("planner.heading")}
          </h1>
          <p className="m-0 text-ui text-text-2">
            {range(t, week.start, week.end)}
            {days !== null && days >= 0 ? (
              <>
                {" · "}
                {days === 1
                  ? t("planner.exam_countdown_one", { exam: examName(week.exam) })
                  : t("planner.exam_countdown", { exam: examName(week.exam), days: String(days) })}
              </>
            ) : null}
          </p>
          <p className="m-0 text-ui-sm text-text-3">
            {t("planner.hours_planned", { planned: hours(week.hours_planned), target: hours(week.hours_target) })}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="secondary" disabled={!onPrev} onClick={() => onPrev?.()} aria-label={t("planner.prev_week")}>
            <ChevronLeft aria-hidden="true" size={18} />
          </Button>
          {onToday ? (
            <Button variant="secondary" onClick={onToday}>
              {t("planner.this_week")}
            </Button>
          ) : null}
          <Button variant="secondary" disabled={!onNext} onClick={() => onNext?.()} aria-label={t("planner.next_week")}>
            <ChevronRight aria-hidden="true" size={18} />
          </Button>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <Button
          variant="secondary"
          disabled={!online || busy}
          aria-busy={busy || undefined}
          icon={<RefreshCw aria-hidden="true" size={16} />}
          onClick={onRebalance}
        >
          {busy ? t("planner.rebalance_working") : t("planner.rebalance")}
        </Button>
        <Link
          to="/settings/account"
          className="inline-flex min-h-12 items-center gap-2 rounded-pill border border-line-control px-5 font-semibold text-text no-underline hover:bg-surface-2"
        >
          <CalendarSync aria-hidden="true" size={16} />
          {t("planner.sync_calendar")}
        </Link>
        {!online ? <span className="text-ui-sm text-text-3">{t("planner.rebalance_offline")}</span> : null}
        {week.behind > 0 && !week.rebalance ? (
          <Chip tone="warning">{t("planner.cta_behind_first", { n: String(week.behind) })}</Chip>
        ) : null}
      </div>
    </header>
  );
}

function noticeText(t: ReturnType<typeof useUiText>, n: Notice): string {
  switch (n.kind) {
    case "applied":
      return t("planner.applied", { moved: String(n.moved), dropped: String(n.dropped) });
    case "declined":
      return t("planner.declined");
    case "stale":
      return t("planner.stale");
    case "nothing":
      return t("planner.rebalance_nothing");
    case "failed":
      return t("planner.rebalance_failed");
    case "decide_failed":
      return t("planner.decide_failed");
  }
}

export function PlannerPage() {
  const t = useUiText();
  const online = useOnlineStatus();
  const userId = useUserId();
  const me = useMe();
  const [start, setStart] = useState<string | undefined>(undefined);
  const [picked, setPicked] = useState<string | null>(null);
  const [open, setOpen] = useState<WeekItemOut | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const isLaptop = useIsLaptop();
  const q = usePlanWeek(start);
  const setStatus = useSetItemStatus();
  const startRebalance = useStartRebalance();
  const decide = useDecideRebalance();
  const pending = usePendingIds(userId);
  useFlushPlanOutbox(online);

  if (!q.data) {
    if (q.isError) {
      return (
        <section aria-labelledby="plan-h" className="flex flex-col gap-6 pt-10">
          <h1 id="plan-h" className="sr-only">
            {t("planner.heading")}
          </h1>
          <ErrorState
            title={t("planner.error_title")}
            message={t(
              q.error instanceof ApiError && q.error.status === null
                ? "today.error_unreachable"
                : "today.error_generic",
            )}
            requestId={q.error instanceof ApiError ? q.error.requestId : null}
            onRetry={() => void q.refetch()}
            retryLabel={t("planner.error_retry")}
            retrying={q.isFetching}
          />
        </section>
      );
    }
    return <Loading />;
  }

  const { data: week, fromCache } = q.data;
  if (!week) return <Empty />;
  const current = week.days.some((d) => d.is_today);
  const selected =
    picked && week.days.some((d) => d.date === picked)
      ? picked
      : (week.days.find((d) => d.is_today) ?? week.days[0])?.date;
  const day = week.days.find((d) => d.date === selected) ?? week.days[0];
  const linkCtx = { exam: week.exam, cardsOn: me.data?.features.cards ?? false };
  const proposal: RebalanceOut | null = week.rebalance;

  const decideOn = (decision: "accept" | "decline") => {
    if (!proposal) return;
    setNotice(null);
    decide.mutate(
      { row: proposal, decision },
      {
        onSuccess: (r) => {
          if (r.kind === "done") {
            const o = r.outcome;
            if (o.outcome === "applied")
              setNotice({ kind: "applied", moved: o.detail?.moved ?? 0, dropped: o.detail?.dropped ?? 0 });
            else if (o.outcome === "declined") setNotice({ kind: "declined" });
            else setNotice({ kind: "stale" });
          } else if (r.kind === "changed") setNotice({ kind: "stale" });
          else setNotice({ kind: "nothing" });
        },
        onError: () => setNotice({ kind: "decide_failed" }),
      },
    );
  };

  return (
    <section aria-labelledby="plan-h" className="flex flex-col gap-6 pt-10 pb-8" data-testid="planner">
      <Header
        week={week}
        online={online}
        onPrev={week.prev_start ? () => setStart(week.prev_start ?? undefined) : null}
        onNext={week.next_start ? () => setStart(week.next_start ?? undefined) : null}
        onToday={current && start === undefined ? null : () => setStart(undefined)}
        busy={startRebalance.isPending}
        onRebalance={() => {
          setNotice(null);
          startRebalance.mutate(undefined, {
            onSuccess: (r) => {
              if (r.kind === "nothing") setNotice({ kind: "nothing" });
            },
            onError: () => setNotice({ kind: "failed" }),
          });
        }}
      />
      {fromCache || !online ? (
        <p role="status" className="m-0 text-ui text-text-2" data-testid="offline-note">
          {t("planner.offline_note")}
        </p>
      ) : null}
      {setStatus.isError ? (
        <p role="alert" className="m-0 text-ui text-amber">
          {t("planner.status_failed")}
        </p>
      ) : null}
      <div role="status" aria-live="polite" className="empty:hidden">
        {notice ? (
          <p className="m-0 text-ui text-text-2" data-testid="planner-notice" data-kind={notice.kind}>
            {noticeText(t, notice)}
          </p>
        ) : null}
      </div>
      {current ? <NextUp week={week} onOpen={setOpen} linkCtx={linkCtx} /> : null}
      {proposal ? <RebalanceCard row={proposal} busy={decide.isPending} offline={!online} onDecide={decideOn} /> : null}
      {isLaptop ? (
        <WeekGrid week={week} pending={pending} onOpen={setOpen} />
      ) : (
        <div className="flex flex-col gap-5">
          <WeekStrip week={week} selected={selected ?? week.start} onSelect={setPicked} />
          {day ? <DayList day={day} pending={pending} onOpen={setOpen} /> : null}
        </div>
      )}
      <Milestones items={week.milestones} exam={week.exam} />
      <ItemSheet
        item={open}
        pending={open ? pending.has(open.id) : false}
        ctx={linkCtx}
        onClose={() => setOpen(null)}
        onStatus={(id, status) => setStatus.mutate({ id, status })}
      />
    </section>
  );
}
