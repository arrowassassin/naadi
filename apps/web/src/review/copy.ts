import { useCallback } from "react";
import { useUiText } from "../copy/useUiText";

/** `t("review.title")`: app_config.ui_copy, else the shipped default. `{name}` placeholders come from `vars`. */
export function useReviewCopy(): (key: string, vars?: Record<string, string | number>) => string {
  const text = useUiText();
  return useCallback(
    (key: string, vars: Record<string, string | number> = {}) =>
      text(key, Object.fromEntries(Object.entries(vars).map(([k, v]) => [k, String(v)]))),
    [text],
  );
}

export type ReviewT = ReturnType<typeof useReviewCopy>;
