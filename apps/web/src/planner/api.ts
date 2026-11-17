import {
  getPlanWeek,
  type ItemPatchOut,
  type PlanWeekOut,
  patchPlanItem,
  type RebalanceOut,
  startPlanRebalance,
  type WeekItemOut,
  type WeekOut,
} from "@naadi/shared";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect } from "react";
import { authed } from "../auth/api";
import { ApiError } from "../auth/apiError";
import { useAuth } from "../auth/session";
import { readOffline, writeOffline } from "../learn/offlineCache";
import { isUnreachable, useUserId } from "../practice/api";
import { todayKey } from "../today/api";
import { enqueueStatus, flushQueue, type ItemStatus, type SendOutcome } from "./outbox";
import { awaitConfirm, type DecideResult, type Decision, decide, type Runtime } from "./runs";

export const weekKey = (userId: string | null, start: string | undefined) =>
  ["plan-week", userId, start ?? "current"] as const;

export interface CachedWeek {
  /** null: the student has no plan yet. */
  data: WeekOut | null;
  /** true when the network failed and this is the copy saved on this device. */
  fromCache: boolean;
}

/** One week of the plan; the last good copy of each week is kept on this device for offline. */
export function usePlanWeek(start: string | undefined) {
  const userId = useUserId();
  return useQuery({
    queryKey: weekKey(userId, start),
    enabled: userId !== null,
    staleTime: 15_000,
    // Runs offline too, so a reload without a connection falls back to the copy saved on this device.
    networkMode: "always",
    placeholderData: keepPreviousData,
    retry: (count, err) => !(err instanceof ApiError && err.status !== null && err.status < 500) && count < 2,
    queryFn: async ({ signal }): Promise<CachedWeek> => {
      const uid = userId as string;
      const id = start ?? "current";
      try {
        const { week } = await authed<PlanWeekOut>((headers) =>
          getPlanWeek({ signal, headers, ...(start ? { query: { start } } : {}) }),
        );
        if (week) void writeOffline(uid, "plan-week", id, week);
        return { data: week, fromCache: false };
      } catch (err) {
        if (isUnreachable(err)) {
          const saved = await readOffline<WeekOut>(uid, "plan-week", id);
          if (saved !== undefined) return { data: saved, fromCache: true };
        }
        throw err;
      }
    },
  });
}

function patchCached(week: WeekOut, id: string, status: ItemStatus, stamp: number): WeekOut {
  const upd = (i: WeekItemOut): WeekItemOut =>
    i.id === id ? { ...i, status, client_updated_at_ms: stamp, behind: status === "planned" && i.behind } : i;
  const days = week.days.map((d) => ({ ...d, items: d.items.map(upd) }));
  return {
    ...week,
    days,
    next_item: week.next_item ? upd(week.next_item) : null,
  };
}

function nextOpen(week: WeekOut): WeekItemOut | null {
  const today = week.days.find((d) => d.is_today);
  return today?.items.find((i) => i.status === "planned" || i.status === "now" || i.status === "behind") ?? null;
}

async function sendStatus(id: string, status: ItemStatus, stamp: number): Promise<ItemPatchOut> {
  return authed<ItemPatchOut>((headers) =>
    patchPlanItem({ headers, path: { item_id: id }, body: { status, client_updated_at_ms: stamp } }),
  );
}

/**
 * Mark an item done / skipped / planned. The screen updates at once; offline the change is queued with its time and
 * sent when the connection is back (last-writer-wins per item on the server).
 */
export function useSetItemStatus() {
  const userId = useUserId();
  const qc = useQueryClient();
  return useMutation({
    // Offline the change is queued with its time (below) instead of waiting for the browser to say it is online.
    networkMode: "always",
    mutationFn: async (v: { id: string; status: ItemStatus }) => {
      const stamp = Date.now();
      try {
        const out = await sendStatus(v.id, v.status, stamp);
        return { ...out, queued: false };
      } catch (err) {
        if (isUnreachable(err) && userId) {
          enqueueStatus(userId, { id: v.id, status: v.status, client_updated_at_ms: stamp });
          return { applied: true, item: null, queued: true };
        }
        throw err;
      }
    },
    onMutate: async (v) => {
      await qc.cancelQueries({ queryKey: ["plan-week", userId] });
      const stamp = Date.now();
      const before = qc.getQueriesData<CachedWeek>({ queryKey: ["plan-week", userId] });
      qc.setQueriesData<CachedWeek>({ queryKey: ["plan-week", userId] }, (c) => {
        if (!c?.data) return c;
        const data = patchCached(c.data, v.id, v.status, stamp);
        return { ...c, data: { ...data, next_item: nextOpen(data) } };
      });
      return { before };
    },
    onError: (_e, _v, ctx) => {
      for (const [k, d] of ctx?.before ?? []) qc.setQueryData(k, d);
    },
    onSettled: (out) => {
      // A queued change stays as the student made it until it syncs; any other outcome re-reads the server's row.
      if (!out?.queued) void qc.invalidateQueries({ queryKey: ["plan-week", userId] });
      void qc.invalidateQueries({ queryKey: todayKey(userId) });
    },
  });
}

/** Replays queued status changes when the page opens and whenever the connection returns. */
export function useFlushPlanOutbox(online: boolean) {
  const userId = useUserId();
  const qc = useQueryClient();
  useEffect(() => {
    if (!online || !userId) return;
    let cancelled = false;
    const send = async (q: { id: string; status: ItemStatus; client_updated_at_ms: number }): Promise<SendOutcome> => {
      try {
        await sendStatus(q.id, q.status, q.client_updated_at_ms);
        return "sent";
      } catch (err) {
        return isUnreachable(err) ? "unreachable" : "refused";
      }
    };
    void flushQueue(userId, send).then((n) => {
      if (n > 0 && !cancelled) void qc.invalidateQueries({ queryKey: ["plan-week", userId] });
    });
    return () => {
      cancelled = true;
    };
  }, [online, userId, qc]);
}

/** The LangGraph SDK is loaded when a rebalance is started or answered, never in the first load. */
async function runtimeFor(token: string): Promise<Runtime> {
  const { Client } = await import("@langchain/langgraph-sdk");
  // The SDK's own client against the Aegra runtime on this origin, with the student's token.
  const client = new Client({ apiUrl: window.location.origin, defaultHeaders: { authorization: `Bearer ${token}` } });
  return client as unknown as Runtime;
}

export type RebalanceStart = { kind: "proposal" } | { kind: "nothing" };

/** The student's own Rebalance: starts the planner run, which proposes and waits for the confirm. */
export function useStartRebalance() {
  const auth = useAuth();
  const userId = useUserId();
  const qc = useQueryClient();
  const token = auth.status === "signed-in" ? auth.session.access_token : null;
  return useMutation({
    mutationFn: async (): Promise<RebalanceStart> => {
      if (!token) throw new ApiError("Sign in to continue.", 401, "unauthorized", null);
      const started = await authed((headers) => startPlanRebalance({ headers }));
      const waiting = await awaitConfirm(await runtimeFor(token), started.thread_id, started.run_id);
      return waiting ? { kind: "proposal" } : { kind: "nothing" };
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: ["plan-week", userId] });
      void qc.invalidateQueries({ queryKey: todayKey(userId) });
    },
  });
}

/** Accept or decline the proposal on screen (always through the planner run's interrupt). */
export function useDecideRebalance() {
  const auth = useAuth();
  const userId = useUserId();
  const qc = useQueryClient();
  const token = auth.status === "signed-in" ? auth.session.access_token : null;
  const run = useCallback(
    async (v: { row: RebalanceOut; decision: Decision }): Promise<DecideResult> => {
      if (!token) throw new ApiError("Sign in to continue.", 401, "unauthorized", null);
      return decide(await runtimeFor(token), v.row, v.decision);
    },
    [token],
  );
  return useMutation({
    mutationFn: run,
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: ["plan-week", userId] });
      void qc.invalidateQueries({ queryKey: todayKey(userId) });
    },
  });
}
