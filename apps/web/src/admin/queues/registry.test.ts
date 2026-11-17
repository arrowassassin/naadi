import { describe, expect, it } from "vitest";
import { refLink } from "./refs";
import { queueView, registeredQueueViews, registerQueueView, rejectNeedsNote, unregisterQueueView } from "./registry";
import "./views";

const Row = () => null;

describe("queue view registry", () => {
  it("registers this segment's views through views/index.ts", () => {
    expect(registeredQueueViews()).toEqual(expect.arrayContaining(["generated_media", "report_issues"]));
    expect(queueView("generated_media")?.titleKey).toBe("admin.queues.generated_media.title");
    expect(queueView("generated_media")?.ApproveForm).toBeTypeOf("function");
  });

  it("throws on a duplicate registration", () => {
    registerQueueView("test_dup", { titleKey: "admin.queues.title", Row });
    try {
      expect(() => registerQueueView("test_dup", { titleKey: "admin.queues.title", Row })).toThrow(
        /already registered/,
      );
    } finally {
      unregisterQueueView("test_dup");
    }
  });

  it("refuses invalid kinds and views without a Row or title", () => {
    expect(() => registerQueueView("Bad-Kind", { titleKey: "x.y", Row })).toThrow(/invalid queue kind/);
    expect(() => registerQueueView("ok_kind", { titleKey: "", Row })).toThrow(/titleKey/);
    expect(() => registerQueueView("ok_kind", { titleKey: "x.y", Row: undefined as unknown as typeof Row })).toThrow(
      /Row/,
    );
    expect(queueView("ok_kind")).toBeNull();
  });

  it("falls back for unregistered kinds: no view (generic row) and reject needs a note", () => {
    expect(queueView("youtube_candidates_unknown")).toBeNull();
    expect(rejectNeedsNote("youtube_candidates_unknown")).toBe(true);
    expect(rejectNeedsNote("report_issues")).toBe(false);
  });

  it("deep-links refs to admin pages, and nothing for refs without one", () => {
    expect(refLink({ type: "section", id: "s1" })).toEqual({
      to: "/admin/sections/$sectionId/edit",
      params: { sectionId: "s1" },
    });
    expect(refLink({ type: "question", id: "q1" })?.to).toBe("/admin/questions/$questionId");
    expect(refLink({ type: "media", id: "m1" })).toEqual({ to: "/admin/media", search: { q: "m1" } });
    expect(refLink({ type: "pathway_task", id: "p1" })).toBeNull();
    expect(refLink(null)).toBeNull();
  });
});
