import { afterEach, describe, expect, it, vi } from "vitest";
import { observeServerClock, resetServerClockForTests, serverClockOffset, serverNow } from "./clock";

afterEach(() => {
  resetServerClockForTests();
  vi.useRealTimers();
});

describe("server clock", () => {
  it("learns a device running 10 minutes fast from one response and stamps with server time", () => {
    vi.useFakeTimers({ now: 1_000_000_000 });
    const sent = Date.now();
    vi.advanceTimersByTime(200);
    observeServerClock(sent - 600_000 + 100, sent, Date.now()); // the server is 10 min behind this device
    expect(serverClockOffset()).toBe(-600_000);
    expect(serverNow()).toBe(Date.now() - 600_000);
  });

  it("ignores jitter inside 2 s, slow round trips and nonsense", () => {
    observeServerClock(Date.now() + 1_500, Date.now(), Date.now());
    expect(serverClockOffset()).toBe(0);
    observeServerClock(Date.now() + 600_000, Date.now() - 20_000, Date.now());
    expect(serverClockOffset()).toBe(0);
    observeServerClock(Number.NaN, Date.now(), Date.now());
    observeServerClock(0, Date.now(), Date.now());
    observeServerClock(Date.now() + 600_000, Date.now(), Date.now() - 5);
    expect(serverClockOffset()).toBe(0);
  });
});
