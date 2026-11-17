import { useCallback, useEffect, useState } from "react";
import { useAuth } from "../auth/session";
import { writeOffline } from "../learn/offlineCache";
import { putCached } from "./cache";
import { getMeta, setMeta } from "./db";
import { findLeaks, stripHidden } from "./guard";
import { FIGURE_CACHE } from "./sw/rules";

/**
 * "Download this week" (segment 18): the manifest of the next 7 plan days (GET /api/offline/week) is stored in
 * `naadi-v1` under the same keys the screens already fall back to when the network fails (chapter, positions,
 * practice-question, cards-due, notes, today), so an offline reload shows them with no new read path. Figures go
 * into the service worker's figure cache (CacheFirst, the same cache the SW serves from); own videos ≤ 1080p into
 * `media`. YouTube is never downloaded (it shows "Needs a connection" offline).
 *
 * Every question-shaped value passes the leak guard before it is stored, and the download refuses to store a part
 * in which the guard still finds an answer field (defence in depth: the server never sends one).
 */

export type PartName =
  | "chapters"
  | "questions"
  | "cards"
  | "due"
  | "notes"
  | "notebooks"
  | "ink"
  | "plan"
  | "pathway"
  | "figures"
  | "videos";

export interface PartSize {
  part: PartName;
  bytes: number;
  count: number;
}

/** The manifest as the client reads it: parts it knows, each an array or object of online response shapes. */
export interface WeekManifest {
  start: string;
  end: string;
  parts: PartSize[];
  chapters?: { id: string }[];
  positions?: { chapter_id: string }[];
  blocks?: { id: string }[];
  questions?: { block_id: string; n: number; view: unknown }[];
  due?: unknown;
  notes?: unknown;
  notebooks?: { id: string }[];
  ink?: { surface_type: string; surface_id: string; layer: { rev: number; items: unknown[] } }[];
  today?: unknown;
  plan?: unknown;
  pathway?: unknown;
  figures?: { media_id: string; url: string }[];
  videos?: { media_id: string; url: string }[];
}

export interface DownloadRecord {
  start: string;
  end: string;
  saved_at: number;
  parts: PartSize[];
  figures: string[];
}

export type DownloadPhase = "idle" | "checking" | "preview" | "downloading" | "ready" | "failed";

export interface Progress {
  part: PartName;
  done: number;
  total: number;
}

/** The manifest fetcher (generated SDK call, set by the module that owns the endpoint binding). */
let fetchManifest: (start: string | null) => Promise<WeekManifest> = () => Promise.reject(new Error("unbound"));
export function bindManifest(fn: (start: string | null) => Promise<WeekManifest>): void {
  fetchManifest = fn;
}

function totalBytes(parts: readonly PartSize[]): number {
  return parts.reduce((n, p) => n + p.bytes, 0);
}

export function manifestBytes(m: Pick<WeekManifest, "parts">): number {
  return totalBytes(m.parts);
}

/** Store one manifest into the device. `onProgress` sees each part as it goes. Pure orchestration, unit-tested. */
export async function storeManifest(
  user: string,
  m: WeekManifest,
  onProgress: (p: Progress) => void = () => {},
  fetcher: typeof fetch = (...a) => fetch(...a),
): Promise<DownloadRecord> {
  const step = async <T>(part: PartName, items: readonly T[], put: (item: T) => Promise<unknown>) => {
    for (const [i, item] of items.entries()) {
      onProgress({ part, done: i, total: items.length });
      await put(item);
    }
    onProgress({ part, done: items.length, total: items.length });
  };

  const guarded = <T>(part: PartName, value: T): T => {
    const clean = stripHidden(value);
    const leaks = findLeaks(clean);
    if (leaks.length) throw new Error(`offline_leak_${part}`);
    return clean;
  };

  await step("chapters", m.chapters ?? [], (c) => writeOffline(user, "chapter", c.id, c));
  for (const p of m.positions ?? []) await writeOffline(user, "positions", p.chapter_id, p);
  for (const b of m.blocks ?? []) await writeOffline(user, "practice-block", b.id, guarded("questions", b));
  await step("questions", m.questions ?? [], (q) =>
    writeOffline(user, "practice-question", `${q.block_id}:${q.n}`, guarded("questions", q.view)),
  );
  if (m.due !== undefined)
    await step("cards", [m.due], (d) => writeOffline(user, "cards-due", "v1", guarded("cards", d)));
  if (m.notes !== undefined) await step("notes", [m.notes], (n) => putCached(user, "notes", "week", n));
  await step("notebooks", m.notebooks ?? [], (b) => putCached(user, "notebooks", `detail:${b.id}`, b));
  await step("ink", m.ink ?? [], (l) => putCached(user, "inkLayers", `${l.surface_type}:${l.surface_id}`, l.layer));
  if (m.today !== undefined) await writeOffline(user, "today", "today", m.today);
  if (m.plan !== undefined) await putCached(user, "planDays", m.start, m.plan);
  if (m.pathway !== undefined) await putCached(user, "pathway", "status", m.pathway);

  const figures = (m.figures ?? []).map((f) => f.url);
  await step("figures", figures, async (url) => {
    if (typeof caches === "undefined") return;
    const cache = await caches.open(FIGURE_CACHE);
    if (await cache.match(url)) return;
    const res = await fetcher(url, { mode: "cors", credentials: "omit" });
    if (res.ok) await cache.put(url, res);
  });
  await step("videos", m.videos ?? [], async (v) => {
    const res = await fetcher(v.url, { mode: "cors", credentials: "omit" });
    if (!res.ok) return;
    await putCached(user, "media", v.media_id, { url: v.url, blob: await res.blob() });
  });

  const record: DownloadRecord = { start: m.start, end: m.end, saved_at: Date.now(), parts: m.parts, figures };
  await setMeta(user, "download", record);
  return record;
}

export async function removeDownload(user: string): Promise<void> {
  const rec = await getMeta<DownloadRecord>(user, "download");
  const { clearCacheFor } = await import("./cache");
  await clearCacheFor(user);
  if (rec && typeof caches !== "undefined") {
    try {
      const cache = await caches.open(FIGURE_CACHE);
      for (const url of rec.figures) await cache.delete(url);
    } catch {
      // nothing cached
    }
  }
}

export interface WeekDownload {
  phase: DownloadPhase;
  record: DownloadRecord | null;
  preview: PartSize[] | null;
  progress: Progress | null;
  error: string | null;
  check: () => Promise<void>;
  start: () => Promise<void>;
  remove: () => Promise<void>;
}

/** The download state for Settings → Offline and Today's action. */
export function useWeekDownload(): WeekDownload {
  const auth = useAuth();
  const user = auth.status === "signed-in" ? auth.session.user.id : null;
  const [phase, setPhase] = useState<DownloadPhase>("idle");
  const [record, setRecord] = useState<DownloadRecord | null>(null);
  const [manifest, setManifest] = useState<WeekManifest | null>(null);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!user) return;
    let live = true;
    void getMeta<DownloadRecord>(user, "download").then((r) => {
      if (!live) return;
      setRecord(r ?? null);
      if (r) setPhase((p) => (p === "idle" ? "ready" : p));
    });
    return () => {
      live = false;
    };
  }, [user]);

  const check = useCallback(async () => {
    setPhase("checking");
    setError(null);
    try {
      const m = await fetchManifest(null);
      setManifest(m);
      setPhase("preview");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setPhase("failed");
    }
  }, []);

  const start = useCallback(async () => {
    if (!user) return;
    setPhase("downloading");
    setError(null);
    try {
      const m = manifest ?? (await fetchManifest(null));
      const rec = await storeManifest(user, m, setProgress);
      setRecord(rec);
      setManifest(null);
      setPhase("ready");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setPhase("failed");
    } finally {
      setProgress(null);
    }
  }, [manifest, user]);

  const remove = useCallback(async () => {
    if (!user) return;
    await removeDownload(user);
    setRecord(null);
    setPhase("idle");
  }, [user]);

  return { phase, record, preview: manifest?.parts ?? null, progress, error, check, start, remove };
}
