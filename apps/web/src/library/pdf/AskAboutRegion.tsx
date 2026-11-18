import type { PxRect } from "@naadi/ink";
import { Button } from "@naadi/ui";
import { MessageCircleQuestion } from "lucide-react";
import { askAssistant, useAssistantAvailable } from "../../assistant/ask";
import { useUiText } from "../../copy/useUiText";
import { cropRegion } from "./crop";

/**
 * Hand a lassoed region of a PDF page to the assistant: the circled part of the rendered page (with the selected
 * ink over it) as a PNG, and the page's ink surface id `{upload_id}:{page}`, whose text the assistant reads (and
 * cites) through the ingest API. False when no assistant is listening.
 */
export async function askAboutPdfRegion(
  surfaceId: string,
  page: HTMLCanvasElement,
  rect: PxRect,
  ink: Blob | null,
): Promise<boolean> {
  const image = await cropRegion(page, rect, ink);
  return askAssistant({ kind: "explain", context: { type: "pdf_page", id: surfaceId, image } });
}

/** "Ask about this" under a lasso selection: present only while the assistant can answer (never a dead button). */
export function AskAboutRegion({ visible, busy, onAsk }: { visible: boolean; busy: boolean; onAsk: () => void }) {
  const t = useUiText();
  const available = useAssistantAvailable();
  if (!available || !visible) return null;
  return (
    <div className="flex justify-end">
      <Button
        variant="assistant"
        icon={<MessageCircleQuestion size={16} />}
        disabled={busy}
        onClick={onAsk}
        data-pdf-ask=""
      >
        {t("library.pdf.ask")}
      </Button>
    </div>
  );
}
