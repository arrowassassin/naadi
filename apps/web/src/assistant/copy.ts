import { UI_COPY_DEFAULTS, uiText } from "@naadi/shared";
import { useAuth } from "../auth/session";

/**
 * A server-referenced ui_copy key (an action's label_key, a column's label_key, a canvas title_key) worded from
 * the config the app already holds, or null when this build has no such key (the caller then shows the plain
 * label or nothing). No extra request: the public config is loaded with the session.
 */
export function useKnownCopy(): (key: string, vars?: Readonly<Record<string, string>>) => string | null {
  const auth = useAuth();
  const copy = auth.status === "signed-in" || auth.status === "signed-out" ? auth.config.ui_copy : null;
  return (key, vars) => (copy?.[key] || UI_COPY_DEFAULTS[key] ? uiText(copy, key, vars) : null);
}
