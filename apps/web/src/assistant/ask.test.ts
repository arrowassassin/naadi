import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { askAssistant, registerAssistantAsk, useAssistantAvailable } from "./ask";

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
});
