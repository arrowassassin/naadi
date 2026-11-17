import { Chip } from "@naadi/ui";
import { Sparkles } from "lucide-react";
import { useGenerationCopy } from "./copy";

/** The AI label: violet (assistant output), a word and an icon, always visible next to generated media. */
export function AiLabel({ className }: { className?: string }) {
  const t = useGenerationCopy();
  return (
    <Chip tone="assistant" icon={<Sparkles size={14} strokeWidth={2.4} />} className={className}>
      {t("generation.label")}
    </Chip>
  );
}

/** Alt text always starts with the label, so a screen reader hears it before the description. */
export function useAltText(): (prompt: string) => string {
  const t = useGenerationCopy();
  return (prompt) => t("generation.alt", { label: t("generation.label"), prompt });
}
