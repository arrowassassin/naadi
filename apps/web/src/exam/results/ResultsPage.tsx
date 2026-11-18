import type { BlockResults, ResultQuestion, TopicStat, TrendPoint, WeightOverlay } from "@naadi/shared";
import { Button, cx, ErrorState, Skeleton } from "@naadi/ui";
import { Link } from "@tanstack/react-router";
import { Flag, Minus, X } from "lucide-react";
import { useId } from "react";
import { ApiError } from "../../auth/apiError";
import { useBlock } from "../../practice/api";
import { useCopy } from "../../practice/copy";
import { shortDate } from "../../practice/format";
import { useBlockResults } from "./api";
import {
  insightText,
  isMissed,
  num,
  previousScore,
  type Translate,
  trendGeometry,
  weightGeometry,
  weightScale,
} from "./model";

const TILE = "flex min-w-0 flex-col gap-1 rounded-tile-sm border border-line-soft bg-surface-1 p-6";
const PANEL = "flex min-w-0 flex-col gap-4 rounded-tile border border-line-soft bg-surface-1 p-6 box-border tablet:p-8";
const H2 = "m-0 font-bold font-display text-[1.75rem] tracking-[-0.02em]";
const H3 = "m-0 font-mono text-label text-text-3 uppercase";

function Loading() {
  const t = useCopy();
  return (
    <section aria-labelledby="results-h" aria-busy="true" className="flex flex-col gap-6 pt-6 tablet:pt-10">
      <h1 id="results-h" className="sr-only">
        {t("results.title")}
      </h1>
      <p role="status" className="sr-only">
        {t("results.loading")}
      </p>
      <div className="flex flex-wrap items-end justify-between gap-6">
        <Skeleton className="h-[clamp(6rem,18vw,15rem)] w-[min(100%,28rem)] rounded-block" />
        <Skeleton className="h-28 w-[min(100%,26rem)] rounded-block" />
      </div>
      <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,10.5rem),1fr))] gap-4">
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-32 rounded-tile-sm" />
        ))}
      </div>
      <div className="flex flex-wrap gap-5">
        <Skeleton className="h-80 min-w-0 flex-[2_1_32rem] rounded-tile" />
        <Skeleton className="h-80 min-w-0 flex-[1_1_20rem] rounded-tile" />
      </div>
    </section>
  );
}

/** 409 not_finished: the block is still running, so its results (and answers) are not out yet. */
export function ResultsNotFinished({ blockId }: { blockId: string }) {
  const t = useCopy();
  const block = useBlock(blockId);
  const n = block.data?.next_n ?? 1;
  return (
    <section aria-labelledby="results-h" className="flex max-w-[40rem] flex-col gap-5 pt-6 tablet:pt-10">
      <h1 id="results-h" className="m-0 font-display text-headline">
        {t("results.not_finished_title")}
      </h1>
      <p className="m-0 text-reading text-text-2">{t("results.not_finished_body")}</p>
      <p className="m-0">
        <Link
          to="/practice/blocks/$blockId/$n"
          params={{ blockId, n }}
          className="inline-flex min-h-14 items-center justify-center rounded-pill bg-lime px-7 font-semibold text-lime-ink no-underline hover:bg-lime-hover hover:text-lime-ink"
        >
          {t("results.back_to_exam")}
        </Link>
      </p>
    </section>
  );
}

function Failed({ error, retry, retrying }: { error: Error | null; retry: () => void; retrying: boolean }) {
  const t = useCopy();
  const api = error instanceof ApiError ? error : null;
  const gone = api?.status === 404;
  const notTimed = api?.code === "not_timed";
  const message = notTimed
    ? t("results.not_timed")
    : gone
      ? t("results.gone_body")
      : api?.status === null
        ? t("results.offline")
        : (error?.message ?? t("results.error_title"));
  return (
    <section aria-labelledby="results-h" className="flex flex-col gap-6 pt-6 tablet:pt-10">
      <h1 id="results-h" className="m-0 font-display text-headline">
        {t("results.title")}
      </h1>
      <ErrorState
        title={gone || notTimed ? t("results.gone_title") : t("results.error_title")}
        message={message}
        requestId={api?.requestId ?? null}
        onRetry={gone || notTimed ? undefined : retry}
        retryLabel={t("results.retry")}
        retrying={retrying}
      />
      <p className="m-0">
        <Link to="/practice" className="inline-flex min-h-11 items-center">
          {t("practice.back_to_practice")}
        </Link>
      </p>
    </section>
  );
}

function Header({ r, t }: { r: BlockResults; t: Translate }) {
  const reasonId = useId();
  const misses = r.questions.filter(isMissed);
  const prev = previousScore(r.trend);
  const delta = prev === null ? null : Math.round(r.percent) - Math.round(prev);
  const firstMiss = misses[0]?.n ?? 1;
  const kicker = t(r.mock ? "results.kicker_mock" : "results.kicker", {
    mode: t(`practice.mode_${r.mode}`),
    date: shortDate(r.finished_at),
  });
  return (
    <header className="flex flex-wrap items-end justify-between gap-6">
      <div className="flex min-w-0 flex-col gap-2">
        <span className="font-mono text-label text-text-3 uppercase">{kicker}</span>
        <h1
          id="results-h"
          className="m-0 font-display font-extrabold text-[clamp(6rem,1rem+18vw,17.5rem)] leading-[0.8] tracking-[-0.06em]"
        >
          <span aria-hidden="true">
            {Math.round(r.percent)}
            <span className="text-text-muted">%</span>
          </span>
          <span className="sr-only">{t("results.score_sr", { percent: Math.round(r.percent) })}</span>
        </h1>
      </div>
      <div className="flex w-full min-w-0 flex-col gap-3 tablet:w-auto tablet:max-w-[28rem]">
        <p className="m-0 font-display text-[clamp(1.375rem,1rem+1.4vw,2rem)] leading-[1.15] tracking-[-0.02em]">
          {t("results.sentence", { correct: r.correct, total: r.total })}
          {delta === null
            ? null
            : ` ${
                delta > 0
                  ? t("results.sentence_up", { delta })
                  : delta < 0
                    ? t("results.sentence_down", { delta: -delta })
                    : t("results.sentence_same")
              }`}
        </p>
        <div className="flex flex-wrap gap-2">
          <Link
            to="/practice/blocks/$blockId/review/$n"
            params={{ blockId: r.block_id, n: misses.length ? firstMiss : 1 }}
            search={{ filter: misses.length ? "missed" : "all" }}
            className="inline-flex min-h-14 items-center justify-center rounded-pill bg-lime px-[26px] font-semibold text-[1rem] text-lime-ink no-underline hover:bg-lime-hover hover:text-lime-ink"
          >
            {misses.length === 0
              ? t("results.review_all")
              : misses.length === 1
                ? t("results.review_miss_one")
                : t("results.review_misses", { n: misses.length })}
          </Link>
          {misses.length ? (
            <Button disabled aria-describedby={reasonId} size="cta" className="px-[26px]">
              {t("results.make_cards")}
            </Button>
          ) : null}
        </div>
        {misses.length ? (
          <p id={reasonId} className="m-0 text-text-3 text-ui-sm">
            {t("results.make_cards_unavailable")}
          </p>
        ) : null}
      </div>
    </header>
  );
}

function Stats({ r, t }: { r: BlockResults; t: Translate }) {
  const mean = r.time.mean_s;
  const tiles: { key: string; label: string; value: string; note: string }[] = [
    {
      key: "correct",
      label: t("results.stat_correct"),
      value: String(r.correct),
      note: t("results.stat_correct_note", { total: r.total }),
    },
    {
      key: "time",
      label: t("results.stat_time"),
      value: mean === null ? "–" : t("results.stat_seconds", { seconds: Math.round(mean) }),
      note:
        mean === null
          ? t("results.stat_time_none")
          : r.time.target_s
            ? t("results.stat_time_note", { target: r.time.target_s })
            : t("results.stat_time_median", { median: Math.round(r.time.median_s ?? mean) }),
    },
    {
      key: "changed",
      label: t("results.stat_changed"),
      value: String(r.changes.changed),
      note: t("results.stat_changed_note", { n: r.changes.right_to_wrong }),
    },
    {
      key: "flagged",
      label: t("results.stat_flagged"),
      value: String(r.flags.flagged),
      note: t("results.stat_flagged_note", { n: r.flags.flagged_correct }),
    },
  ];
  if (r.omitted > 0) {
    tiles.push({
      key: "omitted",
      label: t("results.stat_omitted"),
      value: String(r.omitted),
      note: t("results.stat_omitted_note"),
    });
  }
  return (
    <section aria-label={t("results.stats_label")}>
      <dl className="m-0 grid grid-cols-[repeat(auto-fit,minmax(min(100%,10.5rem),1fr))] gap-4">
        {tiles.map((s) => (
          <div key={s.key} className={TILE} data-stat={s.key}>
            <dt className="text-[0.8125rem] text-text-3">{s.label}</dt>
            <dd className="m-0 font-display font-extrabold text-[clamp(2.25rem,1.6rem+1.6vw,3rem)] leading-none tracking-[-0.03em]">
              {s.value}
            </dd>
            <dd className="m-0 text-[0.8125rem] text-text-2">{s.note}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

/** The accessible name of a grid tile: number plus every status in words (never colour alone). */
export function questionLabel(t: Translate, q: ResultQuestion): string {
  const words = [t("results.q_label", { n: q.n })];
  words.push(
    q.outcome === "correct"
      ? t("results.q_correct")
      : q.outcome === "omitted"
        ? t("results.q_omitted")
        : t("results.q_missed"),
  );
  if (q.flagged) words.push(t("results.q_flagged"));
  if (q.change) words.push(t(`results.change.${q.change}`));
  return words.join(", ");
}

function tileClass(q: ResultQuestion): string {
  const missed = isMissed(q);
  if (q.flagged) {
    return missed
      ? "border-2 border-dashed border-amber text-amber hover:text-amber"
      : "border-2 border-dashed border-paper text-text hover:text-text";
  }
  return missed ? "border-2 border-amber text-amber hover:text-amber" : "bg-paper text-ground hover:text-ground";
}

function QuestionGrid({ r, t }: { r: BlockResults; t: Translate }) {
  const sections = [...new Set(r.questions.map((q) => q.section_n))].sort((a, b) => a - b);
  const grid = (qs: ResultQuestion[]) => (
    <ol className="m-0 grid list-none grid-cols-[repeat(auto-fill,minmax(52px,1fr))] gap-2 p-0">
      {qs.map((q) => (
        <li key={q.n}>
          <Link
            to="/practice/blocks/$blockId/review/$n"
            params={{ blockId: r.block_id, n: q.n }}
            search={{ filter: "all" }}
            aria-label={questionLabel(t, q)}
            data-outcome={q.outcome}
            data-flagged={q.flagged || undefined}
            className={cx(
              "box-border flex h-[52px] items-center justify-center gap-0.5 rounded-block-sm font-mono text-[0.875rem] no-underline",
              tileClass(q),
            )}
          >
            <span aria-hidden="true">{q.n}</span>
            {q.outcome === "incorrect" ? <X aria-hidden="true" size={11} strokeWidth={3} /> : null}
            {q.outcome === "omitted" ? <Minus aria-hidden="true" size={11} strokeWidth={3} /> : null}
            {q.flagged ? <Flag aria-hidden="true" size={10} strokeWidth={2.5} /> : null}
          </Link>
        </li>
      ))}
    </ol>
  );
  return (
    <section aria-labelledby="q-h" className={cx(PANEL, "flex-[2_1_32rem]")}>
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 id="q-h" className={H2}>
          {t("results.grid_title")}
        </h2>
        <span className="text-[0.8125rem] text-text-2">{t("results.grid_legend")}</span>
      </div>
      {sections.length > 1
        ? sections.map((s) => (
            <div key={s} className="flex flex-col gap-2">
              <h3 className={H3}>{t("results.grid_section", { n: s })}</h3>
              {grid(r.questions.filter((q) => q.section_n === s))}
            </div>
          ))
        : grid(r.questions)}
    </section>
  );
}

function Bar({ percent, weak }: { percent: number; weak: boolean }) {
  return (
    <span className="block h-1.5 overflow-hidden rounded-[3px]">
      <svg aria-hidden="true" viewBox="0 0 100 6" preserveAspectRatio="none" className="block h-1.5 w-full">
        <rect width="100" height="6" className="fill-line" />
        <rect width={Math.min(100, Math.max(0, percent))} height="6" className={weak ? "fill-amber" : "fill-paper"} />
      </svg>
    </span>
  );
}

function TopicList({ title, rows, t }: { title: string; rows: TopicStat[]; t: Translate }) {
  return (
    <div className="flex flex-col gap-3">
      <h3 className={H3}>{title}</h3>
      <ul className="m-0 flex list-none flex-col gap-3.5 p-0">
        {rows.map((s) => (
          <li key={s.id} className="flex flex-col gap-1.5" data-weak={s.weak || undefined}>
            <span className="flex items-baseline justify-between gap-3 text-[0.875rem]">
              <span className="min-w-0 break-words">{s.label}</span>
              <span className="flex shrink-0 items-baseline gap-2">
                {s.weak ? (
                  <span className="font-mono text-label-sm text-amber uppercase tracking-[0.08em]">
                    {t("results.weak")}
                  </span>
                ) : null}
                <span aria-hidden="true" className="font-mono text-text-2">
                  {t("results.topic_score", { correct: s.correct, total: s.total })}
                </span>
                <span className="sr-only">
                  {t("results.topic_score_sr", { correct: s.correct, total: s.total, percent: Math.round(s.percent) })}
                </span>
              </span>
            </span>
            <Bar percent={s.percent} weak={s.weak} />
          </li>
        ))}
      </ul>
    </div>
  );
}

function Topics({ r, t }: { r: BlockResults; t: Translate }) {
  const clinical = r.disciplines.some((d) => d.kind === "clinical_science");
  const groups = [
    { key: "systems", title: t("results.topics_systems"), rows: r.systems },
    {
      key: "disciplines",
      title: clinical ? t("results.topics_clinical") : t("results.topics_disciplines"),
      rows: r.disciplines,
    },
    { key: "competencies", title: t("results.topics_competencies"), rows: r.competencies },
  ].filter((g) => g.rows.length > 0);
  return (
    <section aria-labelledby="t-h" className={cx(PANEL, "flex-[1_1_20rem]")}>
      <h2 id="t-h" className={H2}>
        {t("results.topics_title")}
      </h2>
      {groups.length ? (
        groups.map((g) => <TopicList key={g.key} title={g.title} rows={g.rows} t={t} />)
      ) : (
        <p className="m-0 text-text-2">{t("results.topics_empty")}</p>
      )}
    </section>
  );
}

const TREND_W = 600;
const TREND_H = 180;
const TREND_PAD = 16;

function Trend({ points, t }: { points: TrendPoint[]; t: Translate }) {
  const g = trendGeometry(points, TREND_W, TREND_H, TREND_PAD);
  const first = points[0];
  const last = points[points.length - 1];
  const label =
    first && last && points.length > 1
      ? t("results.trend_label", {
          count: points.length,
          first: Math.round(first.score),
          last: Math.round(last.score),
        })
      : t("results.trend_one");
  const yOf = (score: number) => TREND_PAD + (TREND_H - 2 * TREND_PAD) * (1 - score / 100);
  return (
    <section aria-labelledby="trend-h" className={cx(PANEL, "flex-[2_1_32rem]")}>
      <h2 id="trend-h" className={H2}>
        {t("results.trend_title")}
      </h2>
      <svg viewBox={`0 0 ${TREND_W} ${TREND_H}`} role="img" aria-label={label} className="block h-auto w-full">
        {[0, 50, 100].map((s) => (
          <path
            key={s}
            d={`M${TREND_PAD} ${yOf(s)} H${TREND_W - TREND_PAD}`}
            className="stroke-line"
            strokeWidth="1"
            strokeDasharray={s === 50 ? "4 6" : undefined}
          />
        ))}
        {points.length > 1 ? (
          <path
            d={g.path}
            fill="none"
            className="stroke-paper"
            strokeWidth="3"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        ) : null}
        {g.dots.map((d, i) => (
          <circle
            // biome-ignore lint/suspicious/noArrayIndexKey: points are ordered and fixed for a finished block
            key={i}
            cx={d.x}
            cy={d.y}
            r={d.current ? 8 : 4.5}
            className={d.current ? "fill-paper stroke-ground" : "fill-text-2"}
            strokeWidth={d.current ? 3 : 0}
          />
        ))}
      </svg>
      <div className="flex justify-between font-mono text-label-sm text-text-3" aria-hidden="true">
        <span>{first ? shortDate(first.finished_at) : ""}</span>
        <span>{last && last !== first ? shortDate(last.finished_at) : ""}</span>
      </div>
      {points.length < 2 ? <p className="m-0 text-text-2 text-ui-sm">{t("results.trend_one")}</p> : null}
      <div className="sr-only">
        <table>
          <caption>{t("results.trend_table_caption")}</caption>
          <thead>
            <tr>
              <th scope="col">{t("results.trend_col_date")}</th>
              <th scope="col">{t("results.trend_col_mode")}</th>
              <th scope="col">{t("results.trend_col_score")}</th>
            </tr>
          </thead>
          <tbody>
            {points.map((p) => (
              <tr key={p.block_id}>
                <td>
                  {shortDate(p.finished_at)}
                  {p.current ? ` (${t("results.trend_this")})` : ""}
                </td>
                <td>{t(`practice.mode_${p.mode}`)}</td>
                <td>{t("results.percent", { percent: Math.round(p.score) })}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Insights({ r, t }: { r: BlockResults; t: Translate }) {
  const lines = r.insights.map((i) => insightText(t, i)).filter((s): s is string => s !== null);
  const [lead, ...rest] = lines;
  return (
    <section
      aria-labelledby="sees-h"
      className="box-border flex min-w-0 flex-[1_1_20rem] flex-col gap-3.5 rounded-tile border border-violet-line bg-violet-ground p-6 tablet:p-8"
    >
      <h2 id="sees-h" className="m-0 font-mono text-label text-violet-text uppercase">
        {t("results.insights_kicker")}
      </h2>
      {lead ? (
        <>
          <p className="m-0 font-display text-[clamp(1.25rem,1rem+0.9vw,1.5rem)] leading-[1.2]">{lead}</p>
          {rest.length ? (
            <ul className="m-0 flex list-none flex-col gap-2.5 p-0">
              {rest.map((s) => (
                <li key={s} className="border-violet-line border-t pt-2.5 text-[1rem] text-text-2">
                  {s}
                </li>
              ))}
            </ul>
          ) : null}
        </>
      ) : (
        <p className="m-0 text-[1.0625rem] text-text-2">{t("results.insights_none")}</p>
      )}
    </section>
  );
}

function WeightRow({ w, scale, t }: { w: WeightOverlay; scale: number; t: Translate }) {
  const g = weightGeometry(w, scale);
  const parts = [
    w.weight_min === null
      ? t("results.weights_no_official")
      : t("results.weights_official", {
          min: num(w.weight_min),
          max: num(w.weight_max ?? w.weight_min),
        }),
    t("results.weights_share", { share: num(w.share) }),
  ];
  if (g.place) parts.push(t(`results.weights_${g.place}`));
  return (
    <li
      className="grid gap-x-5 gap-y-2 border-line-soft border-t py-3.5 first:border-t-0 laptop:grid-cols-[12rem_minmax(0,1fr)_24rem] laptop:items-center"
      data-place={g.place ?? undefined}
    >
      <span className="font-semibold">{w.label}</span>
      <svg aria-hidden="true" viewBox="0 0 100 12" preserveAspectRatio="none" className="block h-3 w-full">
        <rect width="100" height="12" className="fill-surface-2" />
        {g.band ? <rect x={g.band.x} width={g.band.w} height="12" className="fill-line-dashed" /> : null}
        <rect x={Math.max(0, Math.min(99, g.share - 0.5))} width="1" height="12" className="fill-paper" />
      </svg>
      <span className="flex flex-wrap gap-x-3 gap-y-1 text-[0.8125rem] text-text-2">
        <span>{parts.join(" · ")}</span>
        <span className={cx(w.percent !== null && w.percent < 60 && "text-amber")}>
          {w.percent === null
            ? t("results.weights_none")
            : t("results.weights_accuracy", { percent: Math.round(w.percent) })}
          {w.percent !== null && w.percent < 60 ? ` · ${t("results.weak")}` : ""}
        </span>
      </span>
    </li>
  );
}

function Weights({ r, t }: { r: BlockResults; t: Translate }) {
  const rows = r.weights;
  const scale = weightScale(rows);
  return (
    <section aria-labelledby="w-h" className={PANEL}>
      <h2 id="w-h" className={H2}>
        {t("results.weights_title")}
      </h2>
      {rows.length ? (
        <>
          <p className="m-0 flex flex-wrap items-center gap-x-5 gap-y-2 text-[0.8125rem] text-text-2">
            <span className="inline-flex items-center gap-2">
              <span aria-hidden="true" className="inline-block h-3 w-6 bg-line-dashed" />
              {t("results.weights_legend_band")}
            </span>
            <span className="inline-flex items-center gap-2">
              <span aria-hidden="true" className="inline-block h-3 w-1 bg-paper" />
              {t("results.weights_legend_share")}
            </span>
            <span className="text-text-3">{t("results.weights_scale", { max: scale })}</span>
          </p>
          <ul className="m-0 flex list-none flex-col p-0">
            {rows.map((w) => (
              <WeightRow key={w.id} w={w} scale={scale} t={t} />
            ))}
          </ul>
        </>
      ) : (
        <p className="m-0 text-text-2">{t("results.weights_empty")}</p>
      )}
    </section>
  );
}

function ResultsBody({ r }: { r: BlockResults }) {
  const t = useCopy();
  return (
    <section aria-labelledby="results-h" className="flex flex-col gap-6 pt-6 pb-8 tablet:pt-10">
      <Header r={r} t={t} />
      <Stats r={r} t={t} />
      <div className="flex flex-wrap gap-5">
        <QuestionGrid r={r} t={t} />
        <Topics r={r} t={t} />
      </div>
      <div className="flex flex-wrap gap-5">
        <Trend points={r.trend} t={t} />
        <Insights r={r} t={t} />
      </div>
      <Weights r={r} t={t} />
    </section>
  );
}

/** `/practice/blocks/$blockId/results`: the Results board for a finished timed or exam block. */
export function ResultsPage({ blockId }: { blockId: string }) {
  const q = useBlockResults(blockId);
  if (q.data) return <ResultsBody r={q.data} />;
  const err = q.error;
  if (!err) return <Loading />;
  if (err instanceof ApiError && err.code === "not_finished") return <ResultsNotFinished blockId={blockId} />;
  return <Failed error={err} retry={() => void q.refetch()} retrying={q.isFetching} />;
}
