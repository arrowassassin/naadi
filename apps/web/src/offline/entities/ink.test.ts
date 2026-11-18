import "fake-indexeddb/auto";
import { type InkItem, uuidv7 } from "@naadi/shared";
import { Dexie } from "dexie";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { dexieStore, inkRowsForTests, resetInkDbForTests } from "../../ink/persist/db";
import { replay, startOutbox, whenPersisted } from "../outbox";
import "./ink";

const sent = vi.hoisted(() => ({ items: [] as { surface: string; ids: string[] }[], invalid: new Set<string>() }));
vi.mock("../../ink/persist/transport", () => ({
  classifyInkError: () => "offline",
  apiTransport: (type: string, id: string) => ({
    load: async () => ({ rev: 1, items: [], reset: false, outdated: 0 }),
    send: async (_rev: number, items: InkItem[]) => {
      sent.items.push({ surface: `${type}:${id}`, ids: items.map((i) => i.id) });
      return {
        rev: 2,
        applied: items.filter((i) => !sent.invalid.has(i.id)).map((i) => i.id),
        rejected: items.filter((i) => sent.invalid.has(i.id)).map((i) => ({ id: i.id, reason: "invalid" as const })),
        remote: [],
        reset: false,
      };
    },
    beacon: () => false,
  }),
}));

const T = Date.now();
const stroke = (): InkItem => ({
  id: uuidv7(),
  tool: "pen",
  color: "ink",
  size: 0.002,
  points: [
    [0.1, 0.1, 0.5, 0],
    [0.2, 0.2, 0.5, 8],
  ],
  createdAt: T,
  updatedAt: T,
});

describe("ink drawn on a surface the student has left", () => {
  beforeEach(async () => {
    await whenPersisted();
    resetInkDbForTests();
    await Dexie.delete("naadi-v1");
    resetInkDbForTests();
    sent.items.length = 0;
    sent.invalid.clear();
  });

  it("is sent by the outbox per surface; applied items leave it, invalid ones are quarantined, not dropped", async () => {
    const a = stroke();
    const b = stroke();
    const c = stroke();
    await dexieStore("u1", "section:s1").putPending([a, b]);
    await dexieStore("u1", "flashcard:k1").putPending([c]);
    sent.invalid.add(b.id);
    await startOutbox("u1");
    await replay();
    expect(sent.items.map((s) => s.surface).sort()).toEqual(["flashcard:k1", "section:s1"]);
    const left = inkRowsForTests("u1");
    expect(left.pending).toEqual([]);
    expect(left.rejected.map((i) => i.id)).toEqual([b.id]);
  });
});
