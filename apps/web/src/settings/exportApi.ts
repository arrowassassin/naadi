import { deleteExport, type ExportJob, type ExportList, getExport, listExports, startExport } from "@naadi/shared";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { authed } from "../auth/api";
import { useUserId } from "../notes/api";
import { isActive, pollDelay, sortJobs } from "./exportState";

export const exportsKey = (userId: string | null) => ["exports", userId] as const;

/**
 * The student's latest export jobs. On mount the list shows what was started before (a reload keeps a running
 * job); while one is queued or running, that job is polled (GET /api/export/{id}) with a gentle backoff.
 */
export function useExportJobs() {
  const userId = useUserId();
  const qc = useQueryClient();
  const list = useQuery({
    queryKey: exportsKey(userId),
    enabled: userId !== null,
    staleTime: 0,
    queryFn: async ({ signal }) =>
      sortJobs((await authed<ExportList>((headers) => listExports({ signal, headers }))).items),
  });
  const active = list.data?.find(isActive);
  const attempt = useRef(0);
  const activeId = active?.id;
  // biome-ignore lint/correctness/useExhaustiveDependencies: a new job restarts the backoff
  useEffect(() => {
    attempt.current = 0;
  }, [activeId]);
  const poll = useQuery({
    queryKey: ["export-job", userId, activeId],
    enabled: userId !== null && activeId !== undefined,
    gcTime: 0,
    refetchInterval: () => pollDelay(attempt.current++),
    queryFn: async ({ signal }) =>
      authed<ExportJob>((headers) => getExport({ signal, headers, path: { job_id: activeId as string } })),
  });
  const polled = poll.data;
  useEffect(() => {
    if (!polled) return;
    qc.setQueryData<ExportJob[]>(exportsKey(userId), (old) =>
      old ? sortJobs(old.map((j) => (j.id === polled.id ? polled : j))) : old,
    );
  }, [polled, qc, userId]);
  return { list, active };
}

export function useStartExport() {
  const userId = useUserId();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (includeVault: boolean) =>
      authed<ExportJob>((headers) => startExport({ headers, body: { include_vault: includeVault } })),
    onSuccess: (job) =>
      qc.setQueryData<ExportJob[]>(exportsKey(userId), (old) =>
        sortJobs([job, ...(old ?? []).filter((j) => j.id !== job.id)]),
      ),
    // A 409 means one is already running: reload the list so it shows that job.
    onError: () => void qc.invalidateQueries({ queryKey: exportsKey(userId) }),
  });
}

export function useDeleteExport() {
  const userId = useUserId();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => authed<unknown>((headers) => deleteExport({ headers, path: { job_id: id } })),
    onSuccess: (_d, id) => qc.setQueryData<ExportJob[]>(exportsKey(userId), (old) => old?.filter((j) => j.id !== id)),
    onError: () => void qc.invalidateQueries({ queryKey: exportsKey(userId) }),
  });
}
