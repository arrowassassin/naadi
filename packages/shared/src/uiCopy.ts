/**
 * UI copy from `app_config.ui_copy` (served with GET /api/public-config) with a non-empty last resort.
 *
 * The server refuses an admin edit that drops any key the app shows (REQUIRED_UI_COPY_KEYS in
 * apps/api/naadi_api/config_api/schemas.py), so the fallback is only reached when the config could not be
 * loaded at all or the server is older than the bundle. The fallback text is ui-copy.defaults.json, generated
 * from config/app_config.yaml (the values the seed writes), so the bundle carries no copy of its own.
 */
import defaults from "../ui-copy.defaults.json";

export type UiCopy = Readonly<Record<string, string>>;

export const UI_COPY_DEFAULTS: UiCopy = defaults.values;
export const REQUIRED_UI_COPY_KEYS: readonly string[] = defaults.required;

const warned = new Set<string>();

function fill(template: string, vars: Readonly<Record<string, string>>): string {
  return template.replace(/\{(\w+)\}/g, (m, name: string) => vars[name] ?? m);
}

/**
 * The text for `key`: the configured string, else the shipped default, else the key itself, so a label or
 * accessible name is never empty. A key missing from a loaded config is logged once. `{name}` placeholders are filled from `vars`.
 */
export function uiText(
  copy: UiCopy | null | undefined,
  key: string,
  vars: Readonly<Record<string, string>> = {},
): string {
  const configured = copy?.[key];
  if (configured?.trim()) return fill(configured, vars);
  // Missing from a loaded config is worth a log line; no config yet (still loading, offline) is not.
  if (copy && !warned.has(key)) {
    warned.add(key);
    console.warn(`ui_copy: "${key}" is not configured; using the shipped default`);
  }
  return fill(UI_COPY_DEFAULTS[key] || key, vars);
}

/** Test hook: forget which keys were already reported. */
export function resetUiCopyWarnings(): void {
  warned.clear();
}
