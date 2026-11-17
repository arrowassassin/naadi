import { type Command, type FromFrame, NS, parseFromFrame, type ToFrame } from "./bridge";

/** What the app sees of the player: the same few verbs the IFrame API has, carried over the bridge. */
export interface BridgePlayer {
  play(): void;
  pause(): void;
  seek(seconds: number): void;
  setRate(rate: number): void;
  /** The last position the frame reported, in seconds. */
  currentTime(): number;
  /** The last player state the frame reported (1 playing, 2 paused, 0 ended, -1 unstarted, 3 buffering). */
  state(): number;
  destroy(): void;
}

export interface BridgeHandlers {
  onReady(rates: number[], rate: number): void;
  onState(state: number): void;
  onTime(seconds: number, state: number): void;
  onError(code: number): void;
  onApiFailed(): void;
}

export const STATE_PLAYING = 1;

/**
 * Connect to the sandboxed player page in `frame`. The frame says `hello` when it is ready to listen; the app then
 * sends `init` with the video id. Every incoming message must come from this frame's window, from the opaque origin
 * (`"null"`) and have a valid shape (bridge.ts); anything else is ignored.
 */
export function connectPlayer(
  frame: HTMLIFrameElement,
  init: { videoId: string; start: number | null },
  h: BridgeHandlers,
): BridgePlayer {
  let time = init.start ?? 0;
  let state = -1;
  let live = true;

  // The frame has an opaque origin, so `*` is the only possible target; nothing secret is ever sent.
  const post = (msg: ToFrame) => frame.contentWindow?.postMessage(msg, "*");
  const command = (c: Command) => {
    if (live) post({ ns: NS, type: "cmd", ...c });
  };

  const onMessage = (event: MessageEvent) => {
    if (!live || event.source !== frame.contentWindow || event.origin !== "null") return;
    const msg: FromFrame | null = parseFromFrame(event.data);
    if (!msg) return;
    switch (msg.type) {
      case "hello":
        post({
          ns: NS,
          type: "init",
          videoId: init.videoId,
          start: init.start,
          parentOrigin: window.location.origin,
        });
        break;
      case "ready":
        h.onReady(msg.rates, msg.rate);
        break;
      case "state":
        state = msg.state;
        h.onState(msg.state);
        break;
      case "time":
        time = msg.t;
        state = msg.state;
        h.onTime(msg.t, msg.state);
        break;
      case "error":
        h.onError(msg.code);
        break;
      case "api_failed":
        h.onApiFailed();
        break;
    }
  };
  window.addEventListener("message", onMessage);

  return {
    play: () => command({ cmd: "play" }),
    pause: () => command({ cmd: "pause" }),
    seek: (seconds) => {
      time = seconds;
      command({ cmd: "seek", value: Math.max(0, seconds) });
    },
    setRate: (rate) => command({ cmd: "rate", value: rate }),
    currentTime: () => time,
    state: () => state,
    destroy: () => {
      live = false;
      window.removeEventListener("message", onMessage);
    },
  };
}
