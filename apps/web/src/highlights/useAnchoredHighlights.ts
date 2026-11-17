import { useEffect, useMemo, useRef } from "react";
import { type Note, useNotes, useNoteWriter } from "../notes/api";
import { type AnchorPos, isHighlightColor, type PaintRange, relocate, type TextRun } from "./anchor";

export interface AnchoredHighlights {
  ranges: PaintRange[];
  /** Highlights whose quote is no longer in the text (still listed in Notes as Detached). */
  detached: Note[];
  notes: Note[];
  isPending: boolean;
  isError: boolean;
  fromCache: boolean;
}

/**
 * The student's highlights on one surface (a section, or a question's explanation), located in the current text.
 * A highlight whose text moved is repaired on the server (new positions, same quote); one whose quote is gone is
 * marked `detached` there, so Notes lists it as Detached; one that is found again is re-attached.
 */
export function useAnchoredHighlights(
  anchorType: "section" | "question",
  anchorId: string,
  runs: readonly TextRun[],
  revision: string | null,
  { enabled = true, repair = true }: { enabled?: boolean; repair?: boolean } = {},
): AnchoredHighlights {
  const list = useNotes({ kind: "highlight", anchor_type: anchorType, anchor_id: anchorId, limit: 200 }, { enabled });
  const writer = useNoteWriter();
  const located = useMemo(() => {
    const ranges: PaintRange[] = [];
    const detached: Note[] = [];
    const fixes: { id: string; pos: AnchorPos }[] = [];
    // Oldest first, so a newer highlight paints over an older one where they overlap.
    const notes = [...list.items].sort((a, b) => a.created_at.localeCompare(b.created_at));
    for (const n of notes) {
      const pos = n.anchor_pos as AnchorPos | null;
      if (!pos || !isHighlightColor(n.highlight_color)) continue;
      const at = runs.length ? relocate(pos, runs) : null;
      if (!at) continue;
      if (at.status === "detached") {
        detached.push(n);
        if (!(pos as { detached?: boolean | null }).detached)
          fixes.push({ id: n.id, pos: { ...pos, detached: true } as AnchorPos });
        continue;
      }
      ranges.push({ id: n.id, from: at.from, to: at.to, color: n.highlight_color });
      const wasDetached = Boolean((pos as { detached?: boolean | null }).detached);
      if (at.status === "moved" || wasDetached) {
        fixes.push({
          id: n.id,
          pos: { ...pos, from: at.from, to: at.to, revision, detached: null } as AnchorPos,
        });
      }
    }
    return { ranges, detached, notes, fixes };
  }, [list.items, runs, revision]);

  // Repair each anchor once per mount (never in a loop while the list catches up).
  const repaired = useRef(new Set<string>());
  useEffect(() => {
    if (!repair || list.fromCache || list.isFetching) return;
    for (const f of located.fixes) {
      const key = `${f.id}:${f.pos.from}:${f.pos.to}:${(f.pos as { detached?: unknown }).detached ?? ""}`;
      if (repaired.current.has(key)) continue;
      repaired.current.add(key);
      writer.update(f.id, { anchor_pos: f.pos });
    }
  }, [located.fixes, repair, list.fromCache, list.isFetching, writer]);

  return {
    ranges: located.ranges,
    detached: located.detached,
    notes: located.notes,
    isPending: list.isPending,
    isError: list.isError,
    fromCache: list.fromCache,
  };
}
