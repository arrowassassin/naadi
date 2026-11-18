import { useCallback, useEffect, useRef, useState } from "react";
import { useUiText } from "../copy/useUiText";
import { buildNotesPdfs, liveDeps, type NotesPdfDeps, type NotesPdfOutcome, type Progress } from "./notesPdf";

export type PdfPhase =
  | { kind: "idle" }
  | { kind: "preparing"; progress: Progress | null }
  | { kind: "done"; outcome: NotesPdfOutcome }
  | { kind: "cancelled" }
  | { kind: "failed" };

/** Runs the on-device notes PDFs: progress, cancel, result held in memory only (nothing is uploaded). */
export function usePdfPrep(makeDeps: (t: ReturnType<typeof useUiText>) => NotesPdfDeps = liveDeps) {
  const t = useUiText();
  const [phase, setPhase] = useState<PdfPhase>({ kind: "idle" });
  const ctrl = useRef<AbortController | null>(null);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      ctrl.current?.abort();
    };
  }, []);

  const start = useCallback(async () => {
    if (ctrl.current && !ctrl.current.signal.aborted) return;
    const c = new AbortController();
    ctrl.current = c;
    setPhase({ kind: "preparing", progress: null });
    try {
      const outcome = await buildNotesPdfs(makeDeps(t), {
        signal: c.signal,
        onProgress: (progress) => alive.current && !c.signal.aborted && setPhase({ kind: "preparing", progress }),
      });
      if (alive.current && !c.signal.aborted) setPhase({ kind: "done", outcome });
    } catch (e) {
      if (!alive.current) return;
      setPhase(e instanceof DOMException && e.name === "AbortError" ? { kind: "cancelled" } : { kind: "failed" });
    } finally {
      if (ctrl.current === c) ctrl.current = null;
    }
  }, [makeDeps, t]);

  const cancel = useCallback(() => {
    if (!ctrl.current) return;
    ctrl.current.abort();
    setPhase({ kind: "cancelled" });
  }, []);
  return { phase, start, cancel };
}
