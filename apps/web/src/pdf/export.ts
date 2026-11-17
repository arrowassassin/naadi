/**
 * PDF export in the browser (segment 13): a note, a filtered list of highlights, or a whole notebook, made with
 * pdf-lib 1.17.1 + @pdf-lib/fontkit and the self-hosted Inter / JetBrains Mono. Nothing is uploaded: the student's
 * own data is read through the existing APIs (and the device copy offline) and the file is handed to the browser.
 *
 *   exportNoteToPdf(note, t)            TipTap note → laid-out text with highlight tints and a word legend
 *   exportHighlightsToPdf(list, t)      one entry per highlight: the quote in its colour, the source, the note
 *   exportNotebookToPdf(notebook, t)    one Letter page per notebook page: paper + vector ink, or the whiteboard
 *   deliverPdf(result)                  download (Blob + anchor), share sheet where download is unavailable
 *
 * `t` is the app's ui_copy function; every word in the PDF comes from it.
 */
import { type InkItem, mergeInkItems, type NotebookDetail, type NoteOut } from "@naadi/shared";
import { PDFDocument, type PDFImage } from "pdf-lib";
import { getAuthState } from "../auth/session";
import { dexieStore } from "../ink/persist/db";
import { apiTransport } from "../ink/persist/transport";
import { pagePaper } from "../notebooks/pageOps";
import { PAGE_HEIGHT, PAGE_WIDTH } from "../notebooks/paper";
import { userUploadsBucket } from "../notebooks/whiteboard/files";
import { type Block, type HighlightColor, tiptapToBlocks, usedHighlights } from "./blocks";
import { type PdfPalette, resolvePdfPalette } from "./colors";
import { drawInk, drawLaidPage, drawPaper, embedFonts, fillPage, type PdfFonts } from "./draw";
import { fontkit, fontMeasurer, loadFontBytes } from "./fonts";
import { inkToOps } from "./ink";
import { type LayoutCopy, layoutDocument, type Measurer, TYPE } from "./layout";

export type PdfText = (key: string, vars?: Readonly<Record<string, string>>) => string;

export interface PdfResult {
  blob: Blob;
  filename: string;
  pages: number;
}

/** Hooks for data the export reads (tests and the notebook view pass their own). */
export interface NotebookExportSources {
  /** Live ink items of a page (default: the ink API, else this device's copy and unsent strokes). */
  loadInk?: (pageId: string) => Promise<readonly InkItem[]>;
  /** A whiteboard page as a PNG (default: its stored scene through Excalidraw's exportToBlob). */
  renderWhiteboard?: (page: NoteOut) => Promise<Blob | null>;
}

const MIME = "application/pdf";

function dateText(at: Date = new Date()): string {
  try {
    return at.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
  } catch {
    return at.toISOString().slice(0, 10);
  }
}

/** A safe file name from a title ("Renal – loops!" → "Renal-loops.pdf"). */
export function pdfFileName(title: string, fallback: string): string {
  const slug = title
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^A-Za-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 80);
  return `${slug || fallback}.pdf`;
}

function layoutCopy(t: PdfText, title: string, subtitle: string): LayoutCopy {
  return {
    title,
    subtitle,
    highlightWords: {
      lime: t("pdf.highlight.lime"),
      violet: t("pdf.highlight.violet"),
      amber: t("pdf.highlight.amber"),
    },
    legendLabel: t("pdf.legend"),
    footer: (page, count) => t("pdf.footer", { page: String(page), count: String(count) }),
  };
}

interface Kit {
  doc: PDFDocument;
  fonts: PdfFonts;
  measure: Measurer;
  palette: PdfPalette;
}

async function start(title: string, t: PdfText): Promise<Kit> {
  const bytes = await loadFontBytes();
  const doc = await PDFDocument.create();
  doc.registerFontkit(fontkit);
  doc.setTitle(title);
  doc.setCreator(t("pdf.creator"));
  doc.setProducer(t("pdf.creator"));
  doc.setCreationDate(new Date());
  const fonts = await embedFonts(doc, bytes);
  return { doc, fonts, measure: fontMeasurer(bytes), palette: resolvePdfPalette() };
}

async function finish(kit: Kit, filename: string): Promise<PdfResult> {
  const bytes = await kit.doc.save();
  return { blob: new Blob([bytes as BlobPart], { type: MIME }), filename, pages: kit.doc.getPageCount() };
}

// ── Images in notes (user-uploads paths or same-origin URLs) ──

async function signedUrls(paths: string[]): Promise<Map<string, string>> {
  const out = new Map<string, string>();
  if (!paths.length) return out;
  const auth = getAuthState();
  if (auth.status !== "signed-in") return out;
  const bucket = userUploadsBucket(auth.config);
  if (!bucket) return out;
  const own = paths.filter((p) => p.startsWith(`${auth.session.user.id}/`));
  const { data } = await auth.client.storage.from(bucket).createSignedUrls(own, 300);
  for (const d of data ?? []) if (d.path && d.signedUrl) out.set(d.path, d.signedUrl);
  return out;
}

/** Bytes as PNG or JPEG for pdf-lib (other formats are re-encoded through a canvas). */
async function embedImage(doc: PDFDocument, blob: Blob): Promise<PDFImage | null> {
  const bytes = new Uint8Array(await blob.arrayBuffer());
  try {
    if (bytes[0] === 0x89 && bytes[1] === 0x50) return await doc.embedPng(bytes);
    if (bytes[0] === 0xff && bytes[1] === 0xd8) return await doc.embedJpg(bytes);
  } catch {
    // a malformed file: try the canvas route below
  }
  if (typeof createImageBitmap !== "function" || typeof document === "undefined") return null;
  try {
    const bmp = await createImageBitmap(blob);
    const canvas = document.createElement("canvas");
    canvas.width = bmp.width;
    canvas.height = bmp.height;
    canvas.getContext("2d")?.drawImage(bmp, 0, 0);
    bmp.close();
    const png = await new Promise<Blob | null>((r) => canvas.toBlob(r, "image/png"));
    return png ? await doc.embedPng(new Uint8Array(await png.arrayBuffer())) : null;
  } catch {
    return null;
  }
}

/** Fetch and embed every image block, filling in its size; one that fails becomes its alt text. */
async function resolveImages(kit: Kit, blocks: Block[], t: PdfText): Promise<Map<string, PDFImage>> {
  const images = new Map<string, PDFImage>();
  const srcs = [...new Set(blocks.flatMap((b) => (b.type === "image" ? [b.src] : [])))];
  if (!srcs.length) return images;
  const paths = srcs.filter((s) => !/^[a-z]+:/i.test(s) && !s.startsWith("/"));
  const signed = await signedUrls(paths).catch(() => new Map<string, string>());
  await Promise.all(
    srcs.map(async (src) => {
      const url = signed.get(src) ?? (/^(blob:|data:|\/)/.test(src) || src.startsWith(location.origin) ? src : null);
      if (!url) return;
      try {
        const res = await fetch(url);
        if (!res.ok) return;
        const img = await embedImage(kit.doc, await res.blob());
        if (img) images.set(src, img);
      } catch {
        // offline or gone: the alt text stands in
      }
    }),
  );
  for (const b of blocks)
    if (b.type === "image") {
      const img = images.get(b.src);
      if (img) {
        b.width = img.width;
        b.height = img.height;
      } else if (!b.alt) b.alt = t("pdf.image_missing");
    }
  return images;
}

function drawDocument(
  kit: Kit,
  blocks: Block[],
  copy: LayoutCopy,
  legend: HighlightColor[],
  images: Map<string, PDFImage>,
) {
  const pages = layoutDocument({ blocks, copy, legendColors: legend, measure: kit.measure });
  for (const laid of pages) {
    const page = kit.doc.addPage([PAGE_WIDTH, PAGE_HEIGHT]);
    drawLaidPage(page, laid, kit.fonts, kit.palette, images);
  }
}

// ── Public API ──

/** A typed note (TipTap JSON) as a PDF. */
export async function exportNoteToPdf(
  note: Pick<NoteOut, "title" | "body" | "updated_at">,
  t: PdfText,
): Promise<PdfResult> {
  const title = note.title?.trim() || t("pdf.untitled_note");
  const kit = await start(title, t);
  const blocks = tiptapToBlocks(note.body);
  const images = await resolveImages(kit, blocks, t);
  drawDocument(
    kit,
    blocks,
    layoutCopy(t, title, t("pdf.exported_on", { date: dateText() })),
    usedHighlights(blocks),
    images,
  );
  return finish(kit, pdfFileName(title, "note"));
}

/** Where a highlight comes from, in words ("Chapter 8 · Loop diuretics", "Question 12"). */
export function sourceText(note: Pick<NoteOut, "source" | "anchor_pos">, t: PdfText): string {
  const s = note.source;
  const parts: string[] = [];
  if (s?.type === "section") {
    const chapter = s.chapter_title ?? "";
    const section = s.section_heading ?? "";
    parts.push(chapter && section ? t("pdf.source.section", { chapter, section }) : chapter || section);
  } else if (s?.type === "question") {
    parts.push(
      s.question_number
        ? t("pdf.source.question", { number: String(s.question_number) })
        : t("pdf.source.question_plain"),
    );
  } else if (s?.title) parts.push(s.title);
  if (note.anchor_pos && "detached" in note.anchor_pos && note.anchor_pos.detached) parts.push(t("pdf.detached"));
  return parts.filter(Boolean).join(" · ");
}

/** Highlights (already filtered by the caller) as a PDF: quote in its colour, then its colour word and source. */
export async function exportHighlightsToPdf(
  highlights: readonly NoteOut[],
  t: PdfText,
  opts: { title?: string } = {},
): Promise<PdfResult> {
  const title = opts.title?.trim() || t("pdf.highlights_title");
  const kit = await start(title, t);
  const words: Record<HighlightColor, string> = {
    lime: t("pdf.highlight.lime"),
    violet: t("pdf.highlight.violet"),
    amber: t("pdf.highlight.amber"),
  };
  const blocks: Block[] = [];
  const used = new Set<HighlightColor>();
  for (const h of highlights) {
    const color = (h.highlight_color ?? "lime") as HighlightColor;
    used.add(color);
    const quote = (h.anchor_pos && "quote" in h.anchor_pos ? h.anchor_pos.quote : null) ?? h.excerpt ?? h.body_text;
    blocks.push({ type: "paragraph", runs: [{ text: quote, highlight: color }] });
    const meta = [words[color], sourceText(h, t)].filter(Boolean).join(" · ");
    blocks.push({ type: "paragraph", runs: [{ text: meta }], tone: "muted", small: true });
    const note = h.body ? tiptapToBlocks(h.body) : [];
    if (note.length)
      blocks.push(...note.map((b) => (b.type === "paragraph" ? { ...b, indent: (b.indent ?? 0) + 1 } : b)));
    blocks.push({ type: "space", height: 8 });
  }
  const subtitle = t("pdf.highlights_subtitle", { count: highlights.length.toLocaleString(), date: dateText() });
  const legend = (["lime", "violet", "amber"] as const).filter((c) => used.has(c));
  const images = await resolveImages(kit, blocks, t);
  drawDocument(kit, blocks, layoutCopy(t, title, subtitle), legend, images);
  return finish(kit, pdfFileName(title, "highlights"));
}

/** The ink of a notebook page: the server's layer merged with this device's unsent strokes (offline: device copy). */
export async function loadPageInk(pageId: string): Promise<readonly InkItem[]> {
  const auth = getAuthState();
  const userId = auth.status === "signed-in" ? auth.session.user.id : null;
  const store = userId ? dexieStore(userId, `notebook_page:${pageId}`) : null;
  const [saved, pending] = await Promise.all([
    store?.readLayer().catch(() => null) ?? null,
    store?.readPending().catch(() => [] as InkItem[]) ?? [],
  ]);
  let base: readonly InkItem[] = saved?.items ?? [];
  try {
    base = (await apiTransport("notebook_page", pageId).load(null)).items;
  } catch {
    // offline: the device copy stands in
  }
  return mergeInkItems(base, pending).filter((i) => !i.deleted);
}

async function defaultWhiteboard(page: NoteOut): Promise<Blob | null> {
  const { renderWhiteboardPng } = await import("./whiteboard");
  return renderWhiteboardPng(page);
}

/** A notebook as a PDF: one US Letter page per notebook page, paper template + vector ink, or the whiteboard. */
export async function exportNotebookToPdf(
  notebook: Pick<NotebookDetail, "title" | "paper" | "pages">,
  t: PdfText,
  sources: NotebookExportSources = {},
): Promise<PdfResult> {
  const title = notebook.title.trim() || t("pdf.untitled_notebook");
  const kit = await start(title, t);
  const loadInk = sources.loadInk ?? loadPageInk;
  const renderWhiteboard = sources.renderWhiteboard ?? defaultWhiteboard;
  const count = notebook.pages.length;
  for (const [i, p] of notebook.pages.entries()) {
    const page = kit.doc.addPage([PAGE_WIDTH, PAGE_HEIGHT]);
    fillPage(page, kit.palette.paper);
    if (p.canvas === "whiteboard") {
      const png = await renderWhiteboard(p).catch(() => null);
      const img = png ? await embedImage(kit.doc, png) : null;
      if (img) {
        const m = 36;
        const scale = Math.min((PAGE_WIDTH - 2 * m) / img.width, (PAGE_HEIGHT - 2 * m - 24) / img.height, 1);
        const w = img.width * scale;
        const h = img.height * scale;
        page.drawImage(img, { x: (PAGE_WIDTH - w) / 2, y: PAGE_HEIGHT - m - h, width: w, height: h });
      } else {
        drawLaidPage(
          page,
          {
            ops: [
              {
                op: "text",
                x: 54,
                y: 80,
                text: t("pdf.whiteboard_missing"),
                font: "sans",
                size: TYPE.body.size,
                color: "muted",
              },
            ],
          },
          kit.fonts,
          kit.palette,
        );
      }
    } else {
      drawPaper(page, pagePaper(p, notebook.paper), kit.palette);
      const items = await loadInk(p.id).catch(() => [] as InkItem[]);
      drawInk(page, inkToOps(items, PAGE_WIDTH, kit.measure), kit.fonts, kit.palette);
    }
    const footer = t("pdf.notebook_footer", { title, page: String(i + 1), count: String(count) });
    const w = kit.measure.width(footer, "mono", TYPE.footer.size);
    drawLaidPage(
      page,
      {
        ops: [
          {
            op: "text",
            x: PAGE_WIDTH - 36 - w,
            y: PAGE_HEIGHT - 14,
            text: footer,
            font: "mono",
            size: TYPE.footer.size,
            color: "muted",
          },
        ],
      },
      kit.fonts,
      kit.palette,
    );
  }
  return finish(kit, pdfFileName(title, "notebook"));
}

/**
 * Hand the file to the student: a download through a Blob URL and an anchor (iPadOS Safari shows its download
 * sheet), or the share sheet with the file where the browser has no download attribute. Never uploads it.
 * Returns how it was delivered.
 */
export async function deliverPdf(result: PdfResult): Promise<"download" | "share"> {
  const canDownload = typeof HTMLAnchorElement !== "undefined" && "download" in HTMLAnchorElement.prototype;
  if (!canDownload && typeof navigator.share === "function") {
    const file = new File([result.blob], result.filename, { type: MIME });
    if (!navigator.canShare || navigator.canShare({ files: [file] })) {
      await navigator.share({ files: [file], title: result.filename });
      return "share";
    }
  }
  const url = URL.createObjectURL(result.blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = result.filename;
  a.rel = "noopener";
  a.hidden = true;
  document.body.append(a);
  a.click();
  a.remove();
  // Safari reads the Blob after the click returns: keep the URL alive for a while.
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
  return "download";
}

export { PDF_RECT_TOKENS, PDF_TEXT_TOKENS } from "./colors";
