import type { PxRect } from "@naadi/ink";

/** The biggest side of the picture sent with "Ask about this" (the assistant reads a region, not a poster). */
export const MAX_CROP_SIDE = 1600;

export interface CropBox {
  sx: number;
  sy: number;
  sw: number;
  sh: number;
  dw: number;
  dh: number;
}

/**
 * Where a lasso region (CSS px relative to the page surface) lies in the page canvas's own pixels, clamped to the
 * page, and the size of the output picture (at most MAX_CROP_SIDE on its longer side). Null for an empty region.
 */
export function cropBox(rect: PxRect, canvas: { width: number; height: number }, cssWidth: number): CropBox | null {
  if (cssWidth <= 0 || canvas.width <= 0 || canvas.height <= 0) return null;
  const k = canvas.width / cssWidth;
  const sx = Math.max(0, Math.floor(rect.left * k));
  const sy = Math.max(0, Math.floor(rect.top * k));
  const ex = Math.min(canvas.width, Math.ceil((rect.left + rect.width) * k));
  const ey = Math.min(canvas.height, Math.ceil((rect.top + rect.height) * k));
  const sw = ex - sx;
  const sh = ey - sy;
  if (sw < 1 || sh < 1) return null;
  const fit = Math.min(1, MAX_CROP_SIDE / Math.max(sw, sh));
  return { sx, sy, sw, sh, dw: Math.max(1, Math.round(sw * fit)), dh: Math.max(1, Math.round(sh * fit)) };
}

/**
 * A PNG of the circled part of the rendered page with the student's selected ink drawn over it (the engine's
 * picture holds the ink only). Null when nothing can be cut out (the page is not rendered yet).
 */
export async function cropRegion(page: HTMLCanvasElement, rect: PxRect, ink: Blob | null): Promise<Blob | null> {
  const box = cropBox(rect, page, page.clientWidth);
  if (!box) return null;
  const out = document.createElement("canvas");
  out.width = box.dw;
  out.height = box.dh;
  const ctx = out.getContext("2d");
  if (!ctx) return null;
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, box.dw, box.dh);
  ctx.drawImage(page, box.sx, box.sy, box.sw, box.sh, 0, 0, box.dw, box.dh);
  if (ink) {
    try {
      const bitmap = await createImageBitmap(ink);
      ctx.drawImage(bitmap, 0, 0, box.dw, box.dh);
      bitmap.close();
    } catch {
      // The page region alone still says what was circled.
    }
  }
  return new Promise((resolve) => out.toBlob((b) => resolve(b), "image/png"));
}
