import { uiText } from "@naadi/shared";
import { useAuth } from "../auth/session";

/**
 * `t(key, vars)` for UI copy from app_config.ui_copy (delivered with /api/public-config, which the auth
 * layer already holds). Never empty: before the config has loaded, or for a key the config lacks, the
 * shipped default from config/app_config.yaml is used (see @naadi/shared uiText).
 */
export function useUiText(): (key: string, vars?: Readonly<Record<string, string>>) => string {
  const auth = useAuth();
  const copy = auth.status === "signed-in" || auth.status === "signed-out" ? auth.config.ui_copy : null;
  return (key, vars) => uiText(copy, key, vars);
}
