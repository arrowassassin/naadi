import { existsSync, mkdirSync, readFileSync, rmSync } from "node:fs";
import type { Browser } from "@playwright/test";
import { AUTH_STATE_DIR, authStatePath, callbackPath, type E2ERole, magicLink } from "./auth-users";

/**
 * A signed-in browser storage state per role, shared by every test of the run and renewed when its access
 * token is close to expiry (a full matrix run takes longer than one token lifetime). Renewal signs in again
 * through the real /auth/callback; a lock directory makes sure only one worker renews a role at a time, so
 * parallel contexts never race on one refresh token (Supabase revokes a session on refresh-token reuse).
 */
const MIN_REMAINING_S = 20 * 60;
const LOCK_STALE_MS = 120_000;

function accessTokenExp(file: string): number | null {
  try {
    const state = JSON.parse(readFileSync(file, "utf8")) as {
      origins: { localStorage: { name: string; value: string }[] }[];
    };
    for (const o of state.origins) {
      const item = o.localStorage.find((i) => i.name === "naadi.auth");
      if (!item) continue;
      const token = (JSON.parse(item.value) as { access_token: string }).access_token;
      const payload = JSON.parse(Buffer.from(token.split(".")[1] ?? "", "base64url").toString("utf8")) as {
        exp?: number;
      };
      return typeof payload.exp === "number" ? payload.exp : null;
    }
  } catch {
    return null;
  }
  return null;
}

function fresh(file: string): boolean {
  const exp = accessTokenExp(file);
  return exp !== null && exp - Date.now() / 1000 > MIN_REMAINING_S;
}

async function acquire(lock: string): Promise<boolean> {
  const deadline = Date.now() + LOCK_STALE_MS;
  while (Date.now() < deadline) {
    try {
      mkdirSync(lock);
      return true;
    } catch {
      await new Promise((r) => setTimeout(r, 250));
    }
  }
  rmSync(lock, { recursive: true, force: true }); // a crashed holder: take over
  mkdirSync(lock);
  return true;
}

/**
 * Shared roles land on Today, which sends a student to /welcome until onboarding is done (segment 11): every
 * shared role completes it once through the real API (Step 1, an exam ten weeks out, six days a week).
 * today.spec.ts runs the onboarding flow itself with per-test users.
 */
export async function ensureOnboarded(page: import("@playwright/test").Page): Promise<void> {
  const token = await page.evaluate(() => {
    const raw = localStorage.getItem("naadi.auth");
    return raw ? (JSON.parse(raw) as { access_token: string }).access_token : "";
  });
  const headers = { authorization: `Bearer ${token}` };
  const state = await page.request.get("/api/onboarding", { headers });
  if (!state.ok()) throw new Error(`GET /api/onboarding: ${state.status()}`);
  const body = (await state.json()) as { completed_at: string | null; today: string };
  if (body.completed_at) return;
  const examDate = new Date(`${body.today}T00:00:00Z`);
  examDate.setUTCDate(examDate.getUTCDate() + 70);
  const res = await page.request.put("/api/onboarding", {
    headers,
    data: {
      exam: "step1",
      exam_date: examDate.toISOString().slice(0, 10),
      daily_hours: 8,
      study_days: [1, 2, 3, 4, 5, 6],
      materials: [],
    },
  });
  if (!res.ok()) throw new Error(`PUT /api/onboarding: ${res.status()}`);
}

export async function signedInState(browser: Browser, baseURL: string, role: E2ERole): Promise<string> {
  const file = authStatePath(role, baseURL);
  if (existsSync(file) && fresh(file)) return file;
  mkdirSync(AUTH_STATE_DIR, { recursive: true });
  const lock = `${file}.lock`;
  await acquire(lock);
  try {
    if (existsSync(file) && fresh(file)) return file; // another worker renewed it meanwhile
    const context = await browser.newContext({ baseURL });
    try {
      const page = await context.newPage();
      await page.goto(callbackPath(magicLink(role), "/settings/account"));
      await page.waitForURL((u) => u.pathname === "/settings/account", { timeout: 30_000 });
      await page.getByRole("heading", { name: "Account", level: 1 }).waitFor({ timeout: 30_000 });
      await ensureOnboarded(page);
      await context.storageState({ path: file });
    } finally {
      await context.close();
    }
    return file;
  } finally {
    rmSync(lock, { recursive: true, force: true });
  }
}
