import "fake-indexeddb/auto";
import { beforeEach, describe, expect, it } from "vitest";
import {
  flushOps,
  loadOps,
  type NoteFields,
  type NotesTransport,
  overlay,
  pendingOps,
  queueCreate,
  queueDelete,
  queueUpdate,
  resetNotesDbForTests,
  type SendResult,
} from "./outbox";

const U = "user-1";

function fakeTransport(script: Partial<Record<"create" | "update" | "remove", SendResult[]>> = {}) {
  const calls: { op: string; id: string; fields?: NoteFields }[] = [];
  const next = (op: "create" | "update" | "remove"): SendResult => script[op]?.shift() ?? "ok";
  const t: NotesTransport = {
    async create(id, fields) {
      calls.push({ op: "create", id, fields });
      return next("create");
    },
    async update(id, fields) {
      calls.push({ op: "update", id, fields });
      return next("update");
    },
    async remove(id) {
      calls.push({ op: "remove", id });
      return next("remove");
    },
  };
  return { t, calls };
}

beforeEach(async () => {
  resetNotesDbForTests();
  indexedDB.deleteDatabase("naadi-notes");
});

describe("notes offline queue", () => {
  it("records a write before sending and clears it once the server took it", async () => {
    await queueCreate(U, "n1", { kind: "note", body: { type: "doc" } });
    expect(pendingOps(U)).toHaveLength(1);
    const { t, calls } = fakeTransport();
    expect(await flushOps(U, t)).toBe(1);
    expect(calls).toEqual([{ op: "create", id: "n1", fields: { kind: "note", body: { type: "doc" } } }]);
    expect(pendingOps(U)).toEqual([]);
  });

  it("folds later edits into a create that has not been sent", async () => {
    await queueCreate(U, "n1", { kind: "highlight", highlight_color: "lime" });
    await queueUpdate(U, "n1", { highlight_color: "amber" });
    const { t, calls } = fakeTransport();
    await flushOps(U, t);
    expect(calls).toEqual([{ op: "create", id: "n1", fields: { kind: "highlight", highlight_color: "amber" } }]);
  });

  it("keeps everything, in order, while the server cannot be reached", async () => {
    await queueCreate(U, "a", { kind: "note" });
    await queueCreate(U, "b", { kind: "note" });
    const { t, calls } = fakeTransport({ create: ["unreachable"] });
    expect(await flushOps(U, t)).toBe(0);
    expect(calls.map((c) => c.id)).toEqual(["a"]);
    expect(pendingOps(U).map((o) => o.id)).toEqual(["a", "b"]);
    const again = fakeTransport();
    expect(await flushOps(U, again.t)).toBe(2);
    expect(again.calls.map((c) => c.id)).toEqual(["a", "b"]);
  });

  it("never sends a note made and deleted before any request", async () => {
    await queueCreate(U, "n1", { kind: "note" });
    await queueDelete(U, "n1");
    expect(pendingOps(U)).toEqual([]);
  });

  it("deletes a note whose create may have reached the server", async () => {
    await queueCreate(U, "n1", { kind: "note" });
    await flushOps(U, fakeTransport({ create: ["unreachable"] }).t);
    await queueDelete(U, "n1");
    const { t, calls } = fakeTransport();
    await flushOps(U, t);
    expect(calls.map((c) => c.op)).toEqual(["remove"]);
    expect(pendingOps(U)).toEqual([]);
  });

  it("treats a delete of a note that is already gone as done", async () => {
    await queueDelete(U, "gone");
    await flushOps(U, fakeTransport({ remove: ["gone"] }).t);
    expect(pendingOps(U)).toEqual([]);
  });

  it("keeps a refused write with its reason and moves on", async () => {
    await queueCreate(U, "q", { kind: "highlight", anchor_type: "question" });
    await queueCreate(U, "ok", { kind: "note" });
    const { t } = fakeTransport({ create: [{ refused: "note_question_not_attempted" }] });
    expect(await flushOps(U, t)).toBe(1);
    expect(pendingOps(U)).toMatchObject([{ id: "q", refused: "note_question_not_attempted" }]);
  });

  it("sends an edit made while a create was in flight as an update", async () => {
    await queueCreate(U, "n1", { kind: "note", title: "a" });
    let release: (r: SendResult) => void = () => undefined;
    const calls: string[] = [];
    const t: NotesTransport = {
      create: () => {
        calls.push("create");
        return new Promise<SendResult>((r) => {
          release = r;
        });
      },
      update: async (_id, f) => {
        calls.push(`update:${String(f.title)}`);
        return "ok";
      },
      remove: async () => "ok",
    };
    const done = flushOps(U, t);
    await new Promise((r) => setTimeout(r, 0));
    await queueUpdate(U, "n1", { title: "b" });
    release("ok");
    await done;
    expect(calls).toEqual(["create", "update:b"]);
    expect(pendingOps(U)).toEqual([]);
  });

  it("survives a reload (IndexedDB)", async () => {
    await queueCreate(U, "n1", { kind: "note" });
    resetNotesDbForTests();
    expect(pendingOps(U)).toEqual([]);
    await loadOps(U);
    expect(pendingOps(U).map((o) => o.id)).toEqual(["n1"]);
  });

  it("keeps users apart", async () => {
    await queueCreate(U, "n1", { kind: "note" });
    await queueCreate("user-2", "n2", { kind: "note" });
    expect(pendingOps(U).map((o) => o.id)).toEqual(["n1"]);
  });
});

describe("overlay", () => {
  const base = [
    { id: "a", title: "A" },
    { id: "b", title: "B" },
  ];
  it("applies unsent edits, hides unsent deletes and lists unsent creates first", async () => {
    await queueUpdate(U, "a", { title: "A2" });
    await queueDelete(U, "b");
    await queueCreate(U, "c", { title: "C", kind: "note" });
    const out = overlay(base, pendingOps(U));
    expect(out.map((n) => [n.id, n.title])).toEqual([
      ["c", "C"],
      ["a", "A2"],
    ]);
  });

  it("lists a local create only where it belongs", async () => {
    await queueCreate(U, "c", { kind: "highlight" });
    expect(overlay(base, pendingOps(U), (f) => f.kind === "note").map((n) => n.id)).toEqual(["a", "b"]);
  });
});
