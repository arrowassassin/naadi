import { type BlockResults, getBlockQuestion, getBlockResults, type QuestionView } from "@naadi/shared";
import { useQuery } from "@tanstack/react-query";
import { authed } from "../../auth/api";
import { ApiError } from "../../auth/apiError";
import { useUserId } from "../../practice/api";

export const resultsKey = (userId: string | null, blockId: string) => ["exam-results", userId, blockId] as const;

/** A refusal that a retry cannot change: not found, not finished, not a timed block. */
export function isFinal(err: unknown): boolean {
  return err instanceof ApiError && err.status !== null && err.status >= 400 && err.status < 500 && err.status !== 429;
}

/**
 * GET /api/blocks/{id}/results. Results of a finished block never change, so they stay fresh for the
 * session; a 409 (`not_finished`, `not_timed`) or 404 is final and not retried.
 */
export function useBlockResults(blockId: string) {
  const userId = useUserId();
  return useQuery({
    queryKey: resultsKey(userId, blockId),
    enabled: userId !== null,
    staleTime: Number.POSITIVE_INFINITY,
    retry: (count, err) => !isFinal(err) && count < 2,
    queryFn: ({ signal }) =>
      authed<BlockResults>((headers) => getBlockResults({ signal, headers, path: { block_id: blockId } })),
  });
}

export const reviewQuestionKey = (userId: string | null, blockId: string, n: number) =>
  ["exam-results", userId, blockId, "question", n] as const;

/**
 * A question of a finished block for review, with its answer and explanation. Its own cache entry: the copy
 * the exam view cached while the block ran has no answer fields and must not stand in for this one.
 */
export function useReviewQuestion(blockId: string, n: number, enabled: boolean) {
  const userId = useUserId();
  return useQuery({
    queryKey: reviewQuestionKey(userId, blockId, n),
    enabled: enabled && userId !== null,
    staleTime: 5 * 60_000,
    retry: (count, err) => !isFinal(err) && count < 2,
    queryFn: ({ signal }) =>
      authed<QuestionView>((headers) => getBlockQuestion({ signal, headers, path: { block_id: blockId, n } })),
  });
}
