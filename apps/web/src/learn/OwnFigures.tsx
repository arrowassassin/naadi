import type { MediaOut } from "@naadi/shared";
import { Chip } from "@naadi/ui";
import { FileText } from "lucide-react";
import { useMemo } from "react";
import { ContentRenderer } from "../content/ContentRenderer";
import type { UnknownContent } from "../content/types";
import { useUiText } from "../copy/useUiText";

/**
 * The student's own figures attached to a shared section (segment 15: a photo placed as a figure overlay). Visible
 * to their owner only (RLS); shown after the section body with the content's own figure component.
 */
export function OwnFigures({
  sectionId,
  mediaIds,
  media,
  onUnknown,
}: {
  sectionId: string;
  mediaIds: readonly string[];
  media: Record<string, MediaOut>;
  onUnknown?: (u: UnknownContent) => void;
}) {
  const t = useUiText();
  const doc = useMemo(
    () => ({ type: "doc", content: mediaIds.map((id) => ({ type: "figure", attrs: { mediaId: id } })) }),
    [mediaIds],
  );
  return (
    <section
      aria-labelledby={`own-figures-${sectionId}`}
      className="mx-auto flex w-full max-w-[46rem] flex-col gap-4 border-line-soft border-t pt-5"
      data-own-figures=""
    >
      <div className="flex flex-wrap items-center gap-3">
        <h2 id={`own-figures-${sectionId}`} className="m-0 font-bold font-display text-[1.375rem] tracking-[-0.02em]">
          {t("learn.your_figures")}
        </h2>
        <Chip tone="meta" icon={<FileText size={12} />}>
          {t("learn.your_file")}
        </Chip>
      </div>
      <ContentRenderer
        doc={doc}
        sectionId={`${sectionId}-own`}
        media={media}
        onUnknown={onUnknown}
        text={t}
        headingBase={3}
      />
    </section>
  );
}
