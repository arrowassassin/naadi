import type { RebalanceOut } from "@naadi/shared";
import { beforeEach, describe, expect, it } from "vitest";
import { counted, dateOf, dayMonth, range, relativeDays } from "./format";
import { itemLink } from "./itemLink";
import { dropStatus, enqueueStatus, flushQueue, readQueue } from "./outbox";
import { awaitConfirm, decide, type Interrupt, type Runtime } from "./runs";

const copy: Record<string, string> = {
  "planner.range": "{from} – {to}",
  "planner.suggest_title": "Catch up on {n} missed items",
  "planner.suggest_title_one": "Catch up on 1 missed item",
  "planner.in_days": "in {n} days",
  "planner.in_one_day": "tomorrow",
  "planner.days_ago": "{n} days ago",
  "planner.one_day_ago": "yesterday",
  "planner.milestone_today": "today",
};
const t = (key: string, vars?: Readonly<Record<string, string>>) =>
  (copy[key] ?? key).replace(/\{(\w+)\}/g, (_, k: string) => vars?.[k] ?? "");

describe("format", () => {
  it("reads a date without a time-zone shift", () => {
    expect(dateOf("2026-12-06").getDate()).toBe(6);
    expect(dayMonth("2026-12-06")).toBe("6 Dec");
    expect(range(t, "2026-11-16", "2026-11-22")).toBe("16 Nov – 22 Nov 2026");
    expect(range(t, "2026-12-28", "2027-01-03")).toBe("28 Dec 2026 – 3 Jan 2027");
  });
  it("words counts with their own singular", () => {
    expect(counted(t, "planner.suggest_title", 1)).toBe("Catch up on 1 missed item");
    expect(counted(t, "planner.suggest_title", 12)).toBe("Catch up on 12 missed items");
  });
  it("says how far away a day is", () => {
    expect([0, 1, 5, -1, -3].map((n) => relativeDays(t, n))).toEqual([
      "today",
      "tomorrow",
      "in 5 days",
      "yesterday",
      "3 days ago",
    ]);
  });
});

describe("itemLink", () => {
  const base = {
    id: "i",
    day: "2026-12-01",
    kind: "read",
    title: "x",
    ref_type: null,
    ref_id: null,
    target_minutes: 30,
    target_count: null,
    status: "planned",
    origin: "generator",
    sort: 0,
    plan_day: 1,
  } as const;
  const ctx = { exam: "step1", cardsOn: true };
  it("opens the reader for a chapter and the Qbank builder prefilled for questions", () => {
    expect(itemLink({ ...base, ref_type: "chapter", ref_id: "ch05" }, ctx)).toEqual({
      kind: "start_reading",
      chapterId: "ch05",
    });
    expect(itemLink({ ...base, kind: "questions", ref_type: "qbank", ref_id: "step1", target_count: 80 }, ctx)).toEqual(
      {
        kind: "block",
        exam: "step1",
        count: 40,
        status: "unused",
      },
    );
    expect(itemLink({ ...base, ref_type: "weak_spot", target_count: 20 }, ctx)).toEqual({
      kind: "weak_spot",
      exam: "step1",
      count: 20,
    });
  });
  it("has no page for outside resources, admin tasks or cards that are switched off", () => {
    expect(itemLink({ ...base, ref_type: "external", ref_id: "uworld" }, ctx)).toBeNull();
    expect(itemLink({ ...base, kind: "admin" }, ctx)).toBeNull();
    expect(itemLink({ ...base, ref_type: "cards" }, { ...ctx, cardsOn: false })).toBeNull();
    expect(itemLink({ ...base, ref_type: "cards" }, ctx)).toEqual({ kind: "cards" });
  });
});

describe("outbox", () => {
  beforeEach(() => localStorage.clear());
  it("keeps only the latest change per item and replays in order", async () => {
    enqueueStatus("u", { id: "a", status: "done", client_updated_at_ms: 1 });
    enqueueStatus("u", { id: "b", status: "skipped", client_updated_at_ms: 2 });
    enqueueStatus("u", { id: "a", status: "planned", client_updated_at_ms: 3 });
    expect(readQueue("u").map((q) => [q.id, q.status])).toEqual([
      ["b", "skipped"],
      ["a", "planned"],
    ]);
    const sent: string[] = [];
    const n = await flushQueue("u", async (q) => {
      sent.push(q.id);
      return "sent";
    });
    expect([n, sent, readQueue("u")]).toEqual([2, ["b", "a"], []]);
  });
  it("stops at the first network failure and keeps the rest", async () => {
    enqueueStatus("u", { id: "a", status: "done", client_updated_at_ms: 1 });
    enqueueStatus("u", { id: "b", status: "done", client_updated_at_ms: 2 });
    expect(await flushQueue("u", async () => "unreachable")).toBe(0);
    expect(readQueue("u")).toHaveLength(2);
  });
  it("an answered refusal leaves the queue too (a retry cannot fix it)", async () => {
    enqueueStatus("u", { id: "a", status: "done", client_updated_at_ms: 1 });
    await flushQueue("u", async () => "refused");
    expect(readQueue("u")).toEqual([]);
  });
  it("a newer change made while replaying stays queued", () => {
    enqueueStatus("u", { id: "a", status: "done", client_updated_at_ms: 5 });
    dropStatus("u", "a", 4);
    expect(readQueue("u")).toHaveLength(1);
    dropStatus("u", "a", 5);
    expect(readQueue("u")).toHaveLength(0);
  });
  it("is per user", () => {
    enqueueStatus("u1", { id: "a", status: "done", client_updated_at_ms: 1 });
    expect(readQueue("u2")).toEqual([]);
  });
});

function row(over: Partial<RebalanceOut> = {}): RebalanceOut {
  return {
    id: "r1",
    created_at: "2026-11-17T00:00:00Z",
    trigger: "coach",
    status: "proposed",
    applied_at: null,
    thread_id: null,
    proposal: { moves: [], drops: [], warnings: [], missed: 3, spilled: 0, fingerprint: "fp1" },
    ...over,
  };
}

class FakeRuntime implements Runtime {
  calls: string[] = [];
  interrupts = new Map<string, Interrupt | null>();
  result: unknown = { outcome: "applied", detail: { moved: 2, dropped: 1 } };
  threads = {
    create: async () => {
      this.calls.push("threads.create");
      return { thread_id: "new" };
    },
    getState: async (id: string) => {
      this.calls.push(`getState:${id}`);
      if (!this.interrupts.has(id)) throw new Error("not found");
      const v = this.interrupts.get(id);
      return { values: {}, tasks: v ? [{ interrupts: [{ value: v }] }] : [{}] };
    },
  };
  runs = {
    create: async (thread: string, graph: string, p: { input: Record<string, unknown> }) => {
      this.calls.push(`runs.create:${thread}:${graph}:${JSON.stringify(p.input)}`);
      return { run_id: "run" };
    },
    join: async (thread: string) => {
      this.calls.push(`runs.join:${thread}`);
    },
    wait: async (thread: string, _g: string, p: { command: { resume: { decision: string } } }) => {
      this.calls.push(`runs.wait:${thread}:${p.command.resume.decision}`);
      return this.result;
    },
  };
}

const waiting = (over: Partial<Interrupt> = {}): Interrupt => ({
  rebalance_id: "r1",
  fingerprint: "fp1",
  moves: 3,
  drops: 0,
  warnings: 0,
  ...over,
});

describe("rebalance runs", () => {
  it("resumes the thread that is already waiting on this proposal", async () => {
    const rt = new FakeRuntime();
    rt.interrupts.set("t1", waiting());
    const out = await decide(rt, row({ thread_id: "t1" }), "accept");
    expect(out).toEqual({ kind: "done", outcome: { outcome: "applied", detail: { moved: 2, dropped: 1 } } });
    expect(rt.calls).toEqual(["getState:t1", "runs.wait:t1:accept"]);
  });
  it("brings a proposal without a live run to its confirm first, then answers it", async () => {
    const rt = new FakeRuntime();
    rt.interrupts.set("new", waiting());
    const out = await decide(rt, row(), "decline");
    expect(out.kind).toBe("done");
    expect(rt.calls).toEqual([
      "threads.create",
      'runs.create:new:planner:{"trigger":"student"}',
      "runs.join:new",
      "getState:new",
      "runs.wait:new:decline",
    ]);
  });
  it("starts a new run when the old thread is gone", async () => {
    const rt = new FakeRuntime();
    rt.interrupts.set("new", waiting());
    const out = await decide(rt, row({ thread_id: "expired" }), "accept");
    expect(out.kind).toBe("done");
    expect(rt.calls[0]).toBe("getState:expired");
    expect(rt.calls).toContain("threads.create");
  });
  it("never answers a proposal the student did not see: a changed plan is shown again", async () => {
    const rt = new FakeRuntime();
    rt.interrupts.set("t1", waiting({ fingerprint: "other" }));
    expect(await decide(rt, row({ thread_id: "t1" }), "accept")).toEqual({ kind: "changed" });
    expect(rt.calls.some((c) => c.startsWith("runs.wait"))).toBe(false);
  });
  it("reports nothing to confirm when the plan no longer needs a rebalance", async () => {
    const rt = new FakeRuntime();
    rt.interrupts.set("new", null);
    expect(await decide(rt, row(), "accept")).toEqual({ kind: "nothing" });
  });
  it("waits for a started run and reports whether it reached a confirm", async () => {
    const rt = new FakeRuntime();
    rt.interrupts.set("t", waiting());
    rt.interrupts.set("t2", null);
    expect(await awaitConfirm(rt, "t", "r")).toMatchObject({ rebalance_id: "r1" });
    expect(await awaitConfirm(rt, "t2", "r")).toBeNull();
  });
});
