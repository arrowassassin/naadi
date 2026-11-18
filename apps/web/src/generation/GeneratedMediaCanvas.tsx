import { Skeleton } from "@naadi/ui";
import { type GeneratedMediaData, generatedMediaSchema } from "../canvas/kinds/generated_media";
import { useGenerated } from "./api";
import { useGenerationCopy } from "./copy";
import { GeneratedMedia } from "./GeneratedMedia";

interface Props {
  item: { key: string; data: Record<string, unknown> };
  close?: () => void;
  update?: (next: Record<string, unknown>) => void;
}

/** The canvas body for `generated_media`. Chrome (title, close, violet framing, error state) is the registry's. */
export default function GeneratedMediaCanvas({ item }: Props) {
  const t = useGenerationCopy();
  const parsed = generatedMediaSchema.safeParse(item.data);
  const data: GeneratedMediaData | null = parsed.success ? parsed.data : null;
  const q = useGenerated(data?.media_id ?? null);
  if (!data) return <p className="m-0 text-ui-sm text-amber">{t("generation.load_error")}</p>;
  if (q.isPending) return <Skeleton className="h-64 w-full rounded-block" aria-label={t("generation.loading")} />;
  if (q.isError) {
    return (
      <p role="alert" className="m-0 text-ui-sm text-amber">
        {t("generation.load_error")}
      </p>
    );
  }
  return <GeneratedMedia item={q.data} />;
}
