import { useCallback } from "react";
import { useUiText } from "../copy/useUiText";

/** `t("generation.label")`: app_config.ui_copy, else the shipped default. `{name}` placeholders come from `vars`. */
export function useGenerationCopy(): (key: string, vars?: Record<string, string | number>) => string {
  const text = useUiText();
  return useCallback(
    (key: string, vars: Record<string, string | number> = {}) =>
      text(key, Object.fromEntries(Object.entries(vars).map(([k, v]) => [k, String(v)]))),
    [text],
  );
}
