import { uiText } from "@naadi/shared";
import { describe, expect, it, vi } from "vitest";
import { coverageLine, percent, statusWord } from "./format";
import { pageOwnsArrows } from "./ReaderPage";
import { parseCoursesSearch } from "./routes";
import { measureOffset, scrollToOffset } from "./useReadingPosition";

const t = (key: string, vars?: Readonly<Record<string, string>>) => uiText(null, key, vars);

describe("page arrows", () => {
  it("turn the page only when focus is on the page or the article", () => {
    const article = document.createElement("article");
    const table = document.createElement("section");
    table.tabIndex = 0;
    const field = document.createElement("textarea");
    const video = document.createElement("video");
    article.append(table, field, video);
    document.body.append(article);
    expect(pageOwnsArrows(document.body, article)).toBe(true);
    expect(pageOwnsArrows(article, article)).toBe(true);
    expect(pageOwnsArrows(table, article)).toBe(false);
    expect(pageOwnsArrows(field, article)).toBe(false);
    expect(pageOwnsArrows(video, article)).toBe(false);
    article.remove();
  });
});

describe("course filters from the URL", () => {
  it("keeps valid exam and system ids and drops anything else", () => {
    expect(parseCoursesSearch({ exam: "step2ck", system: "step2ck.sys.cardiovascular" })).toEqual({
      exam: "step2ck",
      system: "step2ck.sys.cardiovascular",
    });
    expect(parseCoursesSearch({ exam: "Step 1", system: "<script>" })).toEqual({ exam: undefined, system: undefined });
    expect(parseCoursesSearch({ exam: 1, system: ["x"] })).toEqual({ exam: undefined, system: undefined });
  });
});

describe("format", () => {
  it("lists only what exists and never shows colour-only status", () => {
    expect(coverageLine(t, { sections: 16, reading_minutes: 80, figures: 7, videos: 0, questions: 45, cards: 1 })).toBe(
      "16 sections · 7 figures · 45 questions · 1 card · about 80 min",
    );
    expect(
      statusWord(t, {
        sections_read: 3,
        sections_total: 9,
        status: "in_progress",
        last_section_id: "a",
        last_read_at: null,
      }),
    ).toBe("3 of 9 read");
    expect(percent(1, 3)).toBe(33);
    expect(percent(0, 0)).toBe(0);
  });
});

function fakeBlocks(tops: number[], height: number): HTMLElement {
  const root = document.createElement("div");
  const content = document.createElement("div");
  content.setAttribute("data-content-root", "");
  root.append(content);
  for (const [i, top] of tops.entries()) {
    const el = document.createElement("div");
    el.setAttribute("data-block-index", String(i));
    el.getBoundingClientRect = () => ({
      top,
      bottom: top + height,
      height,
      left: 0,
      right: 0,
      width: 0,
      x: 0,
      y: top,
      toJSON: () => ({}),
    });
    content.append(el);
  }
  return root;
}

describe("reading position anchor", () => {
  it("is the top-most visible block plus the fraction scrolled past", () => {
    // blocks of 200 px at -500, -300, -100, 100: the anchor line at 24 px is inside block 2, 62 % down
    const root = fakeBlocks([-500, -300, -100, 100], 200);
    expect(measureOffset(root, 24)).toBe(2.62);
    expect(measureOffset(fakeBlocks([300, 500], 200), 24)).toBe(0);
    expect(measureOffset(fakeBlocks([-900, -700], 200), 24)).toBe(2);
  });

  it("scrolls back to the same block and fraction", () => {
    const spy = vi.spyOn(window, "scrollTo").mockImplementation(() => {});
    const root = fakeBlocks([1000, 1200, 1400], 200);
    scrollToOffset(root, 1.5, 24);
    // window.scrollY (0) + block 1 top (1200) + 0.5 * 200 - 24
    expect(spy).toHaveBeenCalledWith({ top: 1276, behavior: "instant" });
    spy.mockRestore();
  });
});
