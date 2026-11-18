import type { HighlightColor } from "../highlights/anchor";

/** Notes filters the screens use (the query of GET /api/notes). */
export interface NoteFilters {
  kind?: "note" | "highlight" | "page";
  color?: HighlightColor;
  anchor_type?: "section" | "question" | "video" | "pdf" | "free";
  anchor_id?: string;
  notebook_id?: string;
  q?: string;
  limit?: number;
}

/** A tab of the Notes page's segmented switch. */
export type NotesTab = "notes" | "notebooks";
