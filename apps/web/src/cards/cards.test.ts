import "fake-indexeddb/auto";
import { type ReviewIn, uiText } from "@naadi/shared";
import { Dexie } from "dexie";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { unwrap } from "../auth/apiError";
import { DB_NAME, resetNaadiDbForTests } from "../offline/db";
import { conflictsFor, resetOutboxForTests, setOnlineProbe, startOutbox, stopOutbox } from "../offline/outbox";

const reviewCardMock = vi.hoisted(() => vi.fn());
vi.mock("@naadi/shared", async (orig) => ({
  ...(await orig<typeof import("@naadi/shared")>()),
  reviewCard: reviewCardMock,
}));
vi.mock("../auth/api", () => ({
  authed: (make: (h: Record<string, string>) => Promise<never>) => unwrap(make({ authorization: "Bearer t" })),
}));

import { cardKey, intervalLabel, ownedElsewhere } from "./format";
import { dropReview, enqueueReview, migrateLegacyCardQueue, readQueue } from "./outbox";
import { isRetryable, submitReview } from "./submit";

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

describe("ratings through the outbox", () => {
  const user = "u-1";
  const body = (id: string, rating: 1 | 2 | 3 | 4 = 3): ReviewIn => ({
    client_review_id: id,
    rating,
    reviewed_at: "2026-11-16T10:00:00.000Z",
    duration_ms: 1000,
  });

  beforeEach(async () => {
    localStorage.clear();
    resetOutboxForTests();
    resetNaadiDbForTests();
    await Dexie.delete(DB_NAME);
    reviewCardMock.mockReset();
    await startOutbox(user);
  });
  afterEach(() => stopOutbox());

  const answer = (status: number | null, code: string | null = null) =>
    status === null
      ? Promise.reject(new TypeError("offline"))
      : Promise.resolve(
          status < 300
            ? { data: { client_review_id: "x" }, response: new Response(null, { status }) }
            : {
                error: { error: { code: code ?? "code", message: "m", request_id: null } },
                response: new Response(null, { status }),
              },
        );

  it("keeps ratings per user, in order, once per review id; migrates the old queue once", async () => {
    setOnlineProbe(() => false);
    enqueueReview(user, { cardId: "c1", body: body("r1") });
    enqueueReview(user, { cardId: "c1", body: body("r2", 4) });
    enqueueReview("u-2", { cardId: "c9", body: body("r9") });
    await vi.waitFor(() => expect(readQueue(user).map((q) => q.body.client_review_id)).toEqual(["r1", "r2"]));
    dropReview(user, "r2");
    await vi.waitFor(() => expect(readQueue(user).map((q) => q.body.client_review_id)).toEqual(["r1"]));
    expect(readQueue("u-2")).toHaveLength(1);
    localStorage.setItem(`naadi.cards.outbox.v1:${user}`, JSON.stringify([{ cardId: "c2", body: body("old") }]));
    expect(await migrateLegacyCardQueue(user)).toBe(1);
    expect(localStorage.getItem(`naadi.cards.outbox.v1:${user}`)).toBeNull();
    expect(readQueue(user).map((q) => q.body.client_review_id)).toEqual(["r1", "old"]);
  });

  it("sends with an Idempotency-Key, in order, and never lets a new rating overtake one that failed", async () => {
    const seen: string[] = [];
    reviewCardMock.mockImplementation((o: { body: ReviewIn; headers: Record<string, string> }) => {
      seen.push(`${o.body.client_review_id}:${o.headers["idempotency-key"] ? "key" : "nokey"}`);
      return answer(o.body.client_review_id === "a" ? 503 : 200);
    });
    expect((await submitReview(user, "c1", body("a"))).kind).toBe("queued");
    expect((await submitReview(user, "c1", body("b"))).kind).toBe("queued"); // behind "a", which backs off
    expect(seen).toEqual(["a:key"]);
    expect(readQueue(user).map((q) => q.body.client_review_id)).toEqual(["a", "b"]);
  });

  it.each([
    [null, "queued"],
    [500, "queued"],
    [503, "queued"],
    [429, "queued"],
    [404, "refused"],
    [422, "refused"],
  ])("status %s → %s; a refusal is shown, never queued, and kept in the conflict log", async (status, kind) => {
    reviewCardMock.mockImplementation(() => answer(status, "card_not_found"));
    const r = await submitReview(user, "c1", body("r"));
    expect(r.kind).toBe(kind);
    expect(readQueue(user).length).toBe(kind === "queued" ? 1 : 0);
    if (kind === "refused") expect((await conflictsFor(user)).map((c) => c.key)).toEqual(["r"]);
  });

  it("sends at once when online and nothing waits", async () => {
    reviewCardMock.mockImplementation(() => answer(200));
    expect((await submitReview(user, "c1", body("x"))).kind).toBe("sent");
    setOnlineProbe(() => false);
    expect((await submitReview(user, "c1", body("y"))).kind).toBe("queued");
    expect(isRetryable(new Error("plain"))).toBe(false);
  });
});
