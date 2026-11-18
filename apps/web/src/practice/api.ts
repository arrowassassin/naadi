import {
  type AttemptIn,
  type AttemptOut,
  type BlockCreate,
  type BlockDetail,
  type BlockList,
  type BlockSummary,
  createAttempt,
  createBlock,
  createSingleQuestionBlock,
  createWeakSpotBlock,
  type FlagOut,
  flagAttempt,
  getBlock,
  getBlockQuestion,
  getLabValues,
  getQbankPool,
  type LabValuesOut,
  listBlocks,
  type PoolResponse,
  type QuestionView,
  type WeakSpotBlock,
  type WeakSpotCreate,
} from "@naadi/shared";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useMemo } from "react";
import { authed } from "../auth/api";
import { ApiError } from "../auth/apiError";
import { useAuth } from "../auth/session";
import { installOfflineCacheReset, readOffline, writeOffline } from "../learn/offlineCache";
import { OFFLINE_FIRST } from "../offline/runtime";

installOfflineCacheReset();

export function useUserId(): string | null {
  const auth = useAuth();
  return auth.status === "signed-in" ? auth.session.user.id : null;
}

/** A network failure (no HTTP response) rather than an answer from the server. */
export function isUnreachable(e: unknown): boolean {
  return e instanceof ApiError && e.status === null && e.code === null;
}

export interface Cached<T> {
  data: T;
  /** true when the network failed and this is the copy saved on this device. */
  fromCache: boolean;
}

export interface PoolFilter {
  exam?: string;
  systems: readonly string[];
  disciplines: readonly string[];
  status: "all" | "unused" | "incorrect" | "flagged" | "correct";
  difficulty: "easy" | "medium" | "hard" | null;
}

export const poolKey = (userId: string | null, f: PoolFilter) =>
  ["qbank-pool", userId, f.exam ?? null, [...f.systems], [...f.disciplines], f.status, f.difficulty] as const;
export const blocksKey = (userId: string | null) => ["qbank-blocks", userId] as const;
export const blockKey = (userId: string | null, id: string) => ["qbank-block", userId, id] as const;
export const questionKey = (userId: string | null, id: string, n: number) => ["qbank-question", userId, id, n] as const;

export function usePool(filter: PoolFilter) {
  const userId = useUserId();
  return useQuery({
    queryKey: poolKey(userId, filter),
    enabled: userId !== null,
    placeholderData: keepPreviousData,
    queryFn: ({ signal }) =>
      authed<PoolResponse>((headers) =>
        getQbankPool({
          signal,
          headers,
          query: {
            exam: filter.exam,
            systems: filter.systems.length ? [...filter.systems] : undefined,
            disciplines: filter.disciplines.length ? [...filter.disciplines] : undefined,
            status: filter.status,
            difficulty: filter.difficulty ?? undefined,
          },
        }),
      ),
  });
}

export function useBlocks() {
  const userId = useUserId();
  return useQuery({
    queryKey: blocksKey(userId),
    enabled: userId !== null,
    queryFn: ({ signal }) => authed<BlockList>((headers) => listBlocks({ signal, headers, query: { limit: 20 } })),
  });
}

export function useBlock(blockId: string) {
  const userId = useUserId();
  return useQuery({
    queryKey: blockKey(userId, blockId),
    ...OFFLINE_FIRST,
    enabled: userId !== null,
    // A copy kept on this device (and written by "Download this week"), so a block opens offline.
    queryFn: async ({ signal }) => {
      try {
        const data = await authed<BlockDetail>((headers) => getBlock({ signal, headers, path: { block_id: blockId } }));
        if (userId) void writeOffline(userId, "practice-block", blockId, data);
        return data;
      } catch (err) {
        if (userId && isUnreachable(err)) {
          const saved = await readOffline<BlockDetail>(userId, "practice-block", blockId);
          if (saved !== undefined) return saved;
        }
        throw err;
      }
    },
  });
}

/** A block question, with a copy kept on this device so an in-progress block continues without a network. */
async function loadQuestion(
  userId: string,
  blockId: string,
  n: number,
  signal: AbortSignal | undefined,
): Promise<Cached<QuestionView>> {
  const id = `${blockId}:${n}`;
  try {
    const data = await authed<QuestionView>((headers) =>
      getBlockQuestion({ signal, headers, path: { block_id: blockId, n } }),
    );
    void writeOffline(userId, "practice-question", id, data);
    return { data, fromCache: false };
  } catch (err) {
    if (isUnreachable(err)) {
      const saved = await readOffline<QuestionView>(userId, "practice-question", id);
      if (saved !== undefined) return { data: saved, fromCache: true };
    }
    throw err;
  }
}

export function useBlockQuestion(blockId: string, n: number) {
  const userId = useUserId();
  return useQuery({
    queryKey: questionKey(userId, blockId, n),
    ...OFFLINE_FIRST,
    enabled: userId !== null,
    staleTime: 30_000,
    queryFn: ({ signal }) => loadQuestion(userId as string, blockId, n, signal),
  });
}

/** Fetch the next question ahead of time (and save it on this device) so Next is instant, even offline. */
export function usePrefetchQuestion() {
  const userId = useUserId();
  const qc = useQueryClient();
  return useCallback(
    (blockId: string, n: number) => {
      if (!userId) return;
      void qc.prefetchQuery({
        queryKey: questionKey(userId, blockId, n),
        ...OFFLINE_FIRST,
        staleTime: 30_000,
        queryFn: ({ signal }) => loadQuestion(userId, blockId, n, signal),
      });
    },
    [qc, userId],
  );
}

export function useLabValues(enabled: boolean) {
  const userId = useUserId();
  return useQuery({
    queryKey: ["qbank-lab-values", userId],
    ...OFFLINE_FIRST,
    enabled: enabled && userId !== null,
    staleTime: 10 * 60_000,
    queryFn: async ({ signal }) => {
      const uid = userId as string;
      try {
        const data = await authed<LabValuesOut>((headers) => getLabValues({ signal, headers }));
        void writeOffline(uid, "practice-labs", "all", data);
        return data;
      } catch (err) {
        if (isUnreachable(err)) {
          const saved = await readOffline<LabValuesOut>(uid, "practice-labs", "all");
          if (saved !== undefined) return saved;
        }
        throw err;
      }
    },
  });
}

function useInvalidatePractice() {
  const userId = useUserId();
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: ["qbank-pool", userId] });
    void qc.invalidateQueries({ queryKey: blocksKey(userId) });
  };
}

export function useCreateBlock() {
  const invalidate = useInvalidatePractice();
  return useMutation({
    mutationFn: (body: BlockCreate) => authed<BlockDetail>((headers) => createBlock({ headers, body })),
    onSuccess: invalidate,
  });
}

export function useCreateWeakSpotBlock() {
  const invalidate = useInvalidatePractice();
  return useMutation({
    mutationFn: (body: WeakSpotCreate) => authed<WeakSpotBlock>((headers) => createWeakSpotBlock({ headers, body })),
    onSuccess: invalidate,
  });
}

export function useCreateSingleBlock() {
  return useMutation({
    mutationFn: (questionId: string) =>
      authed<BlockDetail>((headers) => createSingleQuestionBlock({ headers, body: { question_id: questionId } })),
  });
}

/** Post one answer; `send` is also used to replay answers saved while offline. */
export function useSendAttempt() {
  const userId = useUserId();
  const qc = useQueryClient();
  return useMemo(
    () => ({
      send: (blockId: string, body: AttemptIn) =>
        authed<AttemptOut>((headers) => createAttempt({ headers, path: { block_id: blockId }, body })),
      /** After the server graded: show the result on the question, refresh the block and the pool counts. */
      applied: (blockId: string, out: AttemptOut) => {
        qc.setQueryData<Cached<QuestionView>>(questionKey(userId, blockId, out.n), (old) =>
          old
            ? {
                data: {
                  ...old.data,
                  flagged: out.flagged,
                  attempt: { chosen: out.chosen, seconds: out.seconds, correct: out.correct },
                  explanation: out.explanation,
                },
                fromCache: false,
              }
            : old,
        );
        void qc.invalidateQueries({ queryKey: questionKey(userId, blockId, out.n) });
        void qc.invalidateQueries({ queryKey: blockKey(userId, blockId) });
        void qc.invalidateQueries({ queryKey: ["qbank-pool", userId] });
        void qc.invalidateQueries({ queryKey: blocksKey(userId) });
      },
    }),
    [qc, userId],
  );
}

export function useFlag(blockId: string) {
  const userId = useUserId();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ questionId, flagged }: { questionId: string; flagged: boolean }) =>
      authed<FlagOut>((headers) =>
        flagAttempt({ headers, path: { block_id: blockId, question_id: questionId }, body: { flagged } }),
      ),
    onSuccess: (out) => {
      qc.setQueryData<Cached<QuestionView>>(questionKey(userId, blockId, out.n), (old) =>
        old ? { ...old, data: { ...old.data, flagged: out.flagged } } : old,
      );
      void qc.invalidateQueries({ queryKey: blockKey(userId, blockId) });
      void qc.invalidateQueries({ queryKey: ["qbank-pool", userId] });
    },
  });
}

export type { BlockSummary };
