import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

/**
 * User-facing wording comes from app_config.ui_copy (editable without a redeploy), never from JSX. This scans
 * the reading surfaces (S06) and the sign-in, Today, Account and shell screens (S03) for literal text between
 * tags and for literal user-facing attributes. Segments add their roots to ROOTS.
 */
const ROOTS = [
  "admin",
  "agent",
  "anki",
  "assistant",
  "canvas",
  "cards",
  "content",
  "exam",
  "forms",
  "generation",
  "highlights",
  "ink",
  "learn",
  "library",
  "canvas",
  "notebooks",
  "notes",
  "pathway",
  "pdf",
  "planner",
  "review",
  "offline",
  "routes",
  "search",
  "settings",
  "shell",
  "video",
  "today",
];
const SRC = path.resolve(__dirname, "..");

// Text that is not copy: punctuation and separators that are layout, not wording.
const ALLOWED_TEXT = new Set(["/", "·", ",", ".", ":", "…", "×", "–", "—", "Naadi"]); // "Naadi": the brand
const COPY_ATTRS = ["aria-label", "title", "placeholder", "alt", "label", "retryLabel", "srPrefix", "message"];

function files(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) return files(p);
    return e.name.endsWith(".tsx") && !e.name.includes(".test.") ? [p] : [];
  });
}

function hardcodedCopy(source: string): string[] {
  // Drop comments ({/* */}, /* */ and // lines) so prose in them is not mistaken for copy.
  const code = source
    .replace(/\{\/\*[\s\S]*?\*\/\}/g, "")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^\s*\/\/.*$/gm, "");
  const found: string[] = [];
  // JSX text: between a tag's closing ">" and the next "<" or "{".
  for (const m of code.matchAll(/>([^<>{}=;()]*[A-Za-z][^<>{}=;()]*)</g)) {
    const text = (m[1] ?? "").trim();
    if (text && !ALLOWED_TEXT.has(text) && /[A-Za-z]{2,}/.test(text) && !/^[\w.]+\s*(&&|\|\||\?)/.test(text)) {
      found.push(text);
    }
  }
  for (const attr of COPY_ATTRS) {
    for (const m of code.matchAll(new RegExp(`\\s${attr}="([^"]*[A-Za-z]{2,}[^"]*)"`, "g")))
      found.push(`${attr}="${m[1]}"`);
  }
  return found;
}

describe("no hardcoded copy in user-facing screens", () => {
  it("detects literal text and attributes", () => {
    expect(hardcodedCopy('<p className="x">Hello there</p>')).toEqual(["Hello there"]);
    expect(hardcodedCopy('<button aria-label="Close">{t("x")}</button>')).toEqual(['aria-label="Close"']);
    expect(hardcodedCopy("<span>{t('a')}</span>{/* a comment with words */}")).toEqual([]);
    expect(hardcodedCopy("<span>/</span><span>·</span>")).toEqual([]);
  });

  for (const root of ROOTS) {
    it(`src/${root} renders only ui_copy text`, () => {
      const offenders = files(path.join(SRC, root)).flatMap((f) =>
        hardcodedCopy(readFileSync(f, "utf8")).map((s) => `${path.relative(SRC, f)}: ${s}`),
      );
      expect(offenders).toEqual([]);
    });
  }
});
