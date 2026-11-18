import { describe, expect, it, vi } from "vitest";
import { installTrustedTypesPolicy, sameOriginScriptUrl } from "./trustedTypes";

describe("Trusted Types default policy", () => {
  it("accepts script URLs on our origin only", () => {
    expect(sameOriginScriptUrl("/sw.js", "https://app.example")).toBe("https://app.example/sw.js");
    expect(sameOriginScriptUrl("https://app.example/assets/a.js", "https://app.example")).toBe(
      "https://app.example/assets/a.js",
    );
    expect(() => sameOriginScriptUrl("https://www.youtube.com/iframe_api", "https://app.example")).toThrow(TypeError);
    expect(() => sameOriginScriptUrl("//evil.example/x.js", "https://app.example")).toThrow(TypeError);
  });

  it("installs one default policy with only a script-URL rule, and is a no-op without Trusted Types", () => {
    expect(installTrustedTypesPolicy({ location: { origin: "https://a" } } as unknown as Window)).toBe(false);
    const createPolicy = vi.fn();
    const win = { trustedTypes: { createPolicy }, location: { origin: "https://app.example" } } as unknown as Window;
    expect(installTrustedTypesPolicy(win)).toBe(true);
    expect(installTrustedTypesPolicy(win)).toBe(true);
    expect(createPolicy).toHaveBeenCalledTimes(1);
    const [name, rules] = createPolicy.mock.calls[0] as [string, Record<string, unknown>];
    expect(name).toBe("default");
    expect(Object.keys(rules)).toEqual(["createScriptURL"]); // no blanket createHTML / createScript
  });
});
