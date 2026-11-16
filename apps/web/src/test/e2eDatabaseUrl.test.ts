// @vitest-environment node
import { describe, expect, it } from "vitest";
import { e2eDatabaseUrl, e2eSupabaseUrl, e2eTargets, isLoopbackDatabaseUrl } from "../../e2e/databaseUrl";

const HOSTED = "postgresql://postgres.ref:secret@aws-0-eu-west-2.pooler.supabase.com:5432/postgres";
const HOSTED_SB = "https://abcdefghijklmnop.supabase.co";

describe("e2eDatabaseUrl (the database the e2e servers migrate)", () => {
  it("uses E2E_DATABASE_URL as given, whatever DATABASE_URL is", () => {
    expect(e2eDatabaseUrl({ E2E_DATABASE_URL: "postgresql://u@db.test:5432/e2e", DATABASE_URL: HOSTED })).toBe(
      "postgresql://u@db.test:5432/e2e",
    );
  });

  it.each([
    "postgresql://postgres:postgres@127.0.0.1:54324/postgres",
    "postgresql://postgres:postgres@localhost:54324/postgres",
    "postgresql://postgres:postgres@LOCALHOST/postgres",
    "postgresql://postgres:postgres@[::1]:5432/postgres",
  ])("falls back to a loopback DATABASE_URL: %s", (url) => {
    expect(e2eDatabaseUrl({ DATABASE_URL: url })).toBe(url);
  });

  it("refuses a hosted DATABASE_URL with a clear error that names the host and the override, never the password", () => {
    expect(() => e2eDatabaseUrl({ DATABASE_URL: HOSTED })).toThrow(/E2E_ALLOW_HOSTED=1/);
    expect(() => e2eDatabaseUrl({ DATABASE_URL: HOSTED })).toThrow(/aws-0-eu-west-2\.pooler\.supabase\.com/);
    expect(() => e2eDatabaseUrl({ DATABASE_URL: HOSTED })).not.toThrow(/secret/);
  });

  it.each([
    "postgresql://u@127.0.0.1.evil.example/db",
    "postgresql://u@localhost.example.com/db",
    "postgresql:///postgres?host=/var/run/postgresql",
    "not a url",
  ])("refuses a host that is not plainly loopback: %s", (url) => {
    expect(() => e2eDatabaseUrl({ DATABASE_URL: url })).toThrow(/Refusing/);
  });

  it("allows a hosted DATABASE_URL only with E2E_ALLOW_HOSTED=1", () => {
    expect(e2eDatabaseUrl({ DATABASE_URL: HOSTED, E2E_ALLOW_HOSTED: "1" })).toBe(HOSTED);
    expect(() => e2eDatabaseUrl({ DATABASE_URL: HOSTED, E2E_ALLOW_HOSTED: "true" })).toThrow(/Refusing/);
  });

  it("returns undefined without any database (the quick local run)", () => {
    expect(e2eDatabaseUrl({})).toBeUndefined();
    expect(e2eDatabaseUrl({ DATABASE_URL: "" })).toBeUndefined();
  });
});

describe("e2eSupabaseUrl (the project the e2e servers and test-user helpers use)", () => {
  it("uses E2E_SUPABASE_URL as given and a loopback SUPABASE_URL or VITE_SUPABASE_URL", () => {
    expect(e2eSupabaseUrl({ E2E_SUPABASE_URL: HOSTED_SB, SUPABASE_URL: HOSTED_SB })).toBe(HOSTED_SB);
    expect(e2eSupabaseUrl({ SUPABASE_URL: "http://127.0.0.1:54369" })).toBe("http://127.0.0.1:54369");
    expect(e2eSupabaseUrl({ VITE_SUPABASE_URL: "http://localhost:54321" })).toBe("http://localhost:54321");
  });

  it("refuses a hosted SUPABASE_URL (or VITE_SUPABASE_URL) unless E2E_ALLOW_HOSTED=1", () => {
    expect(() => e2eSupabaseUrl({ SUPABASE_URL: HOSTED_SB })).toThrow(/SUPABASE_URL points at host/);
    expect(() => e2eSupabaseUrl({ VITE_SUPABASE_URL: HOSTED_SB })).toThrow(/VITE_SUPABASE_URL/);
    expect(e2eSupabaseUrl({ SUPABASE_URL: HOSTED_SB, E2E_ALLOW_HOSTED: "1" })).toBe(HOSTED_SB);
  });
});

describe("e2eTargets", () => {
  it("is always explicit: empty strings when there is no database or project", () => {
    expect(e2eTargets({})).toEqual({ databaseUrl: "", supabaseUrl: "" });
  });

  it("refuses when either target is hosted", () => {
    expect(() => e2eTargets({ DATABASE_URL: "postgresql://u@127.0.0.1/db", SUPABASE_URL: HOSTED_SB })).toThrow(
      /Refusing/,
    );
    expect(() => e2eTargets({ DATABASE_URL: HOSTED, SUPABASE_URL: "http://127.0.0.1:54369" })).toThrow(/Refusing/);
  });
});

describe("isLoopbackDatabaseUrl", () => {
  it("is true only for this machine", () => {
    expect(isLoopbackDatabaseUrl("postgresql://u@127.0.0.1:5432/db")).toBe(true);
    expect(isLoopbackDatabaseUrl("postgresql://u@[::1]/db")).toBe(true);
    expect(isLoopbackDatabaseUrl(HOSTED)).toBe(false);
    expect(isLoopbackDatabaseUrl("")).toBe(false);
  });
});
