import { uiText } from "@naadi/shared";
import { describe, expect, it } from "vitest";
import { runtimeSummary } from "./AgentDiagnostics";

// The shipped defaults (config/app_config.yaml via ui-copy.defaults.json): what a user reads.
const t = (key: string, vars?: Readonly<Record<string, string>>) => uiText(null, key, vars);
const at = "2026-11-16T19:05:00Z";

describe("runtimeSummary (agent runtime status in words, from structured facts)", () => {
  it("counts graphs with the right plural and gives the last ping in UTC", () => {
    expect(runtimeSummary({ reason: "ok", graphs: ["ping"], failing: [], last_cron_ping: at }, t)).toBe(
      "1 graph loaded; crons are firing (last hourly ping 19:05 UTC).",
    );
    expect(runtimeSummary({ reason: "ok", graphs: ["ping", "tavily_usage"], last_cron_ping: at }, t)).toBe(
      "2 graphs loaded; crons are firing (last hourly ping 19:05 UTC).",
    );
  });

  it("names failing checks in words, never as codes", () => {
    const s = runtimeSummary({ reason: "checks_failed", failing: ["broker", "cron_scheduler"] }, t);
    expect(s).toBe("Not working: event broker, cron scheduler.");
  });

  it.each([
    ["not_installed", "The agent runtime is not installed on this server."],
    ["not_hosted", "This server does not host the agent runtime."],
    ["no_cron_ping", "Running, but no hourly ping has been recorded yet."],
  ] as const)("%s", (reason, text) => {
    expect(runtimeSummary({ reason }, t)).toBe(text);
  });

  it("a stale cron names the time; a failed check (no facts) says so", () => {
    expect(runtimeSummary({ reason: "cron_stale", last_cron_ping: at }, t)).toBe(
      "Running, but the last hourly ping was at 19:05 UTC.",
    );
    expect(runtimeSummary(null, t)).toBe("The runtime check could not run. Try again shortly.");
  });

  it("every summary is a sentence of copy: no unfilled {placeholders} and no snake_case codes", () => {
    for (const reason of [
      "ok",
      "not_installed",
      "not_hosted",
      "checks_failed",
      "no_cron_ping",
      "cron_stale",
    ] as const) {
      const s = runtimeSummary({ reason, graphs: ["ping"], failing: ["graphs_loaded"], last_cron_ping: at }, t);
      expect(s).not.toMatch(/[{}]|\b[a-z]+_[a-z_]+\b/);
    }
  });
});
