import {
  type AdminGeneratedItem,
  type AdminGeneratedList,
  adminApproveGeneratedApiAdminGeneratedMediaIdApprovePost,
  adminListGeneratedApiAdminGeneratedGet,
  deleteGeneratedApiGeneratedMediaIdDelete,
  type GeneratedApproveBody,
  type GeneratedApproveOut,
  type GeneratedItem,
  type GeneratedList,
  type GenerationStatusOut,
  generationStatusApiGenerationStatusGet,
  getGeneratedApiGeneratedMediaIdGet,
  listGeneratedApiGeneratedGet,
} from "@naadi/shared";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { authed } from "../auth/api";
import { ApiError } from "../auth/apiError";
import { useAuth } from "../auth/session";

export type { AdminGeneratedItem, GeneratedApproveOut, GeneratedItem, GenerationStatusOut };

const KNOWN_CODES = new Set([
  "prompt_empty",
  "prompt_too_long",
  "prompt_real_person",
  "prompt_patient_photo",
  "prompt_gore",
  "prompt_explicit",
  "generation_paused",
  "generation_cap_reached",
  "generation_busy",
  "generation_not_configured",
  "generation_disabled",
  "generation_kind_disabled",
  "generation_failed",
  "generation_timeout",
  "generation_unavailable",
  "generation_bad_result",
  "storage_write_failed",
  "storage_read_failed",
  "generated_not_found",
  "generated_not_ready",
  "generated_video_not_publishable",
  "section_not_found",
]);

/** The ui_copy key for an error code (an API error's `code`, or a tool result's refusal code). */
export function generationErrorKey(code: string | null | undefined): string {
  return code && KNOWN_CODES.has(code) ? `generation.error_${code}` : "generation.error_generic";
}

export function errorKey(e: unknown): string {
  return generationErrorKey(e instanceof ApiError ? e.code : null);
}

function useUserId(): string | null {
  const auth = useAuth();
  return auth.status === "signed-in" ? auth.session.user.id : null;
}

export const generatedKey = (userId: string | null) => ["generated", userId] as const;
export const PAGE = 24;
/** Poll an item that is still being made, so a progress bar and the finished media appear without a reload. */
export const POLL_MS = 2000;

export function isWorking(item: Pick<GeneratedItem, "status">): boolean {
  return item.status === "queued" || item.status === "generating";
}

export function useGenerationStatus(enabled = true) {
  const userId = useUserId();
  return useQuery({
    queryKey: ["generation-status", userId],
    enabled: enabled && userId !== null,
    staleTime: 30_000,
    queryFn: ({ signal }) =>
      authed<GenerationStatusOut>((headers) => generationStatusApiGenerationStatusGet({ signal, headers })),
  });
}

export function useGenerated(id: string | null) {
  const userId = useUserId();
  return useQuery<GeneratedItem>({
    queryKey: [...generatedKey(userId), "item", id],
    enabled: userId !== null && id !== null,
    refetchInterval: (q) => (q.state.data && isWorking(q.state.data) ? POLL_MS : false),
    queryFn: ({ signal }) =>
      authed<GeneratedItem>((headers) =>
        getGeneratedApiGeneratedMediaIdGet({ signal, headers, path: { media_id: id as string } }),
      ),
  });
}

/** The caller's generated items, newest first; `before` pages through older ones. */
export function useGeneratedList(before: string | null) {
  const userId = useUserId();
  return useQuery({
    queryKey: [...generatedKey(userId), "list", before],
    enabled: userId !== null,
    staleTime: 10_000,
    queryFn: ({ signal }) =>
      authed<GeneratedList>((headers) =>
        listGeneratedApiGeneratedGet({ signal, headers, query: { limit: PAGE, ...(before ? { before } : {}) } }),
      ),
  });
}

export function useDeleteGenerated() {
  const qc = useQueryClient();
  const userId = useUserId();
  return useMutation({
    mutationFn: (id: string) =>
      authed<unknown>((headers) => deleteGeneratedApiGeneratedMediaIdDelete({ headers, path: { media_id: id } })),
    onSuccess: () => void qc.invalidateQueries({ queryKey: generatedKey(userId) }),
  });
}

export function usePendingGenerated(enabled: boolean) {
  const userId = useUserId();
  return useQuery({
    queryKey: ["generated-pending", userId],
    enabled: enabled && userId !== null,
    queryFn: ({ signal }) =>
      authed<AdminGeneratedList>((headers) =>
        adminListGeneratedApiAdminGeneratedGet({ signal, headers, query: { pending: true } }),
      ),
  });
}

export function useApproveGenerated() {
  const qc = useQueryClient();
  const userId = useUserId();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: GeneratedApproveBody }) =>
      authed<GeneratedApproveOut>((headers) =>
        adminApproveGeneratedApiAdminGeneratedMediaIdApprovePost({ headers, path: { media_id: id }, body }),
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["generated-pending", userId] });
      void qc.invalidateQueries({ queryKey: generatedKey(userId) });
    },
  });
}
