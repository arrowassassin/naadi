import type { ComponentHealth, HealthResponse, ModelProbe, Quota } from "@naadi/shared";
import { Button, Chip, type ChipTone, ErrorState, OfflineBanner, Skeleton, Tile, TileLabel } from "@naadi/ui";
import { AlertTriangle, Check, RefreshCw, X } from "lucide-react";
import type { ReactNode } from "react";
import { HealthRequestError, useHealth } from "./useHealth";
import { useOnlineStatus } from "./useOnlineStatus";

type Status = HealthResponse["status"];
// `agent_runtime` is optional (only the Aegra host reports it) and has its own panel at /admin/agent.
// `generation` is optional too: it shows "Not configured" until HF_GEN_TOKEN is set (segment 15b).
type ComponentName = Exclude<keyof HealthResponse["components"], "agent_runtime" | "generation">;

/** Display order: infrastructure first, then the two LLM providers side by side (NVIDIA spans two columns
 * from tablet up so its per-model list has room and the grid stays full). */
const COMPONENTS: Array<{ key: ComponentName; name: string; what: string; wide?: boolean }> = [
  { key: "database", name: "Database", what: "Postgres select 1" },
  { key: "storage", name: "Storage", what: "Supabase Storage buckets" },
  { key: "tavily", name: "Tavily", what: "Web-search credits" },
  { key: "openrouter", name: "OpenRouter", what: "Free-model daily quota" },
  { key: "nvidia", name: "NVIDIA", what: "Model probes, hourly", wide: true },
];

const GENERATION_CARD = { name: "Generation", what: "naadi-gen ZeroGPU Space" };

const STATUS_WORD: Record<Status, string> = { ok: "OK", degraded: "Degraded", down: "Down" };
const STATUS_TONE: Record<Status, ChipTone> = { ok: "done", degraded: "warning", down: "critical" };
const STATUS_ICON: Record<Status, ReactNode> = {
  ok: <Check size={14} strokeWidth={2.6} />,
  degraded: <AlertTriangle size={14} strokeWidth={2.4} />,
  down: <X size={14} strokeWidth={2.8} />,
};

const GRID = "m-0 grid list-none grid-cols-1 gap-3 p-0 tablet:grid-cols-2 laptop:grid-cols-3";

const timeFmt = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });

function formatTime(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : timeFmt.format(d);
}

export function StatusChip({ status, prefix }: { status: Status; prefix: string }) {
  return (
    <Chip tone={STATUS_TONE[status]} icon={STATUS_ICON[status]} srPrefix={prefix}>
      {STATUS_WORD[status]}
    </Chip>
  );
}

function QuotaLine({ quota }: { quota: Quota }) {
  const cap = quota.guard ?? quota.limit;
  const unit = quota.unit === "requests" ? "requests" : "credits";
  const period = quota.period === "day" ? "today" : "this month";
  const pct = cap ? Math.min(100, Math.round((quota.used / cap) * 100)) : 0;
  const atGuard = cap !== null && cap !== undefined && quota.used >= cap;
  return (
    <div className="flex flex-col gap-1.5">
      <p className="m-0 text-ui-sm text-text-2">
        <span className="font-semibold text-text">{quota.used}</span>
        {cap !== null && cap !== undefined ? ` of ${cap}` : ""} {unit} used {period}
        {quota.guard !== null && quota.guard !== undefined && quota.limit !== null && quota.guard !== quota.limit
          ? ` (provider limit ${quota.limit})`
          : ""}
      </p>
      {cap ? (
        <span aria-hidden="true" className="block h-1.5 overflow-hidden rounded-pill bg-surface-1">
          <span
            className={atGuard ? "block h-full rounded-pill bg-amber" : "block h-full rounded-pill bg-text-2"}
            style={{ width: `${pct}%` }}
          />
        </span>
      ) : null}
    </div>
  );
}

function ModelList({ models }: { models: ModelProbe[] }) {
  return (
    <ul className="m-0 flex list-none flex-col gap-2 p-0" aria-label="Configured models">
      {models.map((m) => (
        <li
          key={`${m.provider}:${m.model}`}
          className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5"
          data-model={m.model}
          data-ok={m.ok}
        >
          <span className="min-w-0 font-mono text-label-sm break-all text-text-2">{m.model}</span>
          <span
            className={
              m.ok
                ? "inline-flex shrink-0 items-center gap-1 text-ui-sm text-text"
                : "inline-flex shrink-0 items-center gap-1 text-ui-sm font-semibold text-amber"
            }
          >
            {m.ok ? (
              <Check size={13} strokeWidth={2.6} aria-hidden="true" />
            ) : (
              <X size={13} strokeWidth={2.8} aria-hidden="true" />
            )}
            {m.ok ? `Answers${m.latency_ms === null ? "" : ` · ${Math.round(m.latency_ms)} ms`}` : `Fails: ${m.detail}`}
          </span>
        </li>
      ))}
    </ul>
  );
}

function ComponentCard({ name, what, c, wide }: { name: string; what: string; c: ComponentHealth; wide?: boolean }) {
  return (
    <li
      className={`flex min-w-0 flex-col gap-3 rounded-block border border-line-soft bg-surface-2 p-5${wide ? " tablet:col-span-2" : ""}`}
      data-component={name.toLowerCase()}
      data-status={c.status}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 flex-col">
          <h3 className="m-0 font-display text-[1.125rem] font-bold">{name}</h3>
          <span className="font-mono text-label-sm text-text-3 uppercase tracking-[0.1em]">{what}</span>
        </div>
        {c.configured === false && c.status === "ok" ? (
          <Chip tone="meta" icon={<X size={14} strokeWidth={2.8} />} srPrefix={`${name} status`}>
            Not configured
          </Chip>
        ) : (
          <StatusChip status={c.status} prefix={`${name} status`} />
        )}
      </div>
      <p className="m-0 text-ui-sm break-words text-text-2">{c.detail}</p>
      {c.generation?.state === "paused" && c.generation.resets_at ? (
        <p className="m-0 text-ui-sm font-semibold text-amber">
          Quota paused until {formatTime(c.generation.resets_at)}
        </p>
      ) : null}
      {c.quota ? <QuotaLine quota={c.quota} /> : null}
      {c.models && c.models.length > 0 ? <ModelList models={c.models} /> : null}
      <dl className="m-0 mt-auto flex flex-wrap gap-x-4 gap-y-1 font-mono text-label-sm text-text-3">
        <div className="flex gap-1.5">
          <dt>Latency</dt>
          <dd className="m-0 text-text-2">{c.latency_ms === null ? "n/a" : `${Math.round(c.latency_ms)} ms`}</dd>
        </div>
        <div className="flex gap-1.5">
          <dt>Checked</dt>
          <dd className="m-0 text-text-2">
            {formatTime(c.checked_at)}
            {c.cached ? " (cached)" : ""}
          </dd>
        </div>
      </dl>
    </li>
  );
}

function LoadingCards() {
  return (
    <ul className={GRID} aria-hidden="true">
      {COMPONENTS.map((c) => (
        <li
          key={c.key}
          className={`flex flex-col gap-3 rounded-block border border-line-soft bg-surface-2 p-5${c.wide ? " tablet:col-span-2" : ""}`}
        >
          <div className="flex items-center justify-between gap-3">
            <Skeleton className="h-5 w-28" />
            <Skeleton className="h-6 w-16 rounded-pill" />
          </div>
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-3/4" />
          <Skeleton className="mt-2 h-3 w-1/2" />
        </li>
      ))}
    </ul>
  );
}

export function HealthPanel() {
  const online = useOnlineStatus();
  const q = useHealth();
  const result = q.data;
  const error = q.error;
  const checking = q.isFetching;

  // TanStack Query keeps the last data when a refetch fails: show that failure next to the old result.
  const failed = error instanceof HealthRequestError ? error : null;
  const stale = Boolean(result && error);

  let body: ReactNode;
  if (result) {
    const { health } = result;
    body = (
      <>
        {stale ? (
          <ErrorState
            title="The latest check failed"
            message={`${failed?.message ?? "Something went wrong while checking the API."} The results below are from the last successful check at ${formatTime(health.checked_at)} and may be out of date.`}
            requestId={failed?.requestId}
            onRetry={() => q.refetch()}
            retrying={checking}
          />
        ) : null}
        {result.httpStatus === 503 ? (
          <ErrorState
            title="The database is down"
            message="The API is running but cannot reach Postgres, so saving and loading study data would fail. Other components are listed below."
            requestId={result.requestId}
          />
        ) : null}
        <ul className={GRID} data-stale={stale} aria-label={stale ? "Last known component status" : undefined}>
          {COMPONENTS.map(({ key, name, what, wide }) => (
            <ComponentCard key={key} name={name} what={what} wide={wide} c={health.components[key]} />
          ))}
          {health.components.generation ? (
            <ComponentCard key="generation" {...GENERATION_CARD} c={health.components.generation} />
          ) : null}
        </ul>
        <p className="m-0 font-mono text-label-sm text-text-3">
          {stale ? "Stale · " : ""}Version {health.version} · {health.environment} · last checked{" "}
          {formatTime(health.checked_at)}
        </p>
      </>
    );
  } else if (error) {
    const e = error instanceof HealthRequestError ? error : null;
    body = (
      <ErrorState
        title="System health could not be loaded"
        message={e?.message ?? "Something went wrong while checking the API."}
        requestId={e?.requestId}
        onRetry={() => q.refetch()}
        retrying={checking}
      />
    );
  } else if (!online) {
    body = <p className="m-0 text-text-2">Health checks start as soon as you are back online.</p>;
  } else {
    body = (
      <>
        <p className="sr-only" role="status">
          Checking system health…
        </p>
        <LoadingCards />
      </>
    );
  }

  return (
    <Tile as="section" aria-labelledby="health-h" aria-busy={q.isPending && online} className="flex flex-col gap-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex flex-col gap-2">
          <TileLabel>Live · /api/health</TileLabel>
          <h2 id="health-h" className="m-0 font-display text-[1.75rem] leading-tight font-bold tracking-[-0.02em]">
            System health
          </h2>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          {result && stale ? (
            <Chip tone="warning" icon={<AlertTriangle size={14} strokeWidth={2.4} />} srPrefix="Overall status">
              Stale
            </Chip>
          ) : null}
          {result && !stale ? <StatusChip status={result.health.status} prefix="Overall status" /> : null}
          {result ? (
            <Button
              variant="secondary"
              onClick={() => q.refetch()}
              disabled={checking || !online}
              icon={<RefreshCw size={16} className={checking ? "animate-spin" : undefined} />}
            >
              {checking ? "Checking…" : "Check again"}
            </Button>
          ) : null}
        </div>
      </div>
      {online ? null : (
        <OfflineBanner
          message={
            result
              ? `Showing the last result from ${formatTime(result.health.checked_at)}. Checks resume when you reconnect.`
              : "Checks resume when you reconnect."
          }
        />
      )}
      {body}
    </Tile>
  );
}
