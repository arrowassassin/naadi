import { afterEach, describe, expect, it, vi } from "vitest";
import { resetTrustedPolicy, SW_PATH, serviceWorkerScriptUrl } from "./trustedTypes";

afterEach(() => {
  vi.unstubAllGlobals();
  resetTrustedPolicy();
});

describe("serviceWorkerScriptUrl", () => {
  it("is the plain path without Trusted Types", () => {
    expect(serviceWorkerScriptUrl()).toBe(SW_PATH);
  });

  it("goes through a named policy that vouches only for the worker path", () => {
    const made: string[] = [];
    const createPolicy = vi.fn((_name: string, rules: { createScriptURL: (s: string) => string }) => ({
      createScriptURL: (s: string) => {
        made.push(rules.createScriptURL(s));
        return `trusted:${s}`;
      },
    }));
    vi.stubGlobal("trustedTypes", { createPolicy });
    expect(serviceWorkerScriptUrl()).toBe("trusted:/sw.js");
    expect(serviceWorkerScriptUrl()).toBe("trusted:/sw.js");
    expect(createPolicy).toHaveBeenCalledTimes(1); // one policy, created once
    expect(createPolicy.mock.calls[0]?.[0]).toBe("naadi-service-worker");
    const rules = createPolicy.mock.calls[0]?.[1];
    expect(() => rules?.createScriptURL("/evil.js")).toThrow();
    expect(made).toEqual(["/sw.js", "/sw.js"]);
  });
});
