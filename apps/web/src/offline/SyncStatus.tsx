import { Button, cx } from "@naadi/ui";
import { CircleAlert, CircleCheck, CloudOff, History, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { useAuth } from "../auth/session";
import { useUiText } from "../copy/useUiText";
import { useOnlineStatus } from "../health/useOnlineStatus";
import { Sheet } from "../learn/Sheet";
import type { ConflictRow, OutboxEntity, OutboxRow } from "./db";
import {
  conflictsFor,
  discardRow,
  outboxRows,
  outboxStatus,
  outboxVersion,
  replay,
  retryAll,
  retryRow,
  subscribeOutbox,
  wasChangedElsewhere,
} from "./outbox";

/**
 * The global sync state (segment 18), always icon + word, never colour alone:
 *   "Offline · 12 changes will sync" · "Syncing…" · "All changes saved" (for a few seconds after a sync) ·
 *   "2 need attention" (opens the sync sheet: Retry / Discard, and what lost a conflict in the last 7 days).
 * Lime is never used here: syncing is not the student's next action.
 */

function useUserId(): string | null {
  const auth = useAuth();
  return auth.status === "signed-in" ? auth.session.user.id : null;
}

export function useOutboxVersion(): number {
  return useSyncExternalStore(subscribeOutbox, outboxVersion, outboxVersion);
}

export type SyncPhase = "offline" | "syncing" | "attention" | "saved" | "idle";

export interface SyncView {
  phase: SyncPhase;
  pending: number;
  failed: number;
}

const SAVED_SHOWN_MS = 4000;

/** What the indicator says now. "saved" shows briefly after the queue drained, then the line goes away. */
export function useSyncView(forceOffline = false): SyncView {
  const user = useUserId();
  useOutboxVersion();
  const online = useOnlineStatus() && !forceOffline;
  const s = outboxStatus(user);
  const [savedUntil, setSavedUntil] = useState(0);
  const had = useRef(false);
  const busy = s.pending > 0 || s.syncing;
  useEffect(() => {
    if (busy) had.current = true;
    else if (had.current && online) {
      had.current = false;
      const until = Date.now() + SAVED_SHOWN_MS;
      setSavedUntil(until);
      const id = window.setTimeout(() => setSavedUntil(0), SAVED_SHOWN_MS);
      return () => window.clearTimeout(id);
    }
    return undefined;
  }, [busy, online]);
  let phase: SyncPhase = "idle";
  if (!online) phase = "offline";
  else if (s.failed > 0 && !s.syncing) phase = "attention";
  else if (busy) phase = "syncing";
  else if (savedUntil > 0) phase = "saved";
  return { phase, pending: s.pending, failed: s.failed };
}

function phaseText(t: ReturnType<typeof useUiText>, v: SyncView): string {
  const n = String(v.pending);
  switch (v.phase) {
    case "offline":
      if (v.pending === 0) return t("sync.offline_none");
      return v.pending === 1 ? t("sync.offline_pending_one") : t("sync.offline_pending", { count: n });
    case "syncing":
      return t("sync.syncing");
    case "attention":
      return v.failed === 1 ? t("sync.attention_one") : t("sync.attention", { count: String(v.failed) });
    case "saved":
      return t("sync.saved");
    default:
      return "";
  }
}

const ICONS = { offline: CloudOff, syncing: RefreshCw, attention: CircleAlert, saved: CircleCheck } as const;

/**
 * The status line under the nav (every viewport). Shown while offline, syncing, needing attention, or just saved;
 * a tap opens the sync sheet. `data-sync-phase` is what the e2e waits on.
 */
export function SyncIndicator({ className, offline = false }: { className?: string; offline?: boolean }) {
  const t = useUiText();
  const view = useSyncView(offline);
  const [open, setOpen] = useState(false);
  const user = useUserId();
  if (!user) return null;
  const attrs = { "data-sync-phase": view.phase, "data-sync-pending": view.pending, "data-sync-failed": view.failed };
  if (view.phase === "idle") return <span hidden {...attrs} />;
  const Icon = ICONS[view.phase];
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-haspopup="dialog"
        className={cx(
          "flex min-h-11 w-full items-center gap-3 rounded-pill border bg-surface-2 px-4 py-2 text-left text-text",
          view.phase === "attention" ? "border-amber" : "border-line-strong",
          className,
        )}
        {...attrs}
      >
        <Icon
          aria-hidden="true"
          size={18}
          className={cx(
            "shrink-0",
            view.phase === "attention" ? "text-amber" : "text-text-2",
            view.phase === "syncing" && "motion-safe:animate-spin",
          )}
        />
        <span role="status" aria-live="polite" className="min-w-0 flex-1 font-semibold">
          {phaseText(t, view)}
        </span>
        {view.phase === "offline" ? (
          <span className="hidden text-text-2 tablet:inline">{t("sync.offline_hint")}</span>
        ) : null}
      </button>
      <SyncSheet open={open} onClose={() => setOpen(false)} />
    </>
  );
}

const ENTITY_KEYS: Record<OutboxEntity, string> = {
  attempt: "sync.entity.attempt",
  card_review: "sync.entity.card_review",
  ink: "sync.entity.ink",
  note: "sync.entity.note",
  notebook: "sync.entity.notebook",
  plan_item: "sync.entity.plan_item",
  user_pathway_task: "sync.entity.user_pathway_task",
  cost: "sync.entity.cost",
  reading_position: "sync.entity.reading_position",
};

const ERROR_KEYS: Record<string, string> = {
  time_up: "sync.error.time_up",
  offline: "sync.error.offline",
};

export function errorText(t: ReturnType<typeof useUiText>, code: string | null): string {
  if (!code) return t("sync.error.generic");
  if (ERROR_KEYS[code]) return t(ERROR_KEYS[code] as string);
  if (code.endsWith("_not_found") || code === "http_404") return t("sync.error.not_found");
  if (code === "http_422" || code.endsWith("_invalid") || code === "validation_error") return t("sync.error.invalid");
  return t("sync.error.generic");
}

/** The sync sheet: what needs attention (Retry / Discard), what waits, what lost a conflict (kept 7 days). */
export function SyncSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const t = useUiText();
  const user = useUserId();
  useOutboxVersion();
  const online = useOnlineStatus();
  const rows: OutboxRow[] = user ? outboxRows(user) : [];
  const failed = rows.filter((r) => r.state === "failed");
  const waiting = rows.filter((r) => r.state !== "failed");
  const [conflicts, setConflicts] = useState<ConflictRow[]>([]);
  const refresh = useCallback(() => {
    if (user) void conflictsFor(user).then(setConflicts);
  }, [user]);
  useEffect(() => {
    if (open) refresh();
  }, [open, refresh]);
  const byEntity = new Map<OutboxEntity, number>();
  for (const r of waiting) byEntity.set(r.entity, (byEntity.get(r.entity) ?? 0) + 1);

  return (
    <Sheet open={open} onClose={onClose} title={t("sync.sheet_title")} closeLabel={t("sync.sheet_close")}>
      <div className="flex flex-col gap-6" data-sync-sheet="">
        <section aria-labelledby="sync-attention" className="flex flex-col gap-3">
          <h3 id="sync-attention" className="m-0 font-mono text-label text-text-3 uppercase">
            {t("sync.attention_heading")}
          </h3>
          {failed.length === 0 ? (
            <p className="m-0 text-text-2">{t("sync.attention_none")}</p>
          ) : (
            <ul className="m-0 flex list-none flex-col gap-2 p-0">
              {failed.map((r) => (
                <li
                  key={r.op_id}
                  data-sync-failed-row={r.entity}
                  className="flex flex-wrap items-center gap-3 rounded-block border border-amber px-4 py-3"
                >
                  <CircleAlert aria-hidden="true" size={18} className="shrink-0 text-amber" />
                  <span className="min-w-0 flex-1">
                    <span className="block font-semibold">{t(ENTITY_KEYS[r.entity])}</span>
                    <span className="block text-text-2 text-ui-sm">{errorText(t, r.last_error)}</span>
                  </span>
                  <span className="flex gap-2">
                    {r.last_error === "time_up" ? null : (
                      <Button onClick={() => void retryRow(r.op_id)} disabled={!online}>
                        {t("sync.retry")}
                      </Button>
                    )}
                    <Button
                      onClick={() => {
                        void discardRow(r.op_id).then(refresh);
                      }}
                    >
                      {t("sync.discard")}
                    </Button>
                  </span>
                </li>
              ))}
            </ul>
          )}
          {failed.filter((r) => r.last_error !== "time_up").length > 1 ? (
            <Button className="self-start" onClick={() => user && void retryAll(user)} disabled={!online}>
              {t("sync.retry_all")}
            </Button>
          ) : null}
        </section>

        <section aria-labelledby="sync-waiting" className="flex flex-col gap-3">
          <h3 id="sync-waiting" className="m-0 font-mono text-label text-text-3 uppercase">
            {t("sync.waiting_heading")}
          </h3>
          {waiting.length === 0 ? (
            <p className="m-0 flex items-center gap-2 text-text-2">
              <CircleCheck aria-hidden="true" size={18} />
              {t("sync.saved")}
            </p>
          ) : (
            <>
              <ul className="m-0 flex list-none flex-col gap-1 p-0">
                {[...byEntity].map(([e, n]) => (
                  <li key={e} className="flex justify-between gap-3 text-text-2">
                    <span>{t(ENTITY_KEYS[e])}</span>
                    <span className="font-mono">{n}</span>
                  </li>
                ))}
              </ul>
              {online ? (
                <Button className="self-start" onClick={() => void replay()}>
                  {t("sync.sync_now")}
                </Button>
              ) : (
                <p className="m-0 flex items-center gap-2 text-text-2">
                  <CloudOff aria-hidden="true" size={18} />
                  {t("sync.error.offline")}
                </p>
              )}
            </>
          )}
        </section>

        <section aria-labelledby="sync-conflicts" className="flex flex-col gap-3">
          <h3 id="sync-conflicts" className="m-0 font-mono text-label text-text-3 uppercase">
            {t("sync.conflicts_heading")}
          </h3>
          <p className="m-0 text-text-2 text-ui-sm">{t("sync.conflicts_note")}</p>
          {conflicts.length === 0 ? (
            <p className="m-0 text-text-2">{t("sync.conflicts_none")}</p>
          ) : (
            <ul className="m-0 flex list-none flex-col gap-2 p-0">
              {conflicts.map((c) => (
                <li
                  key={c.id}
                  data-sync-conflict={c.entity}
                  className="rounded-block border border-line-control px-4 py-3"
                >
                  <details>
                    <summary className="flex min-h-11 cursor-pointer items-center gap-3">
                      <History aria-hidden="true" size={18} className="shrink-0 text-text-2" />
                      <span className="flex-1 font-semibold">{t(ENTITY_KEYS[c.entity])}</span>
                      <span className="text-text-3 text-ui-sm">
                        {t("sync.conflict_kept_at", { when: new Date(c.at).toLocaleString() })}
                      </span>
                    </summary>
                    <p className="m-0 mt-2 whitespace-pre-wrap break-words text-text-2 text-ui-sm">
                      {summary(c.local)}
                    </p>
                  </details>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </Sheet>
  );
}

/** A readable digest of a losing local version: its text fields (title, quote, plain body), never raw JSON. */
function summary(local: unknown): string {
  const out: string[] = [];
  const visit = (v: unknown, depth: number) => {
    if (depth > 6 || out.join(" ").length > 600) return;
    if (typeof v === "string") {
      if (v.trim()) out.push(v.trim());
    } else if (Array.isArray(v)) for (const x of v) visit(x, depth + 1);
    else if (v && typeof v === "object")
      for (const [k, x] of Object.entries(v))
        if (k === "text" || k === "title" || k === "quote" || k === "label" || typeof x === "object")
          visit(x, depth + 1);
  };
  visit(local, 0);
  return out.join(" ").slice(0, 600);
}

/** "Changed on another device" (word + icon) on an item whose server version won this session. */
export function ChangedElsewhere({ entity, id, className }: { entity: OutboxEntity; id: string; className?: string }) {
  const t = useUiText();
  useOutboxVersion();
  if (!wasChangedElsewhere(entity, id)) return null;
  return (
    <span
      data-changed-elsewhere=""
      className={cx("inline-flex items-center gap-1.5 text-text-2 text-ui-sm", className)}
    >
      <History aria-hidden="true" size={14} />
      {t("sync.changed_elsewhere")}
    </span>
  );
}
