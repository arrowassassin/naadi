import {
  type CheckList,
  type CostList,
  type CostOut,
  completeVaultUpload,
  createCost,
  createVaultUpload,
  deleteCost,
  deleteVaultDocument,
  getPathway,
  getVaultUrl,
  listCosts,
  listPathwayChecks,
  listVault,
  type PathwayOut,
  patchCost,
  patchPathwayTask,
  patchVaultDocument,
  putPathwayStage,
  type StageOut,
  type TaskOut,
  type VaultDocOut,
  type VaultList,
  verifyPathwayTask,
} from "@naadi/shared";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useSyncExternalStore } from "react";
import { authed, useMe } from "../auth/api";
import { ApiError, UNREACHABLE } from "../auth/apiError";
import { getAuthState, supabaseClient, useAuth } from "../auth/session";
import { useOnlineStatus } from "../health/useOnlineStatus";
import { serverNow } from "../ink/persist/clock";
import { installOfflineCacheReset, readOffline, writeOffline } from "../learn/offlineCache";
import { type CostEntry, newId } from "./format";
import {
  flushOps,
  loadOps,
  type PathwayOp,
  type PathwayTransport,
  pathwayOpsVersion,
  pendingOps,
  queueCostCreate,
  queueCostDelete,
  queueCostUpdate,
  queueTask,
  type SendResult,
  subscribePathwayOps,
} from "./outbox";

installOfflineCacheReset();

export function useUserId(): string | null {
  const auth = useAuth();
  return auth.status === "signed-in" ? auth.session.user.id : null;
}

export function isRetryable(e: unknown): boolean {
  return e instanceof ApiError && (e.status === null || e.status >= 500 || e.status === 429 || e.status === 401);
}

const KNOWN = new Set([
  "pathway_task_not_found",
  "pathway_stage_not_found",
  "pathway_unavailable",
  "pathway_url_not_official",
  "cost_not_found",
  "cost_currency_not_offered",
  "cost_id_conflict",
  "cost_limit",
  "vault_not_found",
  "vault_type_unknown",
  "vault_file_type",
  "vault_file_too_large",
  "vault_limit",
  "vault_id_conflict",
  "vault_upload_missing",
  "vault_storage_unavailable",
  "vault_unavailable",
]);

/** The ui_copy key for an API error (`pathway.error.<code>`), else a generic one. */
export function errorKey(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.code && KNOWN.has(e.code)) return `pathway.error.${e.code}`;
    if (e.status === 429) return "pathway.error.rate_limited";
    if (e.status === 422) return "pathway.error.invalid";
    if (e.status === null || e.status === 503 || e.status === 502) return "pathway.error.unreachable";
  }
  return "pathway.error.generic";
}

function assertOnline(): void {
  if (typeof navigator !== "undefined" && navigator.onLine === false) throw new ApiError(UNREACHABLE, null, null, null);
}

// ── reading ──

export interface Cached<T> {
  data: T;
  /** The network failed and this is the copy saved on this device. */
  fromCache: boolean;
}

export const pathwayKey = (userId: string | null) => ["pathway", userId] as const;
export const costsKey = (userId: string | null) => ["costs", userId] as const;
export const vaultKey = (userId: string | null) => ["vault", userId] as const;
export const checksKey = (userId: string | null) => ["pathway-checks", userId] as const;

export function usePathwayData() {
  const userId = useUserId();
  const online = useOnlineStatus();
  return useQuery({
    queryKey: pathwayKey(userId),
    enabled: userId !== null,
    staleTime: 30_000,
    retry: (n, e) => n < 1 && isRetryable(e) && online,
    queryFn: async ({ signal }): Promise<Cached<PathwayOut>> => {
      try {
        assertOnline();
        const data = await authed<PathwayOut>((headers) => getPathway({ signal, headers }));
        if (userId) void writeOffline(userId, "pathway", "main", data);
        return { data, fromCache: false };
      } catch (e) {
        if (userId && isRetryable(e)) {
          const saved = await readOffline<PathwayOut>(userId, "pathway", "main");
          if (saved) return { data: saved, fromCache: true };
        }
        throw e;
      }
    },
  });
}

export function useCostsData() {
  const userId = useUserId();
  const online = useOnlineStatus();
  return useQuery({
    queryKey: costsKey(userId),
    enabled: userId !== null,
    staleTime: 30_000,
    retry: (n, e) => n < 1 && isRetryable(e) && online,
    queryFn: async ({ signal }): Promise<Cached<CostList>> => {
      try {
        assertOnline();
        const data = await authed<CostList>((headers) => listCosts({ signal, headers }));
        if (userId) void writeOffline(userId, "costs", "main", data);
        return { data, fromCache: false };
      } catch (e) {
        if (userId && isRetryable(e)) {
          const saved = await readOffline<CostList>(userId, "costs", "main");
          if (saved) return { data: saved, fromCache: true };
        }
        throw e;
      }
    },
  });
}

/** The vault: metadata only, online only. Never written to the offline cache, never kept after the screen closes. */
export function useVaultList() {
  const userId = useUserId();
  const online = useOnlineStatus();
  return useQuery({
    queryKey: vaultKey(userId),
    enabled: userId !== null && online,
    staleTime: 0,
    gcTime: 0,
    retry: (n, e) => n < 1 && isRetryable(e) && online,
    queryFn: ({ signal }) => authed<VaultList>((headers) => listVault({ signal, headers })),
  });
}

/** Admin only: what the weekly check found that still needs a decision. */
export function useOpenChecks() {
  const userId = useUserId();
  const me = useMe();
  return useQuery({
    queryKey: checksKey(userId),
    enabled: userId !== null && me.data?.is_admin === true,
    staleTime: 30_000,
    queryFn: ({ signal }) =>
      authed<CheckList>((headers) => listPathwayChecks({ signal, headers, query: { open: true, limit: 200 } })),
  });
}

// ── the offline queue ──

function outcome(e: unknown): SendResult {
  if (isRetryable(e) || !(e instanceof ApiError)) return "unreachable";
  return { refused: e.code ?? `http_${e.status}` };
}

export const apiTransport: PathwayTransport = {
  async patchTask(taskId, fields, stamp) {
    try {
      assertOnline();
      await authed((headers) =>
        patchPathwayTask({
          headers,
          path: { task_id: taskId },
          body: {
            ...(fields as { status?: TaskOut["status"]; due?: string | null; notes?: string | null }),
            client_updated_at_ms: stamp,
          },
        }),
      );
      return "ok";
    } catch (e) {
      return outcome(e);
    }
  },
  async createCost(id, fields, stamp) {
    try {
      assertOnline();
      await authed((headers) =>
        createCost({ headers, body: { ...(fields as unknown as CostFields), id, client_updated_at_ms: stamp } }),
      );
      return "ok";
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && e.code === "cost_id_conflict") return { refused: e.code };
      return outcome(e);
    }
  },
  async patchCost(id, fields, stamp) {
    try {
      assertOnline();
      await authed((headers) =>
        patchCost({
          headers,
          path: { cost_id: id },
          body: { ...(fields as Partial<CostFields>), client_updated_at_ms: stamp },
        }),
      );
      return "ok";
    } catch (e) {
      if (e instanceof ApiError && e.status === 404 && e.code === "cost_not_found") return "ok"; // deleted elsewhere
      return outcome(e);
    }
  },
  async deleteCost(id, stamp) {
    try {
      assertOnline();
      await authed((headers) => deleteCost({ headers, path: { cost_id: id }, query: { client_updated_at_ms: stamp } }));
      return "ok";
    } catch (e) {
      return outcome(e);
    }
  },
};

interface CostFields {
  task_id?: string | null;
  stage_key?: string | null;
  label: string;
  amount: number;
  currency: string;
  paid_on?: string | null;
}

export function usePendingOps(): PathwayOp[] {
  const userId = useUserId();
  const version = useSyncExternalStore(subscribePathwayOps, pathwayOpsVersion, pathwayOpsVersion);
  // biome-ignore lint/correctness/useExhaustiveDependencies: `version` is the change signal of the store
  return useMemo(() => (userId ? pendingOps(userId) : []), [userId, version]);
}

/** Keeps this user's queue moving: read it from the device, send it now, on reconnect and every 30 s while pending. */
export function usePathwaySync(transport: PathwayTransport = apiTransport) {
  const userId = useUserId();
  const qc = useQueryClient();
  const online = useOnlineStatus();
  const ops = usePendingOps();
  const pending = ops.filter((o) => !o.refused).length;

  const flush = useCallback(async () => {
    if (!userId) return;
    await loadOps(userId);
    const cleared = await flushOps(userId, transport);
    if (cleared > 0) {
      await Promise.all([
        qc.invalidateQueries({ queryKey: pathwayKey(userId) }),
        qc.invalidateQueries({ queryKey: costsKey(userId) }),
      ]);
    }
  }, [userId, transport, qc]);

  useEffect(() => {
    if (online) void flush();
  }, [online, flush]);

  useEffect(() => {
    if (userId) void loadOps(userId);
  }, [userId]);

  useEffect(() => {
    if (!online || pending === 0) return;
    const id = window.setInterval(() => void flush(), 30_000);
    const onVisible = () => {
      if (document.visibilityState === "visible") void flush();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [online, pending, flush]);

  // A change made while online goes out at once.
  // biome-ignore lint/correctness/useExhaustiveDependencies: `ops` is the change signal
  useEffect(() => {
    if (online && pending > 0) void flush();
  }, [ops, online, pending, flush]);

  return { pending, refused: ops.filter((o) => o.refused), flush };
}

// ── writing (every write is recorded on the device first) ──

export type TaskFields = { status?: TaskOut["status"]; due?: string | null; notes?: string | null };

export function useTaskWriter() {
  const userId = useUserId();
  return useCallback(
    (taskId: string, fields: TaskFields) => {
      if (userId) queueTask(userId, taskId, fields, serverNow());
    },
    [userId],
  );
}

export type NewCost = Omit<CostFields, "stage_key"> & { stage_key?: string | null };

export function useCostWriter() {
  const userId = useUserId();
  return useMemo(
    () => ({
      create(fields: CostFields): string | null {
        if (!userId) return null;
        const id = newId();
        queueCostCreate(userId, id, fields as unknown as Record<string, unknown>, serverNow());
        return id;
      },
      update(id: string, fields: Partial<CostFields>) {
        if (userId) queueCostUpdate(userId, id, fields as Record<string, unknown>, serverNow());
      },
      remove(id: string) {
        if (userId) queueCostDelete(userId, id, serverNow());
      },
    }),
    [userId],
  );
}

/** The pathway with this device's unsent task changes applied, so a status set offline shows at once. */
export function overlayTasks(stages: readonly StageOut[], ops: readonly PathwayOp[]): StageOut[] {
  const byTask = new Map(ops.filter((o) => o.entity === "user_pathway_task" && !o.refused).map((o) => [o.key, o]));
  if (byTask.size === 0) return stages.slice();
  return stages.map((s) => {
    if (!s.tasks.some((t) => byTask.has(t.id))) return s;
    const tasks = s.tasks.map((t) => {
      const op = byTask.get(t.id);
      return op ? ({ ...t, ...(op.fields as Partial<TaskOut>) } as TaskOut) : t;
    });
    return { ...s, tasks, done: tasks.filter((t) => t.status === "done" || t.status === "na").length };
  });
}

/** Cost entries with this device's unsent creates, edits and deletes applied. */
export function overlayCosts(
  items: readonly CostOut[],
  ops: readonly PathwayOp[],
  stageOf: (task: string) => string | null,
): (CostOut & { pending: boolean })[] {
  const live = ops.filter((o) => o.entity === "cost" && !o.refused);
  const deleted = new Set(live.filter((o) => o.kind === "delete").map((o) => o.key));
  const edits = new Map(live.filter((o) => o.kind !== "delete").map((o) => [o.key, o]));
  const nowIso = new Date().toISOString();
  const out: (CostOut & { pending: boolean })[] = [];
  const seen = new Set<string>();
  for (const c of items) {
    seen.add(c.id);
    if (deleted.has(c.id)) continue;
    const op = edits.get(c.id);
    out.push(op ? withFields(c, op.fields, stageOf, true) : { ...c, pending: false });
  }
  for (const op of live) {
    if (op.kind !== "create" || seen.has(op.key)) continue;
    const base: CostOut = {
      id: op.key,
      task_id: null,
      stage_key: null,
      label: "",
      amount: "0.00",
      currency: "USD",
      paid_on: null,
      client_updated_at_ms: op.stamp,
      created_at: new Date(op.queuedAt).toISOString() || nowIso,
      updated_at: nowIso,
    };
    out.push(withFields(base, op.fields, stageOf, true));
  }
  return out;
}

function withFields(
  c: CostOut,
  fields: Record<string, unknown>,
  stageOf: (task: string) => string | null,
  pending: boolean,
): CostOut & { pending: boolean } {
  const next: CostOut & { pending: boolean } = { ...c, pending };
  if ("label" in fields) next.label = String(fields.label ?? "");
  if ("amount" in fields) next.amount = Number(fields.amount ?? 0).toFixed(2);
  if ("currency" in fields) next.currency = String(fields.currency);
  if ("paid_on" in fields) next.paid_on = (fields.paid_on as string | null) ?? null;
  if ("task_id" in fields || "stage_key" in fields) {
    const task =
      "task_id" in fields ? ((fields.task_id as string | null) ?? null) : "stage_key" in fields ? null : c.task_id;
    next.task_id = task;
    next.stage_key = task ? stageOf(task) : ((fields.stage_key as string | null | undefined) ?? c.stage_key);
  }
  return next;
}

export function asEntry(c: CostOut): CostEntry & { created_at: string } {
  return c;
}

// ── mutations that need the network ──

export function useSetStage() {
  const userId = useUserId();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (stageKey: string | null) => {
      assertOnline();
      return authed((headers) => putPathwayStage({ headers, body: { stage_key: stageKey } }));
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: pathwayKey(userId) }),
  });
}

export function useVerifyTask() {
  const userId = useUserId();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (taskId: string) => {
      assertOnline();
      return authed((headers) => verifyPathwayTask({ headers, path: { task_id: taskId } }));
    },
    onSuccess: async () => {
      await Promise.all([
        qc.invalidateQueries({ queryKey: pathwayKey(userId) }),
        qc.invalidateQueries({ queryKey: checksKey(userId) }),
      ]);
    },
  });
}

// ── vault ──

export type UploadFailure = "type" | "size" | "failed" | "offline";

const ACCEPT = new Set(["application/pdf", "image/jpeg", "image/png", "image/heic", "image/heif"]);
export const VAULT_ACCEPT =
  ".pdf,.jpg,.jpeg,.png,.heic,.heif,application/pdf,image/jpeg,image/png,image/heic,image/heif";

/** What a file is called for the server: its type from the browser, else from the extension (iPad sends HEIC as
 * image/heic; some browsers send nothing). The server checks the real bytes either way. */
export function declaredMime(file: File): string | null {
  if (ACCEPT.has(file.type)) return file.type;
  const ext = file.name.toLowerCase().split(".").pop();
  const byExt: Record<string, string> = {
    pdf: "application/pdf",
    jpg: "image/jpeg",
    jpeg: "image/jpeg",
    png: "image/png",
    heic: "image/heic",
    heif: "image/heif",
  };
  return ext ? (byExt[ext] ?? null) : null;
}

export function useUploadVault() {
  const userId = useUserId();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      file: File;
      title: string;
      docType: string;
      expiresOn: string | null;
      maxBytes: number;
    }) => {
      assertOnline();
      const mime = declaredMime(input.file);
      if (!mime) throw new UploadError("type");
      if (input.file.size > input.maxBytes || input.file.size === 0) throw new UploadError("size");
      const started = await authed((headers) =>
        createVaultUpload({
          headers,
          body: {
            title: input.title,
            doc_type: input.docType,
            mime: mime as "application/pdf",
            bytes: input.file.size,
            expires_on: input.expiresOn,
          },
        }),
      );
      const client = getAuthState();
      if (client.status !== "signed-in") throw new UploadError("failed");
      // The bytes go straight from the browser to Storage on the signed URL (supabase-js); the API never carries them.
      const storage = await supabaseClient().catch(() => null);
      if (!storage) throw new UploadError("failed");
      const up = await storage.storage.from(started.bucket).uploadToSignedUrl(started.path, started.token, input.file, {
        contentType: mime,
        // Hosted Storage serves an object's Cache-Control from its upload metadata: a vault file must not stay in the
        // browser's HTTP cache after it is opened through a 60 s signed URL.
        cacheControl: "0", // seconds: sent as max-age=0
      });
      if (up.error) throw new UploadError("failed");
      return authed<VaultDocOut>((headers) => completeVaultUpload({ headers, path: { doc_id: started.id } }));
    },
    onSettled: () => qc.invalidateQueries({ queryKey: vaultKey(userId) }),
  });
}

export class UploadError extends Error {
  readonly reason: UploadFailure;
  constructor(reason: UploadFailure) {
    super(reason);
    this.reason = reason;
  }
}

export function useVaultActions() {
  const userId = useUserId();
  const qc = useQueryClient();
  const done = () => qc.invalidateQueries({ queryKey: vaultKey(userId) });
  return {
    patch: useMutation({
      mutationFn: async (v: { id: string; title?: string; doc_type?: string; expires_on?: string | null }) => {
        assertOnline();
        const { id, ...body } = v;
        return authed((headers) => patchVaultDocument({ headers, path: { doc_id: id }, body }));
      },
      onSuccess: done,
    }),
    remove: useMutation({
      mutationFn: async (id: string) => {
        assertOnline();
        return authed((headers) => deleteVaultDocument({ headers, path: { doc_id: id } }));
      },
      onSuccess: done,
    }),
    /** A signed URL, used once to open the file in a new tab and never kept. */
    open: useMutation({
      mutationFn: async (id: string) => {
        assertOnline();
        return authed((headers) => getVaultUrl({ headers, path: { doc_id: id } }));
      },
    }),
  };
}
