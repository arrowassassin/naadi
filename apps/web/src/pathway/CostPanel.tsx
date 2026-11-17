import type { StageOut } from "@naadi/shared";
import { Button, Chip, Tile, TileLabel } from "@naadi/ui";
import { Pencil, Plus, Trash2 } from "lucide-react";
import { type FormEvent, useEffect, useId, useMemo, useState } from "react";
import { useUiText } from "../copy/useUiText";
import { useCostWriter } from "./api";
import { type CostEntry, formatDate, formatMoney } from "./format";
import { Sheet } from "./Sheet";
import type { Totals } from "./summarise";

export interface CostRow extends CostEntry {
  created_at: string;
  pending: boolean;
}

/**
 * The cost tracker (Pathway board): what has been paid per currency, what is still to pay, the task estimates, and
 * the entries by stage. Amounts are never converted between currencies. Entries are written on the device first.
 */
export function CostPanel({
  stages,
  rows,
  totals,
  currencies,
  loading,
  fromCache,
  onAdd,
  onEdit,
}: {
  stages: readonly StageOut[];
  rows: readonly CostRow[];
  totals: Totals;
  currencies: readonly string[];
  loading: boolean;
  fromCache: boolean;
  onAdd: () => void;
  onEdit: (id: string) => void;
}) {
  const t = useUiText();
  const writer = useCostWriter();
  const titleOf = useMemo(() => new Map(stages.map((s) => [s.key, s.title])), [stages]);
  const [confirm, setConfirm] = useState<string | null>(null);
  const next = totals.next_fee;
  return (
    <Tile as="section" aria-labelledby="costs-h" className="flex flex-col gap-5" data-panel="costs">
      <div className="flex items-start justify-between gap-3">
        <h2 id="costs-h" className="m-0 font-display font-bold text-[1.5rem]">
          {t("costs.title")}
        </h2>
        <Button variant="secondary" icon={<Plus size={16} />} onClick={onAdd} disabled={currencies.length === 0}>
          {t("costs.add")}
        </Button>
      </div>

      {loading ? (
        <p className="m-0 text-text-3">{t("costs.loading")}</p>
      ) : (
        <>
          <div className="flex flex-col gap-1">
            <TileLabel>{t("costs.paid_label")}</TileLabel>
            {totals.paid.length === 0 ? (
              <span
                className="font-display font-extrabold text-[2.5rem] leading-none tracking-[-0.04em]"
                data-total="paid"
              >
                {formatMoney(0, "USD")}
              </span>
            ) : (
              totals.paid.map((m) => (
                <span
                  key={m.currency}
                  data-total="paid"
                  data-currency={m.currency}
                  className="font-display font-extrabold text-[2.5rem] leading-none tracking-[-0.04em]"
                >
                  {formatMoney(m.amount, m.currency)}
                </span>
              ))
            )}
          </div>
          <p className="m-0 text-text-2 text-ui-sm">
            {t("costs.planned_line", { amount: formatMoney(totals.planned_usd, "USD") })}
            {totals.upcoming.length > 0
              ? ` · ${t("costs.upcoming_line", { amount: totals.upcoming.map((m) => formatMoney(m.amount, m.currency)).join(" + ") })}`
              : ""}
          </p>
          {next ? (
            <p className="m-0 text-text-3 text-ui-sm">
              {t("costs.next_fee", { label: next.label, amount: formatMoney(next.amount, next.currency) })}
            </p>
          ) : null}
          {fromCache ? <p className="m-0 text-text-3 text-ui-sm">{t("costs.saved_copy")}</p> : null}

          {rows.length === 0 ? (
            <p className="m-0 text-text-2" data-empty="costs">
              {t("costs.empty")}
            </p>
          ) : (
            <>
              <h3 className="m-0 font-mono text-label text-text-3 uppercase tracking-[0.12em]">
                {t("costs.by_stage")}
              </h3>
              <ul className="m-0 flex list-none flex-col gap-4 p-0">
                {totals.by_stage
                  .filter((s) => s.entries > 0)
                  .map((s) => (
                    <li
                      key={s.stage_key ?? "none"}
                      data-cost-stage={s.stage_key ?? "none"}
                      className="flex flex-col gap-2"
                    >
                      <p className="m-0 flex flex-wrap items-baseline justify-between gap-2">
                        <span className="font-semibold text-text">
                          {s.stage_key ? (titleOf.get(s.stage_key) ?? s.stage_key) : t("costs.no_stage")}
                        </span>
                        <span className="text-text-2 text-ui-sm">
                          {[
                            ...s.paid.map((m) => t("costs.stage_paid", { amount: formatMoney(m.amount, m.currency) })),
                            ...s.upcoming.map((m) =>
                              t("costs.stage_upcoming", { amount: formatMoney(m.amount, m.currency) }),
                            ),
                          ].join(" · ")}
                        </span>
                      </p>
                      <ul className="m-0 flex list-none flex-col p-0">
                        {rows
                          .filter((r) => (r.stage_key ?? null) === s.stage_key)
                          .map((r) => (
                            <li
                              key={r.id}
                              data-cost={r.id}
                              className="flex flex-wrap items-center gap-x-3 gap-y-1 border-line-soft border-t py-2"
                            >
                              <span className="min-w-0 flex-1 basis-40 text-text">{r.label}</span>
                              <span className="text-text">{formatMoney(r.amount, r.currency)}</span>
                              {r.paid_on ? (
                                <span className="text-text-3 text-ui-sm">
                                  {t("costs.paid_on", { date: formatDate(r.paid_on) })}
                                </span>
                              ) : (
                                <Chip tone="planned">{t("costs.not_paid")}</Chip>
                              )}
                              {r.pending ? (
                                <span className="text-text-3 text-ui-sm">{t("pathway.will_sync")}</span>
                              ) : null}
                              <button
                                type="button"
                                onClick={() => onEdit(r.id)}
                                aria-label={t("costs.edit_named", { label: r.label })}
                                className="flex size-11 items-center justify-center rounded-full border border-line-control bg-transparent text-text"
                              >
                                <Pencil aria-hidden="true" size={15} />
                              </button>
                              <button
                                type="button"
                                onClick={() => setConfirm(r.id)}
                                aria-label={t("costs.delete_named", { label: r.label })}
                                className="flex size-11 items-center justify-center rounded-full border border-line-control bg-transparent text-text"
                              >
                                <Trash2 aria-hidden="true" size={15} />
                              </button>
                            </li>
                          ))}
                      </ul>
                    </li>
                  ))}
              </ul>
            </>
          )}
        </>
      )}

      <Sheet
        open={confirm !== null}
        onClose={() => setConfirm(null)}
        title={t("costs.delete_title")}
        handle="cost-delete"
      >
        <p className="m-0 text-text-2">
          {t("costs.delete_body", { label: rows.find((r) => r.id === confirm)?.label ?? "" })}
        </p>
        <div className="flex flex-wrap gap-3">
          <Button
            variant="strong"
            onClick={() => {
              if (confirm) writer.remove(confirm);
              setConfirm(null);
            }}
          >
            {t("costs.delete_confirm")}
          </Button>
          <Button variant="secondary" onClick={() => setConfirm(null)}>
            {t("pathway.cancel")}
          </Button>
        </div>
      </Sheet>
    </Tile>
  );
}

export interface CostDraft {
  id: string | null;
  taskId: string | null;
  stageKey: string | null;
}

/** Add or edit an entry. The stage is its task's when it has one, else chosen here (or none). */
export function CostSheet({
  draft,
  stages,
  rows,
  currencies,
  onClose,
}: {
  draft: CostDraft | null;
  stages: readonly StageOut[];
  rows: readonly CostRow[];
  currencies: readonly string[];
  onClose: () => void;
}) {
  const t = useUiText();
  const ids = useId();
  const writer = useCostWriter();
  const existing = draft?.id ? rows.find((r) => r.id === draft.id) : undefined;
  const [label, setLabel] = useState("");
  const [amount, setAmount] = useState("");
  const [currency, setCurrency] = useState(currencies[0] ?? "USD");
  const [stageKey, setStageKey] = useState("");
  const [paidOn, setPaidOn] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const taskId = draft ? (existing ? existing.task_id : draft.taskId) : null;
  const task = taskId ? stages.flatMap((s) => s.tasks).find((x) => x.id === taskId) : undefined;
  const open = draft !== null;

  // biome-ignore lint/correctness/useExhaustiveDependencies: the form starts again each time a sheet opens
  useEffect(() => {
    if (!draft) return;
    setProblem(null);
    setLabel(existing?.label ?? "");
    setAmount(existing ? existing.amount : "");
    setCurrency(existing?.currency ?? currencies[0] ?? "USD");
    setStageKey(existing?.stage_key ?? draft.stageKey ?? "");
    setPaidOn(existing?.paid_on ?? "");
    if (!existing && draft.taskId) {
      const tk = stages.flatMap((s) => s.tasks).find((x) => x.id === draft.taskId);
      if (tk) {
        setLabel(tk.title.slice(0, 200));
        if (tk.cost_usd) setAmount(String(tk.cost_usd));
        setCurrency(currencies.includes("USD") ? "USD" : (currencies[0] ?? "USD"));
      }
    }
  }, [draft?.id, draft?.taskId, draft === null]);

  function submit(e: FormEvent) {
    e.preventDefault();
    const n = Number(amount);
    if (!label.trim() || !Number.isFinite(n) || n < 0 || amount.trim() === "") {
      setProblem(t("costs.error_invalid"));
      return;
    }
    const fields = {
      label: label.trim().slice(0, 200),
      amount: Math.round(n * 100) / 100,
      currency,
      paid_on: paidOn || null,
      ...(taskId ? { task_id: taskId } : { stage_key: stageKey || null }),
    };
    if (existing) writer.update(existing.id, fields);
    else writer.create(fields);
    onClose();
  }

  return (
    <Sheet open={open} onClose={onClose} title={t(existing ? "costs.edit_title" : "costs.add_title")} handle="cost">
      <form onSubmit={submit} className="flex flex-col gap-4" noValidate>
        {task ? <p className="m-0 text-text-2">{t("costs.for_task", { task: task.title })}</p> : null}
        <div className="flex flex-col gap-2">
          <label htmlFor={`${ids}-label`} className="font-mono text-label text-text-3 uppercase tracking-[0.12em]">
            {t("costs.label_label")}
          </label>
          <input
            id={`${ids}-label`}
            value={label}
            maxLength={200}
            onChange={(e) => setLabel(e.target.value)}
            className="min-h-12 rounded-block border border-line-control bg-surface-2 px-4 text-text"
          />
        </div>
        <div className="grid gap-4 tablet:grid-cols-2">
          <div className="flex flex-col gap-2">
            <label htmlFor={`${ids}-amount`} className="font-mono text-label text-text-3 uppercase tracking-[0.12em]">
              {t("costs.amount_label")}
            </label>
            <input
              id={`${ids}-amount`}
              inputMode="decimal"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              className="min-h-12 rounded-block border border-line-control bg-surface-2 px-4 text-text"
            />
          </div>
          <div className="flex flex-col gap-2">
            <label htmlFor={`${ids}-cur`} className="font-mono text-label text-text-3 uppercase tracking-[0.12em]">
              {t("costs.currency_label")}
            </label>
            <select
              id={`${ids}-cur`}
              value={currency}
              onChange={(e) => setCurrency(e.target.value)}
              className="min-h-12 rounded-block border border-line-control bg-surface-2 px-4 text-text"
            >
              {currencies.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </div>
        </div>
        {task ? null : (
          <div className="flex flex-col gap-2">
            <label htmlFor={`${ids}-stage`} className="font-mono text-label text-text-3 uppercase tracking-[0.12em]">
              {t("costs.stage_label")}
            </label>
            <select
              id={`${ids}-stage`}
              value={stageKey}
              onChange={(e) => setStageKey(e.target.value)}
              className="min-h-12 rounded-block border border-line-control bg-surface-2 px-4 text-text"
            >
              <option value="">{t("costs.no_stage")}</option>
              {stages.map((s) => (
                <option key={s.key} value={s.key}>
                  {s.title}
                </option>
              ))}
            </select>
          </div>
        )}
        <div className="flex flex-col gap-2">
          <label htmlFor={`${ids}-paid`} className="font-mono text-label text-text-3 uppercase tracking-[0.12em]">
            {t("costs.paid_on_label")}
          </label>
          <input
            id={`${ids}-paid`}
            type="date"
            value={paidOn}
            onChange={(e) => setPaidOn(e.target.value)}
            className="min-h-12 rounded-block border border-line-control bg-surface-2 px-4 text-text"
          />
          <p className="m-0 text-text-3 text-ui-sm">{t("costs.paid_on_hint")}</p>
        </div>
        {problem ? (
          <p role="alert" className="m-0 text-amber">
            {problem}
          </p>
        ) : null}
        <div className="flex flex-wrap gap-3">
          <Button type="submit" variant="strong">
            {t("costs.save")}
          </Button>
          <Button variant="secondary" onClick={onClose}>
            {t("pathway.cancel")}
          </Button>
        </div>
      </form>
    </Sheet>
  );
}
