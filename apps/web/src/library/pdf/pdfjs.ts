import type { PDFDocumentLoadingTask } from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";

/**
 * pdf.js 6.5.438, loaded on first use. The worker is bundled by Vite as a same-origin asset (`?url`), so the CSP's
 * `worker-src 'self'` holds and nothing loads from a CDN. The app creates that one worker itself (shared by every
 * document) through a Trusted Types policy that admits exactly that URL, so `require-trusted-types-for 'script'`
 * is satisfied. WebAssembly decoders are off (the CSP allows no `wasm-unsafe-eval`; pdf.js falls back to its
 * JavaScript decoders), and pages render on the main thread so a page canvas can be cropped for "Ask about this".
 */
type PdfJs = typeof import("pdfjs-dist");
let loading: Promise<PdfJs> | null = null;

interface TrustedTypesLike {
  createPolicy(
    name: string,
    rules: { createScriptURL: (url: string) => string },
  ): {
    createScriptURL(url: string): unknown;
  };
}

let policy: ReturnType<TrustedTypesLike["createPolicy"]> | null = null;

/** The worker script URL, as a TrustedScriptURL where the browser has Trusted Types. */
export function workerScriptUrl(): string {
  const tt = (globalThis as { trustedTypes?: TrustedTypesLike }).trustedTypes;
  if (!tt) return workerUrl;
  policy ??= tt.createPolicy("naadi-pdfjs-worker", {
    createScriptURL: (url) => {
      if (url !== workerUrl) throw new TypeError("only the bundled pdf.js worker may be loaded");
      return url;
    },
  });
  // A TrustedScriptURL is accepted wherever the Worker constructor takes a string.
  return policy.createScriptURL(workerUrl) as string;
}

export function loadPdfJs(): Promise<PdfJs> {
  loading ??= import("pdfjs-dist");
  return loading;
}

/**
 * pdf.js's standard fonts (for PDFs that do not embed Helvetica, Times …) and CMaps (CJK text), bundled as
 * hashed same-origin assets: the CSP allows fonts and fetches from 'self' only.
 */
const BINARY_DATA: Record<string, string> = Object.fromEntries(
  Object.entries({
    ...import.meta.glob<string>("/node_modules/pdfjs-dist/standard_fonts/*.{pfb,ttf}", {
      query: "?url",
      import: "default",
      eager: true,
    }),
    ...import.meta.glob<string>("/node_modules/pdfjs-dist/cmaps/*.bcmap", {
      query: "?url",
      import: "default",
      eager: true,
    }),
  }).map(([file, url]) => [file.slice(file.lastIndexOf("/") + 1), url]),
);

/** pdf.js asks for these by name; each name maps to its bundled asset (never a CDN). */
class BundledBinaryData {
  async fetch({ filename }: { kind: string; filename: string }): Promise<Uint8Array> {
    const url = BINARY_DATA[filename];
    if (!url) throw new Error("pdf.js data file not bundled");
    const res = await fetch(url);
    if (!res.ok) throw new Error(`pdf.js data file: HTTP ${res.status}`);
    return new Uint8Array(await res.arrayBuffer());
  }
}

export function bundledDataFile(name: string): string | undefined {
  return BINARY_DATA[name];
}

export interface OpenedPdf {
  task: PDFDocumentLoadingTask;
  /** Destroy the document and terminate its worker. */
  close: () => Promise<void>;
}

/**
 * Open a PDF in its own worker (closed with the document). Big PDFs stream in 256 KB ranges and only the pages
 * that are shown are fetched.
 */
export async function openPdf(url: string): Promise<OpenedPdf> {
  const pdfjs = await loadPdfJs();
  const port = new Worker(workerScriptUrl(), { type: "module", name: "naadi-pdfjs" });
  const worker = pdfjs.PDFWorker.create({ port, name: "naadi-pdfjs" });
  const task = pdfjs.getDocument({
    url,
    worker,
    rangeChunkSize: 256 * 1024,
    disableAutoFetch: true,
    useWasm: false,
    disableWorkerRendering: true,
    enableXfa: false,
    // Glyphs are drawn as paths: no @font-face stylesheet (refused by style-src 'self') and no system-font guess.
    disableFontFace: true,
    useSystemFonts: false,
    // Standard fonts and CMaps come through BundledBinaryData on the main thread; the base URLs only tell pdf.js
    // that they exist (the factory maps each file name to its bundled asset).
    useWorkerFetch: false,
    BinaryDataFactory: BundledBinaryData,
    standardFontDataUrl: "/assets/",
    cMapUrl: "/assets/",
    cMapPacked: true,
    // The signed URL carries its token; no cookies are sent to Storage.
    withCredentials: false,
  });
  return {
    task,
    close: async () => {
      try {
        await task.destroy();
      } finally {
        worker.destroy();
        port.terminate();
      }
    },
  };
}

/** A failure to fetch the file's bytes (an expired signed URL, a dropped connection), not a broken page. */
export function isFetchFailure(e: unknown): boolean {
  const name = (e as { name?: string } | null)?.name ?? "";
  return name === "ResponseException" || name === "UnexpectedResponseException" || name === "MissingPDFException";
}
