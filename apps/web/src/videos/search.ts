import type { Step, Style } from "./api";

/** The Videos screen's state lives in the URL (back button, shared links); absent = the default. */
export interface VideosSearch {
  step?: Step;
  subject?: string;
  q?: string;
  channel?: string;
  style?: Style;
  core?: 1;
  unwatched?: 1;
  view?: "topics";
  /** The open video's media id. */
  v?: string;
}

export const STEPS: readonly Step[] = ["step1", "step2ck"];
export const STYLES: readonly Style[] = ["sketch", "whiteboard", "animation", "lecture"];
const ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;

const text = (x: unknown, max: number): string | undefined =>
  typeof x === "string" && x.trim() ? x.trim().slice(0, max) : undefined;
const flag = (x: unknown): 1 | undefined => (x === 1 || x === "1" || x === true ? 1 : undefined);

export function parseVideosSearch(s: Record<string, unknown>): VideosSearch {
  return {
    step: STEPS.find((x) => x === s.step),
    subject: text(s.subject, 80),
    q: text(s.q, 100),
    channel: text(s.channel, 80),
    style: STYLES.find((x) => x === s.style),
    core: flag(s.core),
    unwatched: flag(s.unwatched),
    view: s.view === "topics" ? "topics" : undefined,
    v: typeof s.v === "string" && ID.test(s.v) ? s.v : undefined,
  };
}
