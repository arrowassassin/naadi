import { act, render, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import {
  getPageContext,
  type PageContextRegistration,
  resetPageContextForTests,
  toPageContextIn,
  usePageContext,
  useRegisterPageContext,
} from "./pageContext";
import { pageContextFor } from "./useTutor";

function Page({ ctx }: { ctx: PageContextRegistration | null }) {
  useRegisterPageContext(ctx);
  return null;
}

afterEach(() => resetPageContextForTests());

describe("page context registry", () => {
  it("the latest mounted registration is current; unmounting restores the previous one", () => {
    const { result } = renderHook(() => usePageContext());
    expect(result.current).toBeNull();
    const outer = render(<Page ctx={{ type: "today" }} />);
    expect(result.current?.type).toBe("today");
    const inner = render(<Page ctx={{ type: "section", id: "s1", title: "Anion gap", chapter_id: "ch05" }} />);
    expect(result.current).toMatchObject({ type: "section", id: "s1" });
    act(() => inner.unmount());
    expect(result.current?.type).toBe("today");
    act(() => outer.unmount());
    expect(getPageContext()).toBeNull();
  });

  it("re-registers when what it publishes changes", () => {
    const { rerender } = render(<Page ctx={{ type: "flashcard", id: "c1" }} />);
    expect(getPageContext()?.id).toBe("c1");
    rerender(<Page ctx={{ type: "flashcard", id: "c2" }} />);
    expect(getPageContext()?.id).toBe("c2");
  });

  it("builds the wire form: ids and a clipped title, question id only, video read at send time", () => {
    let t = 10.7;
    const ctx: PageContextRegistration = {
      type: "section",
      id: "ch05-s03",
      title: `  ${"x".repeat(300)} `,
      chapter_id: "ch05",
      readVideo: () => ({ media_id: "yt:1", t }),
    };
    const first = toPageContextIn(ctx, "/learn/ch05/ch05-s03");
    expect(first).toMatchObject({ route: "/learn/ch05/ch05-s03", type: "section", id: "ch05-s03", chapter_id: "ch05" });
    expect(first.title).toHaveLength(200);
    expect(first.video).toEqual({ media_id: "yt:1", t: 10 });
    t = 742;
    expect(toPageContextIn(ctx, "/x").video?.t).toBe(742);
    // A question page never says whether it was answered: the server reads the attempts.
    const q = toPageContextIn({ type: "question", id: "q12" }, "/practice/blocks/b/1");
    expect(q.question).toEqual({ id: "q12" });
    expect(JSON.stringify(q)).not.toMatch(/answered|chosen|exam_mode/);
    expect(toPageContextIn(null, "/settings")).toEqual({ route: "/settings", type: "page" });
  });

  it("a video ask carries the live position while the page still plays it, else the position it was made at", () => {
    let t = 80.6;
    const page: PageContextRegistration = {
      type: "video",
      id: "yt-loops",
      title: "Loops",
      readVideo: () => ({ media_id: "yt-loops", t }),
    };
    expect(toPageContextIn(page, "/videos")).toEqual({
      route: "/videos",
      type: "video",
      id: "yt-loops",
      title: "Loops",
      video: { media_id: "yt-loops", t: 80 },
    });
    const ask = { type: "video", id: "yt-loops", t: 12 } as const;
    expect(pageContextFor(ask, page, "/videos")).toEqual({
      route: "/videos",
      type: "video",
      id: "yt-loops",
      video: { media_id: "yt-loops", t: 80 },
    });
    t = 95;
    expect(pageContextFor(ask, page, "/videos").video).toEqual({ media_id: "yt-loops", t: 95 });
    // another video is playing now: the ask's own position, or none
    expect(pageContextFor(ask, { type: "page" }, "/videos").video).toEqual({ media_id: "yt-loops", t: 12 });
    expect(pageContextFor({ type: "video", id: "yt-loops" }, null, "/x")).toEqual({
      route: "/x",
      type: "video",
      id: "yt-loops",
    });
  });

  it("an ask's own target wins over the page's registration", () => {
    const page: PageContextRegistration = { type: "section", id: "s1", chapter_id: "ch1", title: "Gap" };
    expect(pageContextFor({ type: "pdf_page", id: "u1:3", image: null }, page, "/library")).toEqual({
      route: "/library",
      type: "pdf_page",
      id: "u1:3",
    });
    expect(pageContextFor({ type: "flashcard", id: "c9" }, page, "/cards")).toEqual({
      route: "/cards",
      type: "flashcard",
      id: "c9",
    });
    expect(pageContextFor({ type: "question", id: "q1" }, page, "/r")).toEqual({
      route: "/r",
      type: "question",
      id: "q1",
      question: { id: "q1" },
    });
    expect(pageContextFor({ type: "coach_suggestion", id: "s7" }, null, "/")).toEqual({
      route: "/",
      type: "today",
      id: "s7",
    });
    // a selection keeps the page's own context (the text goes as an attachment)
    expect(
      pageContextFor({ type: "selection", surface: { type: "section", id: "s1" }, text: "gap" }, page, "/learn/ch1/s1"),
    ).toMatchObject({ type: "section", id: "s1", chapter_id: "ch1" });
  });
});
