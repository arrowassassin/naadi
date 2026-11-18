import "fake-indexeddb/auto";
import { Dexie } from "dexie";
import { beforeEach, describe, expect, it } from "vitest";
import { readOffline } from "../learn/offlineCache";
import { cachedBytes, clearCacheFor } from "./cache";
import { CACHE_TABLES, DB_NAME, getMeta, naadiDb, resetNaadiDbForTests } from "./db";
import { type DownloadRecord, storeManifest, type WeekManifest } from "./download";
import { findLeaks } from "./guard";

const U = "u1";
const unanswered = {
  question: { id: "q1", stem_html: "<p>A 34-year-old…</p>", options: [{ label: "A", text_html: "x" }] },
  attempt: null,
  // A server bug or a tampered response: the guard must still keep these off the device.
  explanation: {
    answer: "B",
    explanation_html: "<p>because</p>",
    why_not_html: "<p>no</p>",
    educational_objective: "o",
  },
};
const graded = {
  question: { id: "q2", stem_html: "<p>…</p>", options: [] },
  attempt: { chosen: "B", correct: true, seconds: 20 },
  explanation: { answer: "B", explanation_html: "<p>ok</p>" },
};

const manifest: WeekManifest = {
  start: "2026-11-16",
  end: "2026-11-22",
  parts: [
    { part: "chapters", bytes: 120, count: 1 },
    { part: "questions", bytes: 300, count: 2 },
    { part: "cards", bytes: 40, count: 1 },
  ],
  chapters: [{ id: "ch01", title: "Biochemistry" } as { id: string }],
  positions: [{ chapter_id: "ch01" }],
  questions: [
    { block_id: "b1", n: 1, view: unanswered },
    { block_id: "b1", n: 2, view: graded },
  ],
  due: { items: [{ id: "c1", front: "f", back: "b", question: { answer_html: "<p>leak</p>" } }] },
  notes: { items: [] },
  notebooks: [{ id: "nb1" }],
  ink: [{ surface_type: "section", surface_id: "s1", layer: { rev: 3, items: [] } }],
  plan: { days: [] },
};

async function everyStoredValue(): Promise<unknown[]> {
  const out: unknown[] = [];
  for (const t of [...CACHE_TABLES, "outbox", "meta", "conflicts"] as const)
    out.push(...(await naadiDb().table(t).toArray()));
  return out;
}

describe("Download this week", () => {
  beforeEach(async () => {
    resetNaadiDbForTests();
    await Dexie.delete(DB_NAME);
  });

  it("stores each part under the screens' offline keys and records the download", async () => {
    const seen: string[] = [];
    const rec = await storeManifest(U, manifest, (p) => seen.push(`${p.part}:${p.done}/${p.total}`));
    expect(seen).toContain("questions:2/2");
    expect(await readOffline(U, "chapter", "ch01")).toMatchObject({ title: "Biochemistry" });
    expect(await readOffline(U, "practice-question", "b1:2")).toEqual(graded);
    expect(rec).toMatchObject({ start: "2026-11-16", end: "2026-11-22" });
    expect(await getMeta<DownloadRecord>(U, "download")).toMatchObject({ end: "2026-11-22" });
    expect((await cachedBytes(U)).inkLayers).toBeGreaterThan(0);
  });

  it("leak scan: after a full download no store holds an answer, explanation, why-not or objective", async () => {
    await storeManifest(U, manifest);
    const stored = await everyStoredValue();
    expect(findLeaks(stored)).toEqual([]);
    // The unanswered question is still there to answer, without its explanation.
    expect(await readOffline(U, "practice-question", "b1:1")).toEqual({ question: unanswered.question, attempt: null });
  });

  it("remove download clears the copy and the record", async () => {
    await storeManifest(U, manifest);
    await clearCacheFor(U);
    expect(await readOffline(U, "chapter", "ch01")).toBeUndefined();
    expect(await getMeta(U, "download")).toBeUndefined();
    expect(Object.values(await cachedBytes(U)).every((n) => n === 0)).toBe(true);
  });
});
