import { useUiCopy } from "../agent/uiCopy";

/**
 * `t("practice.start")`: the configured string (app_config.ui_copy, editable without a redeploy), else the
 * shipped default (packages/shared/ui-copy.defaults.json, generated from config/app_config.yaml), else the key.
 * `{name}` placeholders are filled from `vars`.
 */
export function useCopy(): (key: string, vars?: Record<string, string | number>) => string {
  const copy = useUiCopy();
  return (key, vars = {}) => {
    const text = copy(key, Object.fromEntries(Object.entries(vars).map(([k, v]) => [k, String(v)])));
    return text ?? key;
  };
}
