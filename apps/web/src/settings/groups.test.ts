import { describe, expect, it } from "vitest";
import { groupAvailable, SETTINGS_GROUPS, type SettingsGroup, visibleGroups } from "./groups";

const ALL = { today: true, export: true, assistant: true, offline: true };
const ids = (gs: readonly SettingsGroup[]) => gs.map((g) => g.id);

describe("settings groups registry", () => {
  it("lists the groups in the board's order", () => {
    expect(ids(visibleGroups(ALL))).toEqual([
      "account",
      "ink",
      "plan",
      "appearance",
      "offline",
      "assistant",
      "data",
      "about",
    ]);
  });

  it("hides a group whose flag is off and keeps the always-on ones", () => {
    expect(ids(visibleGroups({ today: false, export: false }))).toEqual([
      "account",
      "ink",
      "appearance",
      "offline",
      "about",
    ]);
    expect(ids(visibleGroups({ today: true, export: false }))).toContain("plan");
  });

  it("hides flagged groups while the flags are unknown", () => {
    expect(ids(visibleGroups(undefined))).toEqual(["account", "ink", "appearance", "offline", "about"]);
  });

  it("shows Offline for everyone (S18, no flag); Assistant (S14) follows its flag", () => {
    expect(ids(SETTINGS_GROUPS)).toContain("offline");
    expect(ids(visibleGroups({}))).toContain("offline");
    expect(ids(visibleGroups(ALL))).toContain("assistant");
    expect(ids(visibleGroups({ ...ALL, assistant: false }))).not.toContain("assistant");
  });

  it("never lists a group whose route does not exist in this build", () => {
    const routes = { "/settings/account": {}, "/settings/data": {} };
    expect(ids(visibleGroups(ALL, routes))).toEqual(["account", "data"]);
  });

  it("treats a flag that is not exactly true as off", () => {
    const g: SettingsGroup = { id: "x", to: "/x", labelKey: "k", flag: "unknown_flag" };
    expect(groupAvailable(g, ALL, undefined)).toBe(false);
    expect(groupAvailable(g, { unknown_flag: true }, undefined)).toBe(true);
  });

  it("has one entry per path and a label key each", () => {
    expect(new Set(SETTINGS_GROUPS.map((g) => g.to)).size).toBe(SETTINGS_GROUPS.length);
    expect(SETTINGS_GROUPS.every((g) => g.labelKey.startsWith("settings.nav_"))).toBe(true);
  });
});
