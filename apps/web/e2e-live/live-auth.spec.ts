import { randomBytes } from "node:crypto";
import { mkdirSync } from "node:fs";
import path from "node:path";
import { type APIRequestContext, expect, test } from "@playwright/test";
import { callbackPath, runUsers, toLink } from "../e2e/auth-users";

/**
 * Real-project auth through the deployed Space. A throwaway user signs in with a server-generated magic
 * link in WebKit; then the owner's admin flag is checked through the API. Nothing about the owner is
 * printed or rendered: only `is_admin === true` is asserted, and the session created for that check is
 * revoked at once. The throwaway user is deleted at the end whatever happens.
 */
const RUN = `live-${randomBytes(4).toString("hex")}`;
const EMAIL = `naadi-e2e-live-${RUN}@example.com`;
const SCREENS = path.resolve(import.meta.dirname, "../../../test-results/screens/03-live");

function need(name: string): string {
  const v = process.env[name];
  if (!v) throw new Error(`${name} must be set`);
  return v;
}

/** Private Space: exchange HF_TOKEN for a Space JWT once; `?__sign=` sets the Space's own cookie. */
async function spaceSignature(request: APIRequestContext, baseURL: string): Promise<string | null> {
  if (!new URL(baseURL).hostname.endsWith(".hf.space")) return null;
  const res = await request.get(`https://huggingface.co/api/spaces/${need("HF_SPACE_ID")}/jwt`, {
    headers: { authorization: `Bearer ${need("HF_TOKEN")}` },
  });
  expect(res.status(), "HF Space JWT").toBe(200);
  return ((await res.json()) as { token: string }).token;
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
  const base = baseURL as string;
  const sign = await spaceSignature(context.request, base);
  if (sign) await page.goto(`/?__sign=${encodeURIComponent(sign)}`);

  const cfg = (await (await context.request.get("/api/public-config")).json()) as {
    supabase_url: string;
    supabase_anon_key: string;
    auth_configured: boolean;
  };
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
  expect(token).toBeTruthy();
  const me = await context.request.get("/api/me", { headers: { authorization: `Bearer ${token}` } });
  expect(me.status()).toBe(200);
  const body = (await me.json()) as { profile: { user_id: string }; is_admin: boolean };
  expect(body.profile.user_id).toBe(link.userId);
  expect(body.is_admin).toBe(false);
  const denied = await context.request.get("/api/admin/overview", { headers: { authorization: `Bearer ${token}` } });
  expect(denied.status()).toBe(403);

  // 2. The owner (first ADMIN_EMAILS entry) is an admin: verified in Node, never rendered or printed.
  const owner = toLink(runUsers(["link", "--owner"], false));
  const verify = await context.request.post(`${cfg.supabase_url}/auth/v1/verify`, {
    headers: { apikey: cfg.supabase_anon_key, "content-type": "application/json" },
    data: { type: "magiclink", token_hash: owner.tokenHash },
  });
  expect(verify.status(), "owner magic-link verification").toBe(200);
  const ownerToken = ((await verify.json()) as { access_token: string }).access_token;
  try {
    const ownerMe = await context.request.get("/api/me", { headers: { authorization: `Bearer ${ownerToken}` } });
    expect(ownerMe.status()).toBe(200);
    expect(((await ownerMe.json()) as { is_admin: boolean }).is_admin).toBe(true);
  } finally {
    const out = await context.request.post(`${cfg.supabase_url}/auth/v1/logout?scope=local`, {
      headers: { apikey: cfg.supabase_anon_key, authorization: `Bearer ${ownerToken}` },
    });
    expect(out.status(), "the owner session created for this check is revoked").toBe(204);
  }
});
