import type { StageOut, TaskOut } from "@naadi/shared";
import { Button, cx, ErrorState, OfflineBanner, Skeleton, Tile, TileLabel } from "@naadi/ui";
import { Check, ChevronRight, MapPin, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMe } from "../auth/api";
import { useUiText } from "../copy/useUiText";
import { useOnlineStatus } from "../health/useOnlineStatus";
import {
  errorKey,
  overlayCosts,
  overlayTasks,
  useCostsData,
  usePathwayData,
  usePathwaySync,
  usePendingOps,
  useSetStage,
  useTaskWriter,
} from "./api";
import { type CostDraft, CostPanel, type CostRow, CostSheet } from "./CostPanel";
import { formatDate, nextTask, oldestCheck, stageState } from "./format";
import { Markdown } from "./Markdown";
import { Sheet } from "./Sheet";
import { StageCard } from "./StageCard";
import { summarise } from "./summarise";
import { TaskSheet } from "./TaskSheet";
import { VaultPanel } from "./VaultPanel";
import "./pathway.css";

/**
 * The Pathway screen (Pathway board): the whole route from an Indian medical degree to a US residency, stage by
 * stage, with what each task costs, when it was last checked against its official page, and the student's own
 * progress. The single lime action is the next task of the student's current stage. The right-hand column holds the
 * official-source check, the document vault and the cost tracker.
 */
export function PathwayPage() {
  const t = useUiText();
  const online = useOnlineStatus();
  const me = useMe();
  const q = usePathwayData();
  const costs = useCostsData();
  const ops = usePendingOps();
  const sync = usePathwaySync();
  const writeTask = useTaskWriter();
  const setStage = useSetStage();
  const [open, setOpen] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<ReadonlySet<string> | null>(null);
  const [onlyReverify, setOnlyReverify] = useState(false);
  const [guide, setGuide] = useState<string | null>(null);
  const [costDraft, setCostDraft] = useState<CostDraft | null>(null);
  const railRef = useRef<HTMLOListElement>(null);

  const payload = q.data?.data ?? null;
  const stages = useMemo(() => (payload ? overlayTasks(payload.stages, ops) : []), [payload, ops]);
  const allTasks = useMemo(() => stages.flatMap((s) => s.tasks.map((task) => ({ task, stage: s }))), [stages]);
  const pendingTasks = useMemo(
    () => new Set(ops.filter((o) => o.entity === "user_pathway_task").map((o) => o.key)),
    [ops],
  );
  const stageOfTask = useCallback(
    (id: string) => allTasks.find((x) => x.task.id === id)?.stage.key ?? null,
    [allTasks],
  );
  const rows: CostRow[] = useMemo(
    () => overlayCosts(costs.data?.data.items ?? [], ops, stageOfTask),
    [costs.data, ops, stageOfTask],
  );
  const totals = useMemo(() => summarise(stages, rows), [stages, rows]);
  const currencies = costs.data?.data.currencies ?? [];

  const current = payload?.current_stage ?? null;
  const currentStage = stages.find((s) => s.key === current);
  const next = nextTask(currentStage);
  const reverifyCount = allTasks.filter((x) => x.task.reverify).length;
  const oldest = oldestCheck(stages);
  const isAdmin = me.data?.is_admin === true;

  const isOpen = (s: StageOut) => (expanded ?? new Set(current ? [current] : [])).has(s.key);
  const toggle = (key: string) => {
    const base = new Set(expanded ?? (current ? [current] : []));
    if (base.has(key)) base.delete(key);
    else base.add(key);
    setExpanded(base);
  };
  const reveal = (key: string) => {
    setExpanded((prev) => new Set([...(prev ?? (current ? [current] : [])), key]));
    window.requestAnimationFrame(() =>
      document.getElementById(`stage-${key}`)?.scrollIntoView({ block: "start", behavior: "smooth" }),
    );
  };

  // The route rail keeps the current stage in view.
  // biome-ignore lint/correctness/useExhaustiveDependencies: only when the current stage changes
  useEffect(() => {
    const el = railRef.current?.querySelector<HTMLElement>("[data-rail-current='true']");
    if (el && railRef.current) railRef.current.scrollLeft = Math.max(0, el.offsetLeft - 16);
  }, [current]);

  const toggleDone = (task: TaskOut) => writeTask(task.id, { status: task.status === "done" ? "todo" : "done" });
  const openTask = open ? allTasks.find((x) => x.task.id === open) : undefined;
  const guideStage = guide ? stages.find((s) => s.key === guide) : undefined;

  if (q.isPending) {
    return (
      <div aria-busy="true" className="flex flex-col gap-6 pt-10" data-state="loading">
        <Skeleton className="h-40 w-full rounded-tile" />
        <Skeleton className="h-64 w-full rounded-tile" />
      </div>
    );
  }
  if (q.isError || !payload) {
    return (
      <div className="pt-10" data-state="error">
        <ErrorState
          title={t("pathway.error_title")}
          message={t(errorKey(q.error))}
          onRetry={() => void q.refetch()}
          retryLabel={t("pathway.retry")}
          retrying={q.isFetching}
        />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-8 pt-6 tablet:pt-10" data-page="pathway">
      {!online ? <OfflineBanner message={t("pathway.offline")} /> : null}
      {q.data?.fromCache && online ? <p className="m-0 text-text-3 text-ui-sm">{t("pathway.saved_copy")}</p> : null}
      {sync.refused.length > 0 ? (
        <p role="alert" className="m-0 text-amber text-ui-sm" data-refused="true">
          {t("pathway.refused", { n: String(sync.refused.length) })}
        </p>
      ) : null}

      <header className="flex flex-wrap items-end justify-between gap-x-8 gap-y-5">
        <h1 className="m-0 font-display font-extrabold text-[clamp(3rem,9vw,8rem)] leading-[0.88] tracking-[-0.05em]">
          {t("pathway.headline_a")}
          <br />
          <span className="text-text-muted">{t("pathway.headline_b")}</span> {t("pathway.headline_c")}
        </h1>
        <p className="m-0 max-w-[26rem] text-text-2 text-ui">{t("pathway.intro")}</p>
      </header>

      <nav aria-label={t("pathway.route_label")} className="min-w-0">
        <ol
          ref={railRef}
          className="pathway-rail relative m-0 flex list-none gap-2 overflow-x-auto p-0 pb-3" /* scroll region is focusable below */
        >
          {stages.map((s, i) => {
            const st = stageState(s, current);
            return (
              <li key={s.key} data-rail-current={st === "current"} className="shrink-0">
                <button
                  type="button"
                  onClick={() => reveal(s.key)}
                  aria-current={st === "current" ? "step" : undefined}
                  className={cx(
                    "flex min-h-12 items-center gap-2 rounded-pill border px-4 text-ui-sm",
                    st === "current"
                      ? "border-text bg-surface-2 font-semibold text-text"
                      : st === "done"
                        ? "border-line-soft bg-surface-2 text-text-2"
                        : "border-dashed border-line-dashed bg-transparent text-text-2",
                  )}
                >
                  <span className="font-mono text-text-3">{i + 1}</span>
                  <span className="max-w-[12rem] truncate">{s.title}</span>
                  {st === "done" ? (
                    <Check aria-hidden="true" size={14} />
                  ) : st === "current" ? (
                    <MapPin aria-hidden="true" size={14} />
                  ) : null}
                  <span className="sr-only">{t(`pathway.stage_state.${st}`)}</span>
                </button>
              </li>
            );
          })}
        </ol>
      </nav>

      {next && currentStage ? (
        <Tile
          as="section"
          aria-labelledby="next-h"
          className="flex flex-wrap items-center justify-between gap-5"
          data-panel="next"
        >
          <div className="flex min-w-0 flex-col gap-2">
            <h2 id="next-h" className="m-0">
              <TileLabel>{t("pathway.next_label", { stage: currentStage.title })}</TileLabel>
            </h2>
            <p className="m-0 font-display font-bold text-[1.5rem] leading-tight tracking-[-0.02em]">{next.title}</p>
          </div>
          <Button
            variant="primary"
            size="cta"
            icon={<ChevronRight size={18} />}
            onClick={() => setOpen(next.id)}
            data-cta="next-task"
          >
            {t("pathway.next_cta")}
          </Button>
        </Tile>
      ) : null}

      <div className="flex flex-wrap items-start gap-5">
        <ol
          className="m-0 flex min-w-0 flex-[2_1_34rem] list-none flex-col gap-3 p-0"
          aria-label={t("pathway.stages_label")}
        >
          {stages.map((s, i) => {
            const st = stageState(s, current);
            const list = onlyReverify ? s.tasks.filter((x) => x.reverify) : s.tasks;
            if (onlyReverify && list.length === 0) return null;
            return (
              <StageCard
                key={s.key}
                stage={s}
                number={i + 1}
                state={st}
                expanded={onlyReverify ? true : isOpen(s)}
                onToggle={() => toggle(s.key)}
                tasks={list}
                pendingTasks={pendingTasks}
                onOpenTask={setOpen}
                onToggleDone={toggleDone}
                onOpenGuide={setGuide}
                onSetCurrent={(key) => setStage.mutate(key)}
                canSetCurrent={online && !setStage.isPending}
                setCurrentReason={online ? null : t("pathway.needs_connection")}
              />
            );
          })}
          {onlyReverify && reverifyCount === 0 ? (
            <li className="list-none text-text-2">{t("pathway.nothing_to_reverify")}</li>
          ) : null}
        </ol>

        <aside className="flex min-w-0 flex-[1_1_20rem] flex-col gap-5" aria-label={t("pathway.side_label")}>
          <Tile
            as="section"
            variant="assistant"
            aria-labelledby="check-h"
            className="flex flex-col gap-3"
            data-panel="check"
          >
            <h2 id="check-h" className="m-0">
              <TileLabel variant="assistant">{t("pathway.check_label")}</TileLabel>
            </h2>
            <p className="m-0 font-display text-[1.5rem] leading-tight">
              {oldest ? t("pathway.check_line", { date: formatDate(oldest) }) : t("pathway.check_none")}{" "}
              {reverifyCount > 0
                ? t("pathway.check_reverify", { n: String(reverifyCount) })
                : t("pathway.check_all_current", { days: String(payload.reverify_days) })}
            </p>
            {reverifyCount > 0 ? (
              <Button
                variant="strong"
                icon={<RefreshCw size={16} />}
                onClick={() => setOnlyReverify((v) => !v)}
                aria-pressed={onlyReverify}
              >
                {t(onlyReverify ? "pathway.show_all" : "pathway.show_changed")}
              </Button>
            ) : null}
            {isAdmin ? <p className="m-0 text-text-3 text-ui-sm">{t("pathway.admin_hint")}</p> : null}
          </Tile>
          <VaultPanel />
          <CostPanel
            stages={stages}
            rows={rows}
            totals={totals}
            currencies={currencies}
            loading={costs.isPending}
            fromCache={costs.data?.fromCache ?? false}
            onAdd={() => setCostDraft({ id: null, taskId: null, stageKey: current })}
            onEdit={(id) => setCostDraft({ id, taskId: null, stageKey: null })}
          />
        </aside>
      </div>

      <TaskSheet
        task={openTask?.task ?? null}
        stageTitle={openTask?.stage.title ?? ""}
        onClose={() => setOpen(null)}
        onChange={writeTask}
        onAddCost={(taskId) => {
          setOpen(null);
          setCostDraft({ id: null, taskId, stageKey: null });
        }}
        planned={rows
          .filter((r) => r.task_id === open)
          .map((r) => ({ currency: r.currency, amount: r.amount, paid: Boolean(r.paid_on) }))}
        pending={open !== null && pendingTasks.has(open)}
      />
      <CostSheet
        draft={costDraft}
        stages={stages}
        rows={rows}
        currencies={currencies}
        onClose={() => setCostDraft(null)}
      />
      <Sheet
        open={guideStage !== undefined}
        onClose={() => setGuide(null)}
        title={guideStage?.guide?.title ?? ""}
        wide
        handle="guide"
      >
        {guideStage?.guide ? <Markdown source={guideStage.guide.body_md} /> : null}
      </Sheet>
      {setStage.isError ? (
        <p role="alert" className="m-0 text-amber text-ui-sm">
          {t(errorKey(setStage.error))}
        </p>
      ) : null}
    </div>
  );
}
