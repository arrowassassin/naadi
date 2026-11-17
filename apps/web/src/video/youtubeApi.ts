/**
 * The official YouTube IFrame Player API, loaded on demand. Nothing here runs until `loadYouTubeApi()` is called,
 * and the player facade calls it only after the student presses play: no script, iframe or image is requested
 * from YouTube before that.
 */
export interface YTPlayer {
  playVideo(): void;
  pauseVideo(): void;
  seekTo(seconds: number, allowSeekAhead: boolean): void;
  getCurrentTime(): number;
  getPlayerState(): number;
  setPlaybackRate(rate: number): void;
  getPlaybackRate(): number;
  getAvailablePlaybackRates(): number[];
  destroy(): void;
}

export interface YTPlayerEvents {
  onReady?: () => void;
  onStateChange?: (e: { data: number }) => void;
  onError?: (e: { data: number }) => void;
}

export interface YTNamespace {
  Player: new (el: HTMLIFrameElement | string, opts: { events?: YTPlayerEvents }) => YTPlayer;
  PlayerState?: { PLAYING: number; PAUSED: number; ENDED: number };
}

declare global {
  interface Window {
    YT?: YTNamespace;
    onYouTubeIframeAPIReady?: () => void;
  }
}

export const IFRAME_API_SRC = "https://www.youtube.com/iframe_api";
export const PLAYER_HOST = "https://www.youtube-nocookie.com";
export const STATE_PLAYING = 1;
const TIMEOUT_MS = 15_000;

let pending: Promise<YTNamespace> | null = null;

export function loadYouTubeApi(): Promise<YTNamespace> {
  if (typeof window === "undefined") return Promise.reject(new Error("no window"));
  if (window.YT?.Player) return Promise.resolve(window.YT);
  if (pending) return pending;
  pending = new Promise<YTNamespace>((resolve, reject) => {
    const previous = window.onYouTubeIframeAPIReady;
    const script = document.createElement("script");
    const fail = () => {
      window.clearTimeout(timer);
      script.remove();
      pending = null;
      reject(new Error("youtube api"));
    };
    const timer = window.setTimeout(fail, TIMEOUT_MS);
    window.onYouTubeIframeAPIReady = () => {
      previous?.();
      window.clearTimeout(timer);
      if (window.YT?.Player) resolve(window.YT);
      else fail();
    };
    script.src = IFRAME_API_SRC;
    script.async = true;
    script.onerror = fail;
    document.head.appendChild(script);
  });
  return pending;
}

/** Test hook: forget a cached load. */
export function resetYouTubeApiForTests(): void {
  pending = null;
}

export function embedSrc(videoId: string, origin: string, start: number | null): string {
  const q = new URLSearchParams({ rel: "0", playsinline: "1", enablejsapi: "1", origin });
  if (start !== null && start > 0) q.set("start", String(start));
  return `${PLAYER_HOST}/embed/${encodeURIComponent(videoId)}?${q.toString()}`;
}
