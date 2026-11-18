import { useUiText } from "../copy/useUiText";

/**
 * Search screen wording: `app_config.ui_copy` `search.*` keys (config/app_config.yaml), all required, so
 * `useUiText` always has a value (the shipped default from packages/shared/ui-copy.defaults.json before the
 * config loads). Dynamic keys are built from the scope, group and result type names.
 */
export type SearchCopyKey = `search.${string}`;
export type SearchCopy = (key: SearchCopyKey, vars?: Record<string, string>) => string;

export function useSearchCopy(): SearchCopy {
  const t = useUiText();
  return (key, vars) => t(key, vars);
}
