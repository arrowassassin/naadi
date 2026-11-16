import type { InkItem } from "@naadi/shared";
import Dexie, { type EntityTable } from "dexie";

/**
 * What a student marks on an exam question that stays on this device (decision, segment 09): highlights and
 * struck-out options (like test day, they help while answering and are not synced), and scratch ink until the
 * cross-device ink API takes it over. Keyed by user, block and question; everything of a block is deleted
 * seven days after the block finished (`sweep`). Never holds an answer or anything from the server.
 */
export interface QuestionMarks {
  /** `${userId}:${blockId}:${n}` */
  key: string;
  userId: string;
  blockId: string;
  n: number;
  /** Character ranges over the stem's text content: [start, end). */
  highlights: [number, number][];
  /** Option labels the student struck out. */
  struck: string[];
  updatedAt: number;
}

export interface ScratchInk {
  /** `${userId}:${blockId}:${n}` (surface id `{block_id}:{n}`, kind exam_scratch) */
  key: string;
  userId: string;
  blockId: string;
  n: number;
  items: InkItem[];
  updatedAt: number;
}

export interface FinishedBlock {
  /** `${userId}:${blockId}` */
  key: string;
  userId: string;
  blockId: string;
  finishedAt: number;
}

class ExamDb extends Dexie {
  marks!: EntityTable<QuestionMarks, "key">;
  scratch!: EntityTable<ScratchInk, "key">;
  finished!: EntityTable<FinishedBlock, "key">;

  constructor() {
    super("naadi-exam");
    this.version(1).stores({
      marks: "key, [userId+blockId], updatedAt",
      scratch: "key, [userId+blockId], updatedAt",
      finished: "key, userId, finishedAt",
    });
  }
}

let db: ExamDb | null = null;

function open(): ExamDb | null {
  if (typeof indexedDB === "undefined") return null;
  db ??= new ExamDb();
  return db;
}

export const KEEP_AFTER_FINISH_MS = 7 * 24 * 3600_000;

const keyOf = (userId: string, blockId: string, n: number) => `${userId}:${blockId}:${n}`;

async function safe<T>(fallback: T, fn: (d: ExamDb) => Promise<T>): Promise<T> {
  const d = open();
  if (!d) return fallback;
  try {
    return await fn(d);
  } catch {
    return fallback; // private mode, quota, blocked upgrade: marks are a convenience, never fatal
  }
}

export function readMarks(userId: string, blockId: string, n: number): Promise<QuestionMarks | undefined> {
  return safe(undefined, (d) => d.marks.get(keyOf(userId, blockId, n)));
}

export function writeMarks(
  userId: string,
  blockId: string,
  n: number,
  marks: Pick<QuestionMarks, "highlights" | "struck">,
): Promise<void> {
  return safe(undefined, async (d) => {
    await d.marks.put({ key: keyOf(userId, blockId, n), userId, blockId, n, ...marks, updatedAt: Date.now() });
  });
}

export function readScratch(userId: string, blockId: string, n: number): Promise<InkItem[]> {
  return safe([], async (d) => (await d.scratch.get(keyOf(userId, blockId, n)))?.items ?? []);
}

export function writeScratch(userId: string, blockId: string, n: number, items: InkItem[]): Promise<void> {
  return safe(undefined, async (d) => {
    await d.scratch.put({ key: keyOf(userId, blockId, n), userId, blockId, n, items, updatedAt: Date.now() });
  });
}

/** Remember when a block finished (first time only), so `sweep` can clear its marks a week later. */
export function markFinished(userId: string, blockId: string, finishedAt: number): Promise<void> {
  return safe(undefined, async (d) => {
    const key = `${userId}:${blockId}`;
    if (!(await d.finished.get(key))) await d.finished.put({ key, userId, blockId, finishedAt });
  });
}

/** Delete the marks and scratch ink of every block of this user that finished more than seven days ago. */
export function sweep(userId: string, now = Date.now()): Promise<number> {
  return safe(0, async (d) => {
    const old = await d.finished
      .where("userId")
      .equals(userId)
      .filter((f) => now - f.finishedAt > KEEP_AFTER_FINISH_MS)
      .toArray();
    for (const f of old) {
      await d.transaction("rw", d.marks, d.scratch, d.finished, async () => {
        await d.marks.where("[userId+blockId]").equals([userId, f.blockId]).delete();
        await d.scratch.where("[userId+blockId]").equals([userId, f.blockId]).delete();
        await d.finished.delete(f.key);
      });
    }
    return old.length;
  });
}

/** Sign-out or a user change: nothing of this user's stays on the device. */
export function clearUser(userId: string): Promise<void> {
  return safe(undefined, async (d) => {
    await d.transaction("rw", d.marks, d.scratch, d.finished, async () => {
      await d.marks.where("[userId+blockId]").between([userId, Dexie.minKey], [userId, Dexie.maxKey]).delete();
      await d.scratch.where("[userId+blockId]").between([userId, Dexie.minKey], [userId, Dexie.maxKey]).delete();
      await d.finished.where("userId").equals(userId).delete();
    });
  });
}
