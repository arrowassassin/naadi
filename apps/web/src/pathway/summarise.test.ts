import { describe, expect, it } from "vitest";
import type { CostEntry } from "./format";
import { summarise } from "./summarise";

// The same vectors as tests/pathway/test_rules_unit.py::test_summary_totals_by_stage_and_currency.
const stage = (key: string, costs: [number | null, string][]) =>
  ({ key, tasks: costs.map(([cost_usd, status], i) => ({ id: `${key}${i}`, cost_usd, status })) }) as never;

describe("summarise", () => {
  const entry = (
    id: string,
    stage_key: string | null,
    amount: string,
    currency: string,
    paid: boolean,
  ): CostEntry & { created_at: string } => ({
    id,
    task_id: null,
    stage_key,
    label: `L${id}`,
    amount,
    currency,
    paid_on: paid ? "2026-01-01" : null,
    client_updated_at_ms: 1,
    created_at: `2026-01-0${id}T00:00:00Z`,
  });

  it("totals by stage and currency without conversion", () => {
    const stages = [stage("a", [[100, "todo"]]), stage("b", [[0, "todo"]]), stage("c", [])];
    const entries = [
      entry("1", "a", "10.10", "USD", true),
      entry("2", "a", "20", "USD", true),
      entry("3", "a", "800", "INR", true),
      entry("4", "b", "5", "USD", false),
      entry("5", null, "7", "INR", true),
      entry("6", "a", "3", "USD", false),
    ];
    const s = summarise(stages, entries);
    expect(s.by_stage.map((x) => x.stage_key)).toEqual(["a", "b", null]);
    const a = s.by_stage[0];
    expect(a?.paid).toEqual([
      { currency: "INR", amount: "800.00" },
      { currency: "USD", amount: "30.10" },
    ]);
    expect(a?.upcoming).toEqual([{ currency: "USD", amount: "3.00" }]);
    expect(a?.planned_usd).toBe("100.00");
    expect(a?.entries).toBe(4);
    expect(s.paid).toEqual([
      { currency: "INR", amount: "807.00" },
      { currency: "USD", amount: "30.10" },
    ]);
    expect(s.upcoming).toEqual([{ currency: "USD", amount: "8.00" }]);
    expect(s.planned_usd).toBe("100.00");
    expect(s.next_fee?.id).toBe("4");
  });

  it("is empty with nothing planned or paid, and tasks marked not needed are not planned", () => {
    const empty = summarise([stage("a", [[50, "na"]])], []);
    expect(empty.paid).toEqual([]);
    expect(empty.next_fee).toBeNull();
    expect(empty.by_stage).toEqual([]);
    expect(empty.planned_usd).toBe("0.00");
    expect(
      summarise(
        [
          stage("a", [
            [50, "todo"],
            [null, "doing"],
          ]),
        ],
        [],
      ).planned_usd,
    ).toBe("50.00");
  });

  it("adds cents exactly", () => {
    const s = summarise([], [entry("1", null, "0.10", "USD", true), entry("2", null, "0.20", "USD", true)]);
    expect(s.paid).toEqual([{ currency: "USD", amount: "0.30" }]);
  });
});
