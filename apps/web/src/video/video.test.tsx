import type { MediaOut } from "@naadi/shared";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ContentRenderer } from "../content/ContentRenderer";
import { formatClock } from "./format";
import { currentVideoPosition, VIDEO_POSITION_EVENT } from "./playback";
import { embedSrc, IFRAME_API_SRC, resetYouTubeApiForTests } from "./youtubeApi";

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

interface Fake {
  time: number;
  rate: number;
  seeks: number[];
  played: number;
  onReady?: () => void;
  destroyed: boolean;
}
let fake: Fake;

function installApi(): void {
  window.YT = {
    Player: class {
      constructor(_el: unknown, opts: { events?: { onReady?: () => void } }) {
        fake.onReady = opts.events?.onReady;
      }
      playVideo() {
        fake.played++;
      }
      pauseVideo() {}
      seekTo(s: number) {
        fake.seeks.push(s);
        fake.time = s;
      }
      getCurrentTime() {
        return fake.time;
      }
      getPlayerState() {
        return 1;
      }
      setPlaybackRate(r: number) {
        fake.rate = r;
      }
      getPlaybackRate() {
        return fake.rate;
      }
      getAvailablePlaybackRates() {
        return [0.5, 1, 1.5, 2];
      }
      destroy() {
        fake.destroyed = true;
      }
    },
  } as unknown as typeof window.YT;
}

function doc(...videos: { startS: number | null; endS: number | null }[]) {
  return {
    type: "doc",
    content: videos.map((v) => ({ type: "video", attrs: { mediaId: "yt-abc", ...v } })),
  };
}

function mount(d: object) {
  return render(<ContentRenderer sectionId="s1" mode="prod" media={{ "yt-abc": VID }} doc={d as never} />);
}

beforeEach(() => {
  fake = { time: 0, rate: 1, seeks: [], played: 0, destroyed: false };
  resetYouTubeApiForTests();
  window.YT = undefined;
  document.head.querySelectorAll(`script[src="${IFRAME_API_SRC}"]`).forEach((s) => {
    s.remove();
  });
});
afterEach(() => {
  window.YT = undefined;
});

describe("format", () => {
  it("clock", () => {
    expect(formatClock(0)).toBe("0:00");
    expect(formatClock(754)).toBe("12:34");
    expect(formatClock(3725)).toBe("1:02:05");
  });
  it("embed url is the privacy-enhanced host with the API enabled and no autoplay", () => {
    const u = new URL(embedSrc("abc", "https://x.test", 30));
    expect(u.origin).toBe("https://www.youtube-nocookie.com");
    expect(u.searchParams.get("enablejsapi")).toBe("1");
    expect(u.searchParams.get("origin")).toBe("https://x.test");
    expect(u.searchParams.get("start")).toBe("30");
    expect(u.searchParams.has("autoplay")).toBe(false);
  });
});

describe("YouTube player", () => {
  it("requests nothing from YouTube until the play button is pressed", () => {
    const { container } = mount(doc({ startS: null, endS: null }));
    expect(container.querySelector("iframe")).toBeNull();
    expect(document.head.querySelector(`script[src="${IFRAME_API_SRC}"]`)).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByRole("link", { name: "YouTube Terms of Service" })).toHaveAttribute(
      "href",
      "https://www.youtube.com/t/terms",
    );
    expect(screen.getByText("YouTube")).toBeInTheDocument();
  });

  it("one press loads the API script, then plays once the player is ready", async () => {
    const { container } = mount(doc({ startS: 30, endS: null }));
    fireEvent.click(screen.getByRole("button", { name: /Play video/ }));
    const script = document.head.querySelector<HTMLScriptElement>(`script[src="${IFRAME_API_SRC}"]`);
    expect(script).not.toBeNull();
    const frame = container.querySelector("iframe");
    expect(frame?.getAttribute("referrerpolicy")).toBe("strict-origin-when-cross-origin");
    expect(frame?.getAttribute("src")).toContain("https://www.youtube-nocookie.com/embed/abc_DEF-123");
    installApi();
    act(() => window.onYouTubeIframeAPIReady?.());
    await waitFor(() => expect(fake.onReady).toBeDefined());
    expect(fake.played).toBe(0);
    act(() => fake.onReady?.());
    expect(fake.played).toBe(1);
  });

  it("chapters from the document's video ranges seek on tap; speed uses the rates the player reports", async () => {
    mount(doc({ startS: 0, endS: 60 }, { startS: 90, endS: 150 }));
    fireEvent.click(screen.getAllByRole("button", { name: /Play video/ })[0] as HTMLElement);
    installApi();
    act(() => window.onYouTubeIframeAPIReady?.());
    await waitFor(() => expect(fake.onReady).toBeDefined());
    act(() => fake.onReady?.());
    fireEvent.click((await screen.findAllByRole("button", { name: /^1:30\s*Part/ }))[0] as HTMLElement);
    expect(fake.seeks).toEqual([90]);
    fireEvent.click(screen.getAllByRole("button", { name: "1.5×" })[0] as HTMLElement);
    expect(fake.rate).toBe(1.5);
    expect(screen.getAllByRole("button", { name: "1.5×" })[0]).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByRole("button", { name: "0.75×" })).toBeNull();
  });

  it("publishes the playhead for the assistant and clears it on unmount", async () => {
    const seen: unknown[] = [];
    window.addEventListener(VIDEO_POSITION_EVENT, (e) => seen.push((e as CustomEvent).detail));
    const { unmount } = mount(doc({ startS: null, endS: null }));
    fireEvent.click(screen.getByRole("button", { name: /Play video/ }));
    installApi();
    fake.time = 42.7;
    vi.useFakeTimers();
    act(() => window.onYouTubeIframeAPIReady?.());
    await vi.advanceTimersByTimeAsync(0);
    act(() => fake.onReady?.());
    await vi.advanceTimersByTimeAsync(600);
    expect(currentVideoPosition()).toEqual({ media_id: "yt-abc", t: 42 });
    vi.useRealTimers();
    unmount();
    expect(currentVideoPosition()).toBeNull();
    expect(fake.destroyed).toBe(true);
    expect(seen).toContainEqual({ media_id: "yt-abc", t: 42 });
  });

  it("an API that cannot load leaves the plain player and shows no controls", async () => {
    const { container } = mount(doc({ startS: null, endS: null }));
    fireEvent.click(screen.getByRole("button", { name: /Play video/ }));
    const script = document.head.querySelector<HTMLScriptElement>(`script[src="${IFRAME_API_SRC}"]`);
    act(() => script?.onerror?.(new Event("error")));
    await waitFor(() => expect(document.head.querySelector(`script[src="${IFRAME_API_SRC}"]`)).toBeNull());
    expect(container.querySelector("iframe")).not.toBeNull();
    expect(screen.queryByRole("button", { name: "1×" })).toBeNull();
  });
});
