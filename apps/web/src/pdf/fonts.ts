/**
 * Self-hosted PDF fonts: Inter (regular, bold, italic, bold italic) and JetBrains Mono as static TrueType files
 * from pinned packages (@expo-google-fonts/inter 0.4.2, @expo-google-fonts/jetbrains-mono 0.4.1; SIL OFL 1.1, the
 * licence ships next to the files in the build as fonts/OFL-*.txt). Vite emits them as hashed same-origin assets,
 * fetched only when the student exports. The app's web fonts are unicode-range woff2 subsets, which fontkit
 * cannot subset for embedding and which lack →, ≥ and ⁺ (the full Inter TTF covers them).
 */

import interRegularUrl from "@expo-google-fonts/inter/400Regular/Inter_400Regular.ttf?url";
import interItalicUrl from "@expo-google-fonts/inter/400Regular_Italic/Inter_400Regular_Italic.ttf?url";
import interBoldUrl from "@expo-google-fonts/inter/700Bold/Inter_700Bold.ttf?url";
import interBoldItalicUrl from "@expo-google-fonts/inter/700Bold_Italic/Inter_700Bold_Italic.ttf?url";
import monoUrl from "@expo-google-fonts/jetbrains-mono/400Regular/JetBrainsMono_400Regular.ttf?url";
import * as fontkitNs from "@pdf-lib/fontkit";
import type { PDFDocument } from "pdf-lib";
import type { FontKey, Measurer } from "./layout";

type Fontkit = Parameters<PDFDocument["registerFontkit"]>[0];

/** The package's ES build has only a default export; its typings declare the named one. */
export const fontkit: Fontkit = ((fontkitNs as unknown as { default?: Fontkit }).default ?? fontkitNs) as Fontkit;

export const FONT_URLS: Record<FontKey, string> = {
  sans: interRegularUrl,
  sansBold: interBoldUrl,
  sansItalic: interItalicUrl,
  sansBoldItalic: interBoldItalicUrl,
  mono: monoUrl,
};

export type FontBytes = Record<FontKey, Uint8Array>;

let cached: Promise<FontBytes> | null = null;

/** Fetch the five faces once per page load (the browser's HTTP cache keeps them across loads). */
export function loadFontBytes(fetcher: typeof fetch = fetch): Promise<FontBytes> {
  if (!cached) {
    const keys = Object.keys(FONT_URLS) as FontKey[];
    cached = Promise.all(
      keys.map(async (k) => {
        const res = await fetcher(FONT_URLS[k]);
        if (!res.ok) throw new Error(`pdf font ${k}: HTTP ${res.status}`);
        return [k, new Uint8Array(await res.arrayBuffer())] as const;
      }),
    ).then(
      (pairs) => Object.fromEntries(pairs) as FontBytes,
      (err: unknown) => {
        cached = null; // a failed fetch (offline before the fonts were cached) can be retried
        throw err;
      },
    );
  }
  return cached;
}

type FkFont = ReturnType<Fontkit["create"]>;

/**
 * Measure text the way pdf-lib draws it: the sum of the glyph advances of fontkit's layout (pdf-lib's
 * widthOfTextAtSize does the same), so the layout and the drawn PDF agree exactly.
 */
export function fontMeasurer(bytes: FontBytes): Measurer {
  const fonts = Object.fromEntries(
    (Object.keys(bytes) as FontKey[]).map((k) => [k, fontkit.create(bytes[k])]),
  ) as Record<FontKey, FkFont>;
  const widthCache = new Map<string, number>();
  return {
    width(text, font, size) {
      const key = `${font}\u0000${text}`;
      let units = widthCache.get(key);
      if (units === undefined) {
        const f = fonts[font];
        units = 0;
        for (const g of f.layout(text).glyphs) units += g.advanceWidth;
        units /= f.unitsPerEm;
        if (widthCache.size > 20_000) widthCache.clear();
        widthCache.set(key, units);
      }
      return units * size;
    },
    covers(font, cp) {
      // Spaces and line breaks are always fine; everything else must have a real glyph.
      return cp === 0x20 || fonts[font].hasGlyphForCodePoint(cp);
    },
  };
}
