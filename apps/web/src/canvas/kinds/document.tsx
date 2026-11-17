import { Button } from "@naadi/ui";
import { Check, NotebookPen } from "lucide-react";
import { useState } from "react";
import { DocEditor } from "../../assistant/DocEditor";
import type { TipTapDoc } from "../../assistant/protocol";
import { useUiText } from "../../copy/useUiText";
import { type CanvasKindProps, useCanvasKeep } from "../registry";
import type { DocumentData } from "./index";

/**
 * A document the assistant drafted (violet) that the student edits. "Keep" saves it as the student's own note
 * (through the registered keep handler, i.e. the notes API) and the item turns paper. Without a handler there
 * is no Keep button (never a button that does nothing).
 */
export default function DocumentKind({ item, update }: CanvasKindProps<DocumentData>) {
  const t = useUiText();
  const keep = useCanvasKeep();
  const [doc, setDoc] = useState<TipTapDoc>(item.data.doc);
  const [state, setState] = useState<"idle" | "saving" | "failed">("idle");
  const kept = item.origin === "student";

  const onKeep = async () => {
    if (!keep) return;
    setState("saving");
    try {
      const ref = await keep({ ...item, data: { ...item.data, doc } });
      update({ origin: "student", ref, data: { ...item.data, doc } });
      setState("idle");
    } catch {
      setState("failed");
    }
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3" data-canvas-document="">
      <DocEditor
        doc={doc}
        onChange={(d) => {
          setDoc(d);
          setState((s) => (s === "failed" ? "idle" : s));
        }}
        label={t(kept ? "canvas.document.edit_note" : "canvas.document.edit_draft")}
        tone={kept ? "paper" : "violet"}
        // Once kept it is the student's note: further edits happen in Notes, so nothing typed here is lost.
        editable={!kept}
        className="min-h-0 flex-1 overflow-y-auto"
      />
      {kept ? (
        <p role="status" className="m-0 flex items-center gap-2 text-paper-text-2 text-ui-sm">
          <NotebookPen aria-hidden="true" size={16} />
          {t("canvas.document.kept")}
        </p>
      ) : keep ? (
        <div className="flex flex-wrap items-center gap-3">
          <Button
            variant="primary"
            icon={<Check size={18} />}
            disabled={state === "saving"}
            onClick={() => void onKeep()}
          >
            {t(state === "saving" ? "canvas.document.keeping" : "canvas.document.keep")}
          </Button>
          {state === "failed" ? (
            <span role="alert" className="text-amber text-ui-sm">
              {t("canvas.document.keep_failed")}
            </span>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
