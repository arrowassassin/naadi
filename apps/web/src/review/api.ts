import {
  type AuthoringGapList,
  type AuthoringRunIn,
  type AuthoringRunJob,
  type AuthoringRunList,
  type AuthoringTaxonomyList,
  actOnPsychometrics,
  getAppConfig,
  getAuthoringTaxonomy,
  getReviewItem,
  getReviewSummary,
  listAuthoringGaps,
  listAuthoringRuns,
  listReviewItems,
  type QuestionReviewIn,
  type ReviewItemDetail,
  type ReviewQueueItem,
  type ReviewQueueList,
  type ReviewSubmitResult,
  type ReviewSummary,
  resubmitReviewItem,
  runPsychometrics,
  runReviewChecks,
  startAuthoringRun,
  submitReview,
} from "@naadi/shared";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { authed } from "../auth/api";
import { ApiError } from "../auth/apiError";
import { useAuth } from "../auth/session";

export type { ReviewItemDetail, ReviewQueueItem, ReviewSubmitResult, ReviewSummary };

export type QueueFilter = "ai_drafts" | "seed_spot_check" | "psychometric_flags" | "adjudication" | "needs_check";
export const FILTERS: readonly QueueFilter[] = [
  "ai_drafts",
  "seed_spot_check",
  "psychometric_flags",
  "adjudication",
  "needs_check",
];
export const PAGE = 25;
/** While a drafting run is going, the run list is re-read this often. */
export const RUN_POLL_MS = 4000;

const KNOWN = new Set([
  "item_not_found",
  "status_not_reviewable",
  "publish_blocked",
  "round_closed",
  "already_reviewed",
  "approve_contradicts_ratings",
  "too_few_responses",
  "not_flagged",
  "run_active",
  "cell_invalid",
  "authoring_unavailable",
  "rate_limited",
  "forbidden",
  "validation_error",
]);

/** The ui_copy key for an API error: a known code, or the generic wording (a plain outage reads as offline). */
export function reviewErrorKey(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === null) return "review.error.offline";
    if (e.code === "solver_unavailable")
      return e.status === 502 ? "review.error.solver_failed" : "review.error.solver_budget";
    if (e.code && KNOWN.has(e.code)) return `review.error.${e.code}`;
  }
  return "review.error.generic";
}

export function requestIdOf(e: unknown): string | null {
  return e instanceof ApiError ? e.requestId : null;
}

function useUserId(): string | null {
  const auth = useAuth();
  return auth.status === "signed-in" ? auth.session.user.id : null;
}

export const reviewKey = (userId: string | null) => ["review", userId] as const;

export function useReviewSummary(enabled = true) {
  const userId = useUserId();
  return useQuery({
    queryKey: [...reviewKey(userId), "summary"],
    enabled: enabled && userId !== null,
    queryFn: ({ signal }) => authed<ReviewSummary>((headers) => getReviewSummary({ signal, headers })),
  });
}

/** One queue filter, newest page last; "Load more" asks for the next `PAGE` rows. */
export function useReviewItems(filter: QueueFilter, enabled = true) {
  const userId = useUserId();
  return useInfiniteQuery({
    queryKey: [...reviewKey(userId), "items", filter],
    enabled: enabled && userId !== null,
    initialPageParam: 0,
    queryFn: ({ signal, pageParam }) =>
      authed<ReviewQueueList>((headers) =>
        listReviewItems({ signal, headers, query: { filter, limit: PAGE, offset: pageParam } }),
      ),
    getNextPageParam: (last, all) => (last.items.length < PAGE ? undefined : all.length * PAGE),
  });
}

export function useReviewItem(id: string) {
  const userId = useUserId();
  return useQuery({
    queryKey: [...reviewKey(userId), "item", id],
    enabled: userId !== null,
    queryFn: ({ signal }) =>
      authed<ReviewItemDetail>((headers) => getReviewItem({ signal, headers, path: { item_id: id } })),
  });
}

/** Re-read the queue and counts after anything that changes an item. */
export function useRefreshQueue() {
  const qc = useQueryClient();
  const userId = useUserId();
  return () => {
    void qc.invalidateQueries({ queryKey: [...reviewKey(userId), "items"] });
    void qc.invalidateQueries({ queryKey: [...reviewKey(userId), "summary"] });
  };
}

function useSetItem(id: string) {
  const qc = useQueryClient();
  const userId = useUserId();
  const refresh = useRefreshQueue();
  return (item: ReviewItemDetail) => {
    qc.setQueryData([...reviewKey(userId), "item", id], item);
    refresh();
  };
}

export function useSubmitReview(id: string) {
  const set = useSetItem(id);
  return useMutation({
    mutationFn: (body: QuestionReviewIn) =>
      authed<ReviewSubmitResult>((headers) => submitReview({ headers, path: { item_id: id }, body })),
    onSuccess: (r) => set(r.item),
  });
}

export function useRunChecks(id: string) {
  const set = useSetItem(id);
  return useMutation({
    mutationFn: (solver: boolean) =>
      authed<ReviewItemDetail>((headers) => runReviewChecks({ headers, path: { item_id: id }, body: { solver } })),
    onSuccess: set,
  });
}

export function useResubmit(id: string) {
  const set = useSetItem(id);
  return useMutation({
    mutationFn: () => authed<ReviewItemDetail>((headers) => resubmitReviewItem({ headers, path: { item_id: id } })),
    onSuccess: set,
  });
}

export function usePsychAction(id: string) {
  const set = useSetItem(id);
  return useMutation({
    mutationFn: (action: "keep" | "revise" | "retire") =>
      authed<ReviewItemDetail>((headers) => actOnPsychometrics({ headers, path: { item_id: id }, body: { action } })),
    onSuccess: set,
  });
}

export function useGaps(enabled: boolean) {
  const userId = useUserId();
  return useQuery({
    queryKey: [...reviewKey(userId), "gaps"],
    enabled: enabled && userId !== null,
    staleTime: 30_000,
    queryFn: ({ signal }) => authed<AuthoringGapList>((headers) => listAuthoringGaps({ signal, headers })),
  });
}

export function useTaxonomy(exam: "step1" | "step2ck", enabled: boolean) {
  const userId = useUserId();
  return useQuery({
    queryKey: [...reviewKey(userId), "taxonomy", exam],
    enabled: enabled && userId !== null,
    staleTime: 5 * 60_000,
    queryFn: ({ signal }) =>
      authed<AuthoringTaxonomyList>((headers) => getAuthoringTaxonomy({ signal, headers, query: { exam } })),
  });
}

export function isRunning(list: AuthoringRunList | undefined): boolean {
  return Boolean(list?.active.some((j) => j.status === "running"));
}

/** The recent runs; polled while one is running so its outcome appears without a reload. */
export function useRuns(enabled: boolean) {
  const userId = useUserId();
  return useQuery<AuthoringRunList>({
    queryKey: [...reviewKey(userId), "runs"],
    enabled: enabled && userId !== null,
    refetchInterval: (q) => (isRunning(q.state.data) ? RUN_POLL_MS : false),
    queryFn: ({ signal }) => authed<AuthoringRunList>((headers) => listAuthoringRuns({ signal, headers })),
  });
}

export function useStartRun() {
  const qc = useQueryClient();
  const userId = useUserId();
  const refresh = useRefreshQueue();
  return useMutation({
    mutationFn: (body: AuthoringRunIn) => authed<AuthoringRunJob>((headers) => startAuthoringRun({ headers, body })),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [...reviewKey(userId), "runs"] });
      refresh();
    },
  });
}

export function useRecompute() {
  const refresh = useRefreshQueue();
  return useMutation({
    mutationFn: () => authed((headers) => runPsychometrics({ headers })),
    onSuccess: refresh,
  });
}

/** The responses an item needs before it can be retired (`authoring.act_min_n`, readable by admins), or null. */
export function useActMinN(enabled: boolean) {
  const userId = useUserId();
  return useQuery({
    queryKey: [...reviewKey(userId), "act-min-n"],
    enabled: enabled && userId !== null,
    staleTime: 5 * 60_000,
    retry: false,
    queryFn: async ({ signal }) => {
      const entry = await authed((headers) => getAppConfig({ signal, headers, path: { key: "authoring" } }));
      const v = entry.value;
      const n = v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>).act_min_n : null;
      return typeof n === "number" ? n : null;
    },
  });
}
