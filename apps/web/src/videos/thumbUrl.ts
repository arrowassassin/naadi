/** Commons serves a still for a video file at one of its standard thumbnail widths (320 is refused, 330 is served). */
const COMMONS_THUMB_WIDTH = 330;
const COMMONS_FILE = /^(https:\/\/upload\.wikimedia\.org\/wikipedia\/commons)\/([0-9a-f]\/[0-9a-f]{2}\/([^/?#]+))$/;

/** The card thumbnail for a video, derived from its ids; null when there is nothing to show (poster only). */
export function thumbUrl(v: { youtube_id: string | null; file_url: string | null }): string | null {
  if (v.youtube_id) return `https://i.ytimg.com/vi/${encodeURIComponent(v.youtube_id)}/mqdefault.jpg`;
  const m = v.file_url ? COMMONS_FILE.exec(v.file_url) : null;
  return m ? `${m[1]}/thumb/${m[2]}/${COMMONS_THUMB_WIDTH}px--${m[3]}.jpg` : null;
}
