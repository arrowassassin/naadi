import { randomBytes } from "node:crypto";
import { mkdirSync } from "node:fs";
import path from "node:path";
import { expect, test } from "@playwright/test";
import { callbackPath, runUsers, toLink } from "../e2e/auth-users";
import { cookieHeader, nodeFetch, spaceSignature } from "./net";

/**
 * Real-project auth through the deployed Space. A throwaway user signs in with a server-generated magic
 * link in WebKit; then the owner's admin flag is checked through the API. Nothing about the owner is
 * printed or rendered: only `is_admin === true` is asserted, and the session created for that check is
 * revoked at once. The throwaway user is deleted at the end whatever happens.
 *
 * Secrets never touch Playwright (its call log ends up in error-context.md on a failure): the HF token exchange,
 * every request with a session token, the Supabase verify/logout calls and the Space cookie go through Node fetch
 * (./net.ts). The page only ever receives the short-lived `__sign` value. The config's global teardown scans
 * test-results for every secret's value afterwards (scripts/scan_artifacts.py) and fails the run on a match.
 */
const RUN = `live-${randomBytes(4).toString("hex")}`;
const EMAIL = `naadi-e2e-live-${RUN}@example.com`;
const SCREENS = path.resolve(import.meta.dirname, "../../../test-results/screens/03-live");

function need(name: string): string {
  const v = process.env[name];
  if (!v) throw new Error(`${name} must be set`);
  return v;
}

test.beforeAll(() => {
  runUsers(["sweep", "--prefix", "naadi-e2e-live-"], false); // a crashed earlier run left its user behind
});

test.afterAll(() => {
  runUsers(["delete", "--email", EMAIL], false);
});

test("real sign-in on the live Space; /api/me; owner is admin", async ({ page, context, baseURL }) => {
  need("SUPABASE_URL");
  need("SUPABASE_SERVICE_ROLE_KEY");
  const base = (baseURL as string).replace(/\/$/, "");
  const privateSpace = new URL(base).hostname.endsWith(".hf.space");
  if (privateSpace) {
    const sign = await spaceSignature(need("HF_SPACE_ID"), need("HF_TOKEN"));
    await page.goto(`/?__sign=${encodeURIComponent(sign)}`);
  }
  // Node-side calls to the Space carry the Space cookie the browser now holds (never through Playwright).
  const spaceCookie = privateSpace ? cookieHeader(await context.cookies(base)) : "";
  const api = (path: string, bearer?: string) =>
    nodeFetch(`${base}${path}`, {
      headers: {
        ...(spaceCookie ? { cookie: spaceCookie } : {}),
        ...(bearer ? { authorization: `Bearer ${bearer}` } : {}),
      },
    });

  const cfgRes = await api("/api/public-config");
  expect(cfgRes.status, "public config").toBe(200);
  const cfg = await cfgRes.json<{ supabase_url: string; supabase_anon_key: string; auth_configured: boolean }>();
  expect(cfg.auth_configured).toBe(true);

  // 1. A throwaway student signs in through the app's own callback (Supabase verifies the token).
  const link = toLink(runUsers(["link", "--email", EMAIL], false));
  await page.goto(callbackPath(link, "/settings/account"));
  await expect(page).toHaveURL(/\/settings\/account$/);
  await expect(page.getByRole("heading", { name: "Account", level: 1 })).toBeVisible();
  await expect(page.getByRole("heading", { name: EMAIL })).toBeVisible();
  mkdirSync(SCREENS, { recursive: true });
  await page.screenshot({ path: path.join(SCREENS, "account--webkit-ipad-portrait.png"), animations: "disabled" });

  const token = await page.evaluate(() => {
    const raw = localStorage.getItem("naadi.auth");
    return raw ? (JSON.parse(raw) as { access_token: string }).access_token : null;
  });
  expect(token, "the student's session is in the page").toBeTruthy();
  const me = await api("/api/me", token as string);
  expect(me.status, "/api/me as the student").toBe(200);
  const body = await me.json<{ profile: { user_id: string }; is_admin: boolean }>();
  expect(body.profile.user_id).toBe(link.userId);
  expect(body.is_admin).toBe(false);
  expect((await api("/api/admin/overview", token as string)).status, "admin overview as the student").toBe(403);

  // 2. The owner (first ADMIN_EMAILS entry) is an admin: verified in Node, never rendered or printed.
  const owner = toLink(runUsers(["link", "--owner"], false));
  const verify = await nodeFetch(`${cfg.supabase_url}/auth/v1/verify`, {
    method: "POST",
    headers: { apikey: cfg.supabase_anon_key, "content-type": "application/json" },
    body: JSON.stringify({ type: "magiclink", token_hash: owner.tokenHash }),
  });
  expect(verify.status, "owner magic-link verification").toBe(200);
  const ownerToken = (await verify.json<{ access_token: string }>()).access_token;
  try {
    const ownerMe = await api("/api/me", ownerToken);
    expect(ownerMe.status, "/api/me as the owner").toBe(200);
    expect((await ownerMe.json<{ is_admin: boolean }>()).is_admin).toBe(true);
  } finally {
    const out = await nodeFetch(`${cfg.supabase_url}/auth/v1/logout?scope=local`, {
      method: "POST",
      headers: { apikey: cfg.supabase_anon_key, authorization: `Bearer ${ownerToken}` },
    });
    expect(out.status, "the owner session created for this check is revoked").toBe(204);
  }
});
