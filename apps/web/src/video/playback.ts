/**
 * Where the student is in a video, for the assistant's page context (S14).
 *
 * The player calls `publishVideoPosition` while a video plays or pauses. Readers either call
 * `currentVideoPosition()` or listen to the `naadi:video-position` CustomEvent on `window`
 * (`detail: { media_id, t }`, t in whole seconds). `clearVideoPosition` runs when the player unmounts.
 */
export interface VideoPosition {
  media_id: string;
  t: number;
}

export const VIDEO_POSITION_EVENT = "naadi:video-position";
let current: VideoPosition | null = null;

export function currentVideoPosition(): VideoPosition | null {
  return current;
}

export function publishVideoPosition(media_id: string, t: number): void {
  const next = { media_id, t: Math.max(0, Math.floor(t)) };
  if (current && current.media_id === next.media_id && current.t === next.t) return;
  current = next;
  window.dispatchEvent(new CustomEvent<VideoPosition>(VIDEO_POSITION_EVENT, { detail: next }));
}

export function clearVideoPosition(media_id: string): void {
  if (current?.media_id !== media_id) return;
  current = null;
  window.dispatchEvent(new CustomEvent(VIDEO_POSITION_EVENT, { detail: null }));
}
