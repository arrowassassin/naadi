/**
 * The admin console's and review queue's shipped ui_copy defaults (packages/shared/ui-copy.admin.defaults.json) load
 * only with the admin and review page chunks (segment 22 budget). A key from that file rendered anywhere else would
 * fall back to the bare key while the config is unavailable, so this keeps the split honest: such a key belongs in
 * EAGER_ADMIN_KEYS of apps/api/tests/config_api/test_ui_copy_defaults.py instead.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const SRC = path.resolve(import.meta.dirname, "..");
const SHARED = path.resolve(SRC, "../../../packages/shared");
const adminKeys = new Set(
  Object.keys(
    (JSON.parse(readFileSync(path.join(SHARED, "ui-copy.admin.defaults.json"), "utf8")) as { values: object }).values,
  ),
);

/** Modules that ship inside a chunk that imports "@naadi/shared/ui-copy-admin" first. */
const ADMIN_CHUNK_DIRS = ["admin/", "review/"];
/**
 * Route files inside those directories render in the first load: treated like the rest of the app. (review/api.ts
 * is also imported by the route for its filters, but the keys it names are shown only by the review pages.)
 */
const EAGER_IN_ADMIN_DIRS = new Set(["review/route.tsx", "admin/routes.tsx"]);

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = path.join(dir, name);
    if (statSync(full).isDirectory()) return sources(full);
    return /\.(ts|tsx)$/.test(name) && !/\.test\.tsx?$/.test(name) ? [full] : [];
  });
}

describe("admin ui_copy defaults load with the admin chunks only", () => {
  it("no module outside them names an admin-only key", () => {
    const offenders: string[] = [];
    for (const file of sources(SRC)) {
      const rel = path.relative(SRC, file).split(path.sep).join("/");
      const inAdminChunk = ADMIN_CHUNK_DIRS.some((d) => rel.startsWith(d)) && !EAGER_IN_ADMIN_DIRS.has(rel);
      if (inAdminChunk) continue;
      for (const m of readFileSync(file, "utf8").matchAll(/["'`]((?:admin|review)\.[a-z0-9_.]+)["'`]/g)) {
        if (adminKeys.has(m[1] as string)) offenders.push(`${rel}: ${m[1]}`);
      }
    }
    expect(offenders).toEqual([]);
  });

  it("the admin and review page chunks register the admin defaults", () => {
    for (const entry of ["admin/pages/index.ts", "review/ReviewHub.tsx", "review/ReviewItemPage.tsx"]) {
      expect(readFileSync(path.join(SRC, entry), "utf8"), entry).toContain('import "@naadi/shared/ui-copy-admin";');
    }
  });
});
