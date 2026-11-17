import type { CheckOut, TaskOut } from "@naadi/shared";
import { Button } from "@naadi/ui";
import { ExternalLink, ShieldCheck } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { useMe } from "../auth/api";
import { useUiText } from "../copy/useUiText";
import { useOnlineStatus } from "../health/useOnlineStatus";
import { errorKey, type TaskFields, useOpenChecks, useVerifyTask } from "./api";
import { ReverifyBadge, STATUSES, type Status } from "./Badges";
import { dueRuleText, formatDate, formatMoney, hostOf, safeHttpsUrl } from "./format";
import { Markdown } from "./Markdown";
import { Sheet } from "./Sheet";

/**
 * One task: what it is (the seeded detail, with its sources), where it was last checked, when it is due, the
 * student's own status, date and notes, and what it costs. Changes are written to this device first (they sync when
 * online). An admin also sees what the weekly check found and can confirm the task ("Mark verified").
 */
export function TaskSheet({
  task,
  stageTitle,
  onClose,
  onChange,
  onAddCost,
  planned,
  pending,
}: {
  task: TaskOut | null;
  stageTitle: string;
  onClose: () => void;
  onChange: (taskId: string, fields: TaskFields) => void;
  onAddCost: (taskId: string) => void;
  /** Entries paid or planned against this task, in their own currencies. */
  planned: { currency: string; amount: string; paid: boolean }[];
  pending: boolean;
}) {
  const t = useUiText();
  const ids = useId();
  const online = useOnlineStatus();
  const me = useMe();
  const isAdmin = me.data?.is_admin === true;
  const checks = useOpenChecks();
  const verify = useVerifyTask();
  const [notes, setNotes] = useState(task?.notes ?? "");
  const taskId = task?.id ?? null;
  // biome-ignore lint/correctness/useExhaustiveDependencies: reset the draft only when another task opens
  useEffect(() => setNotes(task?.notes ?? ""), [taskId]);
  // biome-ignore lint/correctness/useExhaustiveDependencies: clear the last result when another task opens
  useEffect(() => verify.reset(), [taskId, verify.reset]);

  const open = task !== null;
  const findings: CheckOut[] =
    isAdmin && task ? (checks.data?.items ?? []).filter((c) => c.task_id === task.id && !c.resolved_at) : [];
  const href = safeHttpsUrl(task?.official_url);

  return (
    <Sheet open={open} onClose={onClose} title={task?.title ?? ""} wide handle="task">
      {task ? (
        <>
          <p className="m-0 font-mono text-label text-text-3 uppercase tracking-[0.12em]">{stageTitle}</p>
          <div className="flex flex-wrap items-center gap-2">
            {task.reverify ? <ReverifyBadge reason={task.reverify} /> : null}
            {pending ? <span className="text-text-3 text-ui-sm">{t("pathway.will_sync")}</span> : null}
          </div>

          <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
            <legend className="mb-2 font-mono text-label text-text-3 uppercase tracking-[0.12em]">
              {t("pathway.status_label")}
            </legend>
            <div className="flex flex-wrap gap-2">
              {STATUSES.map((s) => (
                <label key={s} className="cursor-pointer">
                  <input
                    type="radio"
                    name={`${ids}-status`}
                    value={s}
                    checked={task.status === s}
                    onChange={() => onChange(task.id, { status: s })}
                    className="peer sr-only"
                  />
                  <span className="inline-flex min-h-11 items-center rounded-pill border border-line-control px-5 text-ui text-text peer-checked:border-text peer-checked:bg-paper peer-checked:font-semibold peer-checked:text-ground peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-lime">
                    {t(`pathway.status.${s as Status}`)}
                  </span>
                </label>
              ))}
            </div>
          </fieldset>

          <Markdown source={task.detail_md} />

          <section
            aria-labelledby={`${ids}-src`}
            className="flex flex-col gap-2 rounded-block border border-violet-line bg-violet-ground p-4"
          >
            <h3
              id={`${ids}-src`}
              className="m-0 flex items-center gap-2 font-mono text-label text-violet-text uppercase tracking-[0.12em]"
            >
              <ShieldCheck aria-hidden="true" size={15} />
              {t("pathway.source_title")}
            </h3>
            <p className="m-0 text-text">
              {task.last_verified
                ? t("pathway.checked_against", {
                    host: hostOf(task.official_url) || t("pathway.source_unknown"),
                    date: formatDate(task.last_verified),
                  })
                : t("pathway.never_checked")}
            </p>
            {href ? (
              <a
                href={href}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex min-h-11 w-fit items-center gap-2 text-violet-text"
              >
                {task.official ? t("pathway.open_official") : t("pathway.open_source")}
                <span className="font-mono text-ui-sm">{hostOf(href)}</span>
                <ExternalLink aria-hidden="true" size={14} />
              </a>
            ) : null}
            {!task.official && href ? (
              <p className="m-0 text-text-3 text-ui-sm">{t("pathway.not_official_note")}</p>
            ) : null}
          </section>

          <div className="grid gap-4 tablet:grid-cols-2">
            <div className="flex flex-col gap-2">
              <label htmlFor={`${ids}-due`} className="font-mono text-label text-text-3 uppercase tracking-[0.12em]">
                {t("pathway.due_label")}
              </label>
              <input
                id={`${ids}-due`}
                type="date"
                value={task.due ?? ""}
                onChange={(e) => onChange(task.id, { due: e.target.value || null })}
                className="min-h-12 rounded-block border border-line-control bg-surface-2 px-4 text-text"
              />
              {dueRuleText(t, task.due_rule) ? (
                <p className="m-0 text-text-3 text-ui-sm">
                  {t("pathway.due_rule_hint", { rule: dueRuleText(t, task.due_rule) ?? "" })}
                </p>
              ) : null}
            </div>
            <div className="flex flex-col gap-2">
              <span className="font-mono text-label text-text-3 uppercase tracking-[0.12em]">
                {t("pathway.cost_label")}
              </span>
              <p className="m-0 text-text">
                {task.cost_usd === null
                  ? t("pathway.cost_unknown")
                  : task.cost_usd === 0
                    ? t("pathway.cost_free")
                    : t("pathway.cost_estimate", { amount: formatMoney(task.cost_usd, "USD") })}
              </p>
              {planned.map((p) => (
                <p key={`${p.currency}-${p.paid}-${p.amount}`} className="m-0 text-text-2 text-ui-sm">
                  {t(p.paid ? "pathway.cost_paid" : "pathway.cost_planned", {
                    amount: formatMoney(p.amount, p.currency),
                  })}
                </p>
              ))}
              <Button variant="secondary" onClick={() => onAddCost(task.id)} className="w-fit">
                {t("costs.add_for_task")}
              </Button>
            </div>
          </div>

          <div className="flex flex-col gap-2">
            <label htmlFor={`${ids}-notes`} className="font-mono text-label text-text-3 uppercase tracking-[0.12em]">
              {t("pathway.notes_label")}
            </label>
            <textarea
              id={`${ids}-notes`}
              value={notes}
              maxLength={4000}
              rows={4}
              placeholder={t("pathway.notes_placeholder")}
              onChange={(e) => setNotes(e.target.value)}
              onBlur={() => {
                const next = notes.trim() ? notes : null;
                if ((task.notes ?? null) !== next) onChange(task.id, { notes: next });
              }}
              className="rounded-block border border-line-control bg-surface-2 p-4 text-text"
            />
          </div>

          {isAdmin ? (
            <section aria-labelledby={`${ids}-admin`} className="flex flex-col gap-3 border-line-soft border-t pt-4">
              <h3 id={`${ids}-admin`} className="m-0 font-mono text-label text-text-3 uppercase tracking-[0.12em]">
                {t("pathway.admin_title")}
              </h3>
              {findings.length === 0 ? (
                <p className="m-0 text-text-3 text-ui-sm">{t("pathway.admin_no_findings")}</p>
              ) : null}
              {findings.map((f) => (
                <div
                  key={f.id}
                  className="flex flex-col gap-2 rounded-block bg-surface-2 p-4"
                  data-finding={f.finding.status}
                >
                  <p className="m-0 font-semibold text-text">
                    {t(`pathway.finding.${f.finding.summary_key ?? f.finding.status}`)}
                  </p>
                  <p className="m-0 text-text-3 text-ui-sm">
                    {t("pathway.finding_when", { date: formatDate(f.checked_at) })}
                  </p>
                  {(f.finding.differences ?? []).map((d) => (
                    <p key={d} className="m-0 text-text-2">
                      {d}
                    </p>
                  ))}
                  {(f.finding.quotes ?? []).map((q) => (
                    <blockquote key={q.text} className="m-0 border-violet-line border-l-[3px] pl-3 text-text-2">
                      “{q.text}”{" "}
                      {safeHttpsUrl(q.url) ? (
                        <a
                          href={safeHttpsUrl(q.url) ?? undefined}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-violet-text"
                        >
                          {hostOf(q.url)}
                        </a>
                      ) : null}
                    </blockquote>
                  ))}
                </div>
              ))}
              <div className="flex flex-wrap items-center gap-3">
                <Button variant="strong" disabled={!online || verify.isPending} onClick={() => verify.mutate(task.id)}>
                  {t("pathway.mark_verified")}
                </Button>
                {!online ? <span className="text-text-3 text-ui-sm">{t("pathway.needs_connection")}</span> : null}
                {verify.isError ? (
                  <span role="alert" className="text-amber text-ui-sm">
                    {t(errorKey(verify.error))}
                  </span>
                ) : null}
                {verify.isSuccess ? (
                  <span role="status" className="text-text-2 text-ui-sm">
                    {t("pathway.verified_done")}
                  </span>
                ) : null}
              </div>
            </section>
          ) : null}
        </>
      ) : null}
    </Sheet>
  );
}
