import "fake-indexeddb/auto";
import { Dexie } from "dexie";
import { beforeEach, describe, expect, it } from "vitest";
import { DB_NAME, resetNaadiDbForTests } from "../offline/db";
import { clearOffline, pendingOfflineWrites, readOffline, subscribeOfflineWrites, writeOffline } from "./offlineCache";

describe("offline copy (naadi-v1.reads)", () => {
  beforeEach(async () => {
    resetNaadiDbForTests();
    await Dexie.delete(DB_NAME);
  });

  it("stores per user and kind, and is wiped on clear", async () => {
    await writeOffline("u1", "chapter", "ch01", { title: "Biochemistry" });
    expect(await readOffline("u1", "chapter", "ch01")).toEqual({ title: "Biochemistry" });
    expect(await readOffline("u2", "chapter", "ch01")).toBeUndefined();
    expect(await readOffline("u1", "courses", "ch01")).toBeUndefined();
    await clearOffline();
    expect(await readOffline("u1", "chapter", "ch01")).toBeUndefined();
  });

  it("never stores an explanation of an unanswered question; keeps it once graded", async () => {
    const view = (attempt: unknown, explanation: unknown) => ({ question: { id: "q1" }, attempt, explanation });
    await writeOffline("u1", "practice-question", "a", view(null, { answer: "B", explanation_html: "<p>x</p>" }));
    expect(await readOffline("u1", "practice-question", "a")).toEqual({ question: { id: "q1" }, attempt: null });
    const graded = view({ chosen: "B", correct: true, seconds: 3 }, { answer: "B" });
    await writeOffline("u1", "practice-question", "b", graded);
    expect(await readOffline("u1", "practice-question", "b")).toEqual(graded);
  });

  it("counts a write until it settles", async () => {
    let seen = 0;
    const off = subscribeOfflineWrites(() => {
      seen += 1;
    });
    const p = writeOffline("u1", "chapter", "x", { a: 1 });
    expect(pendingOfflineWrites()).toBe(1);
    await p;
    expect(pendingOfflineWrites()).toBe(0);
    expect(seen).toBe(2);
    off();
  });
});
