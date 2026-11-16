import type { AgentRuntimeFacts } from "@naadi/shared";
import { Button, Chip, ErrorState, OfflineBanner, Skeleton, Tile, TileLabel } from "@naadi/ui";
import { Activity, AlertTriangle, Check, Clock, Sparkles, X } from "lucide-react";
import { useMe } from "../auth/api";
import { useUiText } from "../copy/useUiText";
import { StatusChip } from "../health/HealthPanel";
import { HealthRequestError, useHealth } from "../health/useHealth";
import { type AgentError, type ReceivedBlock, useAgent } from "./useAgent";

interface PingState extends Record<string, unknown> {
  trigger?: "interactive" | "background" | "cron";
  outcome?: "ok" | "budget_exhausted" | "unavailable";
  recorded_id?: number | null;
}

type T = ReturnType<typeof useUiText>;
type AgentRuntimeCheck = NonNullable<AgentRuntimeFacts["failing"]>[number];

const utcTime = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "UTC" });
const CHIP = "self-start";

function RuntimeStatus({ t }: { t: T }) {
  const q = useHealth();
  if (q.isPending) return <Skeleton className="h-24 w-full" />;
  if (q.isError) {
    return (
      <ErrorState
        title={t("agent.diag.runtime_error_title")}
        message={q.error.message}
        requestId={q.error instanceof HealthRequestError ? q.error.requestId : null}
        onRetry={() => void q.refetch()}
      />
    );
  }
  const rt = q.data.health.components.agent_runtime;
  return (
    <Tile className="flex flex-col gap-3" data-testid="agent-runtime">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <TileLabel>{t("agent.diag.runtime_label")}</TileLabel>
        <StatusChip status={rt ? rt.status : "down"} prefix={`${t("agent.diag.runtime_label")}:`} />
      </div>
      <p className="m-0 text-text-2" data-testid="agent-runtime-summary">
        {rt ? runtimeSummary(rt.agent ?? null, t) : t("agent.diag.runtime_absent")}
      </p>
    </Tile>
  );
}

const CHECK_KEYS: Record<AgentRuntimeCheck, string> = {
  graphs_loaded: "agent.diag.check_graphs_loaded",
  database: "agent.diag.check_database",
  broker: "agent.diag.check_broker",
  cron_scheduler: "agent.diag.check_cron_scheduler",
};

/** The runtime status in words, from the server's structured facts (never its English `detail`). */
export function runtimeSummary(facts: AgentRuntimeFacts | null, t: T): string {
  if (!facts) return t("agent.diag.runtime_unknown");
  const time = facts.last_cron_ping ? `${utcTime.format(new Date(facts.last_cron_ping))} UTC` : "";
  switch (facts.reason) {
    case "ok": {
      const count = facts.graphs?.length ?? 0;
      return t(count === 1 ? "agent.diag.runtime_ok_one" : "agent.diag.runtime_ok_many", {
        count: String(count),
        time,
      });
    }
    case "not_installed":
      return t("agent.diag.runtime_not_installed");
    case "not_hosted":
      return t("agent.diag.runtime_absent");
    case "checks_failed":
      return t("agent.diag.runtime_checks_failed", {
        checks: (facts.failing ?? []).map((c) => t(CHECK_KEYS[c])).join(", "),
      });
    case "no_cron_ping":
      return t("agent.diag.runtime_no_cron_ping");
    case "cron_stale":
      return t("agent.diag.runtime_cron_stale", { time });
  }
}

function PingResult({ block, t }: { block: ReceivedBlock; t: T }) {
  const d = block.data;
  const ok = d.outcome === "ok";
  const rows: Array<[string, string]> = [
    [t("agent.diag.row_outcome"), ok ? t("agent.diag.outcome_ok") : t("agent.diag.outcome_unavailable")],
    [t("agent.diag.row_provider"), typeof d.provider === "string" ? d.provider : "—"],
    [t("agent.diag.row_model"), typeof d.model === "string" ? d.model : "—"],
    [t("agent.diag.row_latency"), typeof d.latency_ms === "number" ? String(d.latency_ms) : "—"],
  ];
  return (
    <Tile className="flex flex-col gap-3" data-testid="agent-block-result">
      <TileLabel>{t("agent.diag.result")}</TileLabel>
      {ok ? null : (
        <>
          <Chip
            tone="warning"
            className={CHIP}
            icon={<AlertTriangle size={14} />}
            srPrefix={`${t("agent.diag.row_outcome")}:`}
          >
            {t("agent.diag.outcome_unavailable")}
          </Chip>
          <p className="m-0 text-text-2">{t("agent.diag.unavailable_detail")}</p>
        </>
      )}
      <table className="w-full border-collapse text-left" data-testid="agent-block-table">
        <thead>
          <tr>
            <th
              scope="col"
              className="border-b border-line-strong py-2 pr-4 font-mono text-label text-text-3 uppercase"
            >
              {t("agent.diag.col_check")}
            </th>
            <th
              scope="col"
              className="border-b border-line-strong py-2 pr-4 font-mono text-label text-text-3 uppercase"
            >
              {t("agent.diag.col_result")}
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([k, v]) => (
            <tr key={k}>
              <th scope="row" className="border-b border-line py-2 pr-4 text-left font-normal">
                {k}
              </th>
              <td className="border-b border-line py-2 pr-4 break-words">{v}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Tile>
  );
}

interface MediaItem {
  kind: "image" | "video";
  source: string;
  title: string;
  link: string;
  license: string;
  credit?: string | null;
  image_url?: string;
  width?: number | null;
  height?: number | null;
  video_id?: string;
}

function isMediaItem(v: unknown): v is MediaItem {
  if (typeof v !== "object" || v === null) return false;
  const m = v as Record<string, unknown>;
  return (m.kind === "image" || m.kind === "video") && typeof m.title === "string" && typeof m.link === "string";
}

/** Media answer block: Commons / PMC images with title, credit and licence; YouTube through the official
 * IFrame player (privacy-enhanced domain, origin + strict-origin-when-cross-origin referrer). */
function MediaBlock({ block, t }: { block: ReceivedBlock; t: T }) {
  const items = (Array.isArray(block.data.items) ? block.data.items : []).filter(isMediaItem);
  if (!items.length) return null;
  return (
    <section
      aria-label={t("agent.diag.media_title")}
      className="flex flex-col gap-4 rounded-block border border-violet-line bg-violet-ground p-4"
      data-testid="agent-block-media"
    >
      <h3 className="m-0 font-mono text-label text-violet-text uppercase">{t("agent.diag.media_title")}</h3>
      <div className="grid grid-cols-1 gap-4 laptop:grid-cols-3">
        {items.map((m) =>
          m.kind === "image" && m.image_url ? (
            <figure
              key={m.link + m.image_url}
              className="m-0 flex min-w-0 flex-col gap-2"
              data-testid={`media-${m.source}`}
            >
              <img
                src={m.image_url}
                alt={m.title}
                loading="lazy"
                decoding="async"
                width={m.width ?? undefined}
                height={m.height ?? undefined}
                className="h-auto max-h-64 w-full rounded-swatch bg-paper object-contain"
              />
              <figcaption className="flex flex-col gap-1 text-[0.8125rem] text-text-2">
                <span className="font-semibold text-text">{m.title}</span>
                <span>{t("agent.diag.media_credit", { credit: m.credit ?? "—", license: m.license })}</span>
                <a href={m.link} target="_blank" rel="noopener noreferrer">
                  {t("agent.diag.media_source")}
                </a>
              </figcaption>
            </figure>
          ) : m.kind === "video" && m.video_id ? (
            <figure key={m.video_id} className="m-0 flex min-w-0 flex-col gap-2" data-testid="media-youtube">
              <iframe
                src={`https://www.youtube-nocookie.com/embed/${encodeURIComponent(m.video_id)}?origin=${encodeURIComponent(window.location.origin)}&rel=0`}
                title={t("agent.diag.video_title", { title: m.title })}
                referrerPolicy="strict-origin-when-cross-origin"
                allow="accelerometer; encrypted-media; gyroscope; picture-in-picture"
                allowFullScreen
                loading="lazy"
                className="aspect-video w-full rounded-swatch border-0"
              />
              <figcaption className="flex flex-col gap-1 text-[0.8125rem] text-text-2">
                <span className="font-semibold text-text">{m.title}</span>
                <span>{t("agent.diag.media_credit", { credit: m.credit ?? "—", license: m.license })}</span>
                <a href={m.link} target="_blank" rel="noopener noreferrer">
                  {t("agent.diag.media_source")}
                </a>
              </figcaption>
            </figure>
          ) : null,
        )}
      </div>
    </section>
  );
}

function errorMessage(err: AgentError, t: T): string {
  if (err.status === 401) return t("agent.diag.error_session");
  if (err.status === 503) return t("agent.diag.error_unavailable");
  return t("agent.diag.error_generic");
}

function PingRun({ t }: { t: T }) {
  const agent = useAgent<PingState>({ assistantId: "ping" });
  const busy = agent.phase === "streaming";
  const recorded = agent.values?.recorded_id;
  const run = () => void agent.start({ trigger: "interactive" });
  return (
    <section aria-labelledby="ping-h" className="flex flex-col gap-4" data-phase={agent.phase} data-testid="ping-panel">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="ping-h" className="m-0 font-display text-title">
          {t("agent.diag.selftest_title")}
        </h2>
        <Button
          variant="primary"
          onClick={run}
          disabled={busy || agent.phase === "offline" || agent.phase === "interrupted"}
        >
          <Activity aria-hidden="true" size={18} /> {t("agent.diag.run")}
        </Button>
      </div>
      <p className="m-0 text-text-2">{t("agent.diag.selftest_intro")}</p>

      {agent.phase === "offline" ? <OfflineBanner message={t("agent.diag.offline")} /> : null}

      {busy || agent.text ? (
        <div role="status" aria-live="polite" className="flex flex-col gap-3">
          {busy ? (
            <Chip tone="assistant" className={CHIP} icon={<Sparkles size={14} />} srPrefix="">
              {t("agent.diag.running")}
            </Chip>
          ) : null}
          {agent.text ? (
            <blockquote
              className="m-0 rounded-block border border-violet-line bg-violet-ground p-4 text-violet-text"
              data-testid="ping-reply"
            >
              {agent.text}
            </blockquote>
          ) : null}
        </div>
      ) : null}

      {agent.blocks.map((b) =>
        b.type === "ping_result" ? (
          <PingResult key={b.id} block={b} t={t} />
        ) : b.type === "media" ? (
          <MediaBlock key={b.id} block={b} t={t} />
        ) : null,
      )}

      {agent.phase === "budget_exhausted" && agent.budget ? (
        <Tile className="flex flex-col gap-2" data-testid="budget-exhausted">
          <Chip tone="warning" className={CHIP} icon={<Clock size={14} />} srPrefix="">
            {t("agent.diag.quota_chip")}
          </Chip>
          <p className="m-0 text-text">
            {t("agent.quota_reset", { time: `${utcTime.format(agent.budget.resetAt)} UTC` })}
          </p>
        </Tile>
      ) : null}

      {agent.phase === "interrupted" ? (
        <Tile className="flex flex-col gap-3 border border-violet-line" data-testid="ping-confirm">
          <p className="m-0 font-semibold">{t("agent.diag.confirm_question")}</p>
          <div className="flex flex-wrap gap-3">
            <Button variant="assistant" onClick={() => void agent.resume({ confirmed: true })}>
              <Check aria-hidden="true" size={18} /> {t("agent.diag.record")}
            </Button>
            <Button variant="secondary" onClick={() => void agent.resume({ confirmed: false })}>
              <X aria-hidden="true" size={18} /> {t("agent.diag.discard")}
            </Button>
          </div>
        </Tile>
      ) : null}

      {agent.phase === "idle" && agent.values?.outcome && agent.values.outcome !== "budget_exhausted" ? (
        <p className="m-0" data-testid="ping-done">
          {recorded ? t("agent.diag.recorded", { id: String(recorded) }) : t("agent.diag.not_recorded")}
        </p>
      ) : null}

      {agent.phase === "error" && agent.error ? (
        <ErrorState
          title={t("agent.diag.error_title")}
          message={errorMessage(agent.error, t)}
          requestId={agent.error.requestId}
          onRetry={run}
        />
      ) : null}
    </section>
  );
}

/** `/admin/agent`: agent runtime status and the `ping` self-test. Admins only (the server enforces it too). */
export function AgentDiagnostics() {
  const me = useMe();
  const t = useUiText();
  if (me.isPending && !me.data) return <Skeleton className="mt-10 h-40 w-full" />;
  if (me.isError && !me.data) {
    return (
      <ErrorState title={t("agent.diag.account_error")} message={me.error.message} onRetry={() => void me.refetch()} />
    );
  }
  if (!me.data?.is_admin) {
    return (
      <section className="flex flex-col gap-3 pt-10">
        <h1 className="m-0 font-display text-headline">{t("agent.diag.not_found_title")}</h1>
        <p className="m-0 text-text-2">{t("agent.diag.not_found_body")}</p>
      </section>
    );
  }
  return (
    <section aria-labelledby="agent-h" className="flex flex-col gap-8 pt-10 pb-24">
      <h1 id="agent-h" className="m-0 font-display text-headline">
        {t("agent.diag.title")}
      </h1>
      <RuntimeStatus t={t} />
      <PingRun t={t} />
    </section>
  );
}
