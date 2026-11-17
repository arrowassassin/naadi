import { describe, expect, it } from "vitest";
import {
  dueRuleText,
  formatDate,
  formatMoney,
  hostOf,
  nextTask,
  oldestCheck,
  safeHttpsUrl,
  stageState,
} from "./format";

const COPY: Record<string, string> = {
  "pathway.due_on": "on {date}",
  "pathway.due_annual": "every year on {date}",
  "pathway.due_at": "at {anchor}",
  "pathway.due_before": "{days} days before {anchor}",
  "pathway.due_after": "{days} days after {anchor}",
  "pathway.anchor.start": "you start",
  "pathway.anchor.step1_date": "your Step 1 date",
};
const t = (key: string, vars: Readonly<Record<string, string>> = {}) =>
  (COPY[key] ?? key).replace(/\{(\w+)\}/g, (_, n: string) => vars[n] ?? `{${n}}`);

describe("formatDate", () => {
  it("writes a date-only value in the Naadi style without a timezone shift", () => {
    expect(formatDate("2026-10-12")).toBe("12 Oct 2026");
    expect(formatDate("2026-01-01T23:59:00Z")).toBe("1 Jan 2026");
    expect(formatDate(null)).toBe("");
    expect(formatDate("nope")).toBe("");
  });
});

describe("dueRuleText", () => {
  it("words anchors, fixed dates and annual rules", () => {
    expect(dueRuleText(t, { kind: "anchor", anchor: "start", days: 7, date: null, month: null, day: null })).toBe(
      "7 days after you start",
    );
    expect(
      dueRuleText(t, { kind: "anchor", anchor: "step1_date", days: -60, date: null, month: null, day: null }),
    ).toBe("60 days before your Step 1 date");
    expect(dueRuleText(t, { kind: "anchor", anchor: "start", days: 0, date: null, month: null, day: null })).toBe(
      "at you start",
    );
    expect(dueRuleText(t, { kind: "date", anchor: null, days: null, date: "2027-02-15", month: null, day: null })).toBe(
      "on 15 Feb 2027",
    );
    expect(dueRuleText(t, { kind: "annual", anchor: null, days: null, date: null, month: 4, day: 15 })).toBe(
      "every year on 15 April",
    );
    expect(dueRuleText(t, null)).toBeNull();
  });
});

describe("formatMoney", () => {
  it("keeps every currency in its own symbol and never converts", () => {
    expect(formatMoney("110.50", "USD")).toBe("$110.50");
    expect(formatMoney("8000.00", "INR")).toContain("8,000");
    expect(formatMoney("8000.00", "INR")).toContain("₹");
    expect(formatMoney(695, "USD")).toBe("$695");
    expect(formatMoney("x", "USD")).toBe("x USD");
  });
});

describe("links", () => {
  it("only https URLs are links", () => {
    expect(safeHttpsUrl("https://www.ecfmg.org/x")).toBe("https://www.ecfmg.org/x");
    expect(safeHttpsUrl("http://www.ecfmg.org/x")).toBeNull();
    expect(safeHttpsUrl("javascript:alert(1)")).toBeNull();
    expect(safeHttpsUrl("data:text/html,x")).toBeNull();
    expect(safeHttpsUrl(null)).toBeNull();
    expect(hostOf("https://www.usmle.org/a")).toBe("usmle.org");
  });
});

const task = (id: string, status: string, last: string | null = "2026-11-15") =>
  ({ id, status, last_verified: last }) as never;

describe("stages", () => {
  it("states: done when every task is done or not needed, current for the student's stage", () => {
    expect(stageState({ key: "a", done: 3, total: 3 }, "a")).toBe("done");
    expect(stageState({ key: "a", done: 1, total: 3 }, "a")).toBe("current");
    expect(stageState({ key: "b", done: 1, total: 3 }, "a")).toBe("started");
    expect(stageState({ key: "b", done: 0, total: 3 }, "a")).toBe("upcoming");
    expect(stageState({ key: "b", done: 0, total: 0 }, null)).toBe("upcoming");
  });
  it("the next task is the first one to do or doing, in the stage's order", () => {
    expect(nextTask({ tasks: [task("1", "done"), task("2", "na"), task("3", "doing"), task("4", "todo")] })?.id).toBe(
      "3",
    );
    expect(nextTask({ tasks: [task("1", "done")] })).toBeNull();
    expect(nextTask(undefined)).toBeNull();
  });
  it("the oldest check is the oldest task date; one never checked makes it unknown", () => {
    const s = (tasks: unknown[]) => ({ tasks }) as never;
    expect(oldestCheck([s([task("1", "todo", "2026-11-15"), task("2", "todo", "2026-09-01")])])).toBe("2026-09-01");
    expect(oldestCheck([s([task("1", "todo", null)])])).toBeNull();
    expect(oldestCheck([])).toBeNull();
  });
});
