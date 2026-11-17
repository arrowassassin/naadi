/**
 * pdf-lib drawing: turns the pure layout (layout.ts), paper (notebooks/paper.ts) and ink (ink.ts) operations
 * into PDF content. Page coordinates in the operations are top-left/y-down points; PDF is bottom-left/y-up, so
 * every y is flipped here and nowhere else.
 */
import {
  BlendMode,
  LineCapStyle,
  type PDFDocument,
  type PDFFont,
  type PDFImage,
  type PDFPage,
  type RGB,
  rgb,
} from "pdf-lib";
import { type Paper, paperPrimitives } from "../notebooks/paper";
import type { PdfColor, PdfPalette } from "./colors";
import type { FontBytes } from "./fonts";
import type { InkOp } from "./ink";
import type { FontKey, LaidPage } from "./layout";

export type PdfFonts = Record<FontKey, PDFFont>;

const toRgb = (c: PdfColor): RGB => rgb(c.r, c.g, c.b);

/** Embed the five faces, subset to the glyphs used (a note's PDF carries tens of KB of font, not MBs). */
export async function embedFonts(doc: PDFDocument, bytes: FontBytes): Promise<PdfFonts> {
  const keys = Object.keys(bytes) as FontKey[];
  const fonts = await Promise.all(keys.map((k) => doc.embedFont(bytes[k], { subset: true })));
  return Object.fromEntries(keys.map((k, i) => [k, fonts[i]])) as PdfFonts;
}

export function fillPage(page: PDFPage, color: PdfColor): void {
  const { width, height } = page.getSize();
  page.drawRectangle({ x: 0, y: 0, width, height, color: toRgb(color) });
}

/** The paper template under the ink, from the same primitives the screen draws. */
export function drawPaper(page: PDFPage, paper: Paper, palette: PdfPalette): void {
  const { width, height } = page.getSize();
  for (const p of paperPrimitives(paper, { width, height })) {
    const { color, opacity } = palette.paperRoles[p.role];
    if (p.kind === "line") {
      page.drawLine({
        start: { x: p.x1, y: height - p.y1 },
        end: { x: p.x2, y: height - p.y2 },
        thickness: p.width,
        color: toRgb(color),
        opacity,
      });
    } else if (p.kind === "dot") {
      page.drawCircle({ x: p.x, y: height - p.y, size: p.r, color: toRgb(color), opacity });
    } else {
      page.drawRectangle({ x: p.x, y: height - p.y - p.h, width: p.w, height: p.h, color: toRgb(color), opacity });
    }
  }
}

/** One laid-out page (text, highlight rects, images). `images` maps an image block's src to its embedded image. */
export function drawLaidPage(
  page: PDFPage,
  laid: LaidPage,
  fonts: PdfFonts,
  palette: PdfPalette,
  images: ReadonlyMap<string, PDFImage> = new Map(),
): void {
  const { height } = page.getSize();
  for (const op of laid.ops) {
    if (op.op === "rect") {
      page.drawRectangle({
        x: op.x,
        y: height - op.y - op.h,
        width: op.w,
        height: op.h,
        color: toRgb(palette.rect[op.color]),
      });
    } else if (op.op === "image") {
      const img = images.get(op.src);
      if (img) page.drawImage(img, { x: op.x, y: height - op.y - op.h, width: op.w, height: op.h });
    } else {
      const font = fonts[op.font];
      const color = toRgb(palette.text[op.color]);
      page.drawText(op.text, { x: op.x, y: height - op.y, size: op.size, font, color });
      if (op.strike) {
        const w = font.widthOfTextAtSize(op.text, op.size);
        const y = height - op.y + op.size * 0.3;
        page.drawLine({
          start: { x: op.x, y },
          end: { x: op.x + w, y },
          thickness: Math.max(0.5, op.size / 16),
          color,
        });
      }
    }
  }
}

/** Ink as vector paths: perfect-freehand outlines filled, shapes stroked, markers with a multiply blend. */
export function drawInk(page: PDFPage, ops: readonly InkOp[], fonts: PdfFonts, palette: PdfPalette): void {
  const { height } = page.getSize();
  for (const op of ops) {
    const color = toRgb(palette.ink[op.color]);
    const blendMode = "marker" in op && op.marker ? BlendMode.Multiply : BlendMode.Normal;
    if (op.op === "fill") {
      // drawSvgPath maps the path's y-down coordinates from (x, y) = the page's top-left corner.
      page.drawSvgPath(op.d, { x: 0, y: height, color, blendMode });
    } else if (op.op === "line") {
      page.drawSvgPath(op.d, {
        x: 0,
        y: height,
        borderColor: color,
        borderWidth: op.width,
        borderLineCap: LineCapStyle.Round,
        blendMode,
      });
    } else {
      page.drawText(op.text, { x: op.x, y: height - op.y, size: op.size, font: fonts[op.font], color });
    }
  }
}
