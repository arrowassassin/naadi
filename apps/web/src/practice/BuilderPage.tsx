import type { BlockSummary, PoolFacet, PoolResponse } from "@naadi/shared";
import { Chip, cx, ErrorState, Skeleton } from "@naadi/ui";
import type { UseQueryResult } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { Minus, Plus } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { ApiError } from "../auth/apiError";
import { CardsDueLink } from "../cards/hooks";
import { useBlocks, useCreateBlock, useCreateWeakSpotBlock, usePool } from "./api";
import { useCopy } from "./copy";
import { resumeIndex, shortDate } from "./format";
import {
  type BuilderRules,
  examOfMock as findExamOfMock,
  type MockId,
  maxSections,
  mocksOf,
  rulesOf,
  type StartableMock,
  sectionsThatFit,
  useCreateMockBlock,
} from "./timed";

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

/** Blocks of a timed/exam session (segment 09): `count` stays the questions per block. */
function SectionsGroup(props: {
  t: ReturnType<typeof useCopy>;
  most: number;
  fit: number;
  sections: number;
  setSections: (n: number) => void;
  count: number;
  minutes: number | null;
  breakMinutes: number | null;
}) {
  const { t } = props;
  const reasonId = useId();
  const chips = Array.from({ length: props.most }, (_, i) => i + 1);
  return (
    <Group title={t("practice.sections_group")}>
      {chips.map((n) => (
        <Chipbutton
          key={n}
          on={props.sections === n}
          disabled={n > props.fit}
          describedBy={reasonId}
          onClick={() => props.setSections(n)}
        >
          {String(n)}
        </Chipbutton>
      ))}
      <p className="m-0 basis-full text-text-2 text-ui-sm">
        {props.minutes === null
          ? t("practice.sections_hint_untimed", { sections: props.sections, count: props.count })
          : t("practice.sections_hint", { sections: props.sections, count: props.count, minutes: props.minutes })}
        {props.sections > 1 && props.breakMinutes !== null
          ? ` ${t("practice.sections_break", { minutes: props.breakMinutes })}`
          : ""}
      </p>
      {props.fit < props.most ? (
        <p id={reasonId} className="m-0 basis-full text-text-3 text-ui-sm">
          {t("practice.sections_not_enough")}
        </p>
      ) : null}
    </Group>
  );
}

/** The seeded mock exams of the selected exam; each starts an exam-mode session in its fixed order. */
function MockExamsGroup(props: {
  t: ReturnType<typeof useCopy>;
  mocks: StartableMock[];
  available: boolean;
  starting: MockId | null;
  busy: boolean;
  focus: MockId | undefined;
  start: (id: MockId) => void;
}) {
  const { t } = props;
  const reasonId = useId();
  const focused = useRef(false);
  const refs = useRef(new Map<string, HTMLButtonElement>());
  const { focus, mocks } = props;
  useEffect(() => {
    // From the Courses page (`?mock=`): bring the mock into view and focus it; it never starts by itself.
    if (focused.current || !focus || !mocks.some((m) => m.id === focus)) return;
    const el = refs.current.get(focus);
    if (!el) return;
    focused.current = true;
    el.scrollIntoView?.({ block: "center" });
    el.focus({ preventScroll: true });
  }, [focus, mocks]);
  return (
    <fieldset
      id="mock-exams"
      className="m-0 flex min-w-0 scroll-mt-24 flex-col gap-3.5 rounded-tile border border-line-soft bg-surface-1 p-6 tablet:p-7"
    >
      <legend className="float-left p-0 font-bold font-display text-[1.5rem] tracking-[-0.02em]">
        {t("practice.mock_group")}
      </legend>
      <div className="clear-both flex flex-col gap-2">
        {props.mocks.map((m) => {
          const disabled = !props.available || props.busy;
          return (
            <button
              key={m.id}
              ref={(el) => {
                if (el) refs.current.set(m.id, el);
                else refs.current.delete(m.id);
              }}
              type="button"
              data-mock={m.id}
              aria-disabled={disabled || undefined}
              aria-describedby={props.available ? undefined : reasonId}
              onClick={disabled ? undefined : () => props.start(m.id)}
              className={cx(
                "flex min-h-14 w-full flex-col items-start gap-0.5 rounded-block px-5 py-3 text-left transition-colors duration-(--duration-control) ease-out",
                props.available
                  ? "border border-line-control bg-transparent text-text hover:bg-surface-2"
                  : CHIP_DISABLED,
                props.busy && props.available && "opacity-60",
              )}
            >
              <span className="font-semibold">{props.starting === m.id ? t("practice.mock_building") : m.title}</span>
              <span className="text-text-3 text-ui-sm">{t("practice.mock_meta", { count: m.question_count })}</span>
            </button>
          );
        })}
        {props.available ? null : (
          <p id={reasonId} className="m-0 text-text-3 text-ui-sm">
            {t("practice.mock_unavailable")}
          </p>
        )}
      </div>
    </fieldset>
  );
}

/** A refusal the student can act on, in ui_copy words when its code has some. */
const TIMED_CODES = new Set(["exam_active", "timed_unavailable", "sections_out_of_range", "tutor_single_section"]);

function errorText(t: ReturnType<typeof useCopy>, err: Error): string {
  if (err instanceof ApiError && err.code === "mock_not_found") return t("practice.mock_not_found");
  // S09 codes (the server sends no sentence for these): worded here with ui_copy.
  if (err instanceof ApiError && err.code && TIMED_CODES.has(err.code)) return t(`practice.error.${err.code}`);
  return err.message;
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
  const error = (props.create.error ?? props.weak.error ?? props.mock.error) as Error | null;
  const busy = props.create.isPending || props.weak.isPending || props.mock.isPending;
  const timed = props.mode !== "tutor";
  const rules = props.rules;
  const most = maxSections(rules, pool.exam);
  const fit = sectionsThatFit(total, count, most);
  const sections = timed ? Math.min(props.sections, fit) : 1;
  const perQuestion = timed ? (rules?.modes[props.mode]?.seconds_per_question ?? null) : null;
  const examModeOn = pool.modes.some((m) => m.id === "exam" && m.available);

  // nothing at all to practise (not just filtered out): a different message from "widen the filters"
  const noFilters =
    props.systems.length === 0 && props.disciplines.length === 0 && props.difficulty === null && pool.status.all === 0;

  const startMock = (id: MockId) =>
    props.mock.mutate(id, {
      onSuccess: (b) => void props.nav({ to: "/practice/blocks/$blockId/$n", params: { blockId: b.id, n: 1 } }),
    });

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
        ...(timed ? { sections } : {}),
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
        {timed ? (
          <SectionsGroup
            t={t}
            most={most}
            fit={fit}
            sections={sections}
            setSections={props.setSections}
            count={count}
            minutes={perQuestion === null ? null : Math.round((count * perQuestion) / 60)}
            breakMinutes={rules?.break_minutes ?? null}
          />
        ) : null}
        {props.mocks.length ? (
          <MockExamsGroup
            t={t}
            mocks={props.mocks}
            available={examModeOn}
            starting={props.mock.isPending ? (props.mock.variables ?? null) : null}
            busy={busy}
            focus={props.focusMock}
            start={startMock}
          />
        ) : null}
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
            {sections > 1 ? ` · ${t("practice.sections_summary", { sections, total: sections * count })}` : ""}.{" "}
            {total === 1 ? t("practice.matches_one") : t("practice.matches", { n: total })}
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
            {errorText(t, error)}
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
  sections: number;
  setSections: (n: number) => void;
  rules: BuilderRules | undefined;
  mocks: StartableMock[];
  mock: ReturnType<typeof useCreateMockBlock>;
  focusMock: MockId | undefined;
}

export function BuilderPage({ exam, mock }: { exam?: string; mock?: MockId }) {
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
  const [sections, setSections] = useState(1);
  const rules = { data: rulesOf(pool.data) };
  const mocks = { data: mocksOf(pool.data) };
  const mockStart = useCreateMockBlock();
  // A `?mock=` link without an exam (the Courses page): find the exam that lists the mock, then show that one.
  const examOfMock = exam === undefined ? findExamOfMock(mock, pool.data) : null;
  const shownExam = pool.data?.exam;
  useEffect(() => {
    if (exam !== undefined || !mock || !examOfMock || !shownExam || examOfMock === shownExam) return;
    void nav({ to: "/practice", search: { exam: examOfMock, mock }, replace: true });
  }, [exam, mock, examOfMock, shownExam, nav]);

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
        <span className="flex flex-wrap items-center gap-3">
          <CardsDueLink className="inline-flex min-h-12 items-center gap-2 rounded-pill border border-line-control px-5 text-[0.875rem] text-text no-underline hover:bg-surface-2 hover:text-text" />
          <a
            href="#past-blocks"
            className="inline-flex min-h-12 items-center rounded-pill border border-line-control px-5 text-[0.875rem] text-text no-underline hover:bg-surface-2 hover:text-text"
          >
            {t("practice.past_blocks")}
          </a>
        </span>
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
          sections,
          setSections,
          rules: rules.data,
          mocks: mocks.data ?? [],
          mock: mockStart,
          focusMock: mock,
        }}
      />
      <PastBlocks query={blocks} />
    </section>
  );
}
