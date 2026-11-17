import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { inflateSync } from "node:zlib";
import type { InkItem, NotebookDetail, NoteOut } from "@naadi/shared";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { parseCssColor, resolvePdfPalette } from "./colors";
import { exportHighlightsToPdf, exportNotebookToPdf, exportNoteToPdf, pdfFileName, sourceText } from "./export";
import { fontMeasurer } from "./fonts";
import { inkToOps, outlinePathD } from "./ink";

const require = createRequire(import.meta.url);
const FONT_FILES: Record<string, string> = {
  "Inter_400Regular.ttf": "@expo-google-fonts/inter/400Regular/Inter_400Regular.ttf",
  "Inter_700Bold.ttf": "@expo-google-fonts/inter/700Bold/Inter_700Bold.ttf",
  "Inter_400Regular_Italic.ttf": "@expo-google-fonts/inter/400Regular_Italic/Inter_400Regular_Italic.ttf",
  "Inter_700Bold_Italic.ttf": "@expo-google-fonts/inter/700Bold_Italic/Inter_700Bold_Italic.ttf",
  "JetBrainsMono_400Regular.ttf": "@expo-google-fonts/jetbrains-mono/400Regular/JetBrainsMono_400Regular.ttf",
};
const bytesOf = (name: string) => new Uint8Array(readFileSync(require.resolve(FONT_FILES[name] as string)));

// The design tokens, as the app loads them (tokens.css), on the document root.
const TOKENS = readFileSync(path.resolve(import.meta.dirname, "../../../../packages/ui/src/tokens.css"), "utf8");

const t = (key: string, vars: Readonly<Record<string, string>> = {}) =>
  `${key}${Object.entries(vars)
    .map(([k, v]) => ` ${k}=${v}`)
    .join("")}`;

beforeAll(() => {
  for (const m of TOKENS.matchAll(/(--color-[a-z0-9-]+):\s*([^;]+);/g))
    document.documentElement.style.setProperty(m[1] as string, (m[2] as string).trim());
  vi.stubGlobal("fetch", async (url: string) => {
    const name = Object.keys(FONT_FILES).find((n) => String(url).endsWith(n));
    if (!name) return new Response(null, { status: 404 });
    return new Response(bytesOf(name));
  });
});
afterAll(() => vi.unstubAllGlobals());

/** Page content streams of a saved PDF (inflated where pdf-lib compressed them), as text. */
function contents(pdf: Uint8Array): string {
  const raw = Buffer.from(pdf).toString("latin1");
  const out: string[] = [];
  for (const m of raw.matchAll(/<<([^>]*?)>>\s*stream\r?\n([\s\S]*?)\r?\nendstream/g)) {
    const dict = m[1] ?? "";
    const body = Buffer.from(m[2] ?? "", "latin1");
    try {
      out.push(dict.includes("FlateDecode") ? inflateSync(body).toString("latin1") : body.toString("latin1"));
    } catch {
      // font programs and other binary streams
    }
  }
  return out.join("\n");
}

const STROKE: InkItem = {
  id: "0190a0a0-0000-7000-8000-0000000000a1",
  tool: "pen",
  color: "ink",
  size: 0.004,
  createdAt: 1,
  updatedAt: 1,
  points: [
    [0.1, 0.2, 0.5, 0],
    [0.2, 0.25, 0.6, 16],
    [0.3, 0.22, 0.7, 32],
    [0.4, 0.3, 0.5, 48],
  ],
};
const MARKER: InkItem = {
  ...STROKE,
  id: "0190a0a0-0000-7000-8000-0000000000a2",
  tool: "marker",
  color: "lime",
  size: 0.02,
};
const SHAPE: InkItem = {
  ...STROKE,
  id: "0190a0a0-0000-7000-8000-0000000000a3",
  shape: "line",
  points: [
    [0.1, 0.5, 0.5, 0],
    [0.6, 0.5, 0.5, 10],
  ],
};
const TEXT: InkItem = {
  id: "0190a0a0-0000-7000-8000-0000000000a4",
  tool: "text",
  color: "violet",
  size: 0.03,
  x: 0.1,
  y: 0.8,
  w: 0.5,
  text: "Na⁺ ≥ 135",
  createdAt: 1,
  updatedAt: 1,
};
const GONE: InkItem = { ...STROKE, id: "0190a0a0-0000-7000-8000-0000000000a5", deleted: true };

describe("ink → PDF operations", () => {
  const m = fontMeasurer(
    Object.fromEntries(
      (["sans", "sansBold", "sansItalic", "sansBoldItalic", "mono"] as const).map((k, i) => [
        k,
        bytesOf(Object.keys(FONT_FILES)[i] as string),
      ]),
    ) as never,
  );

  it("draws markers first, then pens and shapes, then text; tombstones are skipped", () => {
    const ops = inkToOps([STROKE, TEXT, MARKER, SHAPE, GONE], 612, m);
    expect(ops.map((o) => o.op)).toEqual(["fill", "fill", "line", "text"]);
    expect(ops[0]).toMatchObject({ op: "fill", marker: true, color: "lime" });
    expect(ops[2]).toMatchObject({ op: "line", d: "M 61.2 306 L 367.2 306" });
    expect(ops[3]).toMatchObject({ op: "text", text: "Na⁺ ≥ 135", x: 61.2, color: "violet" });
  });

  it("closes the outline path with quadratic midpoints in page points", () => {
    const d = outlinePathD(
      [
        [0, 0],
        [100, 0],
        [100, 100],
      ],
      0.5,
    );
    // Quadratic midpoints written as their exact cubics: Q(0,0)->(25,0), Q(50,0)->(50,25), Q(50,50)->(25,25).
    expect(d).toBe("M 0 0 C 0 0 8.33 0 25 0 C 41.67 0 50 8.33 50 25 C 50 41.67 41.67 41.67 25 25 Z");
    expect(outlinePathD([], 1)).toBe("");
    expect(outlinePathD([[10, 10]], 1)).toMatch(/^M 9.5 10 a /);
  });
});

describe("colours", () => {
  it("parses the token formats", () => {
    expect(parseCssColor("#fff")).toEqual({ r: 1, g: 1, b: 1 });
    expect(parseCssColor(" #050506 ")).toEqual({ r: 5 / 255, g: 5 / 255, b: 6 / 255 });
    expect(parseCssColor("rgb(255 0 0)")).toEqual({ r: 1, g: 0, b: 0 });
    expect(parseCssColor("oklch(0.5 0.1 20)")).toBeNull();
  });

  it("resolves every role from tokens.css and refuses a missing token", () => {
    const p = resolvePdfPalette();
    expect(p.text.text).toEqual(parseCssColor("#050506"));
    expect(p.rect["hl-lime"]).toEqual(parseCssColor("#c6ff3d"));
    expect(p.ink.violet).toEqual(parseCssColor("#5b45d6"));
    expect(() => resolvePdfPalette(() => "")).toThrow(/design token/);
  });
});

const NOTE_BODY = {
  type: "doc",
  content: [
    { type: "heading", attrs: { level: 2 }, content: [{ type: "text", text: "Loop diuretics" }] },
    {
      type: "paragraph",
      content: [
        { type: "text", text: "Block NKCC2 " },
        { type: "text", text: "in the TAL", marks: [{ type: "highlight", attrs: { color: "amber" } }] },
        { type: "text", text: " → hypokalaemia, μ ≥ 2." },
      ],
    },
  ],
};

describe("export (pdf-lib)", () => {
  it("a note: text runs as real text, a highlight rect and the legend", async () => {
    const r = await exportNoteToPdf({ title: "Renal – loops!", body: NOTE_BODY, updated_at: "" }, t);
    expect(r.filename).toBe("Renal-loops.pdf");
    expect(r.pages).toBe(1);
    expect(r.blob.type).toBe("application/pdf");
    const bytes = new Uint8Array(await r.blob.arrayBuffer());
    expect(Buffer.from(bytes.slice(0, 5)).toString()).toBe("%PDF-");
    const c = contents(bytes);
    expect(c).toMatch(/BT[\s\S]*Tj[\s\S]*ET/); // text objects
    expect(c).toMatch(/ l\nh\nf\n/); // the highlight and legend rects (closed, filled paths)
    // Fonts are embedded as subset TrueType with a ToUnicode map (so the text is searchable and copyable).
    // (pdf-lib keeps object dictionaries in compressed object streams; `contents` inflates them too.)
    expect(c).toMatch(/\/FontFile2/);
    expect(c).toMatch(/\/ToUnicode/);
  });

  it("highlights: one entry per highlight with its colour word and source", async () => {
    const h = (color: NoteOut["highlight_color"], quote: string, n: number): NoteOut =>
      ({
        id: `h${n}`,
        kind: "highlight",
        title: null,
        notebook_id: null,
        anchor_type: "section",
        anchor_id: "s1",
        anchor_pos: { from: 0, to: 4, quote, prefix: "", suffix: "" },
        highlight_color: color,
        body: null,
        body_text: "",
        excerpt: quote,
        paper: null,
        canvas: "paper",
        sort: 0,
        source: { type: "section", chapter_title: "Chapter 8", section_heading: "Loops" },
        has_ink: false,
        created_at: "",
        updated_at: "",
      }) as NoteOut;
    expect(sourceText(h("lime", "x", 1), t)).toBe("pdf.source.section chapter=Chapter 8 section=Loops");
    const r = await exportHighlightsToPdf([h("lime", "Furosemide", 1), h("violet", "FENa", 2)], t);
    expect(r.pages).toBe(1);
    expect(r.filename).toBe("pdf-highlights-title.pdf");
  });

  it("a notebook: one Letter page per notebook page with paper and ink paths", async () => {
    const page = (id: string, paper: NoteOut["paper"]): NoteOut =>
      ({ id, kind: "page", paper, canvas: "paper", sort: 0, body: null }) as NoteOut;
    const notebook = {
      title: "Renal",
      paper: "grid",
      pages: [page("p1", null), page("p2", "cornell"), { ...page("p3", null), canvas: "whiteboard" } as NoteOut],
    } as Pick<NotebookDetail, "title" | "paper" | "pages">;
    const r = await exportNotebookToPdf(notebook, t, {
      loadInk: async (id) => (id === "p1" ? [STROKE, MARKER, SHAPE, TEXT] : []),
      renderWhiteboard: async () => null,
    });
    expect(r.pages).toBe(3);
    const c = contents(new Uint8Array(await r.blob.arrayBuffer()));
    // Filled perfect-freehand outlines are curves (c) closed and filled (f); the shape is a stroked path (S).
    expect(c).toMatch(/ c\s/);
    expect(c).toMatch(/\nf\n|\sf\s/);
    expect(c).toMatch(/\sS\s/);
    // Paper rules are lines (m … l … S) and the Cornell summary band a filled rect.
    expect(c).toMatch(/ m\s[\s\S]* l\s/);
  });

  it("makes safe file names", () => {
    expect(pdfFileName("  ", "note")).toBe("note.pdf");
    expect(pdfFileName("Café / Ödem", "x")).toBe("Cafe-Odem.pdf");
  });
});
