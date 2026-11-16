import { describe, expect, it } from "vitest";
import { e2eDatabaseUrl } from "../../e2e/databaseUrl";

const HOSTED = "postgresql://postgres.ref:secret@aws-0-eu-west-2.pooler.supabase.com:5432/postgres";

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

  it("refuses a hosted DATABASE_URL with a clear error that names the override but never the URL", () => {
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
