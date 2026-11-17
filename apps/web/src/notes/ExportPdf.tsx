import { Button } from "@naadi/ui";
import { FileDown } from "lucide-react";
import { useState } from "react";
import { useUiText } from "../copy/useUiText";
import { deliverPdf, exportHighlightsToPdf, exportNoteToPdf } from "../pdf";
import { type Note, useNotes, usePendingOps } from "./api";
import { isDetached } from "./format";
import type { HighlightFilters } from "./HighlightsRail";

type State = "idle" | "working" | "error";
/** A PDF being made (exportNoteToPdf and exportHighlightsToPdf resolve to the same result). */
type Making = ReturnType<typeof exportNoteToPdf>;

function useExport() {
  const t = useUiText();
  const [state, setState] = useState<State>("idle");
  const run = async (make: () => Making) => {
    if (state === "working") return;
    setState("working");
    try {
      await deliverPdf(await make());
      setState("idle");
    } catch (e) {
      // The student closed the share sheet: nothing went wrong.
      setState(e instanceof DOMException && e.name === "AbortError" ? "idle" : "error");
    }
  };
  return { t, state, run };
}

function Status({ state }: { state: State }) {
  const t = useUiText();
  if (state === "error") {
    return (
      <span role="alert" className="text-ui-sm">
        {t("notes.export_failed")}
      </span>
    );
  }
  return (
    <span role="status" className="sr-only">
      {state === "working" ? t("notes.export_working") : ""}
    </span>
  );
}

/** "Export PDF" for one note (made in the browser from what is on screen, nothing uploaded). */
export function ExportNotePdf({
  note,
  body,
  title,
}: {
  note: Note;
  /** The body as edited right now (the latest keystrokes may not be saved yet). */
  body: () => unknown;
  title: string;
}) {
  const { t, state, run } = useExport();
  return (
    <span className="flex flex-wrap items-center gap-2">
      <Button
        variant="secondary"
        icon={<FileDown size={16} />}
        disabled={state === "working" || note.body_omitted}
        onClick={() =>
          void run(() =>
            exportNoteToPdf(
              { title: title.trim() || note.title, body: body() as Note["body"], updated_at: note.updated_at },
              t,
            ),
          )
        }
        className="border-ground! text-ground! hover:bg-ground/10!"
        data-export-pdf="note"
      >
        {state === "working" ? t("notes.export_working") : t("notes.export_pdf")}
      </Button>
      <Status state={state} />
    </span>
  );
}

/** "Export PDF" for the highlights the rail shows (same filters, same list). */
export function ExportHighlightsPdf({ filters }: { filters: HighlightFilters }) {
  const { t, state, run } = useExport();
  const anchorType = filters.anchor === "section" || filters.anchor === "question" ? filters.anchor : undefined;
  const list = useNotes({ kind: "highlight", color: filters.color, anchor_type: anchorType, limit: 100 });
  const local = new Set(
    usePendingOps()
      .filter((o) => o.create)
      .map((o) => o.id),
  );
  const items = list.items.filter((n) =>
    filters.anchor === "detached" ? isDetached(n, local.has(n.id)) : !isDetached(n, local.has(n.id)),
  );
  return (
    <span className="flex flex-wrap items-center gap-2">
      <Button
        variant="secondary"
        icon={<FileDown size={16} />}
        disabled={state === "working" || items.length === 0}
        onClick={() => void run(() => exportHighlightsToPdf(items, t))}
        data-export-pdf="highlights"
      >
        {state === "working" ? t("notes.export_working") : t("notes.export_highlights")}
      </Button>
      <Status state={state} />
    </span>
  );
}
