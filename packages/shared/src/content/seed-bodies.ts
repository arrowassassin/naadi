/**
 * Build-time conversion of the seed chapters (data/chapters.json) into content JSON for the importer
 * (data/generated/section_bodies.json). Node only. Written by `pnpm --filter @naadi/shared seed:convert`;
 * seed-bodies.test.ts fails in CI when the committed file is stale. The Space runtime has no Node, which is
 * why the conversion is committed rather than run at seed time. The Python importer
 * (apps/api/naadi_api/seed/data.py) refuses a section whose source sha256 differs, a schemaVersion it does not
 * read, and any node or mark type outside its pinned list for that version (tests compare that list with the
 * header written here).
 */
import { createHash } from "node:crypto";
import { existsSync, readdirSync, readFileSync } from "node:fs";
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

export const CONTENT_DIR = path.join(DATA_DIR, "content");
export const PACK_BODIES = "section_bodies.json";
/** The importer tests' sample pack (apps/api/tests/fixtures/content), converted the same way. */
export const FIXTURE_CONTENT_DIR = path.resolve(DATA_DIR, "../apps/api/tests/fixtures/content");

/** Every content pack directory (<root>/<pack>/ with a pack.json), sorted. */
export function packDirs(root: string = CONTENT_DIR): string[] {
  if (!existsSync(root)) return [];
  return readdirSync(root, { withFileTypes: true })
    .filter((d) => d.isDirectory() && existsSync(path.join(root, d.name, "pack.json")))
    .map((d) => path.join(root, d.name))
    .sort();
}

function readList<T>(file: string): T[] {
  return existsSync(file) ? (JSON.parse(readFileSync(file, "utf8")) as T[]) : [];
}

export function buildSeedBodies(): string {
  const chapters = JSON.parse(readFileSync(path.join(DATA_DIR, "chapters.json"), "utf8")) as SeedChapter[];
  const figures = JSON.parse(readFileSync(path.join(DATA_DIR, "figures.json"), "utf8")) as { id: string }[];
  return convertChapters(chapters, new Set(figures.map((f) => f.id)));
}

/** A pack's section_bodies.json: its chapters, with its own figures and the legacy figures as known media. */
export function buildPackBodies(packDir: string): string {
  const chapters = readList<SeedChapter>(path.join(packDir, "chapters.json"));
  const own = readList<{ id: string }>(path.join(packDir, "figures.json"));
  const legacy = JSON.parse(readFileSync(path.join(DATA_DIR, "figures.json"), "utf8")) as { id: string }[];
  return convertChapters(chapters, new Set([...legacy, ...own].map((f) => f.id)));
}

function convertChapters(chapters: SeedChapter[], known: Set<string>): string {
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
  return lines.length ? `${head},"sections":{\n${lines.join(",\n")}\n}}\n` : `${head},"sections":{}}\n`;
}
