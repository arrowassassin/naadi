/** Worker: encode an ImageBitmap as PNG with OffscreenCanvas (see encode.ts). */
interface Request {
  id: number;
  bitmap: ImageBitmap;
}

const scope = self as unknown as {
  onmessage: ((e: MessageEvent<Request>) => void) | null;
  postMessage(message: unknown): void;
};

scope.onmessage = async (e) => {
  const { id, bitmap } = e.data;
  try {
    const canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
    const ctx = canvas.getContext("bitmaprenderer");
    if (!ctx) throw new Error("no bitmaprenderer");
    ctx.transferFromImageBitmap(bitmap);
    scope.postMessage({ id, blob: await canvas.convertToBlob({ type: "image/png" }) });
  } catch {
    bitmap.close();
    scope.postMessage({ id, blob: null });
  }
};
