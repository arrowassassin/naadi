import {
  type BlockClock,
  type BlockDetail,
  changeBlockSection,
  createMockBlock,
  finishBlock,
  getBlockClock,
} from "@naadi/shared";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { authed } from "../auth/api";
import { blockKey, blocksKey, questionKey, useUserId } from "../practice/api";
import { addSample, bestOffset, type ClockSample } from "./clock";

export type SectionAction = "start" | "pause" | "resume" | "end";

/** The last offset estimate per user, so an offline reload still shows the server's deadline correctly. */
const OFFSET_KEY = (userId: string) => `naadi.exam.offset.v1:${userId}`;

function readCachedOffset(userId: string | null): number | null {
  if (!userId) return null;
  try {
    const raw = localStorage.getItem(OFFSET_KEY(userId));
    const n = raw === null ? Number.NaN : Number(raw);
    return Number.isFinite(n) ? n : null;
  } catch {
    return null;
  }
}

function writeCachedOffset(userId: string | null, offset: number): void {
  if (!userId) return;
  try {
    localStorage.setItem(OFFSET_KEY(userId), String(Math.round(offset)));
  } catch {
    // storage unavailable: the next sample estimates it again
  }
}

export interface ServerClock {
  /** The latest clock the server sent (from the clock endpoint or any block response). */
  clock: BlockClock | null;
  /** serverTime ≈ Date.now() + offsetMs (0 until the first sample, or the cached offset when offline). */
  offsetMs: number;
  /** true when the last re-sync failed (the display keeps running from the last clock and offset). */
  stale: boolean;
  /** Re-read the clock now (after a transition, at 0, on visibility changes). */
  resync: () => Promise<BlockClock | null>;
  /** Adopt a clock embedded in another response (state only: no round-trip time is known for it). */
  adopt: (clock: BlockClock | null | undefined) => void;
}

const RESYNC_MS = 60_000;

/**
 * The server's clock for one block. The offset comes from the best-RTT sample of `GET /clock`, re-measured on
 * `visibilitychange`, `pageshow`, `focus` and every 60 s; the display is always `deadline − (now + offset)`.
 */
export function useServerClock(blockId: string, initial: BlockClock | null | undefined): ServerClock {
  const userId = useUserId();
  const [clock, setClock] = useState<BlockClock | null>(initial ?? null);
  const [offsetMs, setOffset] = useState<number>(() => readCachedOffset(userId) ?? 0);
  const [stale, setStale] = useState(false);
  const samples = useRef<ClockSample[]>([]);
  const inFlight = useRef<Promise<BlockClock | null> | null>(null);

  const resync = useCallback(async (): Promise<BlockClock | null> => {
    if (inFlight.current) return inFlight.current;
    const run = (async () => {
      const sentAt = Date.now();
      try {
        const c = await authed<BlockClock>((headers) => getBlockClock({ headers, path: { block_id: blockId } }));
        const receivedAt = Date.now();
        samples.current = addSample(samples.current, { sentAt, receivedAt, serverNow: c.server_now });
        const best = bestOffset(samples.current, receivedAt);
        if (best) {
          setOffset(best.offsetMs);
          writeCachedOffset(userId, best.offsetMs);
        }
        setClock(c);
        setStale(false);
        return c;
      } catch {
        setStale(true);
        return null;
      } finally {
        inFlight.current = null;
      }
    })();
    inFlight.current = run;
    return run;
  }, [blockId, userId]);

  const adopt = useCallback((c: BlockClock | null | undefined) => {
    if (!c) return;
    setClock((old) => (old && Date.parse(old.server_now) > Date.parse(c.server_now) ? old : c));
  }, []);

  useEffect(() => {
    void resync();
    const again = () => {
      if (document.visibilityState === "visible") void resync();
    };
    const id = window.setInterval(again, RESYNC_MS);
    document.addEventListener("visibilitychange", again);
    window.addEventListener("pageshow", again);
    window.addEventListener("focus", again);
    window.addEventListener("online", again);
    return () => {
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", again);
      window.removeEventListener("pageshow", again);
      window.removeEventListener("focus", again);
      window.removeEventListener("online", again);
    };
  }, [resync]);

  return { clock, offsetMs, stale, resync, adopt };
}

/** Start, pause, resume or end a section; the block (with its clock) comes back and replaces the cache. */
export function useSectionAction(blockId: string) {
  const userId = useUserId();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ n, action }: { n: number; action: SectionAction }) =>
      authed<BlockDetail>((headers) => changeBlockSection({ headers, path: { block_id: blockId, n, action } })),
    onSuccess: (block) => {
      qc.setQueryData(blockKey(userId, blockId), block);
      void qc.invalidateQueries({ queryKey: ["qbank-question", userId, blockId] });
      void qc.invalidateQueries({ queryKey: blocksKey(userId) });
    },
  });
}

/** End the whole session now (it is graded on the server); answers unlock for review. */
export function useFinishBlock(blockId: string) {
  const userId = useUserId();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => authed<BlockDetail>((headers) => finishBlock({ headers, path: { block_id: blockId } })),
    onSuccess: (block) => {
      qc.setQueryData(blockKey(userId, blockId), block);
      void qc.invalidateQueries({ queryKey: ["qbank-question", userId, blockId] });
      void qc.invalidateQueries({ queryKey: blocksKey(userId) });
      void qc.invalidateQueries({ queryKey: ["exam-results", userId, blockId] });
      void qc.invalidateQueries({ queryKey: ["qbank-pool", userId] });
    },
  });
}

export function useCreateMock() {
  const userId = useUserId();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (chapterId: "mock1" | "mock2") =>
      authed<BlockDetail>((headers) => createMockBlock({ headers, body: { chapter_id: chapterId } })),
    onSuccess: (block) => {
      qc.setQueryData(blockKey(userId, block.id), block);
      void qc.invalidateQueries({ queryKey: blocksKey(userId) });
    },
  });
}

export { questionKey };
