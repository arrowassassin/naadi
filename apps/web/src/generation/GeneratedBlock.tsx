import { type GeneratedMediaData, GeneratedMediaDataSchema } from "@naadi/shared";
import { Skeleton } from "@naadi/ui";
import { useGenerated } from "./api";
import { useGenerationCopy } from "./copy";
import { GeneratedMedia } from "./GeneratedMedia";

/** Data of the assistant's `image` / `video` answer blocks (contract with S14): the item is fetched by id so the
 * signed URL is always fresh and a reopened thread still shows it. */
export const generatedBlockSchema = GeneratedMediaDataSchema;
export type GeneratedBlockData = GeneratedMediaData;

export function GeneratedBlock({ data, onOpen }: { data: GeneratedBlockData; onOpen?: () => void }) {
  const t = useGenerationCopy();
  const q = useGenerated(data.media_id);
  if (q.isPending) return <Skeleton className="h-48 w-full rounded-block" aria-label={t("generation.loading")} />;
  if (q.isError) {
    return (
      <p role="alert" className="m-0 text-ui-sm text-amber">
        {t("generation.load_error")}
      </p>
    );
  }
  return <GeneratedMedia item={q.data} onOpen={onOpen} />;
}
