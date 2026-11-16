import { roundSu } from "@naadi/shared";

/**
 * Surface coordinates. The surface's content-box width is 1 su on both axes, so ink scales uniformly
 * with the surface (resize, rotation, zoom) and keeps its aspect ratio.
 */
export interface SurfaceFrame {
  /** Viewport (client) coordinates of the content box's top-left corner, CSS px. */
  left: number;
  top: number;
  /** Content-box size, CSS px. */
  width: number;
  height: number;
}

export function clientToSurface(frame: SurfaceFrame, clientX: number, clientY: number): [number, number] {
  const w = frame.width > 0 ? frame.width : 1;
  return [roundSu((clientX - frame.left) / w), roundSu((clientY - frame.top) / w)];
}

export function surfaceToClient(frame: SurfaceFrame, x: number, y: number): [number, number] {
  return [frame.left + x * frame.width, frame.top + y * frame.width];
}

/** CSS px inside the surface (origin at the content box's top-left) → su. */
export function pxToSu(width: number, px: number): number {
  return width > 0 ? px / width : 0;
}

export function suToPx(width: number, su: number): number {
  return su * width;
}

/** Height of the surface in su (its aspect ratio). */
export function surfaceHeightSu(frame: SurfaceFrame): number {
  return frame.width > 0 ? frame.height / frame.width : 0;
}
