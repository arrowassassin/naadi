import { Button, Chip, type ChipTone, cx, Tile } from "@naadi/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Ban, Check, CircleDashed, Clock, LoaderCircle, type LucideIcon, TriangleAlert, X } from "lucide-react";
import { useMemo, useState } from "react";
import { type AgentUsage, api, qk } from "../api";
import {
  AdminPage,
  Empty,
  Failure,
  FilterPills,
  fmtDateTime,
  fmtNumber,
  InlineError,
  Loading,
  useT,
} from "../components/ui";

const RUN_STATUSES = ["pending", "running", "success", "error", "timeout", "interrupted"] as const;
const RUN_LOOK: Record<string, { key: string; tone: ChipTone; Icon: LucideIcon }> = {
  pending: { key: "admin.ops.run.pending", tone: "planned", Icon: Clock },
  running: { key: "admin.ops.run.running", tone: "meta", Icon: LoaderCircle },
  success: { key: "admin.ops.run.success", tone: "done", Icon: Check },
  error: { key: "admin.ops.run.error", tone: "warning", Icon: TriangleAlert },
  timeout: { key: "admin.ops.run.timeout", tone: "warning", Icon: Clock },
  interrupted: { key: "admin.ops.run.interrupted", tone: "meta", Icon: Ban },
};
const PURPOSE_KEYS: Record<string, string> = {
  chat: "admin.ops.purpose.chat",
  job: "admin.ops.purpose.job",
  total: "admin.ops.purpose.total",
};

function RunStatus({ status }: { status: string }) {
  const t = useT();
  const look = RUN_LOOK[status];
  if (!look)
    return (
      <Chip tone="meta" icon={<CircleDashed size={13} />}>
        {status}
      </Chip>
    );
  return (
    <Chip tone={look.tone} icon={<look.Icon size={13} />}>
      {t(look.key)}
    </Chip>
  );
}

const TH = "py-2 pr-3 text-left font-mono text-label-sm font-medium text-text-3 uppercase";
const TD = "py-2.5 pr-3 align-top";

function Scroll({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <section
      aria-label={label}
      // biome-ignore lint/a11y/noNoninteractiveTabindex: a scrollable region must be focusable (WCAG 2.1.1)
      tabIndex={0}
      className="overflow-x-auto [-webkit-overflow-scrolling:touch]"
    >
      {children}
    </section>
  );
}

function Runs() {
  const t = useT();
  const qc = useQueryClient();
  const [status, setStatus] = useState("all");
  const params = { status: status === "all" ? undefined : status, limit: 50 };
  const runs = useQuery({ queryKey: qk.runs(params), queryFn: ({ signal }) => api.agentRuns(params, signal) });
  const cancel = useMutation({
    mutationFn: (r: { thread_id: string; run_id: string }) => api.cancelRun(r.thread_id, r.run_id),
    onSettled: () => void qc.invalidateQueries({ queryKey: ["admin", "agent-runs"] }),
  });
  return (
    <Tile as="section" aria-labelledby="runs-h" className="flex min-w-0 flex-col gap-4">
      <h2 id="runs-h" className="m-0 font-display font-bold text-[1.5rem]">
        {t("admin.ops.runs")}
      </h2>
      <FilterPills
        label={t("admin.field.status")}
        value={status}
        options={[
          { value: "all", label: t("admin.filter.all") },
          ...RUN_STATUSES.map((s) => ({ value: s, label: t(RUN_LOOK[s]?.key ?? s) })),
        ]}
        onChange={setStatus}
      />
      <InlineError error={cancel.error} />
      {runs.isPending ? (
        <Loading rows={3} />
      ) : runs.isError ? (
        <Failure error={runs.error} onRetry={() => void runs.refetch()} retrying={runs.isFetching} />
      ) : !runs.data.available ? (
        <p className="m-0 text-text-2">{t("admin.ops.runtime_unavailable")}</p>
      ) : runs.data.items.length === 0 ? (
        <Empty title={t("admin.ops.runs_empty")} />
      ) : (
        <Scroll label={t("admin.ops.runs")}>
          <table className="w-full min-w-[40rem] border-collapse text-ui-sm">
            <thead>
              <tr>
                <th scope="col" className={TH}>
                  {t("admin.ops.graph")}
                </th>
                <th scope="col" className={TH}>
                  {t("admin.field.status")}
                </th>
                <th scope="col" className={TH}>
                  {t("admin.ops.started")}
                </th>
                <th scope="col" className={TH}>
                  {t("admin.ops.updated")}
                </th>
                <th scope="col" className={TH}>
                  <span className="sr-only">{t("admin.ops.actions")}</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {runs.data.items.map((r) => (
                <tr key={r.run_id} className="border-line-soft border-t">
                  <td className={TD}>
                    <span className="flex flex-col gap-0.5">
                      <span className="font-mono text-text">{r.graph_id ?? t("admin.ops.graph_unknown")}</span>
                      <span className="font-mono text-label-sm break-all text-text-3">{r.run_id}</span>
                      {r.system ? <span className="text-label-sm text-text-3">{t("admin.ops.system_run")}</span> : null}
                    </span>
                  </td>
                  <td className={TD}>
                    <span className="flex flex-wrap gap-1.5">
                      <RunStatus status={r.status} />
                      {r.has_error ? (
                        <Chip tone="warning" icon={<X size={13} />}>
                          {t("admin.ops.has_error")}
                        </Chip>
                      ) : null}
                    </span>
                  </td>
                  <td className={cx(TD, "text-text-2")}>{fmtDateTime(r.created_at)}</td>
                  <td className={cx(TD, "text-text-2")}>{fmtDateTime(r.updated_at)}</td>
                  <td className={TD}>
                    {r.status === "pending" || r.status === "running" ? (
                      <Button
                        disabled={cancel.isPending}
                        onClick={() => cancel.mutate(r)}
                        aria-label={t("admin.ops.cancel_run", { id: r.run_id })}
                      >
                        {t("admin.action.cancel_run")}
                      </Button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Scroll>
      )}
    </Tile>
  );
}

function Crons() {
  const t = useT();
  const crons = useQuery({ queryKey: qk.crons, queryFn: ({ signal }) => api.agentCrons(signal) });
  return (
    <Tile as="section" aria-labelledby="crons-h" className="flex min-w-0 flex-col gap-4">
      <h2 id="crons-h" className="m-0 font-display font-bold text-[1.5rem]">
        {t("admin.ops.crons")}
      </h2>
      {crons.isPending ? (
        <Loading rows={2} />
      ) : crons.isError ? (
        <Failure error={crons.error} onRetry={() => void crons.refetch()} retrying={crons.isFetching} />
      ) : !crons.data.available ? (
        <p className="m-0 text-text-2">{t("admin.ops.runtime_unavailable")}</p>
      ) : crons.data.items.length === 0 ? (
        <Empty title={t("admin.ops.crons_empty")} />
      ) : (
        <Scroll label={t("admin.ops.crons")}>
          <table className="w-full min-w-[40rem] border-collapse text-ui-sm">
            <thead>
              <tr>
                <th scope="col" className={TH}>
                  {t("admin.ops.cron")}
                </th>
                <th scope="col" className={TH}>
                  {t("admin.ops.schedule")}
                </th>
                <th scope="col" className={TH}>
                  {t("admin.ops.next_run")}
                </th>
                <th scope="col" className={TH}>
                  {t("admin.ops.last_run")}
                </th>
              </tr>
            </thead>
            <tbody>
              {crons.data.items.map((c) => (
                <tr key={c.cron_id} className="border-line-soft border-t">
                  <td className={TD}>
                    <span className="flex flex-col gap-0.5">
                      <span className="font-mono text-text">{c.key ?? c.graph_id ?? c.cron_id}</span>
                      <span className="text-label-sm text-text-3">
                        {t(c.enabled ? "admin.ops.enabled" : "admin.ops.disabled")}
                      </span>
                    </span>
                  </td>
                  <td className={cx(TD, "font-mono text-text-2")}>{c.schedule}</td>
                  <td className={cx(TD, "text-text-2")}>{fmtDateTime(c.next_run_date) || t("admin.ops.none")}</td>
                  <td className={TD}>
                    {c.last_run ? (
                      <span className="flex flex-wrap items-center gap-1.5">
                        <RunStatus status={c.last_run.status} />
                        <span className="text-text-2">{fmtDateTime(c.last_run.at)}</span>
                      </span>
                    ) : (
                      <span className="text-text-3">{t("admin.ops.never")}</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Scroll>
      )}
    </Tile>
  );
}

/** Today's calls per provider × purpose against the configured caps ("total" sums every purpose). */
export function capUse(u: AgentUsage): { provider: string; purpose: string; used: number; cap: number }[] {
  return u.caps.map((c) => {
    const rows = u.today.filter((d) => d.provider === c.provider && (c.purpose === "total" || d.purpose === c.purpose));
    return { provider: c.provider, purpose: c.purpose, used: rows.reduce((s, d) => s + d.calls, 0), cap: c.cap };
  });
}

function Bar({ label, used, cap }: { label: string; used: number; cap: number }) {
  const t = useT();
  const over = cap > 0 && used >= cap;
  const text = t("admin.ops.used_of", { used: fmtNumber(used), cap: fmtNumber(cap) });
  return (
    <li className="flex flex-col gap-1.5">
      <div className="flex justify-between gap-3 text-ui-sm">
        <span>{label}</span>
        <span className="flex items-center gap-1.5 font-mono text-text-2">
          {over ? <TriangleAlert aria-hidden="true" size={13} className="text-amber" /> : null}
          {text}
          {over ? <span className="text-amber">{t("admin.ops.at_cap")}</span> : null}
        </span>
      </div>
      <progress
        className={cx("admin-bar h-1.5 w-full", over && "over")}
        max={Math.max(cap, 1)}
        value={Math.min(used, Math.max(cap, 1))}
        aria-label={`${label}: ${text}`}
      />
    </li>
  );
}

function Usage() {
  const t = useT();
  const usage = useQuery({ queryKey: qk.usage(14), queryFn: ({ signal }) => api.agentUsage(14, signal) });
  const daily = useMemo(() => {
    if (!usage.data) return [];
    const m = new Map<string, { date: string; calls: number; ok: number; failed: number }>();
    for (const d of usage.data.days) {
      const r = m.get(d.date) ?? { date: d.date, calls: 0, ok: 0, failed: 0 };
      r.calls += d.calls;
      r.ok += d.ok;
      r.failed += d.failed;
      m.set(d.date, r);
    }
    return [...m.values()].sort((a, b) => b.date.localeCompare(a.date));
  }, [usage.data]);
  const health = useMemo(() => {
    const latest = new Map<string, AgentUsage["health"][number]>();
    for (const h of usage.data?.health ?? []) {
      const cur = latest.get(h.kind);
      if (!cur || cur.created_at < h.created_at) latest.set(h.kind, h);
    }
    return [...latest.values()].sort((a, b) => a.kind.localeCompare(b.kind));
  }, [usage.data]);
  if (usage.isPending) return <Loading rows={3} />;
  if (usage.isError)
    return <Failure error={usage.error} onRetry={() => void usage.refetch()} retrying={usage.isFetching} />;
  const u = usage.data;
  return (
    <div className="grid min-w-0 gap-5 laptop:grid-cols-2">
      <Tile as="section" aria-labelledby="caps-h" className="flex min-w-0 flex-col gap-4">
        <h2 id="caps-h" className="m-0 font-display font-bold text-[1.5rem]">
          {t("admin.ops.today_caps")}
        </h2>
        {u.caps.length === 0 ? (
          <Empty title={t("admin.ops.caps_empty")} />
        ) : (
          <ul className="m-0 flex list-none flex-col gap-4 p-0">
            {capUse(u).map((c) => (
              <Bar
                key={`${c.provider}-${c.purpose}`}
                label={t("admin.ops.cap_label", {
                  provider: c.provider,
                  purpose: t(PURPOSE_KEYS[c.purpose] ?? c.purpose),
                })}
                used={c.used}
                cap={c.cap}
              />
            ))}
          </ul>
        )}
        <h3 className="m-0 mt-2 font-semibold">{t("admin.ops.tavily")}</h3>
        <ul className="m-0 flex list-none flex-col gap-4 p-0">
          <Bar label={t("admin.ops.tavily_month")} used={u.tavily.month_credits} cap={u.tavily.monthly_guard} />
        </ul>
        <p className="m-0 text-ui-sm text-text-2">
          {t("admin.ops.tavily_today", { n: fmtNumber(u.tavily.today_credits) })}
        </p>
      </Tile>
      <Tile as="section" aria-labelledby="health-h2" className="flex min-w-0 flex-col gap-4">
        <h2 id="health-h2" className="m-0 font-display font-bold text-[1.5rem]">
          {t("admin.ops.health")}
        </h2>
        {health.length === 0 ? (
          <Empty title={t("admin.ops.health_empty")} />
        ) : (
          <ul className="m-0 flex list-none flex-col p-0">
            {health.map((h) => (
              <li
                key={h.kind}
                className="flex flex-wrap items-center gap-2 border-line-soft border-t py-2.5 first:border-t-0"
              >
                <span className="min-w-0 flex-1 font-mono text-ui-sm text-text">{h.kind}</span>
                <Chip tone={h.ok ? "done" : "warning"} icon={h.ok ? <Check size={13} /> : <X size={13} />}>
                  {t(h.ok ? "admin.ops.ok" : "admin.ops.failing")}
                </Chip>
                <span className="text-ui-sm text-text-2">
                  {h.latency_ms === null ? "" : t("admin.ops.latency", { ms: fmtNumber(h.latency_ms) })}
                </span>
                <span className="w-full text-label-sm text-text-3">
                  {t("admin.ops.checked", { time: fmtDateTime(h.created_at), trigger: h.trigger })}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Tile>
      <Tile as="section" aria-labelledby="days-h" className="flex min-w-0 flex-col gap-4 laptop:col-span-2">
        <h2 id="days-h" className="m-0 font-display font-bold text-[1.5rem]">
          {t("admin.ops.daily")}
        </h2>
        {daily.length === 0 ? (
          <Empty title={t("admin.ops.daily_empty")} />
        ) : (
          <Scroll label={t("admin.ops.daily")}>
            <table className="w-full min-w-[32rem] border-collapse text-ui-sm">
              <thead>
                <tr>
                  <th scope="col" className={TH}>
                    {t("admin.ops.date")}
                  </th>
                  <th scope="col" className={TH}>
                    {t("admin.ops.calls")}
                  </th>
                  <th scope="col" className={TH}>
                    {t("admin.ops.ok")}
                  </th>
                  <th scope="col" className={TH}>
                    {t("admin.ops.failed")}
                  </th>
                </tr>
              </thead>
              <tbody>
                {daily.map((d) => (
                  <tr key={d.date} className="border-line-soft border-t">
                    <td className={cx(TD, "font-mono")}>{d.date}</td>
                    <td className={TD}>{fmtNumber(d.calls)}</td>
                    <td className={TD}>{fmtNumber(d.ok)}</td>
                    <td className={cx(TD, d.failed ? "text-amber" : undefined)}>{fmtNumber(d.failed)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Scroll>
        )}
      </Tile>
    </div>
  );
}

/** `/admin/agent-ops`: agent runs, crons, LLM usage vs caps, Tavily credits and health. Metadata only. */
export function AgentOpsPage() {
  const t = useT();
  return (
    <AdminPage
      label={t("admin.label")}
      title={t("admin.ops.title")}
      actions={
        <Link
          to="/admin/agent"
          className="inline-flex min-h-12 items-center rounded-pill border border-line-control px-5 text-ui-sm text-text no-underline hover:bg-surface-2 hover:text-text"
        >
          {t("admin.nav.agent_diagnostics")}
        </Link>
      }
    >
      <p className="m-0 max-w-[48rem] text-text-2">{t("admin.ops.intro")}</p>
      <Usage />
      <Runs />
      <Crons />
    </AdminPage>
  );
}
