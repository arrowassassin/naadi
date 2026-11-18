import type { ExportJob } from "@naadi/shared";
import { ApiError } from "../auth/apiError";

export type ExportStatus = ExportJob["status"];

export const isActive = (j: Pick<ExportJob, "status">): boolean => j.status === "queued" || j.status === "running";

/** The newest job first (the server already sorts, this keeps the view stable whatever arrives). */
export function sortJobs(items: readonly ExportJob[]): ExportJob[] {
  return [...items].sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at));
}

/** Milliseconds until the next poll of a running job: 1.5 s, easing out to 6 s on a long build. */
export function pollDelay(attempt: number): number {
  return Math.min(6000, Math.round(1500 * 1.2 ** Math.max(0, attempt - 6)));
}

export type StartFailure = "active" | "unavailable" | "rate_limited" | "other";

/** What a refused POST /api/export means for the screen. */
export function startFailure(err: unknown): StartFailure {
  if (!(err instanceof ApiError)) return "other";
  if (err.status === 409 || err.code === "export_active") return "active";
  if (err.status === 503 || err.code === "export_unavailable") return "unavailable";
  if (err.status === 429) return "rate_limited";
  return "other";
}

/** ui_copy key for a failed job's stable error code. */
export function failedKey(code: string | null): string {
  return code === "interrupted" ? "export.failed_interrupted" : "export.failed_generic";
}

export type JobView =
  | { kind: "queued" | "running" | "failed" | "expired" }
  | { kind: "ready"; downloads: number; parts: number }
  | { kind: "ready_no_links" };

/** One job as the screen shows it. A ready job whose links are gone (expiring, storage down) reads as expired. */
export function jobView(job: ExportJob): JobView {
  switch (job.status) {
    case "ready":
      return job.downloads.length === 0
        ? { kind: "ready_no_links" }
        : { kind: "ready", downloads: job.downloads.length, parts: job.parts };
    default:
      return { kind: job.status };
  }
}

export function formatBytes(n: number | null): string | null {
  if (n === null || n < 0) return null;
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB"];
  let v = n / 1024;
  let u = 0;
  while (v >= 1024 && u < units.length - 1) {
    v /= 1024;
    u++;
  }
  return `${v >= 100 ? v.toFixed(0) : v.toFixed(1)} ${units[u]}`;
}

/** "17 Nov 2026, 14:05" in the device's zone; an absolute time, never a countdown. */
export function formatUntil(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeStyle: "short" }).format(d);
}
