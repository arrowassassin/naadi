import { afterEach, describe, expect, it, vi } from "vitest";
import { REQUIRED_UI_COPY_KEYS, resetUiCopyWarnings, UI_COPY_DEFAULTS, uiText } from "./uiCopy";

afterEach(() => {
  resetUiCopyWarnings();
  vi.restoreAllMocks();
});

describe("uiText", () => {
  it("uses the configured text and fills placeholders", () => {
    expect(uiText({ "agent.quota_reset": "Back at {time}" }, "agent.quota_reset", { time: "09:00" })).toBe(
      "Back at 09:00",
    );
  });

  it("falls back to the shipped default, logging once per key for a loaded config", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    expect(uiText({}, "signin.submit")).toBe(UI_COPY_DEFAULTS["signin.submit"]);
    expect(uiText({ "signin.submit": "  " }, "signin.submit")).toBe(UI_COPY_DEFAULTS["signin.submit"]);
    expect(warn).toHaveBeenCalledTimes(1);
  });

  it("does not log while there is no config yet", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    expect(uiText(null, "signin.heading")).toBe(UI_COPY_DEFAULTS["signin.heading"]);
    expect(warn).not.toHaveBeenCalled();
  });

  it("is never empty, even for an unknown key", () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    expect(uiText({}, "nowhere.key")).toBe("nowhere.key");
  });

  it("keeps the admin strings out until the admin chunk registers them, then ships every required key", async () => {
    expect(UI_COPY_DEFAULTS["admin.chapters.title"]).toBeUndefined();
    expect(UI_COPY_DEFAULTS["admin.account_link"]?.trim()).toBeTruthy(); // shown on the Account page
    await import("./uiCopyAdmin");
    expect(REQUIRED_UI_COPY_KEYS.length).toBeGreaterThan(30);
    for (const key of REQUIRED_UI_COPY_KEYS) expect(UI_COPY_DEFAULTS[key]?.trim(), key).toBeTruthy();
  });
});
