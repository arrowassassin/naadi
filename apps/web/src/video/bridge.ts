/**
 * The typed postMessage protocol between the app and the sandboxed YouTube player page
 * (sandbox/youtube.html, src/sandbox/youtube.ts). Both sides import this file, so the shapes cannot drift, and
 * both validate every incoming message by source window and shape before acting on it.
 *
 * Why a bridge: the official IFrame Player API is a third-party script. It must not run in the app's own origin,
 * where it could read the signed-in session, so it runs in a frame with an opaque origin (no `allow-same-origin`)
 * and the app talks to it only through these messages: commands in (play, pause, seek, rate), events out (ready,
 * state, time, error).
 */
export const NS = "naadi:yt" as const;
export const SANDBOX_URL = "/sandbox/youtube.html";
export const SANDBOX_ATTR = "allow-scripts allow-presentation";

export type Command =
  | { cmd: "play" }
  | { cmd: "pause" }
  | { cmd: "seek"; value: number }
  | { cmd: "rate"; value: number };

export type ToFrame =
  | { ns: typeof NS; type: "init"; videoId: string; start: number | null; parentOrigin: string }
  | ({ ns: typeof NS; type: "cmd" } & Command);

export type FromFrame =
  | { ns: typeof NS; type: "hello" }
  | { ns: typeof NS; type: "ready"; rates: number[]; rate: number }
  | { ns: typeof NS; type: "state"; state: number }
  | { ns: typeof NS; type: "time"; t: number; state: number }
  | { ns: typeof NS; type: "error"; code: number }
  | { ns: typeof NS; type: "api_failed" };

const VIDEO_ID = /^[A-Za-z0-9_-]{11}$/;
const MAX_SECONDS = 86_400 * 2;

function isObj(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}
function num(v: unknown, min: number, max: number): v is number {
  return typeof v === "number" && Number.isFinite(v) && v >= min && v <= max;
}

/** A message the app sent to the frame, or null for anything else. */
export function parseToFrame(data: unknown): ToFrame | null {
  if (!isObj(data) || data.ns !== NS) return null;
  if (data.type === "init") {
    if (typeof data.videoId !== "string" || !VIDEO_ID.test(data.videoId)) return null;
    if (typeof data.parentOrigin !== "string" || data.parentOrigin.length > 200) return null;
    if (data.start !== null && !num(data.start, 0, MAX_SECONDS)) return null;
    return { ns: NS, type: "init", videoId: data.videoId, start: data.start, parentOrigin: data.parentOrigin };
  }
  if (data.type !== "cmd") return null;
  if (data.cmd === "play" || data.cmd === "pause") return { ns: NS, type: "cmd", cmd: data.cmd };
  if (data.cmd === "seek" && num(data.value, 0, MAX_SECONDS))
    return { ns: NS, type: "cmd", cmd: "seek", value: data.value };
  if (data.cmd === "rate" && num(data.value, 0.0625, 16))
    return { ns: NS, type: "cmd", cmd: "rate", value: data.value };
  return null;
}

/** A message the frame sent to the app, or null for anything else. */
export function parseFromFrame(data: unknown): FromFrame | null {
  if (!isObj(data) || data.ns !== NS) return null;
  switch (data.type) {
    case "hello":
      return { ns: NS, type: "hello" };
    case "api_failed":
      return { ns: NS, type: "api_failed" };
    case "ready": {
      if (!Array.isArray(data.rates) || data.rates.length > 32 || !data.rates.every((r) => num(r, 0.0625, 16)))
        return null;
      if (!num(data.rate, 0.0625, 16)) return null;
      return { ns: NS, type: "ready", rates: data.rates as number[], rate: data.rate };
    }
    case "state":
      return num(data.state, -1, 5) ? { ns: NS, type: "state", state: data.state } : null;
    case "time":
      return num(data.t, 0, MAX_SECONDS) && num(data.state, -1, 5)
        ? { ns: NS, type: "time", t: data.t, state: data.state }
        : null;
    case "error":
      return num(data.code, 0, 1000) ? { ns: NS, type: "error", code: data.code } : null;
    default:
      return null;
  }
}
