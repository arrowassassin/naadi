import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { REQUIRED_UI_COPY_KEYS, UI_COPY_DEFAULTS } from "@naadi/shared";
import { describe, expect, it } from "vitest";

/** Every admin.* key the console names exists in the shipped ui_copy and in REQUIRED_UI_COPY_KEYS. */
function files(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) return files(p);
    return /\.(ts|tsx)$/.test(e.name) && !e.name.includes(".test.") ? [p] : [];
  });
}

describe("admin copy", () => {
  const root = path.resolve(__dirname);
  const used = new Set<string>();
  for (const f of [...files(root), path.resolve(root, "../routes/AccountPage.tsx")]) {
    const code = readFileSync(f, "utf8")
      .replace(/^\s*(\*|\/\/).*$/gm, "")
      .replace(/\s\/\/ .*$/gm, "");
    for (const m of code.matchAll(/["'`](admin\.[a-z0-9_.]+)["'`]/g)) if (m[1]) used.add(m[1]);
  }

  it("finds the console's keys", () => {
    expect(used.size).toBeGreaterThan(400);
  });

  it("every key has shipped text and is required (an admin edit cannot drop it)", () => {
    const required = new Set(REQUIRED_UI_COPY_KEYS);
    expect([...used].filter((k) => !UI_COPY_DEFAULTS[k]?.trim())).toEqual([]);
    expect([...used].filter((k) => !required.has(k))).toEqual([]);
  });
});
