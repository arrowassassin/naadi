import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { describe, expect, it } from "vitest";
import { type Block, highlightColorOf, tiptapToBlocks, usedHighlights } from "./blocks";
import { type FontBytes, fontMeasurer } from "./fonts";
import {
  cleanText,
  type LayoutCopy,
  LETTER_GEOMETRY,
  layoutDocument,
  type Measurer,
  pageLines,
  wrapPlain,
} from "./layout";

const require = createRequire(import.meta.url);
const ttf = (p: string) => new Uint8Array(readFileSync(require.resolve(p)));
const BYTES: FontBytes = {
  sans: ttf("@expo-google-fonts/inter/400Regular/Inter_400Regular.ttf"),
  sansBold: ttf("@expo-google-fonts/inter/700Bold/Inter_700Bold.ttf"),
  sansItalic: ttf("@expo-google-fonts/inter/400Regular_Italic/Inter_400Regular_Italic.ttf"),
  sansBoldItalic: ttf("@expo-google-fonts/inter/700Bold_Italic/Inter_700Bold_Italic.ttf"),
  mono: ttf("@expo-google-fonts/jetbrains-mono/400Regular/JetBrainsMono_400Regular.ttf"),
};
const measure = fontMeasurer(BYTES);

/** A fixed-width measurer: 1 char = 0.5 em, so wrap points are predictable by hand. */
const fixed: Measurer = { width: (t, _f, size) => [...t].length * size * 0.5, covers: () => true };

const COPY: LayoutCopy = {
  title: "Renal physiology",
  subtitle: "Exported 17 Nov 2026",
  highlightWords: { lime: "Key fact", violet: "Formula", amber: "My mistake" },
  legendLabel: "Highlights:",
  footer: (n, total) => `page ${n} of ${total}`,
};

const text = (t: string, marks: { type: string; attrs?: Record<string, unknown> }[] = []) => ({
  type: "text",
  text: t,
  ...(marks.length ? { marks } : {}),
});
const para = (...content: unknown[]) => ({ type: "paragraph", content });

const DOC = {
  type: "doc",
  content: [
    { type: "heading", attrs: { level: 1 }, content: [text("Diuretics")] },
    para(
      text("Loop diuretics block the "),
      text("Na⁺-K⁺-2Cl⁻ cotransporter", [{ type: "highlight", attrs: { color: "lime" } }]),
      text(" in the thick ascending limb; "),
      text("thiazides", [{ type: "bold" }]),
      text(" act on the DCT. GFR ≥ 60 mL/min → dose as usual; 37 °C, 5 μg."),
    ),
    {
      type: "bulletList",
      content: [
        { type: "listItem", content: [para(text("Acetazolamide: PCT"))] },
        {
          type: "listItem",
          content: [
            para(text("Loops", [{ type: "italic" }])),
            { type: "bulletList", content: [{ type: "listItem", content: [para(text("Ototoxic"))] }] },
          ],
        },
      ],
    },
    {
      type: "orderedList",
      attrs: { start: 3 },
      content: [
        {
          type: "listItem",
          content: [para(text("Clearance = U × V / P", [{ type: "highlight", attrs: { color: "violet" } }]))],
        },
      ],
    },
    { type: "codeBlock", content: [text("FENa = (UNa × PCr) / (PNa × UCr)")] },
    { type: "horizontalRule" },
    {
      type: "blockquote",
      content: [para(text("Mistook spironolactone for a loop", [{ type: "highlight", attrs: { color: "amber" } }]))],
    },
  ],
};

describe("tiptapToBlocks", () => {
  it("keeps headings, lists with markers and depth, marks and highlights", () => {
    const blocks = tiptapToBlocks(DOC);
    expect(
      blocks.map((b) => [
        b.type,
        b.type === "paragraph" ? (b.marker ?? "") : "",
        b.type === "paragraph" ? (b.indent ?? 0) : 0,
      ]),
    ).toEqual([
      ["heading", "", 0],
      ["paragraph", "", 0],
      ["paragraph", "•", 1],
      ["paragraph", "•", 1],
      ["paragraph", "•", 2],
      ["paragraph", "3.", 1],
      ["code", "", 0],
      ["rule", "", 0],
      ["paragraph", "", 0],
    ]);
    const p = blocks[1] as Extract<Block, { type: "paragraph" }>;
    expect(p.runs.map((r) => [r.text.slice(0, 10), r.bold ?? false, r.highlight ?? null])).toEqual([
      ["Loop diure", false, null],
      ["Na⁺-K⁺-2Cl", false, "lime"],
      [" in the th", false, null],
      ["thiazides", true, null],
      [" act on th", false, null],
    ]);
    expect(blocks[8]).toMatchObject({ quote: true });
    expect(usedHighlights(blocks)).toEqual(["lime", "violet", "amber"]);
  });

  it("reads the highlight colour however the editor stores it", () => {
    expect(highlightColorOf({ color: "violet" })).toBe("violet");
    expect(highlightColorOf({ color: "var(--color-mark-amber)" })).toBe("amber");
    expect(highlightColorOf({ kind: "formula" })).toBe("violet");
    expect(highlightColorOf({})).toBe("lime");
    expect(highlightColorOf(null)).toBe("lime");
  });

  it("never drops text from unknown nodes and tolerates junk", () => {
    expect(tiptapToBlocks({ type: "doc", content: [{ type: "callout", content: [text("Remember")] }] })).toEqual([
      { type: "paragraph", runs: [{ text: "Remember" }] },
    ]);
    expect(tiptapToBlocks(null)).toEqual([]);
    expect(tiptapToBlocks("x")).toEqual([]);
    expect(tiptapToBlocks({ type: "doc" })).toEqual([]);
  });
});

describe("layout", () => {
  it("golden: the sample note is one page with these text runs", () => {
    const blocks = tiptapToBlocks(DOC);
    const pages = layoutDocument({ blocks, copy: COPY, legendColors: usedHighlights(blocks), measure });
    expect(pages).toHaveLength(1);
    expect(pageLines(pages[0] as (typeof pages)[number])).toEqual([
      "Renal physiology",
      "Exported 17 Nov 2026",
      "Highlights:Key factFormulaMy mistake",
      "Diuretics",
      "Loop diuretics block the Na⁺-K⁺-2Cl⁻ cotransporter in the thick ascending limb; thiazides act on",
      "the DCT. GFR ≥ 60 mL/min → dose as usual; 37 °C, 5 μg.",
      "•Acetazolamide: PCT",
      "•Loops",
      "•Ototoxic",
      "3.Clearance = U × V / P",
      "FENa = (UNa × PCr) / (PNa × UCr)",
      "Mistook spironolactone for a loop",
      "page 1 of 1",
    ]);
    const ops = pages[0]?.ops ?? [];
    // One tinted rect per highlighted span plus the three legend swatches.
    expect(
      ops.filter((o) => o.op === "rect" && o.color.startsWith("hl-")).map((o) => o.op === "rect" && o.color),
    ).toEqual(["hl-lime", "hl-violet", "hl-amber", "hl-lime", "hl-violet", "hl-amber"]);
    // Bold run drawn with the bold face; the superscript minus that JetBrains Mono lacks is not in a mono run.
    expect(ops.some((o) => o.op === "text" && o.font === "sansBold" && o.text === "thiazides")).toBe(true);
    expect(ops.some((o) => o.op === "text" && o.font === "mono" && o.text.includes("FENa"))).toBe(true);
    // Everything stays inside the margins.
    const g = LETTER_GEOMETRY;
    for (const o of ops)
      if (o.op === "text") {
        expect(o.x).toBeGreaterThanOrEqual(g.margin.left - 20);
        expect(o.x + measure.width(o.text, o.font, o.size)).toBeLessThanOrEqual(g.width - g.margin.right + 0.01);
      }
  });

  it("golden: a long note paginates and numbers every page", () => {
    const content = Array.from({ length: 120 }, (_, i) =>
      para(text(`Paragraph ${i + 1}. The loop of Henle concentrates urine by countercurrent multiplication.`)),
    );
    const pages = layoutDocument({
      blocks: tiptapToBlocks({ type: "doc", content }),
      copy: COPY,
      legendColors: [],
      measure,
    });
    expect(pages).toHaveLength(5);
    expect(pages.map((p) => pageLines(p).at(-1))).toEqual([1, 2, 3, 4, 5].map((n) => `page ${n} of 5`));
    expect(pageLines(pages[1] as (typeof pages)[number])[0]).toMatch(/^Paragraph \d+\./);
    for (const p of pages)
      for (const o of p.ops) if (o.op === "text") expect(o.y).toBeLessThanOrEqual(LETTER_GEOMETRY.height - 20);
  });

  it("keeps a heading with the line after it", () => {
    // 26 one-line paragraphs leave room for the heading but not for the heading plus one body line.
    const filler = Array.from({ length: 26 }, () => para(text("x")));
    const pages = layoutDocument({
      blocks: tiptapToBlocks({
        type: "doc",
        content: [...filler, { type: "heading", attrs: { level: 2 }, content: [text("Next")] }, para(text("body"))],
      }),
      copy: COPY,
      legendColors: [],
      measure: fixed,
    });
    expect(pages).toHaveLength(2);
    expect(pageLines(pages[1] as (typeof pages)[number]).slice(0, 2)).toEqual(["Next", "body"]);
  });

  it("wraps greedily, splits words longer than a line and keeps forced breaks", () => {
    expect(wrapPlain("aaa bbb ccc", "sans", 10, 40, fixed)).toEqual(["aaa bbb", "ccc"]);
    expect(wrapPlain("abcdefghij", "sans", 10, 20, fixed)).toEqual(["abcd", "efgh", "ij"]);
    expect(wrapPlain("one\n\ntwo", "sans", 10, 100, fixed)).toEqual(["one", "", "two"]);
    expect(wrapPlain("", "sans", 10, 100, fixed)).toEqual([""]);
  });

  it("strips control characters and lone surrogates", () => {
    expect(cleanText("a\u0000b\tc\r\nd\ud800e")).toBe("ab    c\nde");
  });

  it("scales images to the content width and moves them to a new page when they do not fit", () => {
    const pages = layoutDocument({
      blocks: [
        { type: "space", height: 500 },
        { type: "image", src: "a", alt: "", width: 1008, height: 800 },
        { type: "image", src: "b", alt: "fig", width: 0, height: 0 },
      ],
      copy: COPY,
      legendColors: [],
      measure: fixed,
    });
    expect(pages).toHaveLength(2);
    const img = pages[1]?.ops.find((o) => o.op === "image");
    expect(img).toMatchObject({ src: "a", x: 54, y: 54, w: 504, h: 400 });
    expect(pageLines(pages[1] as (typeof pages)[number])).toContain("fig");
  });
});
