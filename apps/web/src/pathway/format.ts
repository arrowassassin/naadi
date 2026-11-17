import type { CostOut, DueRuleOut, StageOut, TaskOut } from "@naadi/shared";

export type T = (key: string, vars?: Readonly<Record<string, string>>) => string;

const DATE = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
const DAY_MONTH = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "long", timeZone: "UTC" });

/** `2026-10-12` → "12 Oct 2026". A date-only value, so no timezone shifts it. */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(`${iso.slice(0, 10)}T00:00:00Z`);
  return Number.isNaN(d.getTime()) ? "" : DATE.format(d);
}

/** How a seeded due rule reads: "7 days after you start", "60 days before your Step 1 date", "on 15 Feb 2027". */
export function dueRuleText(t: T, rule: DueRuleOut | null): string | null {
  if (!rule) return null;
  if (rule.kind === "date" && rule.date) return t("pathway.due_on", { date: formatDate(rule.date) });
  if (rule.kind === "annual" && rule.month && rule.day) {
    const d = new Date(Date.UTC(2000, rule.month - 1, rule.day));
    return t("pathway.due_annual", { date: DAY_MONTH.format(d) });
  }
  if (rule.kind === "anchor" && rule.anchor) {
    const days = rule.days ?? 0;
    const anchor = t(`pathway.anchor.${rule.anchor}`);
    if (days === 0) return t("pathway.due_at", { anchor });
    const n = String(Math.abs(days));
    return t(days < 0 ? "pathway.due_before" : "pathway.due_after", { days: n, anchor });
  }
  return null;
}

/** An amount in its own currency, never converted. */
export function formatMoney(amount: string | number, currency: string): string {
  const n = typeof amount === "string" ? Number(amount) : amount;
  if (!Number.isFinite(n)) return `${amount} ${currency}`;
  try {
    return new Intl.NumberFormat("en-IN", {
      style: "currency",
      currency,
      currencyDisplay: "narrowSymbol",
      minimumFractionDigits: Number.isInteger(n) ? 0 : 2,
      maximumFractionDigits: 2,
    }).format(n);
  } catch {
    return `${n.toFixed(2)} ${currency}`;
  }
}

export function hostOf(url: string | null | undefined): string {
  if (!url) return "";
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}

/** https links only: a seeded or admin-edited URL never becomes a `javascript:` or `data:` link. */
export function safeHttpsUrl(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    const u = new URL(url);
    return u.protocol === "https:" ? u.toString() : null;
  } catch {
    return null;
  }
}

export type StageState = "done" | "current" | "started" | "upcoming";

/** The stage's state in words: done when every task is done or not needed; current when it is the student's. */
export function stageState(stage: Pick<StageOut, "done" | "total" | "key">, currentKey: string | null): StageState {
  if (stage.total > 0 && stage.done === stage.total) return "done";
  if (stage.key === currentKey) return "current";
  return stage.done > 0 ? "started" : "upcoming";
}

/** The next task to do in a stage: the first one still to do or in progress, in the stage's order. */
export function nextTask(stage: Pick<StageOut, "tasks"> | undefined): TaskOut | null {
  return stage?.tasks.find((x) => x.status === "todo" || x.status === "doing") ?? null;
}

export function oldestCheck(stages: readonly StageOut[]): string | null {
  let oldest: string | null = null;
  for (const s of stages)
    for (const task of s.tasks) {
      if (!task.last_verified) return null;
      if (oldest === null || task.last_verified < oldest) oldest = task.last_verified;
    }
  return oldest;
}

export function newId(): string {
  return globalThis.crypto.randomUUID();
}

export type CostEntry = Pick<
  CostOut,
  "id" | "task_id" | "stage_key" | "label" | "amount" | "currency" | "paid_on" | "client_updated_at_ms"
>;
