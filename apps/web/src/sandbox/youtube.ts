/*
 * The YouTube player page, loaded only inside <iframe sandbox="allow-scripts allow-presentation"> (an opaque
 * origin: no cookies, no storage, no way to read the app's session) and served with its own CSP, the only place
 * that allows https://www.youtube.com scripts. It runs the official IFrame Player API, unmodified, and talks to the
 * app through the typed messages in src/video/bridge.ts. Nothing here runs until the student has pressed play:
 * the app adds this frame only then.
 */
import { type FromFrame, NS, parseToFrame, type ToFrame } from "../video/bridge";

interface Player {
  playVideo(): void;
  pauseVideo(): void;
  seekTo(seconds: number, allowSeekAhead: boolean): void;
  setPlaybackRate(rate: number): void;
  getPlaybackRate(): number;
  getAvailablePlaybackRates(): number[];
  getCurrentTime(): number;
  getPlayerState(): number;
}
interface YTNamespace {
  Player: new (
    el: string,
    opts: {
      videoId: string;
      host: string;
      width: string;
      height: string;
      playerVars: Record<string, string | number>;
      events: {
        onReady: () => void;
        onStateChange: (e: { data: number }) => void;
        onError: (e: { data: number }) => void;
      };
    },
  ) => Player;
}
declare global {
  interface Window {
    YT?: YTNamespace;
    onYouTubeIframeAPIReady?: () => void;
  }
}

const API = "https://www.youtube.com/iframe_api";
const HOST = "https://www.youtube-nocookie.com";
const TICK_MS = 500;
const API_TIMEOUT_MS = 15_000;

let parentOrigin: string | null = null;
let player: Player | null = null;
let started = false;

function send(msg: FromFrame, toOrigin: string | null = parentOrigin): void {
  // Before `init` the app's origin is unknown: the only message is `hello`, which carries nothing.
  window.parent.postMessage(msg, toOrigin ?? "*");
}

function loadApi(): Promise<YTNamespace> {
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => reject(new Error("timeout")), API_TIMEOUT_MS);
    window.onYouTubeIframeAPIReady = () => {
      window.clearTimeout(timer);
      if (window.YT?.Player) resolve(window.YT);
      else reject(new Error("no player"));
    };
    const script = document.createElement("script");
    script.src = API;
    script.async = true;
    script.onerror = () => reject(new Error("load"));
    document.head.append(script);
  });
}

async function start(msg: Extract<ToFrame, { type: "init" }>): Promise<void> {
  let YT: YTNamespace;
  try {
    YT = await loadApi();
  } catch {
    send({ ns: NS, type: "api_failed" });
    return;
  }
  const playerVars: Record<string, string | number> = { rel: 0, playsinline: 1, controls: 1 };
  if (msg.start !== null && msg.start > 0) playerVars.start = Math.floor(msg.start);
  const p: Player = new YT.Player("player", {
    videoId: msg.videoId,
    host: HOST,
    width: "100%",
    height: "100%",
    playerVars,
    events: {
      onReady: () => {
        player = p;
        let rates = [1];
        let rate = 1;
        try {
          rates = p.getAvailablePlaybackRates();
          rate = p.getPlaybackRate();
        } catch {
          // keep the defaults
        }
        send({ ns: NS, type: "ready", rates, rate });
        p.playVideo();
        window.setInterval(() => {
          try {
            send({ ns: NS, type: "time", t: Math.max(0, p.getCurrentTime()), state: p.getPlayerState() });
          } catch {
            // the player is gone
          }
        }, TICK_MS);
      },
      onStateChange: (e) => send({ ns: NS, type: "state", state: e.data }),
      onError: (e) => send({ ns: NS, type: "error", code: e.data }),
    },
  });
}

window.addEventListener("message", (event: MessageEvent) => {
  if (event.source !== window.parent) return;
  const msg = parseToFrame(event.data);
  if (!msg) return;
  if (msg.type === "init") {
    if (started || event.origin !== msg.parentOrigin) return;
    started = true;
    parentOrigin = msg.parentOrigin;
    void start(msg);
    return;
  }
  if (!player || event.origin !== parentOrigin) return;
  if (msg.cmd === "play") player.playVideo();
  else if (msg.cmd === "pause") player.pauseVideo();
  else if (msg.cmd === "seek") player.seekTo(msg.value, true);
  else player.setPlaybackRate(msg.value);
});

send({ ns: NS, type: "hello" });
