import { uiText } from "@naadi/shared";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { cardKey, intervalLabel, ownedElsewhere } from "./format";
import { dropReview, enqueueReview, flushQueue, readQueue, type SendOutcome } from "./outbox";

const t = (key: string, vars: Record<string, string | number> = {}) =>
  uiText(null, key, Object.fromEntries(Object.entries(vars).map(([k, v]) => [k, String(v)])));

const MIN = 60_000;
const DAY = 86_400_000;

describe("interval labels (shipped ui_copy)", () => {
  it.each([
    [MIN, "1 min"],
    [330_000, "6 min"],
    [10 * MIN, "10 min"],
    [5 * 3_600_000, "5 h"],
    [DAY, "1 day"],
    [3 * DAY, "3 days"],
    [29 * DAY, "29 days"],
    [45 * DAY, "1.5 mo"],
    [300 * DAY, "10 mo"],
    [400 * DAY, "1.1 yr"],
    [36500 * DAY, "100 yr"],
  ])("%d ms -> %s", (ms, label) => {
    expect(intervalLabel(ms, t)).toBe(label);
  });

  it("never says 0 minutes", () => {
    expect(intervalLabel(5_000, t)).toBe("1 min");
  });
});

describe("review keys", () => {
  it("Space flips the question side only; 1-4 rate the answer side only", () => {
    expect(cardKey({ key: " " }, false)).toEqual({ type: "flip" });
    expect(cardKey({ key: " " }, true)).toBeNull();
    expect(cardKey({ key: "3" }, false)).toBeNull();
    expect(cardKey({ key: "3" }, true)).toEqual({ type: "rate", rating: 3 });
    expect(cardKey({ key: "5" }, true)).toBeNull();
    expect(cardKey({ key: "1", ctrlKey: true }, true)).toBeNull();
    expect(cardKey({ key: "2", repeat: true }, true)).toBeNull();
  });

  describe("ownership", () => {
    let root: HTMLElement;
    beforeEach(() => {
      root = document.createElement("div");
      root.innerHTML = `
        <section data-card-shortcuts="on"><p id="plain">card</p><button id="btn">Rate</button></section>
        <textarea id="ta"></textarea>
        <dialog open><p id="indialog">x</p></dialog>
        <section data-ink-surface=""><div id="ink"></div></section>
        <video id="vid"></video>`;
      document.body.append(root);
    });
    afterEach(() => root.remove());

    const el = (id: string) => document.getElementById(id);

    it("ignores text fields, dialogs, the ink pad and media", () => {
      for (const id of ["ta", "indialog", "ink", "vid"]) expect(ownedElsewhere(el(id), "3"), id).toBe(true);
    });
    it("lets a focused button keep Space but not the digits", () => {
      expect(ownedElsewhere(el("btn"), " ")).toBe(true);
      expect(ownedElsewhere(el("btn"), "3")).toBe(false);
    });
    it("takes keys from the card itself", () => {
      expect(ownedElsewhere(el("plain"), " ")).toBe(false);
      expect(ownedElsewhere(null, " ")).toBe(false);
    });
  });
});

describe("rating outbox", () => {
  const user = "u-1";
  const body = (id: string, rating: 1 | 2 | 3 | 4 = 3) => ({
    client_review_id: id,
    rating,
    reviewed_at: "2026-11-16T10:00:00.000Z",
    duration_ms: 1000,
  });
  beforeEach(() => localStorage.clear());

  it("keeps ratings per user, in order, once per review id", () => {
    enqueueReview(user, { cardId: "c1", body: body("r1") });
    enqueueReview(user, { cardId: "c1", body: body("r2", 4) });
    enqueueReview(user, { cardId: "c1", body: body("r1") }); // the same id again moves to the end
    enqueueReview("u-2", { cardId: "c9", body: body("r9") });
    expect(readQueue(user).map((q) => q.body.client_review_id)).toEqual(["r2", "r1"]);
    dropReview(user, "r2");
    expect(readQueue(user).map((q) => q.body.client_review_id)).toEqual(["r1"]);
    expect(readQueue("u-2")).toHaveLength(1);
  });

  it("replays in order, drops what the server answered and stops at the first network failure", async () => {
    for (const id of ["a", "b", "c", "d"]) enqueueReview(user, { cardId: "c1", body: body(id) });
    const outcomes: Record<string, SendOutcome> = { a: "sent", b: "refused", c: "unreachable", d: "sent" };
    const seen: string[] = [];
    const sent = await flushQueue(user, async (item) => {
      seen.push(item.body.client_review_id);
      return outcomes[item.body.client_review_id] as SendOutcome;
    });
    expect(seen).toEqual(["a", "b", "c"]);
    expect(sent).toBe(1);
    expect(readQueue(user).map((q) => q.body.client_review_id)).toEqual(["c", "d"]);
  });

  it("survives unreadable storage", () => {
    localStorage.setItem(`naadi.cards.outbox.v1:${user}`, "{not json");
    expect(readQueue(user)).toEqual([]);
  });
});
