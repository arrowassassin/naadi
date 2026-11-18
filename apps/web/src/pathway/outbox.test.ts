import "fake-indexeddb/auto";
import { Dexie } from "dexie";
import { beforeEach, describe, expect, it } from "vitest";
import { conflictsFor, wasChangedElsewhere, whenPersisted } from "../offline/outbox";
import {
  discardOp,
  flushOps,
  loadOps,
  type PathwayTransport,
  pendingFor,
  pendingOps,
  queueCostCreate,
  queueCostDelete,
  queueCostUpdate,
  queueTask,
  resetPathwayDbForTests,
  type SendResult,
} from "./outbox";

const U = "user-1";

function transport(script: Partial<Record<keyof PathwayTransport, SendResult>> = {}) {
  const calls: { op: string; key: string; fields?: Record<string, unknown>; stamp: number }[] = [];
  const t: PathwayTransport = {
    patchTask: async (key, fields, stamp) => {
      calls.push({ op: "patchTask", key, fields, stamp });
      return script.patchTask ?? "ok";
    },
    createCost: async (key, fields, stamp) => {
      calls.push({ op: "createCost", key, fields, stamp });
      return script.createCost ?? "ok";
    },
    patchCost: async (key, fields, stamp) => {
      calls.push({ op: "patchCost", key, fields, stamp });
      return script.patchCost ?? "ok";
    },
    deleteCost: async (key, stamp) => {
      calls.push({ op: "deleteCost", key, stamp });
      return script.deleteCost ?? "ok";
    },
  };
  return { t, calls };
}

beforeEach(async () => {
  await whenPersisted();
  resetPathwayDbForTests();
  await Dexie.delete("naadi-v1");
  await Dexie.delete("naadi-pathway");
  resetPathwayDbForTests();
});

describe("pathway outbox", () => {
  it("folds later changes to a task into one entry with the newest stamp", () => {
    queueTask(U, "t1", { status: "doing" }, 100);
    queueTask(U, "t1", { notes: "booked" }, 200);
    queueTask(U, "t1", { status: "done" }, 150);
    const ops = pendingOps(U);
    expect(ops).toHaveLength(1);
    expect(ops[0]).toMatchObject({ entity: "user_pathway_task", key: "t1", kind: "patch", stamp: 200, v: 3 });
    expect(ops[0]?.fields).toEqual({ status: "done", notes: "booked" });
  });

  it("sends entries in the order they were made and clears the confirmed ones", async () => {
    queueTask(U, "t1", { status: "done" }, 100);
    queueCostCreate(U, "c1", { label: "Fee", amount: 10, currency: "USD" }, 101);
    const { t, calls } = transport();
    expect(await flushOps(U, t)).toBe(2);
    expect(calls.map((c) => `${c.op}:${c.key}`)).toEqual(["patchTask:t1", "createCost:c1"]);
    expect(pendingOps(U)).toEqual([]);
  });

  it("stops at the first entry the network could not carry and keeps everything", async () => {
    queueTask(U, "t1", { status: "done" }, 1);
    queueTask(U, "t2", { status: "done" }, 2);
    const { t, calls } = transport({ patchTask: "unreachable" });
    expect(await flushOps(U, t)).toBe(0);
    expect(calls).toHaveLength(1);
    expect(pendingOps(U)).toHaveLength(2);
  });

  it("a change made while an entry is in flight survives the confirmation of the older one", async () => {
    queueTask(U, "t1", { status: "doing" }, 1);
    const t: PathwayTransport = {
      ...transport().t,
      patchTask: async () => {
        queueTask(U, "t1", { status: "done" }, 2);
        return "ok";
      },
    };
    await flushOps(U, t);
    expect(pendingFor(U, "user_pathway_task", "t1")?.fields).toEqual({ status: "done" });
  });

  it("a flush with nothing to send does not wedge the next one", async () => {
    const { t, calls } = transport();
    expect(await flushOps(U, t)).toBe(0);
    queueTask(U, "t1", { status: "done" }, 1);
    expect(await flushOps(U, t)).toBe(1);
    expect(calls).toHaveLength(1);
  });

  it("keeps a refused entry, marked, and does not resend it", async () => {
    queueCostCreate(U, "c1", { label: "x", amount: 1, currency: "USD" }, 1);
    const { t, calls } = transport({ createCost: { refused: "cost_limit" } });
    await flushOps(U, t);
    expect(pendingFor(U, "cost", "c1")?.refused).toBe("cost_limit");
    await flushOps(U, t);
    expect(calls).toHaveLength(1);
    discardOp(U, "cost", "c1");
    expect(pendingOps(U)).toEqual([]);
  });

  it("a cost created and deleted before anything was sent never reaches the server", async () => {
    queueCostCreate(U, "c1", { label: "x", amount: 1, currency: "USD" }, 1);
    queueCostDelete(U, "c1", 2);
    expect(pendingOps(U)).toEqual([]);
    const { t, calls } = transport();
    await flushOps(U, t);
    expect(calls).toEqual([]);
  });

  it("an edit of an unsent create stays a create; an edit of a sent cost is a patch; a delete of a sent create follows it", async () => {
    queueCostCreate(U, "c1", { label: "x", amount: 1, currency: "USD" }, 1);
    queueCostUpdate(U, "c1", { amount: 5 }, 2);
    expect(pendingFor(U, "cost", "c1")).toMatchObject({
      kind: "create",
      fields: { label: "x", amount: 5, currency: "USD" },
    });
    queueCostUpdate(U, "c2", { amount: 9 }, 3);
    expect(pendingFor(U, "cost", "c2")?.kind).toBe("patch");
    const { t, calls } = transport({ createCost: "unreachable" });
    await flushOps(U, t); // the create was attempted but not confirmed
    queueCostDelete(U, "c1", 4);
    expect(pendingFor(U, "cost", "c1")?.kind).toBe("delete");
    const second = transport();
    await flushOps(U, second.t);
    expect(second.calls.map((c) => c.op)).toEqual(expect.arrayContaining(["deleteCost", "patchCost"]));
    expect(calls).toHaveLength(1);
  });

  it("LWW: a newer version from another device wins; this one is kept 7 days and the row is marked", async () => {
    queueTask(U, "t5", { status: "doing" }, 10);
    const server = { task: { id: "t5", status: "done" }, applied: false };
    const t: PathwayTransport = {
      patchTask: async () => ({ conflict: server }),
      createCost: async () => "ok",
      patchCost: async () => "ok",
      deleteCost: async () => "ok",
    };
    expect(await flushOps(U, t)).toBe(1);
    expect(pendingFor(U, "user_pathway_task", "t5")).toBeUndefined();
    expect(wasChangedElsewhere("user_pathway_task", "t5")).toBe(true);
    expect((await conflictsFor(U)).map((c) => [c.key, c.local])).toEqual([["t5", { status: "doing" }]]);
  });

  it("survives a reload: entries come back from IndexedDB, per user", async () => {
    queueTask(U, "t1", { status: "done" }, 1);
    queueTask("someone-else", "t9", { status: "done" }, 1);
    await new Promise((r) => setTimeout(r, 20));
    resetPathwayDbForTests();
    expect(pendingOps(U)).toEqual([]);
    await loadOps(U);
    expect(pendingOps(U).map((o) => o.key)).toEqual(["t1"]);
  });
});
