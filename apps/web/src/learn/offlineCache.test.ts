import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { clearOffline, readOffline, writeOffline } from "./offlineCache";

/** A minimal in-memory Cache Storage (jsdom has none); unit test only. */
function installFakeCaches() {
  const stores = new Map<string, Map<string, string>>();
  const api = {
    open: async (name: string) => {
      let s = stores.get(name);
      if (!s) {
        s = new Map();
        stores.set(name, s);
      }
      const store = s;
      return {
        put: async (key: string, res: Response) => void store.set(key, await res.text()),
        match: async (key: string) => (store.has(key) ? new Response(store.get(key)) : undefined),
      };
    },
    delete: async (name: string) => stores.delete(name),
  };
  Object.defineProperty(globalThis, "caches", { value: api, configurable: true });
  return stores;
}

describe("offline copy", () => {
  beforeEach(() => {
    installFakeCaches();
  });
  afterEach(() => {
    Reflect.deleteProperty(globalThis, "caches");
  });

  it("stores per user and kind, and is wiped on clear", async () => {
    await writeOffline("u1", "chapter", "ch01", { title: "Biochemistry" });
    expect(await readOffline("u1", "chapter", "ch01")).toEqual({ title: "Biochemistry" });
    expect(await readOffline("u2", "chapter", "ch01")).toBeUndefined();
    expect(await readOffline("u1", "courses", "ch01")).toBeUndefined();
    await clearOffline();
    expect(await readOffline("u1", "chapter", "ch01")).toBeUndefined();
  });

  it("degrades to no copy when Cache Storage is missing", async () => {
    Reflect.deleteProperty(globalThis, "caches");
    await writeOffline("u1", "chapter", "ch01", { a: 1 });
    expect(await readOffline("u1", "chapter", "ch01")).toBeUndefined();
  });
});
