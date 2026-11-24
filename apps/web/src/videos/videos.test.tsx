import type { VideoOut, VideosOut, VideosOverview, VideoTopicsOut } from "@naadi/shared";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  RouterProvider,
} from "@tanstack/react-router";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { registerAssistantAsk } from "../assistant/ask";
import { getAssistantUi, resetAssistantUiForTests } from "../assistant/store";
import { ApiError } from "../auth/apiError";
import { currentVideoPosition } from "../video/playback";
import { patchWatched } from "./api";
import { FileVideo } from "./FileVideo";
import { parseVideosSearch } from "./search";
import { relatedVideos } from "./VideoPlayerPane";
import { VideosPage } from "./VideosPage";

// The SDK is the boundary (the API contract is covered by the API's db tests): each call is a fake here.
const sdk = vi.hoisted(() => ({
  listVideos: vi.fn(),
  getVideosOverview: vi.fn(),
  listVideoTopics: vi.fn(),
  markVideoWatched: vi.fn(),
  unmarkVideoWatched: vi.fn(),
}));
vi.mock("@naadi/shared", async (orig) => ({ ...(await orig<typeof import("@naadi/shared")>()), ...sdk }));
vi.mock("../auth/api", () => ({
  authed: (make: (h: Headers) => Promise<unknown>) => make(new Headers()),
  useMe: () => ({ data: undefined }),
}));
vi.mock("../auth/session", () => ({
  useAuth: () => ({ status: "signed-in", session: { user: { id: "u1" } }, config: { ui_copy: null } }),
}));

function video(over: Partial<VideoOut> & Pick<VideoOut, "media_id">): VideoOut {
  return {
    provider: "youtube",
    youtube_id: "-0CXs9L9O0A",
    file_url: null,
    page_url: null,
    licence: null,
    title: "Ventricular Pressure-Volume Loops",
    channel_id: "UCZaDAUF7UEcRXIFvGZu3O9Q",
    channel_name: "Dirty Medicine",
    duration_s: 754,
    style: "sketch",
    subject: "Physiology",
    step: "step1",
    core: true,
    topics: [
      { id: "ventricular-pressure-volume-loops", title: "Ventricular Pressure-Volume Loops", chapter: "Cardiac" },
    ],
    attribution: null,
    watched: false,
    ...over,
  };
}

const LOOPS = video({ media_id: "yt--0cxs9l9o0a" });
const LOOPS_2 = video({
  media_id: "yt-pv-2",
  youtube_id: "abcdefghijk",
  title: "Pressure-volume loops, worked",
  channel_name: "Ninja Nerd",
  style: "whiteboard",
  core: false,
});
const STARLING = video({
  media_id: "yt-starling",
  youtube_id: "bcdefghijkl",
  title: "Frank-Starling curve",
  topics: [{ id: "frank-starling", title: "Frank-Starling", chapter: "Cardiac" }],
});
const CHOLE = video({
  media_id: "wm-acute-cholecystitis",
  provider: "commons",
  youtube_id: null,
  file_url: "https://upload.wikimedia.org/wikipedia/commons/0/0f/Acute_cholecystitis.webm",
  page_url: "https://commons.wikimedia.org/wiki/File:Acute_cholecystitis.webm",
  licence: {
    license_short_name: "CC BY-SA 4.0",
    license_url: "https://creativecommons.org/licenses/by-sa/4.0",
    artist: "Osmosis",
  },
  title: "Acute Cholecystitis & Acute Cholangitis SOAP",
  channel_id: null,
  channel_name: "Wikimedia Commons",
  duration_s: 437,
  style: "animation",
  subject: "Internal Medicine",
  step: "step2ck",
  topics: [{ id: "acute-cholecystitis", title: "Acute cholecystitis", chapter: "Biliary" }],
  attribution: '"Acute cholecystitis.webm" by Osmosis, via Wikimedia Commons, CC BY-SA 4.0',
});

const OVERVIEW: VideosOverview = {
  subjects: [
    {
      step: "step1",
      subject: "Physiology",
      videos: 190,
      watched: 12,
      topics: 80,
      topics_direct: 60,
      topics_chapter: 15,
    },
    {
      step: "step2ck",
      subject: "Internal Medicine",
      videos: 215,
      watched: 3,
      topics: 90,
      topics_direct: 70,
      topics_chapter: 10,
    },
  ],
  channels: [
    { id: "UCZaDAUF7UEcRXIFvGZu3O9Q", name: "Dirty Medicine", videos: 120 },
    { id: "commons", name: "Wikimedia Commons", videos: 102 },
  ],
};

const page = (items: VideoOut[], next: string | null = null, total = items.length): VideosOut => ({
  items,
  next,
  total,
});

function renderPage(qs = "") {
  const root = createRootRoute({ component: Outlet });
  const route = createRoute({
    getParentRoute: () => root,
    path: "/videos",
    validateSearch: parseVideosSearch,
    component: () => <VideosPage search={route.useSearch()} />,
  });
  const router = createRouter({
    routeTree: root.addChildren([route]),
    history: createMemoryHistory({ initialEntries: [`/videos${qs}`] }),
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

const lastQuery = () => sdk.listVideos.mock.calls.at(-1)?.[0]?.query as Record<string, unknown>;

beforeEach(() => {
  for (const f of Object.values(sdk)) f.mockReset();
  sdk.getVideosOverview.mockResolvedValue(OVERVIEW);
  sdk.listVideos.mockImplementation(async ({ query }: { query: Record<string, unknown> }) =>
    query.limit === 200 ? page([LOOPS, LOOPS_2, STARLING]) : page([LOOPS, CHOLE], null, 2),
  );
  sdk.listVideoTopics.mockResolvedValue({ items: [] } satisfies VideoTopicsOut);
  sdk.markVideoWatched.mockResolvedValue(undefined);
  sdk.unmarkVideoWatched.mockResolvedValue(undefined);
  resetAssistantUiForTests();
});

describe("URL state", () => {
  it("keeps known values and drops the rest", () => {
    expect(
      parseVideosSearch({
        step: "step2ck",
        style: "sketch",
        core: 1,
        unwatched: "1",
        view: "topics",
        v: "yt-a",
        q: " mi ",
      }),
    ).toEqual({
      step: "step2ck",
      style: "sketch",
      core: 1,
      unwatched: 1,
      view: "topics",
      v: "yt-a",
      q: "mi",
      subject: undefined,
      channel: undefined,
    });
    const bad = parseVideosSearch({ step: "step3", style: "comic", core: 0, view: "grid", v: "<script>", q: "  " });
    expect(Object.values(bad).every((x) => x === undefined)).toBe(true);
  });
});

describe("Videos screen", () => {
  it("shows a skeleton while loading, then videos grouped by subject with the quiet count", async () => {
    let release: (v: VideosOut) => void = () => undefined;
    sdk.listVideos.mockImplementationOnce(() => new Promise<VideosOut>((r) => (release = r)));
    renderPage();
    expect(await screen.findByRole("heading", { level: 1 })).toBeVisible();
    expect(document.querySelector("[aria-busy=true]")).not.toBeNull();
    await act(async () => release(page([LOOPS, CHOLE])));
    expect(await screen.findByRole("heading", { name: "Physiology · Step 1" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Internal Medicine · Step 2 CK" })).toBeVisible();
    expect(screen.getByText("15 of 405 marked watched")).toBeVisible();
    const img = document.querySelector<HTMLImageElement>(`[data-video="${LOOPS.media_id}"] img`);
    expect(img?.src).toBe("https://i.ytimg.com/vi/-0CXs9L9O0A/hqdefault.jpg");
    expect(img?.getAttribute("loading")).toBe("lazy");
    // the Commons card has a neutral poster, never a remote image
    expect(document.querySelector(`[data-video="${CHOLE.media_id}"] img`)).toBeNull();
    expect(lastQuery()).toMatchObject({ step: "all", limit: 48, cursor: null, core: false, unwatched: false });
  });

  it("shows the error with a retry, and an empty state", async () => {
    sdk.listVideos.mockRejectedValueOnce(new ApiError("busy", 429, "rate_limited", "req-1"));
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent("Too many requests just now");
    expect(screen.getByText("Request ID req-1")).toBeVisible();
    sdk.listVideos.mockResolvedValueOnce(page([]));
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("No videos here yet")).toBeVisible();
  });

  it("puts step, subject, filters and the typed query in the URL and the request", async () => {
    const router = renderPage();
    await screen.findByRole("heading", { name: "Physiology · Step 1" });
    await userEvent.click(screen.getByRole("button", { name: "Step 2 CK" }));
    await waitFor(() => expect(router.state.location.search).toMatchObject({ step: "step2ck" }));
    await userEvent.click(screen.getByRole("button", { name: "Internal Medicine" }));
    await userEvent.click(screen.getByRole("button", { name: "Sketch" }));
    await userEvent.click(screen.getByRole("button", { name: "Not watched yet" }));
    await userEvent.click(screen.getByRole("button", { name: "Core only" }));
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Channel" }), "commons");
    await userEvent.type(screen.getByRole("searchbox", { name: "Search videos" }), "gallbladder");
    await waitFor(() =>
      expect(router.state.location.search).toMatchObject({
        step: "step2ck",
        subject: "Internal Medicine",
        style: "sketch",
        unwatched: 1,
        core: 1,
        channel: "commons",
        q: "gallbladder",
      }),
    );
    await waitFor(() =>
      expect(lastQuery()).toMatchObject({
        step: "step2ck",
        subject: "Internal Medicine",
        style: "sketch",
        unwatched: true,
        core: true,
        channel: "commons",
        q: "gallbladder",
      }),
    );
    expect(screen.getByText("3 of 215 marked watched")).toBeVisible();
    expect(screen.getByRole("button", { name: "Sketch" })).toHaveAttribute("aria-pressed", "true");
  });

  it("loads the next page from the cursor", async () => {
    sdk.listVideos.mockResolvedValueOnce(page([LOOPS], "48", 2)).mockResolvedValueOnce(page([CHOLE], null, 2));
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Show more videos" }));
    expect(await screen.findByText("Showing 2 of 2")).toBeVisible();
    expect(lastQuery()).toMatchObject({ cursor: "48" });
    expect(screen.queryByRole("button", { name: "Show more videos" })).toBeNull();
  });

  it("ticks a video off at once and puts it back when the server refuses", async () => {
    let refuse: (e: Error) => void = () => undefined;
    sdk.markVideoWatched.mockImplementationOnce(() => new Promise((_, rej) => (refuse = rej)));
    renderPage();
    const toggle = await screen.findByRole("button", { name: `Mark ${LOOPS.title} as watched` });
    await userEvent.click(toggle);
    const on = screen.getByRole("button", { name: `${LOOPS.title}: watched. Press to unmark` });
    expect(on).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("16 of 405 marked watched")).toBeVisible();
    expect(sdk.markVideoWatched).toHaveBeenCalledWith(expect.objectContaining({ path: { media_id: LOOPS.media_id } }));
    await act(async () => refuse(new ApiError("down", 503, "unavailable", null)));
    expect(await screen.findByRole("button", { name: `Mark ${LOOPS.title} as watched` })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    expect(screen.getByRole("alert")).toHaveTextContent("That change could not be saved");
  });

  it("unmarks a watched video", async () => {
    sdk.listVideos.mockResolvedValue(page([{ ...LOOPS, watched: true }]));
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: `${LOOPS.title}: watched. Press to unmark` }));
    expect(sdk.unmarkVideoWatched).toHaveBeenCalledWith(
      expect.objectContaining({ path: { media_id: LOOPS.media_id } }),
    );
    expect(screen.getByRole("button", { name: `Mark ${LOOPS.title} as watched` })).toBeVisible();
  });

  it("opens the player pane from a card, lists related videos by topic then chapter, and closes", async () => {
    const router = renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /^Ventricular Pressure-Volume Loops/ }));
    await waitFor(() => expect(router.state.location.search).toMatchObject({ v: LOOPS.media_id }));
    const dialog = await screen.findByRole("dialog", { name: LOOPS.title as string });
    expect(within(dialog).getByRole("button", { name: new RegExp(`^Play video: ${LOOPS.title}`) })).toBeVisible();
    expect(within(dialog).getByText("YouTube Terms of Service")).toBeVisible();
    // the one lime action of the pane
    expect(within(dialog).getByRole("button", { name: "Mark watched" })).toHaveAttribute("data-variant", "primary");
    const topic = await within(dialog).findByRole("region", { name: "More on this topic" });
    expect(within(topic).getByText("Pressure-volume loops, worked")).toBeVisible();
    const chapter = within(dialog).getByRole("region", { name: "Related in this chapter" });
    expect(within(chapter).getByText("Frank-Starling curve")).toBeVisible();
    // the related list comes from the subject's catalogue
    expect(sdk.listVideos).toHaveBeenCalledWith(
      expect.objectContaining({ query: expect.objectContaining({ subject: "Physiology", step: "step1", limit: 200 }) }),
    );
    // a related video outside the list opens in the same pane
    await userEvent.click(within(chapter).getByRole("button", { name: /Frank-Starling curve/ }));
    expect(await screen.findByRole("dialog", { name: "Frank-Starling curve" })).toBeVisible();
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Close video" }));
    await waitFor(() => expect(router.state.location.search.v).toBeUndefined());
  });

  it("asks the assistant about the open video only while the assistant can answer", async () => {
    renderPage(`?v=${LOOPS.media_id}`);
    const dialog = await screen.findByRole("dialog", { name: LOOPS.title as string });
    expect(within(dialog).queryByRole("button", { name: "Ask Naadi about this video" })).toBeNull();
    const off = registerAssistantAsk(() => undefined);
    try {
      const ask = await within(dialog).findByRole("button", { name: "Ask Naadi about this video" });
      expect(ask).toHaveAttribute("data-variant", "assistant");
      await userEvent.click(ask);
      expect(getAssistantUi().open).toBe(true);
      expect(getAssistantUi().draft).toContain(LOOPS.title);
    } finally {
      off();
    }
  });

  it("opens a shared link to a video outside the list from its subject's catalogue", async () => {
    const vs = encodeURIComponent(JSON.stringify({ step: "step1", subject: "Physiology" }));
    renderPage(`?v=${STARLING.media_id}&vs=${vs}`);
    expect(await screen.findByRole("dialog", { name: "Frank-Starling curve" })).toBeVisible();
  });

  it("says so when the URL names a video the list does not hold", async () => {
    renderPage("?v=yt-unknown");
    expect(await screen.findByRole("dialog", { name: "Video not in this list" })).toBeVisible();
  });

  it("shows topic coverage in words and an icon, and jumps to a topic's videos", async () => {
    sdk.listVideoTopics.mockResolvedValue({
      items: [
        {
          id: "a",
          title: "Inguinal Canal",
          subject: "Anatomy",
          step: "step1",
          chapter: "Abdominal Wall",
          unit: "Abdomen",
          core: true,
          coverage: "topic",
          media_ids: ["yt-1"],
          chapter_media_ids: [],
        },
        {
          id: "b",
          title: "Intraperitoneal Organs",
          subject: "Anatomy",
          step: "step1",
          chapter: "Abdominal Overview",
          unit: "Abdomen",
          core: true,
          coverage: "chapter",
          media_ids: [],
          chapter_media_ids: ["yt-2"],
        },
        {
          id: "c",
          title: "Hernia repair types",
          subject: "Anatomy",
          step: "step1",
          chapter: null,
          unit: null,
          core: false,
          coverage: "gap",
          media_ids: [],
          chapter_media_ids: [],
        },
      ],
    } satisfies VideoTopicsOut);
    const router = renderPage("?view=topics");
    expect(await screen.findByText("Has videos")).toBeVisible();
    expect(screen.getByText("Chapter videos only")).toBeVisible();
    expect(screen.getByText("No video yet")).toBeVisible();
    expect(screen.getByText("1 of 3 topics have videos")).toBeVisible();
    expect(document.querySelector("[data-coverage=gap] svg")).not.toBeNull();
    expect(screen.getAllByRole("button", { name: /^Show videos for/ })).toHaveLength(2);
    await userEvent.click(screen.getByRole("button", { name: "Show videos for Intraperitoneal Organs" }));
    await waitFor(() =>
      expect(router.state.location.search).toMatchObject({
        subject: "Anatomy",
        step: "step1",
        q: "Abdominal Overview",
      }),
    );
    expect(router.state.location.search.view).toBeUndefined();
  });
});

describe("FileVideo", () => {
  it("loads nothing until pressed, then plays the file natively and publishes its position", async () => {
    const play = vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
    render(
      <FileVideo
        mediaId={CHOLE.media_id}
        src={CHOLE.file_url as string}
        title="Acute cholecystitis"
        durationS={437}
        attribution={CHOLE.attribution}
        licence={CHOLE.licence}
        pageUrl={CHOLE.page_url}
      />,
    );
    expect(document.querySelector("video")).toBeNull();
    expect(screen.getByText("7:17 · Loads the file from Wikimedia Commons")).toBeVisible();
    expect(screen.getByText(CHOLE.attribution as string)).toBeVisible();
    expect(screen.getByRole("link", { name: "CC BY-SA 4.0" })).toHaveAttribute("href", CHOLE.licence?.license_url);
    expect(screen.getByRole("link", { name: "File page on Wikimedia Commons" })).toHaveAttribute(
      "href",
      CHOLE.page_url,
    );
    await userEvent.click(screen.getByRole("button", { name: /^Play video: Acute cholecystitis/ }));
    const v = document.querySelector("video") as HTMLVideoElement;
    expect(v.getAttribute("src")).toBe(CHOLE.file_url);
    expect(v.getAttribute("preload")).toBe("none");
    expect(v.hasAttribute("playsinline")).toBe(true);
    expect(v.hasAttribute("controls")).toBe(true);
    expect(play).toHaveBeenCalled();
    Object.defineProperty(v, "currentTime", { value: 61.7, configurable: true });
    fireEvent.timeUpdate(v);
    expect(currentVideoPosition()).toEqual({ media_id: CHOLE.media_id, t: 61 });
    play.mockRestore();
  });

  it("refuses a source that is not https", () => {
    render(
      <FileVideo
        mediaId="x"
        src="javascript:alert(1)"
        title="x"
        durationS={null}
        attribution={null}
        licence={null}
        pageUrl={null}
      />,
    );
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByText("This video is not available right now.")).toBeVisible();
  });
});

describe("pure helpers", () => {
  it("relates by direct topic first, then by chapter, never the video itself", () => {
    const other = video({ media_id: "yt-x", topics: [{ id: "x", title: "X", chapter: "Renal" }] });
    expect(relatedVideos(LOOPS, [LOOPS, STARLING, LOOPS_2, other])).toEqual({ topic: [LOOPS_2], chapter: [STARLING] });
  });

  it("patches a video in list pages and the overview count, and nothing else", () => {
    const change = { video: LOOPS, watched: true };
    const inf = { pages: [page([LOOPS, CHOLE])], pageParams: [null] };
    const out = patchWatched(inf, change) as typeof inf;
    expect(out.pages[0]?.items.map((v) => v.watched)).toEqual([true, false]);
    const ov = patchWatched(OVERVIEW, change) as VideosOverview;
    expect(ov.subjects.map((s) => s.watched)).toEqual([13, 3]);
    const topics: VideoTopicsOut = { items: [] };
    expect(patchWatched(topics, change)).toBe(topics);
  });
});
