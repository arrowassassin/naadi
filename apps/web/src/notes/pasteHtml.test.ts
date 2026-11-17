import { describe, expect, it } from "vitest";
import { stripPastedHtml } from "./pasteHtml";

describe("stripPastedHtml", () => {
  it("removes style attributes, classes and event handlers but keeps the text and structure", () => {
    const out = stripPastedHtml(
      '<p style="color:red" class="x" onclick="alert(1)">Loop <b style="font-weight:700">diuretics</b></p>' +
        '<ul><li style="margin:0">one</li></ul>',
    );
    expect(out).not.toMatch(/style=|class=|onclick/);
    expect(out).toContain("<p>Loop <b>diuretics</b></p>");
    expect(out).toContain("<li>one</li>");
  });

  it("drops style, link, script and meta elements entirely", () => {
    const out = stripPastedHtml(
      '<meta charset="utf-8"><style>p{color:red}</style><link rel="stylesheet" href="x.css"><script>alert(1)</script><p>text</p>',
    );
    expect(out).toBe("<p>text</p>");
  });

  it("keeps the data attributes the note schema reads (highlight colour, image path)", () => {
    const out = stripPastedHtml('<mark data-color="amber" class="note-mark">k</mark><img data-path="u/notes/a.png">');
    expect(out).toContain('data-color="amber"');
    expect(out).toContain('data-path="u/notes/a.png"');
    expect(out).not.toContain("class=");
  });
});

describe("stripPastedHtml before parsing", () => {
  it("never lets a style attribute or style element reach the parser", () => {
    const html =
      "<style>p{x:y}</style><p style='a:b' class=Mso>one</p><p style=color:red>two</p>" +
      '<a href="u" title="keep">link</a>';
    const out = stripPastedHtml(html);
    expect(out).not.toMatch(/<style|style=/);
    expect(out).toContain("<p>one</p>");
    expect(out).toContain("<p>two</p>");
    expect(out).toContain('title="keep"');
  });
});
