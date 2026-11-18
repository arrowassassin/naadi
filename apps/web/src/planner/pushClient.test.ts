import { afterEach, describe, expect, it, vi } from "vitest";
import {
  currentEndpoint,
  keyBytes,
  permissionState,
  pushSupport,
  subscribeThisDevice,
  unsubscribeThisDevice,
  webcalUrl,
} from "./pushClient";

const sub = (endpoint = "https://push.example/x") => ({
  endpoint,
  toJSON: () => ({ endpoint, keys: { p256dh: "P", auth: "A" } }),
  unsubscribe: vi.fn(async () => true),
});

function install(opts: {
  standalone?: boolean;
  push?: boolean;
  existing?: ReturnType<typeof sub> | null;
  permission?: string;
}) {
  const subscribe = vi.fn(async () => sub());
  const pushManager = { getSubscription: vi.fn(async () => opts.existing ?? null), subscribe };
  const reg = { pushManager };
  vi.stubGlobal("Notification", {
    permission: opts.permission ?? "default",
    requestPermission: vi.fn(async () => opts.permission ?? "granted"),
  });
  Object.defineProperty(navigator, "serviceWorker", {
    configurable: true,
    value: { ready: Promise.resolve(reg), getRegistration: async () => reg },
  });
  if (opts.push !== false) vi.stubGlobal("PushManager", class {});
  else Reflect.deleteProperty(window, "PushManager");
  window.matchMedia = ((q: string) => ({ matches: q.includes("standalone") && !!opts.standalone })) as never;
  return { subscribe, pushManager };
}

afterEach(() => {
  vi.unstubAllGlobals();
  Reflect.deleteProperty(window, "PushManager");
});

describe("push support", () => {
  it("is unsupported without PushManager, not standalone in a tab, ready when installed", () => {
    install({ push: false });
    expect(pushSupport()).toBe("unsupported");
    install({ standalone: false });
    expect(pushSupport()).toBe("not_standalone");
    install({ standalone: true });
    expect(pushSupport()).toBe("ready");
  });
  it("treats iOS navigator.standalone as installed", () => {
    install({ standalone: false });
    Object.defineProperty(navigator, "standalone", { configurable: true, value: true });
    expect(pushSupport()).toBe("ready");
    Reflect.deleteProperty(navigator, "standalone");
  });
});

describe("subscribing", () => {
  it("asks permission, subscribes with the server key and returns the browser's JSON", async () => {
    const { subscribe } = install({ standalone: true });
    const r = await subscribeThisDevice("AQID");
    expect(r).toMatchObject({
      ok: true,
      subscription: { endpoint: "https://push.example/x", keys: { p256dh: "P", auth: "A" } },
    });
    const arg = (subscribe.mock.calls[0] as unknown[])[0] as {
      userVisibleOnly: boolean;
      applicationServerKey: Uint8Array;
    };
    expect(arg.userVisibleOnly).toBe(true);
    expect([...arg.applicationServerKey]).toEqual([1, 2, 3]);
  });
  it("reuses an existing subscription and reports a denied permission without subscribing", async () => {
    const existing = sub("https://push.example/old");
    const a = install({ existing });
    expect(await subscribeThisDevice("AQID")).toMatchObject({
      ok: true,
      subscription: { endpoint: "https://push.example/old" },
    });
    expect(a.subscribe).not.toHaveBeenCalled();
    const b = install({ permission: "denied" });
    expect(await subscribeThisDevice("AQID")).toEqual({ ok: false, reason: "denied" });
    expect(b.subscribe).not.toHaveBeenCalled();
    expect(permissionState()).toBe("denied");
  });
  it("reports a failure when the browser refuses", async () => {
    const a = install({});
    a.subscribe.mockRejectedValueOnce(new Error("blocked"));
    expect(await subscribeThisDevice("AQID")).toEqual({ ok: false, reason: "failed" });
  });
});

describe("this device", () => {
  it("knows its endpoint and unsubscribes", async () => {
    const existing = sub();
    install({ existing });
    expect(await currentEndpoint()).toBe("https://push.example/x");
    expect(await unsubscribeThisDevice()).toBe(true);
    expect(existing.unsubscribe).toHaveBeenCalled();
    install({});
    expect(await currentEndpoint()).toBeNull();
  });
});

describe("helpers", () => {
  it("decodes base64url", () => expect([...keyBytes("-_8")]).toEqual([251, 255]));
  it("turns https links into webcal", () => {
    expect(webcalUrl("https://n.example/api/plan/ics/abc")).toBe("webcal://n.example/api/plan/ics/abc");
    expect(webcalUrl("http://localhost:7860/x")).toBe("webcal://localhost:7860/x");
  });
});
