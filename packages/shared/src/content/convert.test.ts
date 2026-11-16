import { getSchema } from "@tiptap/core";
import { describe, expect, it } from "vitest";
import { ContentConversionError, htmlToContentDoc, rewriteFigurePlaceholders } from "./convert";
import { contentExtensions } from "./extensions";
import { CONTENT_MARK_TYPES, CONTENT_NODE_TYPES, ContentDocSchema, figureIdsOf, textOf } from "./schema";

const cell = { colspan: 1, rowspan: 1, colwidth: null, align: null };
const t = (text: string, ...marks: string[]) =>
  marks.length ? { type: "text", text, marks: marks.map((type) => ({ type })) } : { type: "text", text };
const p = (...content: unknown[]) => ({ type: "paragraph", content });

describe("content schema", () => {
  it("node and mark lists match the TipTap extension set exactly", () => {
    const schema = getSchema(contentExtensions);
    expect(Object.keys(schema.nodes).sort()).toEqual([...CONTENT_NODE_TYPES].sort());
    expect(Object.keys(schema.marks).sort()).toEqual([...CONTENT_MARK_TYPES].sort());
  });

  it("rejects unknown node and mark types and unexpected attributes", () => {
    expect(ContentDocSchema.safeParse({ type: "doc", content: [{ type: "blockquote", content: [] }] }).success).toBe(
      false,
    );
    expect(
      ContentDocSchema.safeParse({ type: "doc", content: [p({ type: "text", text: "x", marks: [{ type: "link" }] })] })
        .success,
    ).toBe(false);
    expect(
      ContentDocSchema.safeParse({ type: "doc", content: [{ type: "figure", attrs: { mediaId: "a", caption: "x" } }] })
        .success,
    ).toBe(false);
    expect(
      ContentDocSchema.safeParse({ type: "doc", content: [{ type: "figure", attrs: { mediaId: "../etc" } }] }).success,
    ).toBe(false);
  });

  it("accepts every future node with valid attrs", () => {
    const doc = {
      type: "doc",
      content: [
        { type: "video", attrs: { mediaId: "yt-abc", startS: 10, endS: 70 } },
        { type: "pdf", attrs: { mediaId: "pdf-1", page: 3 } },
        { type: "questionEmbed", attrs: { questionId: "ch01-q01" } },
        { type: "mermaid", attrs: { code: "graph TD; A-->B" } },
        { type: "math", attrs: { latex: "\\frac{a}{b}" } },
      ],
    };
    expect(ContentDocSchema.parse(doc)).toEqual(doc);
  });
});

describe("htmlToContentDoc: every tag and class in the seed", () => {
  it("paragraph with bold, italic, sub, sup and a hard break", () => {
    const r = htmlToContentDoc("<p><b>Na</b><sup>+</sup> and H<sub>2</sub>O, <i>in vivo</i><br>next</p>");
    expect(r.doc).toEqual({
      type: "doc",
      content: [
        p(
          t("Na", "bold"),
          t("+", "superscript"),
          t(" and H"),
          t("2", "subscript"),
          t("O, "),
          t("in vivo", "italic"),
          {
            type: "hardBreak",
          },
          t("next"),
        ),
      ],
    });
  });

  it("strong/em map to bold/italic", () => {
    expect(htmlToContentDoc("<p><strong>a</strong><em>b</em></p>").doc.content[0]).toEqual(
      p(t("a", "bold"), t("b", "italic")),
    );
  });

  it("h3 heading", () => {
    expect(htmlToContentDoc("<h3>Stains</h3>").doc.content[0]).toEqual({
      type: "heading",
      attrs: { level: 3 },
      content: [t("Stains")],
    });
  });

  it("bullet and ordered lists wrap item text in paragraphs", () => {
    const r = htmlToContentDoc("<ul>\n<li><b>A.</b> one</li>\n</ul><ol><li>x</li><li>y</li></ol>");
    expect(r.doc.content).toEqual([
      { type: "bulletList", content: [{ type: "listItem", content: [p(t("A.", "bold"), t(" one"))] }] },
      {
        type: "orderedList",
        attrs: { start: 1, type: null },
        content: [
          { type: "listItem", content: [p(t("x"))] },
          { type: "listItem", content: [p(t("y"))] },
        ],
      },
    ]);
  });

  it("high-yield table with thead/tbody, th, td, colspan and rowspan", () => {
    const r = htmlToContentDoc(
      '<table class="hy"><thead><tr><th>Drug</th><th>Use</th></tr></thead><tbody>' +
        '<tr><td rowspan="2">A</td><td>x</td></tr><tr><td>y</td></tr><tr><td colspan="2">both</td></tr></tbody></table>',
    );
    expect(r.doc.content[0]).toEqual({
      type: "table",
      attrs: { highYield: true },
      content: [
        {
          type: "tableRow",
          content: [
            { type: "tableHeader", attrs: cell, content: [p(t("Drug"))] },
            { type: "tableHeader", attrs: cell, content: [p(t("Use"))] },
          ],
        },
        {
          type: "tableRow",
          content: [
            { type: "tableCell", attrs: { ...cell, rowspan: 2 }, content: [p(t("A"))] },
            { type: "tableCell", attrs: cell, content: [p(t("x"))] },
          ],
        },
        { type: "tableRow", content: [{ type: "tableCell", attrs: cell, content: [p(t("y"))] }] },
        { type: "tableRow", content: [{ type: "tableCell", attrs: { ...cell, colspan: 2 }, content: [p(t("both"))] }] },
      ],
    });
  });

  it("a plain table is not high-yield", () => {
    expect(htmlToContentDoc("<table><tbody><tr><td>x</td></tr></tbody></table>").doc.content[0]).toMatchObject({
      attrs: { highYield: false },
    });
  });

  it.each(["pearl", "trap", "bridge"] as const)("callout %s", (variant) => {
    const r = htmlToContentDoc(`<div class="${variant}"><b>Pearl:</b> text<ul><li>x</li></ul></div>`);
    expect(r.doc.content[0]).toEqual({
      type: "callout",
      attrs: { variant },
      content: [
        p(t("Pearl:", "bold"), t(" text")),
        { type: "bulletList", content: [{ type: "listItem", content: [p(t("x"))] }] },
      ],
    });
  });

  it("span.hy becomes a lime key-fact highlight", () => {
    expect(htmlToContentDoc('<p>a <span class="hy">key</span></p>').doc.content[0]).toEqual(
      p(t("a "), {
        type: "text",
        text: "key",
        marks: [{ type: "highlight", attrs: { color: "lime", meaning: "key_fact" } }],
      }),
    );
  });

  it("[[figure:ID]] between blocks and inside a callout becomes figure nodes in order", () => {
    const html = '<p>a</p>\n[[figure:ch01-fig01]]\n<div class="trap"><p>b</p>[[figure:ch01-fig02]]</div>';
    const r = htmlToContentDoc(html, { knownMediaIds: new Set(["ch01-fig01", "ch01-fig02"]) });
    expect(r.figureIds).toEqual(["ch01-fig01", "ch01-fig02"]);
    expect(figureIdsOf(r.doc)).toEqual(["ch01-fig01", "ch01-fig02"]);
    expect(r.doc.content[1]).toEqual({ type: "figure", attrs: { mediaId: "ch01-fig01" } });
    expect(r.html).not.toContain("[[figure:");
    expect(r.html).toContain('<figure data-media-id="ch01-fig01"></figure>');
    // body_html round-trips to the same body
    expect(htmlToContentDoc(r.html).doc).toEqual(r.doc);
  });

  it("reading minutes: 200 words per minute, at least 1", () => {
    expect(htmlToContentDoc("<p>one</p>").readingMinutes).toBe(1);
    expect(htmlToContentDoc(`<p>${"word ".repeat(401)}</p>`).readingMinutes).toBe(3);
  });

  it("textOf joins blocks with spaces", () => {
    expect(textOf(htmlToContentDoc("<p>a<b>b</b></p><p>c</p>").doc)).toBe("ab c");
  });
});

describe("htmlToContentDoc: strictness", () => {
  const bad: [string, string][] = [
    ["<span>x</span>", "without a class"],
    ['<p style="color:red">x</p>', "unknown attribute style"],
    ['<div class="note">x</div>', 'unknown class "note"'],
    ['<p onclick="alert(1)">x</p>', "unknown attribute onclick"],
    ["<script>alert(1)</script>", "unknown element <script>"],
    ['<a href="https://x">x</a>', "unknown element <a>"],
    ["<blockquote>x</blockquote>", "unknown element <blockquote>"],
    ['<img src="x.png">', "unknown element <img>"],
    ['<table><tbody><tr><td colspan="abc">x</td></tr></tbody></table>', "invalid colspan"],
    ["[[figure:../../etc]]", "invalid figure id"],
    ["[[figure:ch01-fig01", "unterminated"],
  ];
  it.each(bad)("%s is refused", (html, why) => {
    expect(() => htmlToContentDoc(html)).toThrow(ContentConversionError);
    expect(() => htmlToContentDoc(html)).toThrow(why);
  });

  it("an unknown figure id is refused when the media set is given", () => {
    expect(() => htmlToContentDoc("[[figure:ch99-fig01]]", { knownMediaIds: new Set(["ch01-fig01"]) })).toThrow(
      "unknown figure ch99-fig01",
    );
  });

  it("rewriteFigurePlaceholders reports bad ids without emitting them", () => {
    const r = rewriteFigurePlaceholders("[[figure:A B]] [[figure:ok-1]]");
    expect(r.figureIds).toEqual(["ok-1"]);
    expect(r.problems).toHaveLength(1);
  });
});
