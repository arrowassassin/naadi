import type { ComponentHealth, HealthResponse, Quota } from "@naadi/shared";
import { Button, Chip, type ChipTone, ErrorState, OfflineBanner, Skeleton, Tile, TileLabel } from "@naadi/ui";
import { AlertTriangle, Check, RefreshCw, X } from "lucide-react";
import type { ReactNode } from "react";
import { HealthRequestError, useHealth } from "./useHealth";
import { useOnlineStatus } from "./useOnlineStatus";

type Status = HealthResponse["status"];
type ComponentName = keyof HealthResponse["components"];

const COMPONENTS: Array<{ key: ComponentName; name: string; what: string }> = [
  { key: "database", name: "Database", what: "Postgres select 1" },
  { key: "storage", name: "Storage", what: "Supabase Storage buckets" },
  { key: "openrouter", name: "OpenRouter", what: "Free-model daily quota" },
  { key: "tavily", name: "Tavily", what: "Web-search credits" },
];

const STATUS_WORD: Record<Status, string> = { ok: "OK", degraded: "Degraded", down: "Down" };
const STATUS_TONE: Record<Status, ChipTone> = { ok: "done", degraded: "warning", down: "critical" };
const STATUS_ICON: Record<Status, ReactNode> = {
  ok: <Check size={14} strokeWidth={2.6} />,
  degraded: <AlertTriangle size={14} strokeWidth={2.4} />,
  down: <X size={14} strokeWidth={2.8} />,
};

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

function ComponentCard({ name, what, c }: { name: string; what: string; c: ComponentHealth }) {
  return (
    <li
      className="flex min-w-0 flex-col gap-3 rounded-block border border-line-soft bg-surface-2 p-5"
      data-component={name.toLowerCase()}
      data-status={c.status}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 flex-col">
          <h3 className="m-0 font-display text-[1.125rem] font-bold">{name}</h3>
          <span className="font-mono text-label-sm text-text-3 uppercase tracking-[0.1em]">{what}</span>
        </div>
        <StatusChip status={c.status} prefix={`${name} status`} />
      </div>
      <p className="m-0 text-ui-sm break-words text-text-2">{c.detail}</p>
      {c.quota ? <QuotaLine quota={c.quota} /> : null}
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
    <ul className="m-0 grid list-none grid-cols-1 gap-3 p-0 tablet:grid-cols-2 laptop:grid-cols-4" aria-hidden="true">
      {COMPONENTS.map((c) => (
        <li key={c.key} className="flex flex-col gap-3 rounded-block border border-line-soft bg-surface-2 p-5">
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

  let body: ReactNode;
  if (result) {
    const { health } = result;
    body = (
      <>
        {result.httpStatus === 503 ? (
          <ErrorState
            title="The database is down"
            message="The API is running but cannot reach Postgres, so saving and loading study data would fail. Other components are listed below."
            requestId={result.requestId}
          />
        ) : null}
        <ul className="m-0 grid list-none grid-cols-1 gap-3 p-0 tablet:grid-cols-2 laptop:grid-cols-4">
          {COMPONENTS.map(({ key, name, what }) => (
            <ComponentCard key={key} name={name} what={what} c={health.components[key]} />
          ))}
        </ul>
        <p className="m-0 font-mono text-label-sm text-text-3">
          Version {health.version} · {health.environment} · checked {formatTime(health.checked_at)}
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
          {result ? <StatusChip status={result.health.status} prefix="Overall status" /> : null}
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
