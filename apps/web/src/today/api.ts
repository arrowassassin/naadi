import {
  acceptCoachSuggestion,
  type CoachActionOut,
  type CoachSuggestionOut,
  createDiagnostic,
  type DiagnosticOut,
  dismissCoachSuggestion,
  getOnboarding,
  getToday,
  type OnboardingIn,
  type OnboardingOut,
  type OnboardingState,
  type PlanItemOut,
  type PlanPreview,
  previewPlan,
  saveOnboarding,
  setPlanItemStatus,
  type TodayOut,
} from "@naadi/shared";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { authed } from "../auth/api";
import { ApiError } from "../auth/apiError";
import { readOffline, writeOffline } from "../learn/offlineCache";
import { OFFLINE_FIRST } from "../offline/runtime";
import { isUnreachable, useUserId } from "../practice/api";

export const todayKey = (userId: string | null) => ["today", userId] as const;
export const onboardingKey = (userId: string | null) => ["onboarding", userId] as const;

export interface CachedToday {
  data: TodayOut;
  /** true when the network failed and this is the copy saved on this device. */
  fromCache: boolean;
}

/** Today in one request, kept on this device so the last day still shows offline. Warm loads come from the
 * query cache (30 s staleTime), so going back to Today is instant. */
export function useToday() {
  const userId = useUserId();
  return useQuery({
    queryKey: todayKey(userId),
    ...OFFLINE_FIRST,
    enabled: userId !== null,
    staleTime: 30_000,
    queryFn: async ({ signal }): Promise<CachedToday> => {
      const uid = userId as string;
      try {
        const data = await authed<TodayOut>((headers) => getToday({ signal, headers }));
        void writeOffline(uid, "today", "today", data);
        return { data, fromCache: false };
      } catch (err) {
        if (isUnreachable(err)) {
          const saved = await readOffline<TodayOut>(uid, "today", "today");
          if (saved !== undefined) return { data: saved, fromCache: true };
        }
        throw err;
      }
    },
  });
}

export function useOnboarding() {
  const userId = useUserId();
  return useQuery({
    queryKey: onboardingKey(userId),
    enabled: userId !== null,
    staleTime: 60_000,
    queryFn: ({ signal }) => authed<OnboardingState>((headers) => getOnboarding({ signal, headers })),
  });
}

export interface PreviewInput {
  exam: string;
  exam_date: string;
  daily_hours: number;
  study_days: number[];
  /** The device zone on a first onboarding, so the preview counts from the student's own today. */
  tz?: string;
}

export function usePlanPreview(input: PreviewInput | null) {
  const userId = useUserId();
  return useQuery({
    queryKey: ["plan-preview", userId, input],
    enabled: userId !== null && input !== null && input.study_days.length > 0,
    placeholderData: keepPreviousData,
    staleTime: 60_000,
    queryFn: ({ signal }) => {
      const q = input as PreviewInput;
      return authed<PlanPreview>((headers) =>
        previewPlan({
          signal,
          headers,
          query: {
            exam: q.exam,
            exam_date: q.exam_date,
            daily_hours: q.daily_hours,
            study_days: q.study_days,
            ...(q.tz ? { tz: q.tz } : {}),
          },
        }),
      );
    },
  });
}

function useInvalidateToday() {
  const userId = useUserId();
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: todayKey(userId) });
    void qc.invalidateQueries({ queryKey: onboardingKey(userId) });
    void qc.invalidateQueries({ queryKey: ["me", userId] });
  };
}

export function useSaveOnboarding() {
  const userId = useUserId();
  const qc = useQueryClient();
  const invalidate = useInvalidateToday();
  return useMutation({
    mutationFn: (body: OnboardingIn) => authed<OnboardingOut>((headers) => saveOnboarding({ headers, body })),
    // The cached Today predates the plan (it said "not onboarded"): drop it so Today loads fresh.
    onSuccess: () => qc.removeQueries({ queryKey: todayKey(userId) }),
    onSettled: invalidate,
  });
}

export function useCreateDiagnostic() {
  const invalidate = useInvalidateToday();
  return useMutation({
    mutationFn: () => authed<DiagnosticOut>((headers) => createDiagnostic({ headers })),
    onSuccess: invalidate,
  });
}

/** Tick an item: the Today copy updates at once and rolls back if the server refuses. */
export function useItemStatus() {
  const userId = useUserId();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { id: string; status: "planned" | "done" | "skipped" }) =>
      authed<PlanItemOut>((headers) =>
        setPlanItemStatus({ headers, path: { item_id: v.id }, body: { status: v.status } }),
      ),
    onMutate: async (v) => {
      await qc.cancelQueries({ queryKey: todayKey(userId) });
      const before = qc.getQueryData<CachedToday>(todayKey(userId));
      if (before?.data.plan) {
        const items = before.data.plan.items.map((i) => (i.id === v.id ? { ...i, status: v.status } : i));
        const now = items.find((i) => i.status !== "done" && i.status !== "skipped")?.id ?? null;
        qc.setQueryData<CachedToday>(todayKey(userId), {
          ...before,
          data: { ...before.data, plan: { ...before.data.plan, items, now_item: now } },
        });
      }
      return { before };
    },
    onError: (_e, _v, ctx) => {
      if (ctx?.before) qc.setQueryData(todayKey(userId), ctx.before);
    },
    onSettled: () => void qc.invalidateQueries({ queryKey: todayKey(userId) }),
  });
}

export function useAcceptSuggestion() {
  const invalidate = useInvalidateToday();
  return useMutation({
    mutationFn: (id: string) =>
      authed<CoachActionOut>((headers) => acceptCoachSuggestion({ headers, path: { suggestion_id: id } })),
    onSuccess: invalidate,
  });
}

export function useDismissSuggestion() {
  const invalidate = useInvalidateToday();
  return useMutation({
    mutationFn: (id: string) =>
      authed<CoachSuggestionOut>((headers) => dismissCoachSuggestion({ headers, path: { suggestion_id: id } })),
    onSuccess: invalidate,
  });
}

/** The structured code of a refused request (e.g. `too_few_study_days`), or null. */
export function errorCode(e: unknown): string | null {
  return e instanceof ApiError ? e.code : null;
}
