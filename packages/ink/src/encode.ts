/**
 * PNG encoding for settled ink, off the main thread where the browser allows it: the canvas is copied
 * into an ImageBitmap (a fast GPU/memory copy) and a worker encodes it with OffscreenCanvas. Browsers
 * without module workers, OffscreenCanvas or createImageBitmap fall back to canvas.toBlob.
 */
import { inkProbe } from "./probe";

type Pending = (blob: Blob | null) => void;

let worker: Worker | null | undefined;
let nextId = 1;
const pending = new Map<number, Pending>();

function getWorker(): Worker | null {
  if (worker !== undefined) return worker;
  worker = null;
  if (
    typeof Worker === "undefined" ||
    typeof OffscreenCanvas === "undefined" ||
    typeof createImageBitmap !== "function"
  )
    return worker;
  try {
    const w = new Worker(new URL("./encode.worker.ts", import.meta.url), { type: "module", name: "naadi-ink-encode" });
    w.onmessage = (e: MessageEvent<{ id: number; blob: Blob | null }>) =>
      inkProbe.time(() => {
        const done = pending.get(e.data.id);
        pending.delete(e.data.id);
        done?.(e.data.blob);
      });
    w.onerror = () => {
      // The worker could not start (e.g. blocked): fail what is pending and use toBlob from now on.
      for (const done of pending.values()) done(null);
      pending.clear();
      worker = null;
    };
    worker = w;
  } catch {
    worker = null;
  }
  return worker;
}

function toBlob(canvas: HTMLCanvasElement): Promise<Blob | null> {
  return new Promise((resolve) => canvas.toBlob((b) => inkProbe.time(() => resolve(b)), "image/png"));
}

export async function encodePng(canvas: HTMLCanvasElement): Promise<Blob | null> {
  const w = getWorker();
  if (!w) return toBlob(canvas);
  let bitmap: ImageBitmap;
  try {
    bitmap = await createImageBitmap(canvas);
  } catch {
    return toBlob(canvas);
  }
  const id = nextId++;
  const blob = await new Promise<Blob | null>((resolve) => {
    pending.set(id, resolve);
    w.postMessage({ id, bitmap }, [bitmap]);
  });
  return blob ?? toBlob(canvas);
}
