import type { ExportJob } from "@naadi/shared";
import { describe, expect, it } from "vitest";
import { ApiError } from "../auth/apiError";
import {
  failedKey,
  formatBytes,
  formatUntil,
  isActive,
  jobView,
  pollDelay,
  sortJobs,
  startFailure,
} from "./exportState";

const job = (over: Partial<ExportJob> = {}): ExportJob => ({
  id: "j1",
  status: "queued",
  include_vault: false,
  parts: 1,
  bytes: null,
  created_at: "2026-11-17T10:00:00Z",
  expires_at: null,
  error: null,
  downloads: [],
  ...over,
});

describe("export job state", () => {
  it("is active only while queued or running", () => {
    expect(["queued", "running", "ready", "failed", "expired"].map((s) => isActive({ status: s as never }))).toEqual([
      true,
      true,
      false,
      false,
      false,
    ]);
  });

  it("maps every status to a view", () => {
    expect(jobView(job({ status: "queued" })).kind).toBe("queued");
    expect(jobView(job({ status: "running" })).kind).toBe("running");
    expect(jobView(job({ status: "failed", error: "export_failed" })).kind).toBe("failed");
    expect(jobView(job({ status: "expired" })).kind).toBe("expired");
    expect(
      jobView(
        job({
          status: "ready",
          parts: 2,
          downloads: [
            { part: 1, url: "u" },
            { part: 2, url: "v" },
          ],
        }),
      ),
    ).toEqual({
      kind: "ready",
      downloads: 2,
      parts: 2,
    });
    expect(jobView(job({ status: "ready" })).kind).toBe("ready_no_links");
  });

  it("polls at 1.5 s and eases out to a ceiling", () => {
    expect(pollDelay(0)).toBe(1500);
    expect(pollDelay(6)).toBe(1500);
    expect(pollDelay(10)).toBeGreaterThan(1500);
    expect(pollDelay(500)).toBe(6000);
    const delays = Array.from({ length: 40 }, (_, i) => pollDelay(i));
    expect([...delays].sort((a, b) => a - b)).toEqual(delays);
  });

  it("reads a refused start: 409 active, 503 unavailable, 429, anything else", () => {
    expect(startFailure(new ApiError("x", 409, "export_active", null))).toBe("active");
    expect(startFailure(new ApiError("x", 503, "export_unavailable", null))).toBe("unavailable");
    expect(startFailure(new ApiError("x", 429, null, null))).toBe("rate_limited");
    expect(startFailure(new ApiError("x", 500, null, null))).toBe("other");
    expect(startFailure(new Error("boom"))).toBe("other");
  });

  it("words stable failure codes", () => {
    expect(failedKey("interrupted")).toBe("export.failed_interrupted");
    expect(failedKey("export_failed")).toBe("export.failed_generic");
    expect(failedKey(null)).toBe("export.failed_generic");
  });

  it("sorts newest first", () => {
    const a = job({ id: "a", created_at: "2026-11-16T10:00:00Z" });
    const b = job({ id: "b", created_at: "2026-11-17T10:00:00Z" });
    expect(sortJobs([a, b]).map((j) => j.id)).toEqual(["b", "a"]);
  });

  it("formats sizes and an absolute expiry, never a countdown", () => {
    expect(formatBytes(null)).toBeNull();
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MB");
    expect(formatUntil(null)).toBeNull();
    expect(formatUntil("not a date")).toBeNull();
    expect(formatUntil("2026-11-18T09:05:00Z")).toMatch(/2026/);
  });
});
