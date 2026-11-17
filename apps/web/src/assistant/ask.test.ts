import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { type AssistantAsk, askAssistant, registerAssistantAsk, sendsAtOnce, useAssistantAvailable } from "./ask";

describe("assistant ask registry", () => {
  it("is unavailable until a handler registers, and routes asks to the latest one", () => {
    const { result } = renderHook(() => useAssistantAvailable());
    const ask = { kind: "hint", context: { type: "flashcard", id: "c1" } } as const;
    expect(result.current).toBe(false);
    expect(askAssistant(ask)).toBe(false);
    const first = vi.fn();
    const second = vi.fn();
    let off1: () => void = () => {};
    let off2: () => void = () => {};
    act(() => {
      off1 = registerAssistantAsk(first);
      off2 = registerAssistantAsk(second);
    });
    expect(result.current).toBe(true);
    expect(askAssistant(ask)).toBe(true);
    expect(second).toHaveBeenCalledWith(ask);
    expect(first).not.toHaveBeenCalled();
    act(() => off2());
    askAssistant(ask);
    expect(first).toHaveBeenCalledTimes(1);
    act(() => {
      off1();
      off1(); // twice is harmless
    });
    expect(result.current).toBe(false);
  });

  it("carries every ask kind and context unchanged; only a bare ask waits for the student", () => {
    const seen: AssistantAsk[] = [];
    const off = registerAssistantAsk((a) => seen.push(a));
    const png = new Blob([new Uint8Array([1])], { type: "image/png" });
    const asks: AssistantAsk[] = [
      { kind: "hint", context: { type: "flashcard", id: "c1" } },
      { kind: "explain", context: { type: "question", id: "q1" } },
      { kind: "ask", context: { type: "section", id: "s1", chapter_id: "ch1" }, draft: "Why?" },
      { kind: "summarise", context: { type: "note", id: "n1" } },
      { kind: "flashcards", context: { type: "selection", surface: { type: "note", id: "n1" }, text: "gap" } },
      { kind: "quiz", context: { type: "note", id: "n1" } },
      { kind: "ask", context: { type: "coach_suggestion", id: "cs1" } },
      { kind: "ask", context: { type: "ink_lasso", surface: { type: "section", id: "s1" }, png, text: "" } },
    ];
    for (const a of asks) expect(askAssistant(a)).toBe(true);
    expect(seen).toEqual(asks);
    expect(asks.map(sendsAtOnce)).toEqual([true, true, false, true, true, true, false, false]);
    off();
  });
});
