import { describe, expect, it } from "vitest";
import { parseCustomEvent, parseInterrupt, parseValues } from "./protocol";

const citation = {
  id: "c1",
  message_id: "m1",
  title: "Anion gap",
  source: "Naadi",
  layer: "course",
  url: null,
  target: { chapter_id: "ch05", section_id: "ch05-s03" },
  license: null,
  n: 1,
};

const doc = {
  type: "doc",
  content: [{ type: "paragraph", content: [{ type: "text", text: "Gap = Na − (Cl + HCO3)" }] }],
};

describe("tutor protocol parsing", () => {
  it("accepts every block type the protocol defines", () => {
    const blocks = [
      { type: "flowchart", data: { mermaid: "flowchart TD\nA-->B" } },
      {
        type: "image",
        data: {
          url: "https://upload.wikimedia.org/a.png",
          title: "Nephron",
          license: "CC BY",
          credit: "X",
          link: null,
        },
      },
      {
        type: "video",
        data: { provider: "youtube", video_id: "dQw4w9WgXcQ", media_id: "yt:1", title: "RTA", start_s: 30 },
      },
      { type: "sources", data: { items: [citation] } },
      {
        type: "actions",
        data: {
          items: [{ action: "open_block", label_key: "agent.action.open_block", payload: { block_id: "b1", n: 1 } }],
        },
      },
      { type: "card_preview", data: { cards: [{ front: "Q", back: "A" }] } },
      {
        type: "table",
        data: {
          columns: [{ key: "system", label_key: "agent.analytics.system", label: null }],
          rows: [{ system: "Renal" }],
        },
      },
      {
        type: "chart",
        data: {
          chart: "bar",
          x: "system",
          series: [{ key: "percent", label_key: null, label: "Score" }],
          rows: [{ system: "Renal", percent: 72 }],
          unit: "percent",
        },
      },
      {
        type: "canvas",
        data: {
          key: "cv1",
          kind: "document",
          origin: "assistant",
          title_key: null,
          title: "Draft",
          ref: null,
          data: { doc },
        },
      },
      { type: "notice", data: { kind: "budget_exhausted", reset_at: "2026-11-17T23:00:00Z" } },
      { type: "notice", data: { kind: "disclaimer" } },
    ];
    for (const [i, b] of blocks.entries()) {
      const ev = parseCustomEvent({ type: "block", block: { id: `b${i}`, message_id: null, ...b } });
      expect(ev, b.type).not.toBeNull();
      expect(ev?.type).toBe("block");
    }
  });

  it("ignores payloads that do not parse (never half-rendered)", () => {
    const bad = [
      null,
      "text",
      { type: "unknown" },
      { type: "block", block: { id: "x", message_id: null, type: "script", data: {} } },
      // http image, javascript: URL, foreign-looking video id, mermaid over the cap
      {
        type: "block",
        block: {
          id: "x",
          message_id: null,
          type: "image",
          data: { url: "http://x/a.png", title: "", license: null, credit: null, link: null },
        },
      },
      {
        type: "block",
        block: {
          id: "x",
          message_id: null,
          type: "image",
          data: { url: "javascript:alert(1)", title: "", license: null, credit: null, link: null },
        },
      },
      {
        type: "block",
        block: {
          id: "x",
          message_id: null,
          type: "video",
          data: { provider: "youtube", video_id: "<script>", media_id: "m", title: "", start_s: null },
        },
      },
      { type: "block", block: { id: "x", message_id: null, type: "flowchart", data: { mermaid: "a".repeat(4001) } } },
      { type: "block", block: { id: "x", message_id: null, type: "notice", data: { kind: "budget_exhausted" } } },
      { type: "citation", citation: { ...citation, layer: "vault" } },
      { type: "tool", tool: "search_knowledge", status: "maybe" },
    ];
    for (const b of bad) expect(parseCustomEvent(b)).toBeNull();
  });

  it("parses citations and tool status events", () => {
    expect(parseCustomEvent({ type: "citation", citation })).toEqual({ type: "citation", citation });
    expect(parseCustomEvent({ type: "tool", tool: "web_search", status: "running" })).toEqual({
      type: "tool",
      tool: "web_search",
      status: "running",
    });
  });

  it("parses every interrupt preview kind and rejects malformed ones", () => {
    const previews = [
      { kind: "note", mode: "create", note_id: null, anchor: { type: "section", id: "s1", label: "Anion gap" }, doc },
      { kind: "cards", cards: [{ front: "Q", back: "A" }] },
      { kind: "block", count: 10, mode: "tutor", systems: ["renal"], disciplines: [], status: "flagged" },
      { kind: "plan_item", day: "2026-11-18", title: "Renal review", item_kind: "review", target_minutes: 30 },
      {
        kind: "chapter_diff",
        section_id: "s1",
        heading: "Anion gap",
        target: "overlay_note",
        ops: [{ op: "replace", index: 0, before: doc.content[0], after: doc.content[0] }],
      },
      { kind: "memory", key: "k1", fact: "Exam on 3 March" },
      { kind: "forget", key: "k1", fact: "Exam on 3 March" },
    ];
    for (const preview of previews) {
      expect(parseInterrupt({ type: "confirm", tool: "x", preview, editable: true }), preview.kind).not.toBeNull();
    }
    expect(
      parseInterrupt({ type: "confirm", tool: "x", preview: { kind: "cards", cards: [] }, editable: true }),
    ).toBeNull();
    expect(
      parseInterrupt({
        type: "confirm",
        tool: "x",
        preview: { kind: "block", count: 1, mode: "tutor", systems: [], disciplines: [], status: "marked" },
        editable: false,
      }),
    ).toBeNull();
    expect(parseInterrupt({ confirmed: true })).toBeNull();
    expect(parseInterrupt(undefined)).toBeNull();
  });

  it("reads values item by item, dropping what does not parse", () => {
    const v = parseValues({
      blocks: [
        { id: "b1", message_id: "m1", type: "flowchart", data: { mermaid: "flowchart TD\nA-->B" } },
        { id: "b2", message_id: "m1", type: "flowchart", data: {} },
      ],
      citations: [citation, { id: "bad" }],
      canvas: [
        {
          key: "cv1",
          kind: "diagram",
          origin: "assistant",
          title_key: null,
          title: null,
          ref: null,
          data: { mermaid: "x" },
        },
      ],
      notice: { kind: "turn_cap" },
      refusal: { reason: "exam_mode" },
      disclaimer_shown: true,
    });
    expect(v.blocks.map((b) => b.id)).toEqual(["b1"]);
    expect(v.citations).toHaveLength(1);
    expect(v.canvas).toHaveLength(1);
    expect(v.notice).toEqual({ kind: "turn_cap" });
    expect(v.refusal).toEqual({ reason: "exam_mode" });
    expect(v.disclaimer_shown).toBe(true);
    expect(parseValues(undefined)).toEqual({
      blocks: [],
      citations: [],
      canvas: [],
      notice: null,
      refusal: null,
      disclaimer_shown: false,
    });
  });
});
