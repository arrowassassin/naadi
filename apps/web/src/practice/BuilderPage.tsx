import type { BlockSummary, PoolFacet, PoolResponse } from "@naadi/shared";
import { Chip, cx, ErrorState, Skeleton } from "@naadi/ui";
import type { UseQueryResult } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { Minus, Plus } from "lucide-react";
import { useId, useState } from "react";
import { ApiError } from "../auth/apiError";
import { useBlocks, useCreateBlock, useCreateWeakSpotBlock, usePool } from "./api";
import { useCopy } from "./copy";
import { resumeIndex, shortDate } from "./format";

type Mode = "tutor" | "timed" | "exam";
type Status = "all" | "unused" | "incorrect" | "flagged" | "correct";
type Difficulty = "easy" | "medium" | "hard";

const STATUSES: Status[] = ["unused", "incorrect", "flagged", "correct", "all"];
const DIFFICULTIES: (Difficulty | null)[] = [null, "easy", "medium", "hard"];

const CHIP_BASE =
  "inline-flex min-h-12 items-center gap-2 rounded-pill px-[18px] text-[0.9375rem] transition-colors duration-(--duration-control) ease-out";
const CHIP_ON = "border border-paper bg-paper font-semibold text-ground";
const CHIP_OFF = "border border-line-control bg-transparent text-text hover:bg-surface-2";
const CHIP_DISABLED = "cursor-not-allowed border border-dashed border-line-dashed bg-transparent text-text-muted";

function Chipbutton({
  on,
  onClick,
  children,
  count,
  disabled,
  describedBy,
}: {
  on: boolean;
  onClick: () => void;
  children: string;
  count?: number;
  disabled?: boolean;
  describedBy?: string;
}) {
  return (
    <button
      type="button"
      aria-pressed={on}
      aria-disabled={disabled || undefined}
      aria-describedby={disabled ? describedBy : undefined}
      onClick={disabled ? undefined : onClick}
      className={cx(CHIP_BASE, disabled ? CHIP_DISABLED : on ? CHIP_ON : CHIP_OFF)}
    >
      {children}
      {count !== undefined ? <span className="font-mono text-label-sm opacity-75">{count}</span> : null}
    </button>
  );
}

function Group({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <fieldset className="m-0 flex min-w-0 flex-col gap-3.5 rounded-tile border border-line-soft bg-surface-1 p-6 tablet:p-7">
      <legend className="sr-only">{title}</legend>
      <p aria-hidden="true" className="m-0 font-bold font-display text-[1.5rem] tracking-[-0.02em]">
        {title}
      </p>
      <div className="flex flex-wrap gap-2">{children}</div>
    </fieldset>
  );
}

function toggle(list: string[], id: string): string[] {
  return list.includes(id) ? list.filter((x) => x !== id) : [...list, id];
}

function Loading() {
  const t = useCopy();
  return (
    <section aria-labelledby="practice-h" aria-busy="true" className="flex flex-col gap-8 pt-6 tablet:pt-10">
      <h1 id="practice-h" className="sr-only">
        {t("practice.headline")} {t("practice.headline_muted")}
      </h1>
      <p role="status" className="sr-only">
        {t("practice.loading")}
      </p>
      <Skeleton className="h-[clamp(5rem,12vw,9rem)] w-2/3 rounded-block" />
      <div className="grid gap-5 laptop:grid-cols-[2fr_1fr]">
        <div className="flex flex-col gap-5">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-40 rounded-tile" />
          ))}
        </div>
        <Skeleton className="h-96 rounded-tile" />
      </div>
    </section>
  );
}

function Headline({ lead, muted }: { lead: string; muted: string }) {
  return (
    <h1
      id="practice-h"
      className="m-0 font-display font-extrabold text-[clamp(3.5rem,1.5rem+8vw,8.5rem)] leading-[0.88] tracking-[-0.05em]"
    >
      {lead}
      <br />
      <span className="text-text-muted">{muted}</span>
    </h1>
  );
}

function PastBlocks({ query }: { query: UseQueryResult<{ blocks: BlockSummary[] }, Error> }) {
  const t = useCopy();
  const blocks = query.data?.blocks;
  return (
    <section id="past-blocks" aria-labelledby="past-h" className="flex scroll-mt-24 flex-col gap-4">
      <h2 id="past-h" className="m-0 font-bold font-display text-[1.5rem] tracking-[-0.02em]">
        {t("practice.past_blocks")}
      </h2>
      {query.isPending ? (
        <div aria-busy="true">
          <p role="status" className="sr-only">
            {t("practice.past_loading")}
          </p>
          <Skeleton className="h-24 rounded-tile" />
        </div>
      ) : !blocks ? (
        <ErrorState
          title={t("practice.past_error")}
          message={query.error?.message ?? t("practice.generic_error")}
          requestId={query.error instanceof ApiError ? query.error.requestId : null}
          onRetry={() => void query.refetch()}
          retrying={query.isFetching}
          retryLabel={t("practice.retry")}
        />
      ) : blocks.length === 0 ? (
        <p className="m-0 rounded-block border border-line-soft border-dashed px-5 py-6 text-text-2">
          {t("practice.past_empty")}
        </p>
      ) : (
        <ol className="m-0 flex list-none flex-col rounded-tile border border-line-soft bg-surface-1 p-0">
          {blocks.map((b) => {
            const done = b.finished_at !== null;
            return (
              <li key={b.id} className="border-line-soft border-t first:border-t-0">
                <Link
                  to="/practice/blocks/$blockId/$n"
                  params={{ blockId: b.id, n: done ? 1 : resumeIndex(b.answered, b.total) }}
                  className="grid min-h-[72px] grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-1.5 px-5 py-3 text-text no-underline hover:text-text"
                >
                  <span className="flex min-w-0 flex-col gap-1">
                    <span className="font-semibold">{shortDate(b.created_at)}</span>
                    <span className="text-text-3 text-ui-sm">
                      {t("practice.past_progress", { answered: b.answered, total: b.total })}
                      {b.correct !== null ? ` · ${t("practice.past_correct", { correct: b.correct })}` : ""}
                    </span>
                  </span>
                  <span className="flex flex-wrap items-center justify-end gap-1.5">
                    <Chip tone="meta" srPrefix={t("practice.group_mode")}>
                      {t(`practice.mode_${b.config.mode}`)}
                    </Chip>
                    <Chip tone={done ? "done" : "planned"}>
                      {done ? t("practice.past_review") : t("practice.past_continue")}
                    </Chip>
                  </span>
                </Link>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}

function BuilderBody(props: BodyProps) {
  const { t, pool } = props;
  const reasonId = useId();
  const countId = useId();
  const limits = pool.limits;
  const total = pool.total;
  const maxCount = Math.max(limits.min_questions, Math.min(limits.max_questions, total));
  const count = Math.min(Math.max(props.wanted ?? limits.default_questions, limits.min_questions), maxCount);
  const unavailable = pool.modes.some((m) => !m.available);
  const picked = [...pool.systems, ...pool.disciplines].filter(
    (f) => props.systems.includes(f.id) || props.disciplines.includes(f.id),
  );
  const topics = picked.length ? picked.map((f) => f.label).join(", ") : t("practice.all_systems");
  const error = (props.create.error ?? props.weak.error) as Error | null;
  const busy = props.create.isPending || props.weak.isPending;
  // nothing at all to practise (not just filtered out): a different message from "widen the filters"
  const noFilters =
    props.systems.length === 0 && props.disciplines.length === 0 && props.difficulty === null && pool.status.all === 0;

  const start = () =>
    props.create.mutate(
      {
        exam: pool.exam,
        systems: props.systems,
        disciplines: props.disciplines,
        status: props.status,
        difficulty: props.difficulty ?? undefined,
        count,
        mode: props.mode,
      },
      { onSuccess: (b) => void props.nav({ to: "/practice/blocks/$blockId/$n", params: { blockId: b.id, n: 1 } }) },
    );
  const weakSpot = () =>
    props.weak.mutate(
      { exam: pool.exam, mode: props.mode },
      {
        onSuccess: (r) =>
          void props.nav({ to: "/practice/blocks/$blockId/$n", params: { blockId: r.block.id, n: r.block.next_n } }),
      },
    );

  const facet = (f: PoolFacet, list: string[], set: (v: string[]) => void) => (
    <Chipbutton key={f.id} on={list.includes(f.id)} onClick={() => set(toggle(list, f.id))} count={f.count}>
      {f.label}
    </Chipbutton>
  );

  return (
    <div className="grid gap-5 laptop:grid-cols-[minmax(0,2fr)_minmax(20rem,1fr)]">
      <div className="flex min-w-0 flex-col gap-5">
        {pool.exams.length > 1 ? (
          <Group title={t("practice.group_exam")}>
            {pool.exams.map((e) => (
              <Link
                key={e.id}
                to="/practice"
                search={{ exam: e.id }}
                aria-current={e.id === pool.exam ? "page" : undefined}
                className={cx(
                  CHIP_BASE,
                  "no-underline",
                  e.id === pool.exam ? `${CHIP_ON} hover:text-ground` : `${CHIP_OFF} hover:text-text`,
                )}
              >
                {e.label}
              </Link>
            ))}
          </Group>
        ) : null}
        <Group title={t("practice.group_mode")}>
          {pool.modes.map((m) => (
            <Chipbutton
              key={m.id}
              on={props.mode === m.id}
              disabled={!m.available}
              describedBy={reasonId}
              onClick={() => props.setMode(m.id)}
            >
              {t(`practice.mode_${m.id}`)}
            </Chipbutton>
          ))}
          {unavailable ? (
            <p id={reasonId} className="m-0 basis-full text-text-3 text-ui-sm">
              {t("practice.mode_unavailable")}
            </p>
          ) : null}
        </Group>
        <Group title={t("practice.group_systems")}>
          <Chipbutton on={props.systems.length === 0} onClick={() => props.setSystems([])}>
            {t("practice.all_systems")}
          </Chipbutton>
          {pool.systems.map((f) => facet(f, props.systems, props.setSystems))}
        </Group>
        {pool.disciplines.length ? (
          <Group title={t("practice.group_disciplines")}>
            {pool.disciplines.map((f) => facet(f, props.disciplines, props.setDisciplines))}
          </Group>
        ) : null}
        <Group title={t("practice.group_status")}>
          {STATUSES.map((s) => (
            <Chipbutton key={s} on={props.status === s} count={pool.status[s] ?? 0} onClick={() => props.setStatus(s)}>
              {t(`practice.status_${s}`)}
            </Chipbutton>
          ))}
        </Group>
        <Group title={t("practice.group_difficulty")}>
          {DIFFICULTIES.map((d) => (
            <Chipbutton
              key={d ?? "all"}
              on={props.difficulty === d}
              count={pool.difficulty[d ?? "all"] ?? 0}
              onClick={() => props.setDifficulty(d)}
            >
              {t(`practice.difficulty_${d ?? "all"}`)}
            </Chipbutton>
          ))}
        </Group>
      </div>

      <aside aria-label={t("practice.your_block")} className="flex min-w-0 flex-col gap-5">
        <div className="flex flex-col justify-between gap-6 rounded-tile bg-lime p-7 text-lime-ink tablet:p-8">
          <span className="font-mono text-label uppercase">{t("practice.your_block")}</span>
          <div className="flex items-center justify-between gap-3">
            <button
              type="button"
              aria-label={t("practice.fewer")}
              disabled={count <= limits.min_questions}
              onClick={() => props.setWanted(count - 1)}
              className="flex size-14 shrink-0 items-center justify-center rounded-full border-2 border-lime-ink bg-transparent text-lime-ink disabled:opacity-40"
            >
              <Minus aria-hidden="true" size={24} />
            </button>
            <output
              htmlFor={countId}
              aria-live="polite"
              className="font-display font-extrabold text-[clamp(5rem,3rem+6vw,9rem)] leading-[0.85] tracking-[-0.06em]"
            >
              {total === 0 ? 0 : count}
            </output>
            <button
              type="button"
              aria-label={t("practice.more")}
              disabled={count >= maxCount}
              onClick={() => props.setWanted(count + 1)}
              className="flex size-14 shrink-0 items-center justify-center rounded-full border-2 border-lime-ink bg-transparent text-lime-ink disabled:opacity-40"
            >
              <Plus aria-hidden="true" size={24} />
            </button>
          </div>
          <input
            id={countId}
            type="range"
            aria-label={t("practice.count_label")}
            min={limits.min_questions}
            max={maxCount}
            value={count}
            disabled={total === 0 || maxCount <= limits.min_questions}
            onChange={(e) => props.setWanted(Number(e.target.value))}
            className="w-full accent-lime-ink"
          />
          <p className="m-0 font-medium text-[1rem]">
            {t("practice.summary", {
              mode: t(`practice.mode_${props.mode}`),
              topics,
              status: t(`practice.status_${props.status}`).toLowerCase(),
            })}
            . {total === 1 ? t("practice.matches_one") : t("practice.matches", { n: total })}
          </p>
          {total === 0 ? (
            <p role="status" className="m-0 font-semibold">
              {noFilters ? t("practice.empty_exam") : t("practice.empty_pool")}
            </p>
          ) : null}
          <button
            type="button"
            onClick={start}
            disabled={busy || total === 0}
            className="flex min-h-[60px] items-center justify-center rounded-pill bg-lime-ink font-semibold text-[1.0625rem] text-lime transition-opacity disabled:opacity-50"
          >
            {props.create.isPending ? t("practice.building") : t("practice.start")}
          </button>
        </div>

        <div className="flex flex-col gap-3 rounded-tile border border-violet-line bg-violet-ground p-7">
          <span className="font-mono text-label text-violet-text uppercase">{t("practice.weak_kicker")}</span>
          <p className="m-0 font-display text-[1.375rem] leading-[1.2]">{t("practice.weak_body")}</p>
          <button
            type="button"
            onClick={weakSpot}
            disabled={busy}
            className="min-h-[52px] rounded-pill bg-paper font-semibold text-[0.9375rem] text-ground transition-opacity disabled:opacity-50"
          >
            {props.weak.isPending ? t("practice.weak_building") : t("practice.weak_cta")}
          </button>
        </div>

        {error ? (
          <p role="alert" className="m-0 rounded-block border border-amber px-4 py-3 text-amber">
            {error.message}
          </p>
        ) : null}
      </aside>
    </div>
  );
}

interface BodyProps {
  pool: PoolResponse;
  t: ReturnType<typeof useCopy>;
  mode: Mode;
  setMode: (m: Mode) => void;
  status: Status;
  setStatus: (s: Status) => void;
  difficulty: Difficulty | null;
  setDifficulty: (d: Difficulty | null) => void;
  systems: string[];
  setSystems: (v: string[]) => void;
  disciplines: string[];
  setDisciplines: (v: string[]) => void;
  wanted: number | null;
  setWanted: (n: number) => void;
  create: ReturnType<typeof useCreateBlock>;
  weak: ReturnType<typeof useCreateWeakSpotBlock>;
  nav: ReturnType<typeof useNavigate>;
}

export function BuilderPage({ exam }: { exam?: string }) {
  const t = useCopy();
  // The filters live here so the pool query can follow them; the chips below only report changes.
  const [mode, setMode] = useState<Mode>("tutor");
  const [status, setStatus] = useState<Status>("unused");
  const [difficulty, setDifficulty] = useState<Difficulty | null>(null);
  const [systems, setSystems] = useState<string[]>([]);
  const [disciplines, setDisciplines] = useState<string[]>([]);
  const [wanted, setWanted] = useState<number | null>(null);
  const pool = usePool({ exam, systems, disciplines, status, difficulty });
  const blocks = useBlocks();
  const create = useCreateBlock();
  const weak = useCreateWeakSpotBlock();
  const nav = useNavigate();

  if (pool.isPending && !pool.data) return <Loading />;
  if (!pool.data) {
    const err = pool.error;
    return (
      <section aria-labelledby="practice-h" className="flex flex-col gap-8 pt-6 tablet:pt-10">
        <Headline lead={t("practice.headline")} muted={t("practice.headline_muted")} />
        <ErrorState
          title={t("practice.load_error_title")}
          message={err?.message ?? t("practice.generic_error")}
          retryLabel={t("practice.retry")}
          requestId={err instanceof ApiError ? err.requestId : null}
          onRetry={() => void pool.refetch()}
          retrying={pool.isFetching}
        />
      </section>
    );
  }

  return (
    <section aria-labelledby="practice-h" className="flex flex-col gap-8 pt-6 pb-8 tablet:gap-10 tablet:pt-10">
      <header className="flex flex-wrap items-end justify-between gap-6">
        <Headline lead={t("practice.headline")} muted={t("practice.headline_muted")} />
        <a
          href="#past-blocks"
          className="inline-flex min-h-12 items-center rounded-pill border border-line-control px-5 text-[0.875rem] text-text no-underline hover:bg-surface-2 hover:text-text"
        >
          {t("practice.past_blocks")}
        </a>
      </header>
      <BuilderBody
        {...{
          pool: pool.data,
          t,
          mode,
          setMode,
          status,
          setStatus,
          difficulty,
          setDifficulty,
          systems,
          setSystems,
          disciplines,
          setDisciplines,
          wanted,
          setWanted,
          create,
          weak,
          nav,
        }}
      />
      <PastBlocks query={blocks} />
    </section>
  );
}
