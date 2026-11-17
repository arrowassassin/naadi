/**
 * The Settings groups, in display order: ONE list drives the nav. A group shows only when its feature flag is on
 * (flags come from /api/me) and only when its route exists in this build, so Settings never links to a missing
 * page. A segment that owns a group adds ONE entry here and ONE route line in router.tsx.
 */
export interface SettingsGroup {
  id: string;
  /** In-app path of the group's page. */
  to: string;
  /** ui_copy key of the nav label. */
  labelKey: string;
  /** Feature flag that must be on; absent = always shown. */
  flag?: string;
}

export const SETTINGS_GROUPS = [
  { id: "account", to: "/settings/account", labelKey: "settings.nav_account" },
  { id: "ink", to: "/settings/ink", labelKey: "settings.nav_ink" },
  { id: "plan", to: "/settings/plan", labelKey: "settings.nav_plan", flag: "today" },
  { id: "appearance", to: "/settings/appearance", labelKey: "settings.nav_appearance" },
  // S18 registers Offline here once apps/web/src/offline/OfflineSettingsPage.tsx exists (and adds its route line):
  // { id: "offline", to: "/settings/offline", labelKey: "settings.nav_offline", flag: "offline" },
  // S14 registers Assistant here once its memory UI exists:
  // { id: "assistant", to: "/settings/assistant", labelKey: "settings.nav_assistant", flag: "assistant" },
  { id: "data", to: "/settings/data", labelKey: "settings.nav_data", flag: "export" },
  { id: "about", to: "/settings/about", labelKey: "settings.nav_about" },
] as const satisfies readonly SettingsGroup[];

export type SettingsGroupId = (typeof SETTINGS_GROUPS)[number]["id"];

export function groupAvailable(
  group: SettingsGroup,
  features: Readonly<Record<string, boolean>> | undefined,
  routes: Readonly<Record<string, unknown>> | undefined,
): boolean {
  if (routes && !(group.to in routes)) return false;
  if (group.flag === undefined) return true;
  // An unknown flag hides the group (never show a feature the server did not switch on).
  return features?.[group.flag] === true;
}

export function visibleGroups(
  features: Readonly<Record<string, boolean>> | undefined,
  routes?: Readonly<Record<string, unknown>>,
  groups: readonly SettingsGroup[] = SETTINGS_GROUPS,
): SettingsGroup[] {
  return groups.filter((g) => groupAvailable(g, features, routes));
}
