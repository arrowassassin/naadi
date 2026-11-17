/**
 * PDF colours from the design tokens (tokens.css): the export reads the live CSS custom properties, so the PDF
 * uses exactly the colours the screen does and no colour value is written in this code.
 */
import { PALETTE_TOKENS } from "@naadi/ink";
import type { InkColor } from "@naadi/shared";
import { PAPER_GROUND_TOKEN, PAPER_ROLE_TOKENS, type PaperRole } from "../notebooks/paper";
import type { RectRole, TextRole } from "./layout";

export interface PdfColor {
  r: number;
  g: number;
  b: number;
}

/** `#rgb`, `#rrggbb`, `#rrggbbaa` (alpha ignored) or `rgb(r g b)` / `rgb(r, g, b)` → 0..1 channels. */
export function parseCssColor(value: string): PdfColor | null {
  const v = value.trim().toLowerCase();
  let m = /^#([0-9a-f]{3})$/.exec(v);
  if (m?.[1]) {
    const [r, g, b] = [...m[1]].map((c) => Number.parseInt(c + c, 16) / 255) as [number, number, number];
    return { r, g, b };
  }
  m = /^#([0-9a-f]{6})(?:[0-9a-f]{2})?$/.exec(v);
  if (m?.[1]) {
    const n = Number.parseInt(m[1], 16);
    return { r: ((n >> 16) & 255) / 255, g: ((n >> 8) & 255) / 255, b: (n & 255) / 255 };
  }
  m = /^rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)/.exec(v);
  if (m) {
    const c = (s: string | undefined) => Math.min(1, Math.max(0, Number(s) / 255));
    return { r: c(m[1]), g: c(m[2]), b: c(m[3]) };
  }
  return null;
}

export interface PdfPalette {
  text: Record<TextRole, PdfColor>;
  rect: Record<RectRole, PdfColor>;
  ink: Record<InkColor, PdfColor>;
  paper: PdfColor;
  paperRoles: Record<PaperRole, { color: PdfColor; opacity: number }>;
}

/** Which token each PDF colour role uses (paper theme: dark ink on light paper). */
export const PDF_TEXT_TOKENS: Record<TextRole, string> = { text: "--color-ground", muted: "--color-paper-text-2" };
export const PDF_RECT_TOKENS: Record<RectRole, string> = {
  "hl-lime": "--color-lime",
  "hl-violet": "--color-violet-text",
  "hl-amber": "--color-mark-amber",
  code: "--color-paper",
  quote: "--color-paper-text-2",
  rule: "--color-text-3",
};

export type TokenReader = (token: string) => string;

export function cssTokenReader(el: Element = document.documentElement): TokenReader {
  const cs = getComputedStyle(el);
  return (token) => cs.getPropertyValue(token);
}

/** Resolve every role; a missing or unparseable token is an error (tokens.css not loaded), never a guess. */
export function resolvePdfPalette(read: TokenReader = cssTokenReader()): PdfPalette {
  const get = (token: string): PdfColor => {
    const c = parseCssColor(read(token));
    if (!c) throw new Error(`pdf: design token ${token} is not defined`);
    return c;
  };
  const map = <K extends string>(tokens: Record<K, string>) =>
    Object.fromEntries(Object.entries(tokens).map(([k, t]) => [k, get(t as string)])) as Record<K, PdfColor>;
  const ink = PALETTE_TOKENS.paper;
  return {
    text: map(PDF_TEXT_TOKENS),
    rect: map(PDF_RECT_TOKENS),
    ink: { ink: get(ink.ink), violet: get(ink.violet), lime: get(ink.lime), amber: get(ink.amber) },
    paper: get(PAPER_GROUND_TOKEN),
    paperRoles: Object.fromEntries(
      Object.entries(PAPER_ROLE_TOKENS).map(([role, spec]) => [
        role,
        { color: get(spec.token), opacity: spec.opacity },
      ]),
    ) as PdfPalette["paperRoles"],
  };
}
