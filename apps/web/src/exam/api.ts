import {
  type ActiveExamOut,
  type BlockClock,
  type BlockDetail,
  changeBlockSection,
  createMockBlock,
  finishBlock,
  getActiveExam,
  getBlockClock,
} from "@naadi/shared";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { authed } from "../auth/api";
import { blockKey, blocksKey, questionKey, useUserId } from "../practice/api";
import { addSample, bestOffset, type ClockSample, monoNow, wallJumped, wallSkew } from "./clock";

export type SectionAction = "start" | "pause" | "resume" | "end";

export const activeExamKey = (userId: string | null) => ["exam-active", userId] as const;

/**
 * The caller's timed/exam session that is running, paused or on a break (GET /api/exam/active, the server's
 * `active_exam()`): the shell gates Search and tutor practice on it, also after "Leave" mid-exam, and the
 * assistant (S14) stays silent while an exam-mode session is active. Polled every 30 s and on focus.
 */
export function useActiveExam() {
  const userId = useUserId();
  return useQuery({
    queryKey: activeExamKey(userId),
    enabled: userId !== null,
    staleTime: 10_000,
    refetchInterval: 30_000,
    refetchOnWindowFocus: true,
    queryFn: ({ signal }) => authed<ActiveExamOut>((headers) => getActiveExam({ signal, headers })),
  });
}

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
  /** serverTime ≈ monoNow() + offsetMs (0 until the first sample, or the cached offset when offline). */
  offsetMs: number;
  /** true when the last re-sync failed (the display keeps running from the last clock and offset). */
  stale: boolean;
  /** Re-read the clock now (after a transition, at 0, on visibility changes). */
  resync: () => Promise<BlockClock | null>;
  /** Adopt a clock embedded in another response (state only: no round-trip time is known for it). */
  adopt: (clock: BlockClock | null | undefined) => void;
}

const RESYNC_MS = 60_000;
const JUMP_CHECK_MS = 1_000;

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
      const sentAt = monoNow();
      try {
        const c = await authed<BlockClock>((headers) => getBlockClock({ headers, path: { block_id: blockId } }));
        const receivedAt = monoNow();
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
    // The device clock was set (or jumped) mid-session: samples are monotonic, but drop them anyway and measure
    // again at once, so a changed wall clock can never leave a stale estimate behind.
    let skew = wallSkew();
    const watch = window.setInterval(() => {
      const next = wallSkew();
      if (wallJumped(skew, next)) {
        samples.current = [];
        void resync();
      }
      skew = next;
    }, JUMP_CHECK_MS);
    document.addEventListener("visibilitychange", again);
    window.addEventListener("pageshow", again);
    window.addEventListener("focus", again);
    window.addEventListener("online", again);
    return () => {
      window.clearInterval(id);
      window.clearInterval(watch);
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
      void qc.invalidateQueries({ queryKey: activeExamKey(userId) });
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
      void qc.invalidateQueries({ queryKey: activeExamKey(userId) });
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
