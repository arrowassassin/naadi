import { contentExtensions } from "@naadi/shared/content";
import { Node } from "@tiptap/pm/model";
import { getSchema } from "@tiptap/react";
import { describe, expect, it } from "vitest";
import { htmlTextRuns } from "../content/SafeHtml";
import {
  fingerprint,
  flatten,
  makeAnchor,
  type PaintRange,
  relocate,
  sectionRuns,
  sectionTextPositions,
  splitRun,
} from "./anchor";

const p = (...content: unknown[]) => ({ type: "paragraph", content });
const txt = (text: string, marks?: unknown[]) => ({ type: "text", text, ...(marks ? { marks } : {}) });
const doc = (...content: unknown[]) => ({ type: "doc", content });

const body = doc(
  { type: "heading", attrs: { level: 2 }, content: [txt("Renal tubular acidosis")] },
  p(txt("Every RTA is a "), txt("normal anion gap", [{ type: "bold" }]), txt(" metabolic acidosis.")),
  { type: "figure", attrs: { mediaId: "m1" } },
  {
    type: "bulletList",
    content: [
      { type: "listItem", content: [p(txt("Type 1 is distal."))] },
      {
        type: "listItem",
        content: [p(txt("Type 4 is the one with hyperkalemia."), { type: "hardBreak" }, txt("Low aldosterone."))],
      },
    ],
  },
);

describe("sectionRuns", () => {
  it("gives ProseMirror positions (checked against the real content schema)", () => {
    const schema = getSchema(contentExtensions);
    const node = Node.fromJSON(schema, body);
    for (const run of sectionRuns(body)) {
      expect(node.textBetween(run.pos, run.pos + run.text.length)).toBe(run.text);
    }
    expect(sectionRuns(body)).toHaveLength(7);
  });

  it("positions every text node object by identity", () => {
    const map = sectionTextPositions(body);
    const runs = sectionRuns(body);
    expect([...map.values()]).toEqual(runs.map((r) => r.pos));
  });

  it("ignores anything that is not a doc", () => {
    expect(sectionRuns(null)).toEqual([]);
    expect(sectionRuns({ type: "paragraph" })).toEqual([]);
  });
});

function anchorOf(runs: ReturnType<typeof sectionRuns>, quote: string) {
  const flat = flatten(runs);
  const i = flat.text.indexOf(quote);
  expect(i).toBeGreaterThanOrEqual(0);
  const a = makeAnchor(runs, flat.posOf(i), flat.posOf(i + quote.length - 1) + 1);
  if (!a) throw new Error("no anchor");
  return a;
}

describe("makeAnchor", () => {
  it("records the quote, context and revision", () => {
    const runs = sectionRuns(body);
    const a = anchorOf(runs, "normal anion gap metabolic");
    expect(a.quote).toBe("normal anion gap metabolic");
    expect(a.prefix.endsWith("Every RTA is a ")).toBe(true);
    expect(a.suffix.startsWith(" acidosis.")).toBe(true);
    expect(a.revision).toBe(fingerprint(flatten(runs).text));
  });

  it("trims whitespace and block joiners at the ends", () => {
    const runs = sectionRuns(body);
    const flat = flatten(runs);
    const i = flat.text.indexOf("acidosis.");
    const a = makeAnchor(runs, flat.posOf(i), flat.posOf(i + "acidosis.\n".length) + 0);
    expect(a?.quote).toBe("acidosis.");
  });

  it("refuses an empty or whitespace-only range", () => {
    const runs = sectionRuns(body);
    expect(makeAnchor(runs, 5, 5)).toBeNull();
    const flat = flatten(runs);
    const sp = flat.text.indexOf(" ");
    expect(makeAnchor(runs, flat.posOf(sp), flat.posOf(sp) + 1)).toBeNull();
  });
});

describe("relocate", () => {
  it("is exact when nothing changed", () => {
    const runs = sectionRuns(body);
    const a = anchorOf(runs, "hyperkalemia");
    expect(relocate(a, runs)).toEqual({ status: "exact", from: a.from, to: a.to });
  });

  it("follows the quote when text before it is inserted (drift)", () => {
    const runs = sectionRuns(body);
    const a = anchorOf(runs, "hyperkalemia");
    const edited = doc(p(txt("A new opening paragraph was added by the editor.")), ...body.content);
    const newRuns = sectionRuns(edited);
    const r = relocate(a, newRuns);
    expect(r.status).toBe("moved");
    const flat = flatten(newRuns);
    expect(flat.text.slice(flat.indexOf(r.from as number), flat.indexOf(r.to as number))).toBe("hyperkalemia");
  });

  it("picks the occurrence whose context still matches when the quote repeats", () => {
    const d = doc(
      p(txt("Type 1: low potassium.")),
      p(txt("Type 2: low potassium.")),
      p(txt("Type 4: high potassium.")),
    );
    const runs = sectionRuns(d);
    const flat = flatten(runs);
    const i = flat.text.indexOf("low potassium", flat.text.indexOf("Type 2"));
    const a = makeAnchor(runs, flat.posOf(i), flat.posOf(i + 12) + 1);
    if (!a) throw new Error("no anchor");
    const edited = doc(p(txt("Intro.")), ...d.content);
    const r = relocate(a, sectionRuns(edited));
    const f2 = flatten(sectionRuns(edited));
    const at = f2.indexOf(r.from as number);
    expect(f2.text.slice(at - 8, at)).toBe("Type 2: ");
  });

  it("survives whitespace edits inside the quote", () => {
    const d = doc(p(txt("Winters: expected PCO2 = 1.5 x HCO3 + 8.")));
    const runs = sectionRuns(d);
    const a = anchorOf(runs, "expected PCO2 = 1.5");
    const edited = doc(p(txt("Winters: expected  PCO2 =\t1.5 x HCO3 + 8.")));
    const r = relocate(a, sectionRuns(edited));
    expect(r.status).toBe("moved");
    const f = flatten(sectionRuns(edited));
    expect(f.text.slice(f.indexOf(r.from as number), f.indexOf(r.to as number))).toBe("expected  PCO2 =\t1.5");
  });

  it("is detached when the quote was edited away, never dropped", () => {
    const runs = sectionRuns(body);
    const a = anchorOf(runs, "hyperkalemia");
    const edited = doc(p(txt("Type 4 is the one with high potassium.")));
    expect(relocate(a, sectionRuns(edited))).toEqual({ status: "detached", from: null, to: null });
    expect(relocate({ ...a, quote: "" }, runs).status).toBe("detached");
  });

  it("works on explanation text offsets", () => {
    const html = "<p>Type 4 RTA is the one with <b>hyperkalemia</b>.</p><p>Think aldosterone.</p>";
    const runs = htmlTextRuns(html);
    const a = anchorOf(runs, "hyperkalemia.");
    expect(relocate(a, runs).status).toBe("exact");
    const moved = relocate(a, htmlTextRuns(`<p>Added first.</p>${html}`));
    expect(moved.status).toBe("moved");
    expect(moved.from).toBe(a.from + "Added first.".length);
  });
});

describe("splitRun", () => {
  const r = (id: string, from: number, to: number, color: PaintRange["color"] = "lime"): PaintRange => ({
    id,
    from,
    to,
    color,
  });

  it("returns the text untouched without ranges", () => {
    expect(splitRun("abc", 10, [])).toEqual([{ text: "abc", range: null, first: false }]);
  });

  it("splits at range edges and marks where a highlight starts", () => {
    const pieces = splitRun("abcdef", 10, [r("a", 11, 13)]);
    expect(pieces.map((x) => [x.text, x.range?.id ?? null, x.first])).toEqual([
      ["a", null, false],
      ["bc", "a", true],
      ["def", null, false],
    ]);
  });

  it("continues a highlight from an earlier run without repeating its word", () => {
    const pieces = splitRun("abc", 10, [r("a", 5, 12)]);
    expect(pieces[0]).toMatchObject({ text: "ab", first: false });
  });

  it("lets the later highlight win where two overlap", () => {
    const pieces = splitRun("abcdef", 0, [r("a", 0, 4), r("b", 2, 6, "amber")]);
    expect(pieces.map((x) => [x.text, x.range?.id])).toEqual([
      ["ab", "a"],
      ["cdef", "b"],
    ]);
  });
});
