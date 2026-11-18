import type { QueryClient } from "@tanstack/react-query";
import { getAuthState, onAuthChange } from "../auth/session";
import { migrateLegacyCardQueue } from "../cards/outbox";
import { serverNow } from "../ink/persist/clock";
import { migrateLegacyPlanQueue } from "../planner/outbox";
import { migrateLegacyPracticeQueue } from "../practice/outbox";
import { clearCacheFor } from "./cache";
import "./entities";
import { setStampClock, startOutbox, stopOutbox, wireOutboxTriggers } from "./outbox";
import { setOfflineQueryClient } from "./runtime";
import { persistOnFirstStandaloneLaunch } from "./storage";

/**
 * Start the offline layer once per page (main.tsx): the outbox replays for whoever is signed in, legacy queues
 * move in once, and a sign-out stops the replay and clears that user's downloaded copy (unsent writes stay: they
 * belong to that user and are sent when they sign in here again).
 */
let booted = false;

export function bootOffline(qc: QueryClient): void {
  if (booted) return;
  booted = true;
  setOfflineQueryClient(qc);
  // LWW stamps use the server's clock as this device measures it (ink responses carry server_now).
  setStampClock(serverNow);
  wireOutboxTriggers();
  void persistOnFirstStandaloneLaunch();
  const start = async (user: string) => {
    await migrateLegacyPracticeQueue(user);
    await migrateLegacyCardQueue(user);
    await migrateLegacyPlanQueue(user);
    await startOutbox(user);
  };
  const initial = getAuthState();
  if (initial.status === "signed-in") void start(initial.session.user.id);
  onAuthChange((_event, prev, next) => {
    if (prev === next) return;
    stopOutbox();
    if (prev) void clearCacheFor(prev);
    if (next) void start(next);
  });
}
