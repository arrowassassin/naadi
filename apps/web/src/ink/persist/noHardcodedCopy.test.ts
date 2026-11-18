import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

/**
 * Every user-facing string of ink persistence and Settings → Pencil & ink comes from ui_copy (REVIEW_CHECKLIST):
 * no text between JSX tags and no literal aria-label / title / placeholder in these files.
 */
const ROOTS = [import.meta.dirname, path.resolve(import.meta.dirname, "../../routes/SettingsNav.tsx")];

function files(): string[] {
  const out: string[] = [];
  for (const root of ROOTS) {
    if (root.endsWith(".tsx")) out.push(root);
    else
      for (const f of readdirSync(root))
        if (f.endsWith(".tsx") && !f.endsWith(".test.tsx")) out.push(path.join(root, f));
  }
  return out;
}

describe("no hard-coded copy (ink persistence)", () => {
  it.each(files().map((f) => [path.basename(f), f]))("%s", (_name, file) => {
    const src = readFileSync(file, "utf8")
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/\/\/.*$/gm, "");
    const text = [...src.matchAll(/>\s*([A-Za-z][^<>{}]*[A-Za-z.!?…])\s*</g)].map((m) => m[1]);
    const attrs = [...src.matchAll(/\b(aria-label|title|placeholder|alt)="([^"]+)"/g)].map((m) => m[0]);
    expect([...text, ...attrs]).toEqual([]);
  });
});
