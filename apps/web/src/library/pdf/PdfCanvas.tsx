import { useCallback } from "react";
import { type PdfCanvasData, pdfCanvasSchema } from "../../canvas/kinds/pdf";
import { useUiText } from "../../copy/useUiText";
import { PdfViewer } from "./PdfViewer";

interface Props {
  item: { key: string; data: Record<string, unknown> };
  close?: () => void;
  update?: (next: Record<string, unknown>) => void;
}

/** The canvas body for the `pdf` kind. Chrome (title, close, framing, error state) is the registry's. */
export default function PdfCanvas({ item, update }: Props) {
  const t = useUiText();
  const parsed = pdfCanvasSchema.safeParse(item.data);
  const data: PdfCanvasData | null = parsed.success ? parsed.data : null;
  const onPageChange = useCallback(
    (page: number) => {
      if (data && data.page !== page) update?.({ ...data, page });
    },
    [data, update],
  );
  if (!data) return <p className="m-0 text-ui-sm text-amber">{t("library.pdf.load_failed")}</p>;
  return <PdfViewer uploadId={data.upload_id.toLowerCase()} page={data.page ?? 1} onPageChange={onPageChange} />;
}
