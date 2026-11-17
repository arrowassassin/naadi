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
