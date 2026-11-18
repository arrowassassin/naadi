import {
  createPushSubscription,
  deletePushSubscription,
  getCalendarFeed,
  getPushKey,
  listPushSubscriptions,
  type PushSubscriptionIn,
  revokeCalendarFeed,
  rotateCalendarFeed,
  sendTestPush,
} from "@naadi/shared";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { authed } from "../auth/api";
import { ApiError } from "../auth/apiError";
import { useUserId } from "../practice/api";

const feedKey = (u: string | null) => ["plan-feed", u] as const;
const pushKey = (u: string | null) => ["push-key", u] as const;
const devicesKey = (u: string | null) => ["push-devices", u] as const;

/** A 204 answer has no body; the shared unwrap reports it as an error with status 204. */
async function noContent(run: () => Promise<unknown>): Promise<void> {
  try {
    await run();
  } catch (err) {
    if (err instanceof ApiError && err.status === 204) return;
    throw err;
  }
}

export function useCalendarFeed() {
  const userId = useUserId();
  return useQuery({
    queryKey: feedKey(userId),
    enabled: userId !== null,
    queryFn: ({ signal }) => authed((headers) => getCalendarFeed({ signal, headers })),
  });
}

/** Make (or replace) the feed link: the URL comes back once and is never stored by the server. */
export function useRotateFeed() {
  const userId = useUserId();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => authed((headers) => rotateCalendarFeed({ headers })),
    onSuccess: () => qc.invalidateQueries({ queryKey: feedKey(userId) }),
  });
}

export function useRevokeFeed() {
  const userId = useUserId();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => noContent(() => authed((headers) => revokeCalendarFeed({ headers }))),
    onSuccess: () => qc.invalidateQueries({ queryKey: feedKey(userId) }),
  });
}

export function usePushKey() {
  const userId = useUserId();
  return useQuery({
    queryKey: pushKey(userId),
    enabled: userId !== null,
    staleTime: 60_000,
    queryFn: ({ signal }) => authed((headers) => getPushKey({ signal, headers })),
  });
}

export function useDevices(enabled: boolean) {
  const userId = useUserId();
  return useQuery({
    queryKey: devicesKey(userId),
    enabled: userId !== null && enabled,
    queryFn: ({ signal }) => authed((headers) => listPushSubscriptions({ signal, headers })),
  });
}

export function useAddDevice() {
  const userId = useUserId();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: PushSubscriptionIn) => authed((headers) => createPushSubscription({ body, headers })),
    onSuccess: () => qc.invalidateQueries({ queryKey: devicesKey(userId) }),
  });
}

export function useRemoveDevice() {
  const userId = useUserId();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      noContent(() => authed((headers) => deletePushSubscription({ path: { subscription_id: id }, headers }))),
    onSuccess: () => qc.invalidateQueries({ queryKey: devicesKey(userId) }),
  });
}

export function useSendTest() {
  return useMutation({ mutationFn: () => authed((headers) => sendTestPush({ headers })) });
}
