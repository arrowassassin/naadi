import { readFileSync } from "node:fs";
import { getSchema } from "@tiptap/core";
import { Node } from "@tiptap/pm/model";
import { describe, expect, it } from "vitest";
import { InkAnchorSchema } from "../ink";
import { contentExtensions } from "./extensions";
import { blockFingerprint, nodeSize, topLevelBlockPositions, topLevelBlocks } from "./positions";
import { SEED_BODIES_FILE } from "./seed-bodies";

const schema = getSchema(contentExtensions);

function pmPositions(json: unknown): number[] {
  const doc = Node.fromJSON(schema, json as Record<string, unknown>);
  const out: number[] = [];
  doc.forEach((_child, offset) => {
    out.push(offset);
  });
  return out;
}

const sample = {
  type: "doc",
  content: [
    { type: "heading", attrs: { level: 2 }, content: [{ type: "text", text: "Renal tubular acidosis" }] },
    {
      type: "paragraph",
      content: [
        { type: "text", text: "Every RTA is a " },
        { type: "text", marks: [{ type: "bold" }], text: "normal anion gap" },
        { type: "hardBreak" },
        { type: "text", text: "K⁺ 😀 metabolic acidosis." },
      ],
    },
    { type: "paragraph" },
    { type: "figure", attrs: { mediaId: "fig-1" } },
    {
      type: "bulletList",
      content: [
        { type: "listItem", content: [{ type: "paragraph", content: [{ type: "text", text: "Type 1" }] }] },
        { type: "listItem", content: [{ type: "paragraph", content: [{ type: "text", text: "Type 2" }] }] },
      ],
    },
    { type: "math", attrs: { latex: "x^2" } },
    { type: "paragraph", content: [{ type: "text", text: "end" }] },
  ],
};

describe("topLevelBlockPositions", () => {
  it("matches ProseMirror's positions for every node kind", () => {
    expect(topLevelBlockPositions(sample)).toEqual(pmPositions(sample));
    const doc = Node.fromJSON(schema, sample);
    expect(nodeSize(sample) - 2).toBe(doc.content.size);
  });

  it("matches ProseMirror on every seeded section body", () => {
    const file = JSON.parse(readFileSync(SEED_BODIES_FILE, "utf8")) as { sections: Record<string, { body: unknown }> };
    const bodies = Object.values(file.sections).map((s) => s.body);
    expect(bodies.length).toBeGreaterThan(0);
    for (const body of bodies) expect(topLevelBlockPositions(body)).toEqual(pmPositions(body));
  });

  it("is empty for anything that is not a doc", () => {
    expect(topLevelBlockPositions(null)).toEqual([]);
    expect(topLevelBlockPositions({ type: "paragraph" })).toEqual([]);
  });
});

describe("blockFingerprint", () => {
  const p = (text: string) => ({ type: "paragraph", content: [{ type: "text", text }] });
  it("is <type>:<8 hex>, valid as InkAnchor.fp, stable, and changes with the block", () => {
    const fp = blockFingerprint(p("Glomerular filtration"));
    expect(fp).toMatch(/^paragraph:[0-9a-f]{8}$/);
    expect(InkAnchorSchema.safeParse({ pm: 0, fp }).success).toBe(true);
    expect(blockFingerprint(p("Glomerular filtration"))).toBe(fp);
    expect(blockFingerprint(p("Glomerular filtration rate"))).not.toBe(fp);
    expect(blockFingerprint({ type: "heading", content: p("Glomerular filtration").content })).not.toBe(fp);
    expect(blockFingerprint(null)).toMatch(/^block:[0-9a-f]{8}$/);
  });

  it("topLevelBlocks pairs every position with its block's fingerprint; inserting a block shifts the rest", () => {
    const doc = { type: "doc", content: [p("one"), p("two")] };
    const before = topLevelBlocks(doc);
    expect(before.map((b) => b.pm)).toEqual(topLevelBlockPositions(doc));
    const after = topLevelBlocks({ type: "doc", content: [p("new"), p("one"), p("two")] });
    // "one" moved to "new"'s old position: same pm, different fingerprint, so an anchor on "one" cannot snap to "new".
    expect(after[0]?.pm).toBe(before[0]?.pm);
    expect(after[0]?.fp).not.toBe(before[0]?.fp);
    expect(after[1]?.fp).toBe(before[0]?.fp);
    expect(topLevelBlocks({ type: "paragraph" })).toEqual([]);
  });
});
