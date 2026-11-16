import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import type { AttemptIn, AttemptOut } from "@naadi/shared";
import { REQUIRED_UI_COPY_KEYS } from "@naadi/shared";
import { beforeEach, describe, expect, it } from "vitest";
import { percent, resumeIndex, splitWhyNot } from "./format";
import { isTypingTarget, type KeyLike, keyAction } from "./keys";
import { enqueueAttempt, flushQueue, queuedFor, readQueue } from "./outbox";
import { parsePracticeSearch } from "./routes";

const press = (key: string, mods: Partial<KeyLike> = {}): KeyLike => ({
  key,
  metaKey: false,
  ctrlKey: false,
  altKey: false,
  shiftKey: false,
  ...mods,
});

describe("keyboard map (HANDOFF 5.1)", () => {
  it("1-5 and A-E choose, within the options the question has", () => {
    expect(keyAction(press("1"), 5)).toEqual({ type: "choose", index: 0 });
    expect(keyAction(press("5"), 5)).toEqual({ type: "choose", index: 4 });
    expect(keyAction(press("c"), 5)).toEqual({ type: "choose", index: 2 });
    expect(keyAction(press("E"), 5)).toEqual({ type: "choose", index: 4 });
    expect(keyAction(press("5"), 4)).toBeNull();
    expect(keyAction(press("e"), 4)).toBeNull();
    expect(keyAction(press("6"), 5)).toBeNull();
    expect(keyAction(press("0"), 5)).toBeNull();
  });

  it("F flags, N goes next, Command or Control + Enter submits", () => {
    expect(keyAction(press("f"), 5)).toEqual({ type: "flag" });
    expect(keyAction(press("N"), 5)).toEqual({ type: "next" });
    expect(keyAction(press("Enter", { metaKey: true }), 5)).toEqual({ type: "submit" });
    expect(keyAction(press("Enter", { ctrlKey: true }), 5)).toEqual({ type: "submit" });
    expect(keyAction(press("Enter"), 5)).toBeNull(); // a bare Enter belongs to the focused control
    expect(keyAction(press("Enter", { metaKey: true, shiftKey: true }), 5)).toBeNull();
  });

  it("leaves browser and assistive-tech shortcuts alone", () => {
    expect(keyAction(press("f", { metaKey: true }), 5)).toBeNull(); // find
    expect(keyAction(press("a", { ctrlKey: true }), 5)).toBeNull(); // select all
    expect(keyAction(press("n", { altKey: true }), 5)).toBeNull();
    expect(keyAction(press("Tab"), 5)).toBeNull();
    expect(keyAction(press("ArrowDown"), 5)).toBeNull();
  });

  it("does not take keys typed into a text field or a dialog, but does from a radio option", () => {
    const text = document.createElement("input");
    text.type = "text";
    const radio = document.createElement("input");
    radio.type = "radio";
    const area = document.createElement("textarea");
    const dialog = document.createElement("dialog");
    const inside = document.createElement("button");
    dialog.append(inside);
    document.body.append(text, radio, area, dialog);
    expect(isTypingTarget(text)).toBe(true);
    expect(isTypingTarget(area)).toBe(true);
    expect(isTypingTarget(inside)).toBe(true);
    expect(isTypingTarget(radio)).toBe(false);
    expect(isTypingTarget(document.body)).toBe(false);
    expect(isTypingTarget(null)).toBe(false);
  });
});

describe("why-not rows", () => {
  const text =
    "Why the others are wrong: A — base excision repair handles single damaged bases. B — mismatch repair defects cause Lynch syndrome (colon/endometrial cancer). D — homologous recombination defects.";

  it("splits the seeded text into one row per option", () => {
    expect(splitWhyNot(text)).toEqual([
      { label: "A", text: "base excision repair handles single damaged bases." },
      { label: "B", text: "mismatch repair defects cause Lynch syndrome (colon/endometrial cancer)." },
      { label: "D", text: "homologous recombination defects." },
    ]);
  });

  it("shows anything else as written", () => {
    expect(splitWhyNot(null)).toBeNull();
    expect(splitWhyNot("Because it is not.")).toBeNull();
    expect(splitWhyNot("A — only one row")).toBeNull();
    expect(splitWhyNot("<p>A — x B — y</p>")).toBeNull(); // markup is rendered by the sanitiser instead
  });
});

describe("block helpers", () => {
  it("percent and resume position", () => {
    expect(percent(7, 10)).toBe(70);
    expect(percent(0, 0)).toBe(0);
    expect(resumeIndex(0, 10)).toBe(1);
    expect(resumeIndex(3, 10)).toBe(4);
    expect(resumeIndex(10, 10)).toBe(10);
  });

  it("practice URL filters keep only a valid exam id", () => {
    expect(parsePracticeSearch({ exam: "step2ck" })).toEqual({ exam: "step2ck" });
    expect(parsePracticeSearch({ exam: "Step 1" })).toEqual({ exam: undefined });
    expect(parsePracticeSearch({ exam: 4 })).toEqual({ exam: undefined });
  });
});

describe("offline answer queue", () => {
  const user = "u1";
  const body = (q: string, chosen = "B"): AttemptIn => ({ question_id: q, chosen, seconds: 5, flagged: false });
  const out = (q: string): AttemptOut => ({
    question_id: q,
    n: 1,
    chosen: "B",
    correct: true,
    flagged: false,
    seconds: 5,
    explanation: null,
    answered: 1,
    total: 2,
    finished: false,
  });
  const unreachable = (e: unknown) => e instanceof TypeError;

  beforeEach(() => localStorage.clear());

  it("keeps one entry per (block, question) and finds it again", () => {
    enqueueAttempt(user, "b1", body("q1", "A"));
    enqueueAttempt(user, "b1", body("q1", "B"));
    enqueueAttempt(user, "b1", body("q2"));
    enqueueAttempt(user, "b2", body("q1"));
    expect(readQueue(user)).toHaveLength(3);
    expect(queuedFor(user, "b1", "q1")?.body.chosen).toBe("B");
    expect(queuedFor(user, "b1", "q9")).toBeUndefined();
    expect(readQueue("someone-else")).toEqual([]);
  });

  it("replays in order and removes what the server answered", async () => {
    enqueueAttempt(user, "b1", body("q1"));
    enqueueAttempt(user, "b1", body("q2"));
    const sent: string[] = [];
    const n = await flushQueue(
      user,
      async (b, a) => {
        sent.push(`${b}:${a.question_id}`);
        return out(a.question_id);
      },
      unreachable,
    );
    expect(n).toBe(2);
    expect(sent).toEqual(["b1:q1", "b1:q2"]);
    expect(readQueue(user)).toEqual([]);
  });

  it("stops at a network failure and keeps the rest; drops answers the server refuses for good", async () => {
    enqueueAttempt(user, "b1", body("q1"));
    enqueueAttempt(user, "b1", body("q2"));
    enqueueAttempt(user, "b1", body("q3"));
    let calls = 0;
    const n = await flushQueue(
      user,
      async (_b, a) => {
        calls += 1;
        if (a.question_id === "q1") return out("q1");
        if (a.question_id === "q2") throw new Error("404 block gone"); // answered by the server: not retried
        throw new TypeError("offline");
      },
      unreachable,
    );
    expect(n).toBe(1);
    expect(calls).toBe(3);
    expect(readQueue(user).map((q) => q.body.question_id)).toEqual(["q3"]);
  });
});

describe("practice copy", () => {
  const files = readdirSync(import.meta.dirname).filter((f) => /\.tsx?$/.test(f) && !f.includes(".test."));
  const source = files.map((f) => readFileSync(path.join(import.meta.dirname, f), "utf8")).join("\n");

  it("every practice.* string the screens read is a required ui_copy key (so it is seeded, editable and has a fallback)", () => {
    const used = new Set([...source.matchAll(/\bt\(\s*"(practice\.[a-z_]+)"/g)].map((m) => m[1] as string));
    for (const mode of ["tutor", "timed", "exam"]) used.add(`practice.mode_${mode}`);
    for (const s of ["all", "unused", "incorrect", "flagged", "correct"]) used.add(`practice.status_${s}`);
    for (const d of ["all", "easy", "medium", "hard"]) used.add(`practice.difficulty_${d}`);
    expect(used.size).toBeGreaterThan(40);
    const required = new Set(REQUIRED_UI_COPY_KEYS);
    expect([...used].filter((k) => !required.has(k))).toEqual([]);
  });

  it("the screens carry no sentence of their own: the only literals are loading statuses and the config fallback", () => {
    expect(source).not.toMatch(/>\s*Start block\s*</);
    expect(source).not.toMatch(/Build my weak-spot block/);
  });
});
