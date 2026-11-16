import { Button, Chip, ErrorState, OfflineBanner, Skeleton, Tile, TileLabel } from "@naadi/ui";
import { Activity, Check, Clock, Sparkles, X } from "lucide-react";
import { useMe } from "../auth/api";
import { StatusChip } from "../health/HealthPanel";
import { useHealth } from "../health/useHealth";
import { useUiCopy } from "./uiCopy";
import { type AgentBlock, useAgent } from "./useAgent";

interface PingState extends Record<string, unknown> {
  trigger?: "interactive" | "background" | "cron";
  outcome?: "ok" | "budget_exhausted" | "unavailable";
  provider?: string | null;
  model?: string | null;
  latency_ms?: number | null;
  recorded_id?: number | null;
  confirmed?: boolean | null;
}

const utcTime = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "UTC" });

function RuntimeStatus() {
  const q = useHealth();
  if (q.isPending) return <Skeleton className="h-24 w-full" />;
  if (q.isError)
    return <ErrorState title="Runtime status unavailable" message={q.error.message} onRetry={() => void q.refetch()} />;
  const rt = q.data.health.components.agent_runtime;
  return (
    <Tile className="flex flex-col gap-3" data-testid="agent-runtime">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <TileLabel>Agent runtime</TileLabel>
        {rt ? (
          <StatusChip status={rt.status} prefix="Agent runtime:" />
        ) : (
          <StatusChip status="down" prefix="Agent runtime:" />
        )}
      </div>
      <p className="m-0 text-text-2">{rt ? rt.detail : "This server does not host the agent runtime."}</p>
    </Tile>
  );
}

function BlockTable({ block }: { block: AgentBlock }) {
  const columns = Array.isArray(block.data.columns) ? (block.data.columns as string[]) : [];
  const rows = Array.isArray(block.data.rows) ? (block.data.rows as string[][]) : [];
  return (
    <table className="w-full border-collapse text-left" data-testid="agent-block-table">
      <thead>
        <tr>
          {columns.map((c) => (
            <th
              key={c}
              scope="col"
              className="border-b border-line-strong py-2 pr-4 font-mono text-label text-text-3 uppercase"
            >
              {c}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.join("|")}>
            {columns.map((c, ci) => (
              <td key={c} className="border-b border-line py-2 pr-4 break-words">
                {r[ci] ?? ""}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function PingRun() {
  const agent = useAgent<PingState>({ assistantId: "ping" });
  const copy = useUiCopy();
  const busy = agent.phase === "streaming";
  const recorded = agent.values?.recorded_id;
  return (
    <section aria-labelledby="ping-h" className="flex flex-col gap-4" data-phase={agent.phase} data-testid="ping-panel">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="ping-h" className="m-0 font-display text-title">
          Runtime self-test
        </h2>
        <Button
          variant="primary"
          onClick={() => void agent.start({ trigger: "interactive" })}
          disabled={busy || agent.phase === "offline" || agent.phase === "interrupted"}
        >
          <Activity aria-hidden="true" size={18} /> Run ping
        </Button>
      </div>
      <p className="m-0 text-text-2">
        Streams one answer from the router model, shows a result block, then asks before recording it in the health log.
      </p>

      {agent.phase === "offline" ? <OfflineBanner message="The self-test needs a connection." /> : null}

      {/* Rendered only with content: an empty zero-height live region crashed Firefox's axe contrast scan. */}
      {busy || agent.text ? (
        <div role="status" aria-live="polite" className="flex flex-col gap-3">
          {busy ? (
            <Chip tone="assistant" icon={<Sparkles size={14} />} srPrefix="Agent:">
              Running
            </Chip>
          ) : null}
          {agent.text ? (
            <blockquote
              className="m-0 rounded-block border border-assistant/40 bg-assistant/10 p-4 text-text"
              data-testid="ping-reply"
            >
              {agent.text}
            </blockquote>
          ) : null}
        </div>
      ) : null}

      {agent.blocks.map((b) =>
        b.type === "table" ? (
          <Tile key={b.id} className="flex flex-col gap-2">
            <TileLabel>Result</TileLabel>
            <BlockTable block={b} />
          </Tile>
        ) : null,
      )}

      {agent.phase === "budget_exhausted" && agent.budget ? (
        <Tile className="flex flex-col gap-2" data-testid="budget-exhausted">
          <Chip tone="warning" icon={<Clock size={14} />} srPrefix="AI quota:">
            Quota used
          </Chip>
          <p className="m-0 text-text">
            {copy("assistant.quota_reset", { time: utcTime.format(agent.budget.resetAt) }) ?? (
              <time dateTime={agent.budget.resetAt.toISOString()}>{utcTime.format(agent.budget.resetAt)} UTC</time>
            )}
          </p>
        </Tile>
      ) : null}

      {agent.phase === "interrupted" ? (
        <Tile className="flex flex-col gap-3 border border-assistant/40" data-testid="ping-confirm">
          <p className="m-0 font-semibold">{String(agent.interrupt?.question ?? "")}</p>
          <div className="flex flex-wrap gap-3">
            <Button variant="assistant" onClick={() => void agent.resume({ confirmed: true })}>
              <Check aria-hidden="true" size={18} /> Record
            </Button>
            <Button variant="secondary" onClick={() => void agent.resume({ confirmed: false })}>
              <X aria-hidden="true" size={18} /> Discard
            </Button>
          </div>
        </Tile>
      ) : null}

      {agent.phase === "idle" && agent.values?.outcome ? (
        <p className="m-0" data-testid="ping-done">
          {recorded ? `Recorded as health log entry ${recorded}.` : "Not recorded."}
        </p>
      ) : null}

      {agent.phase === "error" && agent.error ? (
        <ErrorState
          title="The self-test failed"
          message={agent.error}
          onRetry={() => void agent.start({ trigger: "interactive" })}
        />
      ) : null}
    </section>
  );
}

/** `/admin/agent`: agent runtime status and the `ping` self-test. Admins only (the server enforces it too). */
export function AgentDiagnostics() {
  const me = useMe();
  if (me.isPending && !me.data) return <Skeleton className="mt-10 h-40 w-full" />;
  if (me.isError && !me.data)
    return (
      <ErrorState
        title="Your account could not be loaded"
        message={me.error.message}
        onRetry={() => void me.refetch()}
      />
    );
  if (!me.data?.is_admin) {
    return (
      <section className="flex flex-col gap-3 pt-10">
        <h1 className="m-0 font-display text-headline">Page not found</h1>
        <p className="m-0 text-text-2">There is nothing at this address.</p>
      </section>
    );
  }
  return (
    <section aria-labelledby="agent-h" className="flex flex-col gap-8 pt-10 pb-24">
      <h1 id="agent-h" className="m-0 font-display text-headline">
        Agent diagnostics
      </h1>
      <RuntimeStatus />
      <PingRun />
    </section>
  );
}
