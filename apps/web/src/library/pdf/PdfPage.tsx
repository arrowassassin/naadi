import { Link, useNavigate } from "@tanstack/react-router";
import { ArrowLeft } from "lucide-react";
import { useCallback, useEffect, useRef } from "react";
import { useUiText } from "../../copy/useUiText";
import { useIngestFile } from "../api";
import { PdfViewer } from "./PdfViewer";

/**
 * `/learn/library/pdf/$uploadId?page=`: the PDF viewer on its own page (until S14's canvas panel hosts the `pdf`
 * kind, and for citation links). The page in view is kept in `?page=` (replace, so Back leaves the viewer).
 */
export function PdfPage({ uploadId, page }: { uploadId: string; page: number }) {
  const t = useUiText();
  const navigate = useNavigate();
  const file = useIngestFile(uploadId);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const onPageChange = useCallback(
    (n: number) => {
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => {
        void navigate({
          to: "/learn/library/pdf/$uploadId",
          params: { uploadId },
          search: { page: n },
          replace: true,
          resetScroll: false,
        });
      }, 400);
    },
    [navigate, uploadId],
  );
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );
  return (
    <section aria-labelledby="pdf-h" className="flex flex-col gap-5 pt-6 pb-8 tablet:pt-8">
      <Link
        to="/learn/library"
        className="inline-flex min-h-11 w-fit items-center gap-2 text-text-2 text-ui-sm no-underline hover:text-text"
      >
        <ArrowLeft aria-hidden="true" size={16} />
        {t("library.pdf.back")}
      </Link>
      <h1
        id="pdf-h"
        className="m-0 font-display font-extrabold text-[clamp(1.75rem,1.2rem+2vw,3rem)] leading-[1] tracking-[-0.03em] [overflow-wrap:anywhere]"
      >
        {file.data?.name ?? t("library.pdf.title")}
      </h1>
      <PdfViewer uploadId={uploadId} page={page} onPageChange={onPageChange} />
    </section>
  );
}
