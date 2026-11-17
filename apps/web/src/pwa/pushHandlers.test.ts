import { describe, expect, it, vi } from "vitest";
import {
  type ClickLike,
  type PushLike,
  parsePayload,
  registerPushHandlers,
  safeUrl,
  type WindowClientLike,
  type WorkerScope,
} from "./pushHandlers";

function fakeScope(clients: WindowClientLike[] = []) {
  const handlers: Record<string, (e: never) => void> = {};
  const shown: { title: string; options?: NotificationOptions }[] = [];
  const open = vi.fn(async () => undefined);
  const scope = {
    addEventListener: (type: string, fn: (e: never) => void) => {
      handlers[type] = fn;
    },
    registration: {
      showNotification: async (title: string, options?: NotificationOptions) => {
        shown.push({ title, options });
      },
    },
    clients: { matchAll: async () => clients, openWindow: open },
    location: { origin: "https://naadi.example" },
  } as unknown as WorkerScope;
  registerPushHandlers(scope);
  const push = async (text: string | null) => {
    const waits: Promise<unknown>[] = [];
    const e: PushLike = { data: text === null ? null : { text: () => text }, waitUntil: (p) => void waits.push(p) };
    (handlers.push as (e: PushLike) => void)(e);
    await Promise.all(waits);
  };
  const click = async (data: unknown) => {
    const waits: Promise<unknown>[] = [];
    const close = vi.fn();
    const e: ClickLike = { notification: { close, data }, waitUntil: (p) => void waits.push(p) };
    (handlers.notificationclick as (e: ClickLike) => void)(e);
    await Promise.all(waits);
    return close;
  };
  return { shown, open, push, click };
}

describe("push payloads", () => {
  it("shows the worded title and body with the tag and deep link", async () => {
    const w = fakeScope();
    await w.push(
      JSON.stringify({
        k: "push.reminder_body",
        n: { items: 3 },
        title: "Naadi",
        body: "3 items",
        url: "/plan",
        tag: "r",
      }),
    );
    expect(w.shown).toHaveLength(1);
    expect(w.shown[0]?.title).toBe("Naadi");
    expect(w.shown[0]?.options).toMatchObject({ body: "3 items", tag: "r", data: { url: "/plan" } });
  });

  it("falls back to a generic notification when the payload is missing or not JSON", async () => {
    const w = fakeScope();
    await w.push(null);
    await w.push("not json");
    await w.push("[1,2]");
    expect(w.shown.map((s) => s.title)).toEqual(["Naadi", "Naadi", "Naadi"]);
    expect(w.shown.every((s) => s.options?.body === undefined)).toBe(true);
    expect(parsePayload(null).url).toBe("/plan");
  });

  it("follows only same-origin in-app paths", () => {
    expect(safeUrl("/learn/ch05?x=1")).toBe("/learn/ch05?x=1");
    for (const bad of ["https://evil.example", "//evil.example", "/\\evil", "javascript:alert(1)", "", 7, undefined]) {
      expect(safeUrl(bad)).toBe("/plan");
    }
  });
});

describe("notification click", () => {
  it("focuses an open Naadi window and navigates it", async () => {
    const focus = vi.fn(async () => undefined);
    const navigate = vi.fn(async () => undefined);
    const w = fakeScope([
      { url: "https://other.example/", focus: vi.fn(), navigate: vi.fn() },
      { url: "https://naadi.example/today", focus, navigate },
    ]);
    const close = await w.click({ url: "/plan" });
    expect(close).toHaveBeenCalled();
    expect(focus).toHaveBeenCalled();
    expect(navigate).toHaveBeenCalledWith("/plan");
    expect(w.open).not.toHaveBeenCalled();
  });

  it("opens a window when none is open, and never an off-site address", async () => {
    const w = fakeScope([]);
    await w.click({ url: "/plan" });
    await w.click({ url: "https://evil.example/" });
    await w.click(undefined);
    expect(w.open.mock.calls.map((c) => (c as unknown[])[0])).toEqual(["/plan", "/plan", "/plan"]);
  });
});
