import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";

/**
 * Test-only: throwaway Supabase Auth users and server-generated magic-link tokens (service role), via
 * apps/api/tests/auth/e2e_users.py. Nothing here ships in the app bundle. Tokens are never logged.
 *
 * Every run uses its own addresses (`E2E_RUN_ID`, set once in playwright.config.ts), so parallel CI jobs
 * never delete each other's users. In CI the "project" is Supabase's auth server in a container; locally
 * it can be the hosted project.
 */
const API_DIR = path.resolve(import.meta.dirname, "../../api");

// `adminb` and `adminc` are the second and third admin of the review flows (S21: two blind reviewers, adjudication).
export const ROLES = ["student", "editor", "admin", "adminb", "adminc", "link", "global", "mail"] as const;
export type E2ERole = (typeof ROLES)[number];

export const isLive = (): boolean => Boolean(process.env.E2E_BASE_URL);

/** Throwaway users can be minted (service role available) and the target is not the live Space. */
export function canMintUsers(): boolean {
  return (
    !isLive() &&
    Boolean(process.env.SUPABASE_URL && process.env.SUPABASE_ANON_KEY && process.env.SUPABASE_SERVICE_ROLE_KEY)
  );
}

function runId(): string {
  const id = process.env.E2E_RUN_ID;
  if (!id || !/^[a-z0-9-]{4,40}$/.test(id)) throw new Error("E2E_RUN_ID is not set (playwright.config.ts sets it)");
  return id;
}

/** The throwaway address for a role in this run. */
export function emailFor(role: E2ERole): string {
  return `naadi-e2e-${role}-${runId()}@example.com`;
}

export const AUTH_STATE_DIR = path.resolve(import.meta.dirname, "../../../test-results/.auth");
/** One storage state per role and origin (localStorage is per origin; the e2e may run two API servers). */
export const authStatePath = (role: E2ERole, baseURL?: string) =>
  path.join(AUTH_STATE_DIR, baseURL ? `${role}-${new URL(baseURL).port || "80"}.json` : `${role}.json`);

/**
 * A local Postgres the API under test uses, when the auth server is the hosted project (whose users then
 * have to be mirrored into the local auth.users). Unset for the local auth container (same database) and
 * against the live Space.
 */
export function localDbUrl(): string | undefined {
  if (isLive() || !(process.env.SUPABASE_URL ?? "").startsWith("https://")) return undefined;
  const url = process.env.E2E_DATABASE_URL ?? process.env.DATABASE_URL;
  if (!url) return undefined;
  const host = new URL(url).hostname;
  return ["localhost", "127.0.0.1", "::1"].includes(host) ? url : undefined;
}

export function runUsers(args: string[], withLocalDb = true): Record<string, unknown> {
  const all = ["--directory", API_DIR, "run", "--frozen", "python", "-m", "tests.auth.e2e_users", ...args];
  const db = withLocalDb ? localDbUrl() : undefined;
  if (db) all.push("--local-db", db);
  const out = execFileSync("uv", all, { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"], timeout: 60_000 });
  const line = out.trim().split("\n").pop() ?? "{}";
  return JSON.parse(line) as Record<string, unknown>;
}

export interface MagicLink {
  userId: string;
  tokenHash: string;
}

export function toLink(r: Record<string, unknown>): MagicLink {
  if (typeof r.user_id !== "string" || typeof r.token_hash !== "string") throw new Error("e2e_users link failed");
  return { userId: r.user_id, tokenHash: r.token_hash };
}

/** Create (or reuse) the role's throwaway user and mint a single-use magic-link token for it. */
export function magicLink(role: E2ERole): MagicLink {
  return toLink(runUsers(["link", "--email", emailFor(role)]));
}

export function deleteUser(role: E2ERole): void {
  runUsers(["delete", "--email", emailFor(role)]);
}

/** The app's own callback URL for a generated token: the same path a real email link lands on. */
export function callbackPath(link: MagicLink, next = "/"): string {
  const q = new URLSearchParams({ token_hash: link.tokenHash, type: "magiclink" });
  if (next !== "/") q.set("next", next);
  return `/auth/callback?${q.toString()}`;
}

/** The access token in a saved storage state. */
export function accessTokenIn(stateFile: string): string {
  const state = JSON.parse(readFileSync(stateFile, "utf8")) as {
    origins: { localStorage: { name: string; value: string }[] }[];
  };
  for (const o of state.origins) {
    const item = o.localStorage.find((i) => i.name === "naadi.auth");
    if (item) return (JSON.parse(item.value) as { access_token: string }).access_token;
  }
  throw new Error("no stored session");
}
