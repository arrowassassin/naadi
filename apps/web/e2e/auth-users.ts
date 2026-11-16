import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";

/**
 * Test-only: real Supabase Auth users and server-generated magic-link tokens (service role), via
 * apps/api/tests/auth/e2e_users.py. Nothing here ships in the app bundle. Tokens are never logged.
 *
 * Every run uses its own addresses (`E2E_RUN_ID`, set once in playwright.config.ts), so parallel CI jobs
 * never delete each other's users. Against the live Space the "admin" role is the owner's account
 * (first ADMIN_EMAILS entry, never printed); only booleans are asserted about it.
 */
const API_DIR = path.resolve(import.meta.dirname, "../../api");

export const ROLES = ["student", "editor", "admin", "link", "global"] as const;
export type E2ERole = (typeof ROLES)[number];

export const isLive = (): boolean => Boolean(process.env.E2E_BASE_URL);

function runId(): string {
  const id = process.env.E2E_RUN_ID;
  if (!id || !/^[a-z0-9-]{4,40}$/.test(id)) throw new Error("E2E_RUN_ID is not set (playwright.config.ts sets it)");
  return id;
}

/** The throwaway address for a role in this run (never the owner). */
export function emailFor(role: E2ERole): string {
  return `naadi-e2e-${role}-${runId()}@example.com`;
}

const usesOwner = (role: E2ERole): boolean => role === "admin" && isLive();

export const AUTH_STATE_DIR = path.resolve(import.meta.dirname, "../../../test-results/.auth");
export const authStatePath = (role: E2ERole) => path.join(AUTH_STATE_DIR, `${role}.json`);

/** A local Postgres the API under test uses (mirrors auth.users there); unset against the live Space. */
export function localDbUrl(): string | undefined {
  if (isLive()) return undefined;
  const url = process.env.E2E_DATABASE_URL ?? process.env.DATABASE_URL;
  if (!url) return undefined;
  const host = new URL(url).hostname;
  return ["localhost", "127.0.0.1", "::1"].includes(host) ? url : undefined;
}

function run(action: "link" | "delete", role: E2ERole): Record<string, unknown> {
  const who = usesOwner(role) ? ["--owner"] : ["--email", emailFor(role)];
  const args = ["--directory", API_DIR, "run", "--frozen", "python", "-m", "tests.auth.e2e_users", action, ...who];
  const db = localDbUrl();
  if (db) args.push("--local-db", db);
  const out = execFileSync("uv", args, { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"], timeout: 60_000 });
  const line = out.trim().split("\n").pop() ?? "{}";
  return JSON.parse(line) as Record<string, unknown>;
}

export interface MagicLink {
  userId: string;
  tokenHash: string;
}

/** Create (or reuse) the user and mint a single-use magic-link token for it. */
export function magicLink(role: E2ERole): MagicLink {
  const r = run("link", role);
  if (typeof r.user_id !== "string" || typeof r.token_hash !== "string") throw new Error("e2e_users link failed");
  return { userId: r.user_id, tokenHash: r.token_hash };
}

/** Delete a throwaway test user (the owner's account is never deleted). */
export function deleteUser(role: E2ERole): void {
  if (usesOwner(role)) return;
  run("delete", role);
}

/** The app's own callback URL for a generated token: the same path a real email link lands on. */
export function callbackPath(link: MagicLink, next = "/"): string {
  const q = new URLSearchParams({ token_hash: link.tokenHash, type: "magiclink" });
  if (next !== "/") q.set("next", next);
  return `/auth/callback?${q.toString()}`;
}

/** The access token saved for a role by global-setup (read from the storage-state file, not a page). */
export function storedAccessToken(role: E2ERole): string {
  const state = JSON.parse(readFileSync(authStatePath(role), "utf8")) as {
    origins: { localStorage: { name: string; value: string }[] }[];
  };
  for (const o of state.origins) {
    const item = o.localStorage.find((i) => i.name === "naadi.auth");
    if (item) return (JSON.parse(item.value) as { access_token: string }).access_token;
  }
  throw new Error(`no stored session for ${role}`);
}
