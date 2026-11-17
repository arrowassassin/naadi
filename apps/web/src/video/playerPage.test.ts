import { readFileSync } from "node:fs";
import path from "node:path";
import { beforeAll, describe, expect, it } from "vitest";
import { parseToFrame as appParseToFrame, NS } from "./bridge";

/**
 * deploy/player-space/player.js runs on its own origin and is uploaded as is. Here it is loaded in jsdom without
 * auto-running, and its origin checks and message validation are tested, and compared with the app's bridge.ts so the
 * two cannot drift apart.
 */
interface Core {
  allowedParent(origin: unknown): boolean;
  trustedParent(search: string, ancestors: string[] | null, referrer: string): string | null;
  parseToFrame(data: unknown): unknown;
  originOf(url: string): string | null;
}
let core: Core;

beforeAll(() => {
  const file = path.resolve(import.meta.dirname, "../../../../deploy/player-space/player.js");
  (globalThis as { __NAADI_PLAYER_NO_AUTORUN__?: boolean }).__NAADI_PLAYER_NO_AUTORUN__ = true;
  new Function(readFileSync(file, "utf8"))();
  core = (globalThis as { NaadiPlayerCore?: Core }).NaadiPlayerCore as Core;
});

const NAADI = "https://arrowassassin-naadi.hf.space";

describe("player page: who may embed it", () => {
  it("allows the Naadi Space and loopback hosts, nothing else", () => {
    expect(core.allowedParent(NAADI)).toBe(true);
    expect(core.allowedParent("http://localhost:5173")).toBe(true);
    expect(core.allowedParent("http://127.0.0.1:7860")).toBe(true);
    for (const bad of [
      "https://evil.example",
      `${NAADI}.evil.example`,
      "https://arrowassassin-naadi.hf.space.evil.example",
      "https://evil.example/arrowassassin-naadi.hf.space",
      `${NAADI}/`,
      `${NAADI}/x`,
      "http://arrowassassin-naadi.hf.space",
      "javascript:alert(1)",
      "null",
      "",
      null,
      42,
    ]) {
      expect(core.allowedParent(bad), String(bad)).toBe(false);
    }
  });

  it("trusts ?parent= only when it is allowed AND is the page that really embeds it", () => {
    const q = `?parent=${encodeURIComponent(NAADI)}`;
    expect(core.trustedParent(q, [NAADI], "")).toBe(NAADI); // ancestorOrigins (Chromium, WebKit)
    expect(core.trustedParent(q, null, `${NAADI}/learn/ch01`)).toBe(NAADI); // referrer (browsers without it)
    expect(core.trustedParent(q, ["https://evil.example"], "")).toBeNull(); // embedded by someone else
    expect(core.trustedParent(q, null, "https://evil.example/")).toBeNull();
    expect(core.trustedParent(q, null, "")).toBeNull(); // no way to tell: refuse
    expect(core.trustedParent("", [NAADI], "")).toBeNull();
    expect(core.trustedParent("?parent=https%3A%2F%2Fevil.example", ["https://evil.example"], "")).toBeNull();
  });
});

describe("player page: messages", () => {
  const cases: unknown[] = [
    { ns: NS, type: "init", videoId: "abc_DEF-123", start: null, parentOrigin: NAADI },
    { ns: NS, type: "init", videoId: "abc_DEF-123", start: 30, parentOrigin: NAADI },
    { ns: NS, type: "init", videoId: "../evil", start: null, parentOrigin: NAADI },
    { ns: NS, type: "init", videoId: "abc_DEF-123", start: -1, parentOrigin: NAADI },
    { ns: NS, type: "init", videoId: "abc_DEF-123", start: null, parentOrigin: "x".repeat(300) },
    { ns: NS, type: "cmd", cmd: "play" },
    { ns: NS, type: "cmd", cmd: "pause" },
    { ns: NS, type: "cmd", cmd: "seek", value: 12 },
    { ns: NS, type: "cmd", cmd: "seek", value: -1 },
    { ns: NS, type: "cmd", cmd: "seek", value: Number.NaN },
    { ns: NS, type: "cmd", cmd: "rate", value: 1.5 },
    { ns: NS, type: "cmd", cmd: "rate", value: 99 },
    { ns: NS, type: "cmd", cmd: "eval", value: 1 },
    { ns: "other", type: "cmd", cmd: "play" },
    { type: "cmd", cmd: "play" },
    "text",
    null,
    [],
    42,
  ];
  it("validates exactly like the app's bridge, so the two cannot drift", () => {
    for (const c of cases) {
      expect(core.parseToFrame(c), JSON.stringify(c)).toEqual(appParseToFrame(c));
    }
  });
});
