import { execFileSync } from "node:child_process";
import path from "node:path";

/**
 * Test-only: real Supabase Auth users and server-generated magic-link tokens (service role), via
 * apps/api/tests/auth/e2e_users.py. Nothing here ships in the app bundle. Tokens are never logged.
 */
const API_DIR = path.resolve(import.meta.dirname, "../../api");

export const E2E_USERS = {
  student: "naadi-e2e-student@example.com",
  admin: "naadi-e2e-admin@example.com",
  link: "naadi-e2e-link@example.com",
  global: "naadi-e2e-global@example.com",
} as const;
export type E2ERole = keyof typeof E2E_USERS;

export const AUTH_STATE_DIR = path.resolve(import.meta.dirname, "../../../test-results/.auth");
export const authStatePath = (role: E2ERole) => path.join(AUTH_STATE_DIR, `${role}.json`);

/** A local Postgres the API under test uses (mirrors auth.users there); unset against the live Space. */
export function localDbUrl(): string | undefined {
  if (process.env.E2E_BASE_URL) return undefined;
  const url = process.env.E2E_DATABASE_URL ?? process.env.DATABASE_URL;
  if (!url) return undefined;
  const host = new URL(url).hostname;
  return ["localhost", "127.0.0.1", "::1"].includes(host) ? url : undefined;
}

function run(action: "link" | "delete", email: string): Record<string, unknown> {
  const args = [
    "--directory",
    API_DIR,
    "run",
    "--frozen",
    "python",
    "-m",
    "tests.auth.e2e_users",
    action,
    "--email",
    email,
  ];
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
  const r = run("link", E2E_USERS[role]);
  if (typeof r.user_id !== "string" || typeof r.token_hash !== "string") throw new Error("e2e_users link failed");
  return { userId: r.user_id, tokenHash: r.token_hash };
}

export function deleteUser(role: E2ERole): void {
  run("delete", E2E_USERS[role]);
}

/** The app's own callback URL for a generated token: the same path a real email link lands on. */
export function callbackPath(link: MagicLink, next = "/"): string {
  const q = new URLSearchParams({ token_hash: link.tokenHash, type: "magiclink" });
  if (next !== "/") q.set("next", next);
  return `/auth/callback?${q.toString()}`;
}
