import type { CostSummary, StageOut } from "@naadi/shared";
import type { CostEntry } from "./format";

/**
 * Totals by stage and currency, computed on the device from the cost entries and the pathway's task estimates, so
 * an entry made offline counts at once. The rule is the server's (`store.summarise_costs`, same golden vectors in
 * summarise.test.ts and tests/pathway/test_rules_unit.py): planned = task estimates in USD (tasks marked not needed
 * left out), paid = entries with a paid date, upcoming = entries without one, nothing converted between currencies.
 */
function cents(amount: string): number {
  const n = Number(amount);
  return Number.isFinite(n) ? Math.round(n * 100) : 0;
}

type Money = Map<string, number>;

function add(m: Money, currency: string, c: number): void {
  m.set(currency, (m.get(currency) ?? 0) + c);
}

function list(m: Money): { currency: string; amount: string }[] {
  return [...m.entries()]
    .filter(([, c]) => c !== 0)
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
    .map(([currency, c]) => ({ currency, amount: (c / 100).toFixed(2) }));
}

export interface StageTotals {
  stage_key: string | null;
  planned_usd: string;
  paid: { currency: string; amount: string }[];
  upcoming: { currency: string; amount: string }[];
  entries: number;
}

export type Totals = Pick<CostSummary, "paid" | "upcoming" | "planned_usd"> & {
  by_stage: StageTotals[];
  next_fee: CostEntry | null;
};

export function summarise(
  stages: readonly StageOut[],
  entries: readonly (CostEntry & { created_at?: string })[],
): Totals {
  const planned = new Map<string, number>();
  for (const s of stages) {
    let sum = 0;
    let any = false;
    for (const t of s.tasks)
      if (t.status !== "na") {
        any = true;
        sum += Math.round((t.cost_usd ?? 0) * 100);
      }
    if (any) planned.set(s.key, sum); // like the server: a stage with every task not needed has no planned row
  }
  const paid = new Map<string | null, Money>();
  const up = new Map<string | null, Money>();
  const count = new Map<string | null, number>();
  for (const e of entries) {
    const key = e.stage_key;
    count.set(key, (count.get(key) ?? 0) + 1);
    const into = e.paid_on ? paid : up;
    if (!into.has(key)) into.set(key, new Map());
    add(into.get(key) as Money, e.currency, cents(e.amount));
  }
  const totalPaid: Money = new Map();
  const totalUp: Money = new Map();
  for (const m of paid.values()) for (const [c, v] of m) add(totalPaid, c, v);
  for (const m of up.values()) for (const [c, v] of m) add(totalUp, c, v);
  const keys: (string | null)[] = stages.map((s) => s.key).filter((k) => planned.has(k) || count.has(k));
  if (count.has(null)) keys.push(null);
  const upcoming = entries.filter((e) => !e.paid_on);
  upcoming.sort((a, b) => (a.created_at ?? "").localeCompare(b.created_at ?? "") || a.id.localeCompare(b.id));
  return {
    planned_usd: ([...planned.values()].reduce((a, b) => a + b, 0) / 100).toFixed(2),
    paid: list(totalPaid),
    upcoming: list(totalUp),
    next_fee: upcoming[0] ?? null,
    by_stage: keys.map((k) => ({
      stage_key: k,
      planned_usd: ((k === null ? 0 : (planned.get(k) ?? 0)) / 100).toFixed(2),
      paid: list(paid.get(k) ?? new Map()),
      upcoming: list(up.get(k) ?? new Map()),
      entries: count.get(k) ?? 0,
    })),
  };
}
