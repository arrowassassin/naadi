/**
 * Build-time conversion of the seed chapters (data/chapters.json) into content JSON for the importer.
 *
 *   pnpm --filter @naadi/shared seed:convert   # regenerate data/generated/section_bodies.json
 *
 * Without UPDATE_SEED_BODIES the test fails when the committed file is stale (drift guard in CI), so the
 * Python importer (apps/api/naadi_api/seed) always loads JSON produced by this exact extension set.
 * The Space runtime has no Node; that is why the conversion is committed rather than run at seed time.
 */
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { collectTypes } from "./schema";
import { buildSeedBodies, SEED_BODIES_FILE as OUT } from "./seed-bodies";

describe("seed section bodies", () => {
  it("data/generated/section_bodies.json matches a fresh conversion", () => {
    const fresh = buildSeedBodies();
    if (process.env.UPDATE_SEED_BODIES) {
      mkdirSync(path.dirname(OUT), { recursive: true });
      writeFileSync(OUT, fresh);
    }
    const committed = readFileSync(OUT, "utf8");
    expect(committed === fresh, "stale: run `pnpm --filter @naadi/shared seed:convert` and commit").toBe(true);
  }, 60_000);

  it("covers 259 sections and 95 figures with only seed node types", () => {
    const parsed = JSON.parse(readFileSync(OUT, "utf8")) as {
      sections: Record<string, { figureIds: string[]; body: Parameters<typeof collectTypes>[0] }>;
    };
    const entries = Object.values(parsed.sections);
    expect(entries).toHaveLength(259);
    expect(entries.flatMap((e) => e.figureIds)).toHaveLength(95);
    const nodes = new Set<string>();
    const marks = new Set<string>();
    for (const e of entries) {
      const t = collectTypes(e.body);
      for (const k of t.nodes.keys()) nodes.add(k);
      for (const k of t.marks.keys()) marks.add(k);
    }
    expect([...nodes].sort()).toEqual(
      [
        "bulletList",
        "callout",
        "doc",
        "figure",
        "hardBreak",
        "heading",
        "listItem",
        "orderedList",
        "paragraph",
        "table",
        "tableCell",
        "tableHeader",
        "tableRow",
        "text",
      ].sort(),
    );
    expect([...marks].sort()).toEqual(["bold", "italic", "subscript", "superscript"]);
  }, 60_000);
});
