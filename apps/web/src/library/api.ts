import {
  type ConfirmBody,
  confirmIngestBatch,
  confirmIngestItem,
  createIngestPaste,
  createIngestUploadBatch,
  createIngestUrl,
  deleteIngestItem,
  type FileOut,
  getIngestFile,
  getLibrary,
  type IngestItemList,
  type IngestItemOut,
  type LibraryOut,
  type PasteCreate,
  retryIngestItem,
  startIngestBatch,
  type UploadBatchOut,
  type UploadCreate,
  undoIngestPlacement,
} from "@naadi/shared";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSyncExternalStore } from "react";
import { authed } from "../auth/api";
import { ApiError } from "../auth/apiError";
import { useAuth } from "../auth/session";
import { needsPolling } from "./model";

export type { FileOut, IngestItemOut, LibraryOut };

/** The Library is polled every 2 s while something is moving (uploading, reading, placing), never otherwise. */
export const POLL_MS = 2000;

/** Codes the ingest API returns (`ingest.error.<code>`); anything else is worded generically. */
export const ERROR_CODES = [
  "library_disabled",
  "ingest_unavailable",
  "ingest_item_not_found",
  "ingest_state_conflict",
  "ingest_option_unknown",
  "ingest_disabled",
  "validation_error",
  "rate_limited",
  "file_too_large",
  "empty_file",
  "unsupported_type",
  "executable_rejected",
  "macro_enabled_office",
  "legacy_office_unsupported",
  "encrypted_pdf",
  "encrypted_archive",
  "zip_bomb",
  "zip_too_many_entries",
  "zip_too_large",
  "zip_too_deep",
  "zip_unsafe_path",
  "image_too_large",
  "unreadable_file",
  "svg_unsupported",
  "storage_full",
  "daily_limit",
  "upload_missing",
  "no_text_found",
  "too_many_pages",
  "audio_too_long",
  "extract_timeout",
  "extract_failed",
  "url_unsupported",
  "url_fetch_failed",
  "web_budget_exhausted",
  "anki_format_unsupported",
  "vault_too_large",
  "vault_type_unsupported",
  "discarded",
  "no_placement",
  "placement_unavailable",
  "placement_failed",
  "internal_error",
  "upload_failed",
  "upload_incomplete",
] as const;
const KNOWN = new Set<string>(ERROR_CODES);

/** The ui_copy key for an ingest reason code (an item's `error`, an API error's `code`). */
export function ingestErrorKey(code: string | null | undefined): string {
  return code && KNOWN.has(code) ? `ingest.error.${code}` : "ingest.error.generic";
}

/** The ui_copy key for a failed request (offline and server-down are worded as such, never the server's text). */
export function requestErrorKey(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.code && KNOWN.has(e.code)) return `ingest.error.${e.code}`;
    if (e.status === null) return "ingest.error.offline";
    if (e.status === 429) return "ingest.error.rate_limited";
    if (e.status >= 500) return "ingest.error.ingest_unavailable";
  }
  return "ingest.error.generic";
}

function useUserId(): string | null {
  const auth = useAuth();
  return auth.status === "signed-in" ? auth.session.user.id : null;
}

export const libraryKey = (userId: string | null) => ["library", userId] as const;

// ── items this tab is uploading right now (the server only knows they were reserved) ──

const uploading = new Set<string>();
const uploadFailed = new Set<string>();
/** Answered confirm cards / Vault prompts: the item's `updated_at` when answered, until the run moves it. */
const awaiting = new Map<string, { updatedAt: string; until: number }>();
/** How long an answered card stays hidden while its run has not picked the answer up. */
export const AWAIT_MS = 2 * 60_000;
let version = 0;
const listeners = new Set<() => void>();
function changed(): void {
  version++;
  for (const l of listeners) l();
}
export const uploads = {
  begin(ids: readonly string[]): void {
    for (const id of ids) uploading.add(id);
    changed();
  },
  done(id: string, ok: boolean): void {
    uploading.delete(id);
    if (!ok) uploadFailed.add(id);
    changed();
  },
  forget(id: string): void {
    uploading.delete(id);
    uploadFailed.delete(id);
    changed();
  },
  /** The student answered these items: hide their cards and poll until the server reports a change. */
  answered(items: readonly Pick<IngestItemOut, "id" | "updated_at">[]): void {
    for (const i of items) awaiting.set(i.id, { updatedAt: i.updated_at, until: Date.now() + AWAIT_MS });
    changed();
  },
  unanswered(ids: readonly string[]): void {
    for (const id of ids) awaiting.delete(id);
    changed();
  },
  isUploading: (id: string): boolean => uploading.has(id),
  hasFailed: (id: string): boolean => uploadFailed.has(id),
  isAwaiting: (item: Pick<IngestItemOut, "id" | "updated_at">): boolean => {
    const a = awaiting.get(item.id);
    return Boolean(a && a.updatedAt === item.updated_at && Date.now() < a.until);
  },
};
function subscribe(cb: () => void): () => void {
  listeners.add(cb);
  return () => listeners.delete(cb);
}
/** Re-render when this tab's uploads start or finish. */
export function useUploadsVersion(): number {
  return useSyncExternalStore(
    subscribe,
    () => version,
    () => 0,
  );
}

// ── queries ──

export function useLibrary(enabled = true) {
  const userId = useUserId();
  useUploadsVersion();
  return useQuery<LibraryOut>({
    queryKey: libraryKey(userId),
    enabled: enabled && userId !== null,
    staleTime: 5_000,
    refetchInterval: (q) => (q.state.data && needsPolling(q.state.data.items, uploads) ? POLL_MS : false),
    queryFn: ({ signal }) => authed<LibraryOut>((headers) => getLibrary({ signal, headers })),
  });
}

/** A signed URL of the student's original file, renewed a minute before it expires. */
export function useIngestFile(uploadId: string | null) {
  const userId = useUserId();
  return useQuery<FileOut>({
    queryKey: ["ingest-file", userId, uploadId],
    enabled: userId !== null && uploadId !== null,
    staleTime: (q) => Math.max(30, (q.state.data?.expires_in ?? 600) - 60) * 1000,
    refetchInterval: (q) => Math.max(30, (q.state.data?.expires_in ?? 600) - 60) * 1000,
    refetchOnWindowFocus: false,
    retry: (n, e) => n < 2 && !(e instanceof ApiError && e.status !== null && e.status < 500),
    queryFn: ({ signal }) =>
      authed<FileOut>((headers) => getIngestFile({ signal, headers, path: { upload_id: uploadId as string } })),
  });
}

// ── mutations ──

function useInvalidate() {
  const qc = useQueryClient();
  const userId = useUserId();
  return () => {
    void qc.invalidateQueries({ queryKey: libraryKey(userId) });
    // Placements create or remove private chapters: the course map and search show them.
    void qc.invalidateQueries({ queryKey: ["courses"] });
    void qc.invalidateQueries({ queryKey: ["search", "local"] });
  };
}

export async function reserveUploads(files: UploadCreate[]): Promise<UploadBatchOut> {
  return authed<UploadBatchOut>((headers) => createIngestUploadBatch({ headers, body: { files } }));
}

export async function startBatch(batchId: string): Promise<IngestItemList> {
  return authed<IngestItemList>((headers) => startIngestBatch({ headers, path: { batch_id: batchId } }));
}

export function useAddLink() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (url: string) => authed<IngestItemOut>((headers) => createIngestUrl({ headers, body: { url } })),
    onSuccess: invalidate,
  });
}

export function useAddPaste() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (body: PasteCreate) => authed<IngestItemOut>((headers) => createIngestPaste({ headers, body })),
    onSuccess: invalidate,
  });
}

export interface BatchChoice {
  item_id: string;
  option_id: string;
}

/** One grouped confirm: a single item goes through its own endpoint, a batch through the batch endpoint. */
async function sendConfirm(batchId: string | null, choices: BatchChoice[]): Promise<IngestItemOut[]> {
  const [first] = choices;
  if (choices.length === 1 && first) {
    const one = await authed<IngestItemOut>((headers) =>
      confirmIngestItem({
        headers,
        path: { item_id: first.item_id },
        body: { placement: { option_id: first.option_id } },
      }),
    );
    return [one];
  }
  if (!batchId) throw new Error("a grouped confirm needs its batch");
  const many = await authed<IngestItemList>((headers) =>
    confirmIngestBatch({ headers, path: { batch_id: batchId }, body: { items: choices } }),
  );
  return many.items;
}

/**
 * Answer a confirm card or Vault prompt: the card is hidden at once and the Library polls until the run reports
 * back (the answer is accepted before the run has placed anything). A refused answer brings the card back.
 */
async function answer<T extends IngestItemOut | IngestItemOut[]>(
  items: readonly IngestItemOut[],
  send: () => Promise<T>,
): Promise<T> {
  uploads.answered(items);
  try {
    const out = await send();
    uploads.answered(Array.isArray(out) ? out : [out]);
    return out;
  } catch (e) {
    uploads.unanswered(items.map((i) => i.id));
    throw e;
  }
}

/** One grouped confirm: a single item goes through its own endpoint, a batch through the batch endpoint. */
export function useConfirmPlacements() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({
      batchId,
      choices,
      items,
    }: {
      batchId: string | null;
      choices: BatchChoice[];
      items: readonly IngestItemOut[];
    }) => answer(items, () => sendConfirm(batchId, choices)),
    onSettled: invalidate,
  });
}

export function useVaultDecision() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ item, vault }: { item: IngestItemOut; vault: NonNullable<ConfirmBody["vault"]> }) =>
      answer([item], () =>
        authed<IngestItemOut>((headers) => confirmIngestItem({ headers, path: { item_id: item.id }, body: { vault } })),
      ),
    onSettled: invalidate,
  });
}

export function useUndoPlacement() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (itemId: string) =>
      authed<IngestItemOut>((headers) => undoIngestPlacement({ headers, path: { item_id: itemId } })),
    onSettled: invalidate,
  });
}

export function useRetryItem() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (itemId: string) =>
      authed<IngestItemOut>((headers) => retryIngestItem({ headers, path: { item_id: itemId } })),
    onSettled: invalidate,
  });
}

export function useDeleteItem() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: async (itemId: string) => {
      await authed<unknown>((headers) => deleteIngestItem({ headers, path: { item_id: itemId } }));
      uploads.forget(itemId);
    },
    onSettled: invalidate,
  });
}
