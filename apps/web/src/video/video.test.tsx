import type { MediaOut } from "@naadi/shared";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ContentRenderer } from "../content/ContentRenderer";
import { NS, parseFromFrame, parseToFrame, SANDBOX_ATTR, SANDBOX_URL } from "./bridge";
import { formatClock } from "./format";
import { currentVideoPosition, VIDEO_POSITION_EVENT } from "./playback";

const VID: MediaOut = {
  id: "yt-abc",
  kind: "video_youtube",
  title: "Acid–base",
  caption: null,
  attribution: null,
  source: null,
  url: null,
  youtube_id: "abc_DEF-123",
  duration_s: 600,
  embeddable: true,
  width: null,
  height: null,
} as MediaOut;

function doc(...videos: { startS: number | null; endS: number | null }[]) {
  return {
    type: "doc",
    content: videos.map((v) => ({ type: "video", attrs: { mediaId: "yt-abc", ...v } })),
  };
}

function mount(d: object) {
  return render(<ContentRenderer sectionId="s1" mode="prod" media={{ "yt-abc": VID }} doc={d as never} />);
}

/** The sandboxed frame, played by the test: records what the app posts and posts frame messages back. */
function frameOf(container: HTMLElement) {
  const frame = container.querySelector("iframe") as HTMLIFrameElement;
  const win = frame.contentWindow as Window;
  const sent: unknown[] = [];
  vi.spyOn(win, "postMessage").mockImplementation((m: unknown) => {
    sent.push(m);
  });
  const say = (data: unknown, origin = "null") =>
    act(() => {
      window.dispatchEvent(new MessageEvent("message", { data, origin, source: win }));
    });
  return { frame, sent, say };
}
const from = (type: string, extra: object = {}) => ({ ns: NS, type, ...extra });

describe("format", () => {
  it("clock", () => {
    expect(formatClock(0)).toBe("0:00");
    expect(formatClock(754)).toBe("12:34");
    expect(formatClock(3725)).toBe("1:02:05");
  });
});

describe("bridge messages", () => {
  it("accepts only well-formed messages in each direction", () => {
    expect(
      parseToFrame({ ns: NS, type: "init", videoId: "abc_DEF-123", start: null, parentOrigin: "https://a.test" }),
    ).not.toBeNull();
    expect(parseToFrame({ ns: NS, type: "init", videoId: "../evil", start: null, parentOrigin: "x" })).toBeNull();
    expect(parseToFrame({ ns: NS, type: "cmd", cmd: "seek", value: -1 })).toBeNull();
    expect(parseToFrame({ ns: NS, type: "cmd", cmd: "seek", value: Number.NaN })).toBeNull();
    expect(parseToFrame({ ns: NS, type: "cmd", cmd: "rate", value: 1.5 })).not.toBeNull();
    expect(parseToFrame({ ns: NS, type: "cmd", cmd: "eval", value: 1 })).toBeNull();
    expect(parseToFrame({ ns: "other", type: "cmd", cmd: "play" })).toBeNull();
    expect(parseFromFrame(from("ready", { rates: [1, 2], rate: 1 }))).not.toBeNull();
    expect(parseFromFrame(from("ready", { rates: ["1"], rate: 1 }))).toBeNull();
    expect(parseFromFrame(from("time", { t: 5, state: 1 }))).not.toBeNull();
    expect(parseFromFrame(from("time", { t: "5", state: 1 }))).toBeNull();
    expect(parseFromFrame(from("nope"))).toBeNull();
    expect(parseFromFrame("text")).toBeNull();
    expect(parseFromFrame(null)).toBeNull();
  });
});

describe("YouTube player over the bridge", () => {
  it("requests nothing from YouTube until the play button is pressed", () => {
    const { container } = mount(doc({ startS: null, endS: null }));
    expect(container.querySelector("iframe")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByRole("link", { name: "YouTube Terms of Service" })).toHaveAttribute(
      "href",
      "https://www.youtube.com/t/terms",
    );
    expect(screen.getByText("YouTube")).toBeInTheDocument();
  });

  it("one press adds only the sandboxed same-origin player page; the video id goes over the bridge", () => {
    const { container } = mount(doc({ startS: 30, endS: null }));
    fireEvent.click(screen.getByRole("button", { name: /Play video/ }));
    const frame = container.querySelector("iframe") as HTMLIFrameElement;
    expect(frame.getAttribute("src")).toBe(SANDBOX_URL);
    expect(frame.getAttribute("sandbox")).toBe(SANDBOX_ATTR);
    expect(frame.getAttribute("sandbox")).not.toContain("allow-same-origin");
    expect(container.querySelector('iframe[src*="youtube.com"], iframe[src*="nocookie"]')).toBeNull();
    expect(document.querySelector('script[src*="youtube.com"]')).toBeNull();
    const { sent, say } = frameOf(container);
    say(from("hello"));
    expect(sent).toEqual([
      { ns: NS, type: "init", videoId: "abc_DEF-123", start: 30, parentOrigin: window.location.origin },
    ]);
  });

  it("ignores messages from other windows, other origins and bad shapes", () => {
    const { container } = mount(doc({ startS: null, endS: null }));
    fireEvent.click(screen.getByRole("button", { name: /Play video/ }));
    const { sent, say } = frameOf(container);
    act(() => {
      window.dispatchEvent(new MessageEvent("message", { data: from("hello"), origin: "null", source: window }));
    });
    say(from("hello"), "https://evil.test");
    say({ ns: NS, type: "init" });
    expect(sent).toEqual([]);
  });

  it("chapters seek over the bridge; speed uses the rates the frame reports", async () => {
    const { container } = mount(doc({ startS: 0, endS: 60 }, { startS: 90, endS: 150 }));
    fireEvent.click(screen.getAllByRole("button", { name: /Play video/ })[0] as HTMLElement);
    const { sent, say } = frameOf(container);
    say(from("hello"));
    say(from("ready", { rates: [0.5, 1, 1.5, 2], rate: 1 }));
    sent.length = 0;
    fireEvent.click((await screen.findAllByRole("button", { name: /^1:30\s*Part/ }))[0] as HTMLElement);
    expect(sent).toEqual([
      { ns: NS, type: "cmd", cmd: "seek", value: 90 },
      { ns: NS, type: "cmd", cmd: "play" },
    ]);
    fireEvent.click(screen.getAllByRole("button", { name: "1.5×" })[0] as HTMLElement);
    expect(sent.at(-1)).toEqual({ ns: NS, type: "cmd", cmd: "rate", value: 1.5 });
    expect(screen.getAllByRole("button", { name: "1.5×" })[0]).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByRole("button", { name: "0.75×" })).toBeNull();
  });

  it("publishes the playhead for the assistant and clears it on unmount", () => {
    const seen: unknown[] = [];
    window.addEventListener(VIDEO_POSITION_EVENT, (e) => seen.push((e as CustomEvent).detail));
    const { container, unmount } = mount(doc({ startS: null, endS: null }));
    fireEvent.click(screen.getByRole("button", { name: /Play video/ }));
    const { say } = frameOf(container);
    say(from("ready", { rates: [1], rate: 1 }));
    say(from("time", { t: 42.7, state: 1 }));
    expect(currentVideoPosition()).toEqual({ media_id: "yt-abc", t: 42 });
    unmount();
    expect(currentVideoPosition()).toBeNull();
    expect(seen).toContainEqual({ media_id: "yt-abc", t: 42 });
  });

  it("pauses at the end of a range", () => {
    const { container } = mount(doc({ startS: 0, endS: 60 }));
    fireEvent.click(screen.getByRole("button", { name: /Play video/ }));
    const { sent, say } = frameOf(container);
    say(from("ready", { rates: [1], rate: 1 }));
    say(from("time", { t: 61, state: 1 }));
    expect(sent).toContainEqual({ ns: NS, type: "cmd", cmd: "pause" });
  });

  it("a player that cannot load or errors shows the message and a link, and no controls", () => {
    const { container } = mount(doc({ startS: null, endS: null }));
    fireEvent.click(screen.getByRole("button", { name: /Play video/ }));
    const { say } = frameOf(container);
    say(from("api_failed"));
    expect(screen.queryByRole("button", { name: "1×" })).toBeNull();
    say(from("error", { code: 153 }));
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Watch on YouTube" })).toHaveAttribute(
      "href",
      "https://www.youtube.com/watch?v=abc_DEF-123",
    );
  });
});
