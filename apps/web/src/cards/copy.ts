import { useCallback } from "react";
import { useUiText } from "../copy/useUiText";

/**
 * `t("cards.show_answer")`: the configured string (app_config.ui_copy, editable without a redeploy), else the
 * shipped default (packages/shared/ui-copy.defaults.json), else the key. `{name}` placeholders come from `vars`.
 */
export function useCardsCopy(): (key: string, vars?: Record<string, string | number>) => string {
  const text = useUiText();
  return useCallback(
    (key: string, vars: Record<string, string | number> = {}) =>
      text(key, Object.fromEntries(Object.entries(vars).map(([k, v]) => [k, String(v)]))),
    [text],
  );
}
