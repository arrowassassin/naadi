/**
 * Build-time conversion of the seed chapters (data/chapters.json) into content JSON for the importer
 * (data/generated/section_bodies.json). Node only. Written by `pnpm --filter @naadi/shared seed:convert`;
 * seed-bodies.test.ts fails in CI when the committed file is stale. The Space runtime has no Node, which is
 * why the conversion is committed rather than run at seed time; the Python importer re-checks every
 * section's source sha256 and node types against this file's header.
 */
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import path from "node:path";
import { htmlToContentDoc } from "./convert";
import { CONTENT_SCHEMA_VERSION } from "./extensions";
import { CONTENT_MARK_TYPES, CONTENT_NODE_TYPES } from "./schema";

export const DATA_DIR = path.resolve(import.meta.dirname, "../../../../data");
export const SEED_BODIES_FILE = path.join(DATA_DIR, "generated", "section_bodies.json");

interface SeedSection {
  id: string;
  heading: string;
  html: string;
  figureIds: string[];
}
interface SeedChapter {
  id: string;
  sections: SeedSection[];
}

export function buildSeedBodies(): string {
  const chapters = JSON.parse(readFileSync(path.join(DATA_DIR, "chapters.json"), "utf8")) as SeedChapter[];
  const figures = JSON.parse(readFileSync(path.join(DATA_DIR, "figures.json"), "utf8")) as { id: string }[];
  const known = new Set(figures.map((f) => f.id));
  const lines: string[] = [];
  const ids = new Set<string>();
  for (const chapter of chapters) {
    for (const s of chapter.sections) {
      if (ids.has(s.id)) throw new Error(`duplicate section id ${s.id}`);
      ids.add(s.id);
      let converted: ReturnType<typeof htmlToContentDoc>;
      try {
        converted = htmlToContentDoc(s.html, { knownMediaIds: known });
      } catch (e) {
        throw new Error(`section ${s.id}: ${(e as Error).message}`);
      }
      if (JSON.stringify(converted.figureIds) !== JSON.stringify(s.figureIds)) {
        throw new Error(
          `section ${s.id}: figureIds ${s.figureIds} do not match the placeholders ${converted.figureIds}`,
        );
      }
      const entry = {
        sourceSha256: createHash("sha256").update(s.html, "utf8").digest("hex"),
        figureIds: converted.figureIds,
        readingMinutes: converted.readingMinutes,
        bodyHtml: converted.html,
        body: converted.doc,
      };
      lines.push(`${JSON.stringify(s.id)}:${JSON.stringify(entry)}`);
    }
  }
  const header = {
    schemaVersion: CONTENT_SCHEMA_VERSION,
    generator: "pnpm --filter @naadi/shared seed:convert",
    nodeTypes: CONTENT_NODE_TYPES,
    markTypes: CONTENT_MARK_TYPES,
  };
  const head = JSON.stringify(header).slice(0, -1);
  return `${head},"sections":{\n${lines.join(",\n")}\n}}\n`;
}
