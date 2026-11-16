import type { BlockClock } from "@naadi/shared";
import { describe, expect, it } from "vitest";
import { type CalcKey, INITIAL, keyToCalc, press } from "./calc";
import {
  addSample,
  bestOffset,
  crossedThreshold,
  formatRemaining,
  remainingMs,
  sampleOffset,
  spokenParts,
} from "./clock";
import { breakLeft, sectionLeft } from "./ExamPage";
import { normalize, offsetOf, rangesFor, selectionSpan, toggleSpan } from "./highlight";
import { tileOf } from "./Navigator";

const iso = (ms: number) => new Date(ms).toISOString();

describe("offset estimator", () => {
  it("assumes the server read its clock halfway through the round trip", () => {
    // client clock is 10 minutes behind the server; 200 ms round trip
    const server = Date.UTC(2026, 10, 16, 9, 0, 0);
    const sentAt = server - 600_000 - 100;
    const e = sampleOffset({ sentAt, receivedAt: sentAt + 200, serverNow: iso(server) });
    expect(e.rttMs).toBe(200);
    expect(e.offsetMs).toBe(600_000);
  });

  it("keeps the best round trip among recent samples", () => {
    const base = 1_000_000;
    const samples = [
      { sentAt: base, receivedAt: base + 900, serverNow: iso(base + 450 + 5000) },
      { sentAt: base + 1000, receivedAt: base + 1040, serverNow: iso(base + 1020 + 5003) },
      { sentAt: base + 2000, receivedAt: base + 2600, serverNow: iso(base + 2300 + 4990) },
    ];
    const best = bestOffset(samples, base + 3000);
    expect(best).toEqual({ offsetMs: 5003, rttMs: 40 });
  });

  it("drops stale samples but never all of them", () => {
    const s = { sentAt: 0, receivedAt: 10, serverNow: iso(5 + 42) };
    expect(bestOffset([s], 60 * 60_000)?.offsetMs).toBe(42);
    expect(bestOffset([], 0)).toBeNull();
  });

  it("keeps only the newest samples", () => {
    let list: ReturnType<typeof addSample> = [];
    for (let i = 0; i < 12; i++) list = addSample(list, { sentAt: i, receivedAt: i, serverNow: iso(i) });
    expect(list).toHaveLength(8);
    expect(list[0]?.sentAt).toBe(4);
  });

  it("computes time left from the deadline and the offset, not from a counter", () => {
    const deadline = Date.UTC(2026, 10, 16, 10, 0, 0);
    // the device clock is 10 minutes slow: offset +600 000
    const now = deadline - 600_000 - 125_000;
    expect(remainingMs(iso(deadline), 600_000, now)).toBe(125_000);
    expect(remainingMs(iso(deadline), 0, deadline + 5)).toBe(0);
  });
});

describe("display rounding", () => {
  it("shows the nearest whole second", () => {
    expect(formatRemaining(3_600_000)).toBe("60:00");
    expect(formatRemaining(3_599_501)).toBe("60:00");
    expect(formatRemaining(3_599_400)).toBe("59:59");
    expect(formatRemaining(61_000)).toBe("1:01");
    expect(formatRemaining(600)).toBe("0:01");
    expect(formatRemaining(400)).toBe("0:00");
    expect(formatRemaining(0)).toBe("0:00");
    expect(formatRemaining(-50)).toBe("0:00");
    expect(formatRemaining(3_601_000)).toBe("1:00:01");
  });

  it("speaks minutes, or seconds under a minute", () => {
    expect(spokenParts(4 * 60_000 + 1)).toEqual({ minutes: 5 });
    expect(spokenParts(59_500)).toEqual({ minutes: 1 });
    expect(spokenParts(59_000)).toEqual({ seconds: 59 });
  });

  it("announces 5 and 1 minutes once, and not after a reload below them", () => {
    expect(crossedThreshold(300_400, 299_900)).toBe(300_000);
    expect(crossedThreshold(299_900, 299_600)).toBeNull();
    expect(crossedThreshold(60_200, 59_950)).toBe(60_000);
    expect(crossedThreshold(null, 30_000)).toBeNull();
  });
});

const clock = (over: Partial<BlockClock>): BlockClock => ({
  server_now: iso(1_000_000),
  state: "running",
  section_n: 1,
  sections: 2,
  deadline_at: iso(1_000_000 + 60_000),
  remaining_ms: 60_000,
  break_remaining_ms: null,
  break_left_ms: 2_700_000,
  paused: false,
  time_limit_ms: 3_600_000,
  pausable: false,
  grace_ms: 2000,
  ...over,
});

describe("section and break time", () => {
  it("is frozen while paused", () => {
    const c = clock({ state: "paused", paused: true, remaining_ms: 42_000 });
    expect(sectionLeft(c, 0, 9_999_999)).toBe(42_000);
  });

  it("counts the break down from server_now", () => {
    const c = clock({ state: "break", deadline_at: null, break_remaining_ms: 120_000 });
    expect(breakLeft(c, 0, 1_000_000 + 20_000)).toBe(100_000);
    expect(breakLeft(c, 5_000, 1_000_000 + 20_000)).toBe(95_000);
    expect(breakLeft(clock({}), 0, 5_000_000)).toBe(2_700_000);
  });
});

describe("calculator", () => {
  const run = (keys: (CalcKey | string)[]) =>
    keys.reduce((s, k) => press(s, typeof k === "string" && /^[0-9]$/.test(k) ? { digit: k } : (k as CalcKey)), INITIAL)
      .display;

  it("does four-function arithmetic left to right", () => {
    expect(run(["1", "2", { op: "+" }, "3", "="])).toBe("15");
    expect(run(["2", { op: "+" }, "3", { op: "*" }, "4", "="])).toBe("20");
    expect(run(["7", { op: "/" }, "2", "="])).toBe("3.5");
    expect(run(["0", ".", "1", { op: "+" }, "0", ".", "2", "="])).toBe("0.3");
  });

  it("switches operators, changes sign and clears", () => {
    expect(run(["5", { op: "+" }, { op: "-" }, "2", "="])).toBe("3");
    expect(run(["5", "sign"])).toBe("-5");
    expect(run(["9", { op: "+" }, "4", "CE", "1", "="])).toBe("10");
    expect(run(["9", "C"])).toBe("0");
  });

  it("shows an error for division by zero until cleared", () => {
    const s = [{ digit: "1" }, { op: "/" as const }, { digit: "0" }, "=" as const].reduce(
      (st, k) => press(st, k as CalcKey),
      INITIAL,
    );
    expect(s.error).toBe(true);
    expect(press(s, { digit: "4" }).error).toBe(true);
    expect(press(s, "C")).toEqual(INITIAL);
  });

  it("maps keyboard keys", () => {
    expect(keyToCalc("7")).toEqual({ digit: "7" });
    expect(keyToCalc("x")).toEqual({ op: "*" });
    expect(keyToCalc("Enter")).toBe("=");
    expect(keyToCalc("a")).toBeNull();
  });
});

describe("highlights", () => {
  it("merges spans and toggles a selection inside a highlight off", () => {
    expect(
      normalize([
        [5, 9],
        [0, 3],
        [3, 4],
        [8, 12],
        [7, 7],
      ]),
    ).toEqual([
      [0, 4],
      [5, 12],
    ]);
    expect(toggleSpan([[0, 10]], [3, 5])).toEqual([
      [0, 3],
      [5, 10],
    ]);
    expect(toggleSpan([[0, 2]], [1, 6])).toEqual([[0, 6]]);
  });

  it("maps a DOM selection to text offsets and back", () => {
    const root = document.createElement("div");
    root.innerHTML = "<p>A 34-year-old <b>woman</b></p><p>has fatigue.</p>";
    document.body.append(root);
    const woman = root.querySelector("b")?.firstChild as Text;
    const second = root.querySelectorAll("p")[1]?.firstChild as Text;
    expect(offsetOf(root, woman, 0)).toBe(14);
    const range = document.createRange();
    range.setStart(woman, 0);
    range.setEnd(second, 3);
    const sel = window.getSelection();
    sel?.removeAllRanges();
    sel?.addRange(range);
    const span = selectionSpan(root, sel);
    expect(span).toEqual([14, 22]);
    const [r] = rangesFor(root, [span as [number, number]]);
    expect(r?.toString()).toBe("womanhas");
    root.remove();
  });
});

describe("navigator tiles", () => {
  it("say their status in words, flagged included", () => {
    const q = { n: 3, section_n: 1, answered: false, flagged: true, correct: null };
    expect(tileOf(q, 3)).toEqual({ status: "current", flagged: true });
    expect(tileOf(q, 1)).toEqual({ status: "unanswered", flagged: true });
    expect(tileOf(q, 1, true)).toEqual({ status: "answered", flagged: true });
  });
});
