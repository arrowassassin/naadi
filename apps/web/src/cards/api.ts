import {
  type CardCreate,
  type CardOut,
  type CardPatch,
  type CardStats,
  createCard,
  type DueResponse,
  deleteCard,
  getCardStats,
  getDueCards,
  type ReviewIn,
  type ReviewOut,
  reviewCard,
  updateCard,
} from "@naadi/shared";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import { authed } from "../auth/api";
import { ApiError } from "../auth/apiError";
import { useAuth } from "../auth/session";
import { installOfflineCacheReset, readOffline, writeOffline } from "../learn/offlineCache";
import { subscribeOutbox } from "../offline/outbox";
import { OFFLINE_FIRST } from "../offline/runtime";
import { enqueueReview, flushQueue, readQueue } from "./outbox";

installOfflineCacheReset();

export const DUE_LIMIT = 200;

export function useUserId(): string | null {
  const auth = useAuth();
  return auth.status === "signed-in" ? auth.session.user.id : null;
}

/** A network failure (no HTTP response) rather than an answer from the server. */
export function isUnreachable(e: unknown): boolean {
  return e instanceof ApiError && e.status === null && e.code === null;
}

/** The ui_copy key for an API error: `cards.error.<code>` for the Cards API's codes, else a generic one. */
export function errorKey(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.code && KNOWN_CODES.has(e.code)) return `cards.error.${e.code}`;
    if (e.status === 422) return "cards.error.invalid";
    if (isUnreachable(e) || e.status === 503) return "cards.error.cards_unavailable";
  }
  return "cards.error.generic";
}

const KNOWN_CODES = new Set([
  "card_not_found",
  "card_not_editable",
  "card_question_not_attempted",
  "card_question_not_found",
  "card_section_not_found",
  "review_out_of_order",
  "review_before_card",
  "review_id_reused",
  "review_too_old",
  "cards_unavailable",
  "card_zone_unknown",
]);

export const dueKey = (userId: string | null) => ["cards-due", userId] as const;
export const statsKey = (userId: string | null, tz: string) => ["cards-stats", userId, tz] as const;

export interface CachedDue {
  data: DueResponse;
  /** true when the network failed and this is the copy saved on this device. */
  fromCache: boolean;
}

/** The student's IANA time zone, for "due today" counts. */
export function browserZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

/**
 * Due cards, saved on this device after every successful load so review continues offline. A card that is
 * question-linked reaches this list only after the student's graded attempt (the server applies the gate), so the
 * saved copy never holds an answer the student has not earned.
 */
export function useDueCards() {
  const userId = useUserId();
  return useQuery({
    queryKey: dueKey(userId),
    ...OFFLINE_FIRST,
    enabled: userId !== null,
    staleTime: 30_000,
    refetchOnWindowFocus: false,
    queryFn: async ({ signal }): Promise<CachedDue> => {
      try {
        const data = await authed<DueResponse>((headers) =>
          getDueCards({ signal, headers, query: { limit: DUE_LIMIT } }),
        );
        if (userId) void writeOffline(userId, "cards-due", "v1", data);
        return { data, fromCache: false };
      } catch (err) {
        if (userId && isUnreachable(err)) {
          const saved = await readOffline<DueResponse>(userId, "cards-due", "v1");
          if (saved) return { data: saved, fromCache: true };
        }
        throw err;
      }
    },
  });
}

/** Counts for Today and the Builder link. `enabled` false keeps it from loading (e.g. flag off). */
export function useCardStats(enabled = true) {
  const userId = useUserId();
  const tz = browserZone();
  return useQuery({
    queryKey: statsKey(userId, tz),
    enabled: enabled && userId !== null,
    staleTime: 60_000,
    queryFn: ({ signal }) => authed<CardStats>((headers) => getCardStats({ signal, headers, query: { tz } })),
  });
}

function useInvalidateCards() {
  const qc = useQueryClient();
  const userId = useUserId();
  return useCallback(() => {
    void qc.invalidateQueries({ queryKey: ["cards-stats", userId] });
  }, [qc, userId]);
}

export function useCreateCard() {
  const invalidate = useInvalidateCards();
  const qc = useQueryClient();
  const userId = useUserId();
  return useMutation({
    mutationFn: (body: CardCreate) => authed<CardOut>((headers) => createCard({ body, headers })),
    onSuccess: () => {
      invalidate();
      void qc.invalidateQueries({ queryKey: dueKey(userId) });
    },
  });
}

export function useUpdateCard() {
  const invalidate = useInvalidateCards();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: CardPatch }) =>
      authed<CardOut>((headers) => updateCard({ body, headers, path: { card_id: id } })),
    onSuccess: invalidate,
  });
}

export function useDeleteCard() {
  const invalidate = useInvalidateCards();
  return useMutation({
    mutationFn: (id: string) => authed<unknown>((headers) => deleteCard({ headers, path: { card_id: id } })),
    onSuccess: invalidate,
  });
}

export function sendReview(cardId: string, body: ReviewIn): Promise<ReviewOut> {
  return authed<ReviewOut>((headers) => reviewCard({ body, headers, path: { card_id: cardId } }));
}

/**
 * Ratings waiting to sync: queue one, see how many wait, and replay them when the connection returns (on the
 * `online` event, on mount and on demand). Replays are idempotent server-side (client_review_id).
 */
export function usePendingReviews() {
  const userId = useUserId();
  const count = useSyncExternalStore(
    subscribeOutbox,
    () => (userId ? readQueue(userId).length : 0),
    () => 0,
  );
  const [syncing, setSyncing] = useState(false);

  // The count follows the outbox on its own; kept for the screen's existing calls.
  const refresh = useCallback(() => {}, []);

  const flush = useCallback(async () => {
    if (!userId || readQueue(userId).length === 0) return 0;
    setSyncing(true);
    try {
      return await flushQueue(userId);
    } finally {
      setSyncing(false);
    }
  }, [userId]);

  const enqueue = useCallback(
    (cardId: string, body: ReviewIn) => {
      if (!userId) return;
      enqueueReview(userId, { cardId, body });
    },
    [userId],
  );

  useEffect(() => {
    // The outbox replays on `online`, on visibility and every 30 s; one try on arrival.
    void flush();
  }, [flush]);

  return { userId, count, syncing, enqueue, flush, refresh };
}
