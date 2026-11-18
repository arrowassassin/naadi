import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetPageLeaveForTests, untilLeave } from "./pageLeave";

beforeEach(() => {
  vi.useFakeTimers();
  resetPageLeaveForTests();
});
afterEach(() => {
  vi.useRealTimers();
});

describe("requests end as aborts when the page is left", () => {
  it("aborts on beforeunload, keeps aborting while the page goes, and serves fresh signals if it stays", () => {
    const a = untilLeave();
    expect(a.aborted).toBe(false);
    window.dispatchEvent(new Event("beforeunload"));
    expect(a.aborted).toBe(true);
    expect(untilLeave().aborted).toBe(true); // a request started while leaving never goes out
    vi.advanceTimersByTime(2_000);
    const b = untilLeave();
    expect(b.aborted).toBe(false); // the page stayed: new requests go out
    window.dispatchEvent(new Event("pagehide"));
    expect(b.aborted).toBe(true);
    window.dispatchEvent(new Event("pageshow")); // back from the back-forward cache
    expect(untilLeave().aborted).toBe(false);
  });

  it("follows the caller's own signal too", () => {
    const own = new AbortController();
    const s = untilLeave(own.signal);
    expect(s.aborted).toBe(false);
    own.abort();
    expect(s.aborted).toBe(true);
    const other = untilLeave(new AbortController().signal);
    window.dispatchEvent(new Event("beforeunload"));
    expect(other.aborted).toBe(true);
  });
});
