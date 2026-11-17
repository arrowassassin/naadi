import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AdminApiError, type Revision } from "../api";
import { AUTOSAVE_MS, useAutosave } from "./useAutosave";

const putDraft = vi.fn();
vi.mock("../api", async (orig) => {
  const real = await orig<typeof import("../api")>();
  return { ...real, api: { ...real.api, putDraft: (...a: unknown[]) => putDraft(...a) } };
});

const doc = (text: string) => ({
  type: "doc" as const,
  content: [{ type: "paragraph" as const, content: [{ type: "text" as const, text }] }],
});
const rev = (id: number, at: string, parent: number | null = 7): Revision =>
  ({
    id,
    updated_at: at,
    parent_revision_id: parent,
    section_id: "s1",
    status: "draft",
    body: null,
    note: null,
    author_user_id: null,
    created_at: at,
    published_at: null,
    restored_from_id: null,
    is_live: false,
    origin: "admin",
    word_count: 1,
  }) as Revision;

beforeEach(() => {
  vi.useFakeTimers();
  putDraft.mockReset();
});
afterEach(() => vi.useRealTimers());

describe("draft autosave", () => {
  it("debounces edits into one PUT with the live revision as parent and no expected_updated_at", async () => {
    putDraft.mockResolvedValue(rev(11, "2026-11-17T10:00:00Z"));
    const { result } = renderHook(() => useAutosave("s1", null, 7));
    act(() => {
      result.current.change(doc("a"));
      result.current.change(doc("ab"));
    });
    expect(result.current.state.kind).toBe("dirty");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(AUTOSAVE_MS + 10);
    });
    expect(putDraft).toHaveBeenCalledTimes(1);
    expect(putDraft).toHaveBeenCalledWith("s1", { body: doc("ab"), parent_revision_id: 7, expected_updated_at: null });
    expect(result.current.state).toEqual({ kind: "saved", at: "2026-11-17T10:00:00Z" });
  });

  it("sends the draft's updated_at next time (optimistic concurrency) and saves edits made during a save", async () => {
    let resolve: (r: Revision) => void = () => {};
    putDraft.mockImplementationOnce(() => new Promise<Revision>((r) => (resolve = r)));
    putDraft.mockResolvedValueOnce(rev(11, "2026-11-17T10:00:05Z"));
    const { result } = renderHook(() => useAutosave("s1", rev(11, "2026-11-17T09:00:00Z"), 7));
    act(() => result.current.change(doc("one")));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(AUTOSAVE_MS + 10);
    });
    expect(putDraft).toHaveBeenCalledTimes(1);
    expect(putDraft.mock.calls[0]?.[1]).toMatchObject({ expected_updated_at: "2026-11-17T09:00:00Z" });
    act(() => result.current.change(doc("two")));
    await act(async () => {
      resolve(rev(11, "2026-11-17T10:00:01Z"));
      await vi.advanceTimersByTimeAsync(AUTOSAVE_MS * 2 + 10);
    });
    expect(putDraft).toHaveBeenCalledTimes(2);
    expect(putDraft.mock.calls[1]?.[1]).toMatchObject({
      body: doc("two"),
      expected_updated_at: "2026-11-17T10:00:01Z",
    });
  });

  it("stops on a 409 (conflict) until reset, and keeps the edit to send on 'keep mine'", async () => {
    putDraft.mockRejectedValueOnce(new AdminApiError("stale", 409, "stale_draft", "rid"));
    putDraft.mockResolvedValueOnce(rev(12, "2026-11-17T11:00:00Z"));
    const { result } = renderHook(() => useAutosave("s1", rev(11, "2026-11-17T09:00:00Z"), 7));
    act(() => result.current.change(doc("mine")));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(AUTOSAVE_MS + 10);
    });
    expect(result.current.state.kind).toBe("conflict");
    act(() => result.current.change(doc("mine 2")));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(AUTOSAVE_MS * 3);
    });
    expect(putDraft).toHaveBeenCalledTimes(1);
    await act(async () => {
      result.current.reset(rev(11, "2026-11-17T10:30:00Z"), { keepPending: true });
      await vi.advanceTimersByTimeAsync(10);
    });
    expect(putDraft).toHaveBeenCalledTimes(2);
    expect(putDraft.mock.calls[1]?.[1]).toMatchObject({
      body: doc("mine 2"),
      expected_updated_at: "2026-11-17T10:30:00Z",
    });
    expect(result.current.state.kind).toBe("saved");
  });

  it("never sends an invalid document and names its path", async () => {
    const { result } = renderHook(() => useAutosave("s1", null, null));
    act(() =>
      result.current.change({ type: "doc", content: [{ type: "figure", attrs: { mediaId: "Bad Id" } }] } as never),
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(AUTOSAVE_MS + 10);
    });
    expect(putDraft).not.toHaveBeenCalled();
    expect(result.current.state).toEqual({ kind: "invalid", path: "body.content.0.attrs.mediaId" });
  });

  it("keeps the edit after a network failure and retries on flush", async () => {
    putDraft.mockRejectedValueOnce(new AdminApiError("", null, "unreachable", null));
    putDraft.mockResolvedValueOnce(rev(13, "2026-11-17T12:00:00Z"));
    const { result } = renderHook(() => useAutosave("s1", null, 7));
    act(() => result.current.change(doc("x")));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(AUTOSAVE_MS + 10);
    });
    expect(result.current.state.kind).toBe("error");
    let d: unknown;
    await act(async () => {
      d = await result.current.flush();
    });
    expect(putDraft).toHaveBeenCalledTimes(2);
    expect(d).toMatchObject({ id: 13 });
  });
});
