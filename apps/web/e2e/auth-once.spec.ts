import type { Page, TestInfo } from "@playwright/test";
import { callbackPath, emailFor, magicLink } from "./auth-users";
import { type ConsoleGuard, expect, snapSegment, test } from "./fixtures";

/**
 * Flows that run exactly once per run, in the `webkit-auth-once` project (WebKit, iPad portrait; see
 * playwright.config.ts), because they revoke sessions or read the local mail catcher:
 * - a server-generated magic link through /auth/callback, reload, sign out;
 * - "sign out of all devices" revoking the other device's session (refresh token and API access);
 * - the real magic-link email: sent by Supabase's auth server, caught by Mailpit, /verify -> ?code= (PKCE).
 */
const emailBox = (page: Page) => page.getByRole("textbox", { name: "Email" });
const sendButton = (page: Page) => page.getByRole("button", { name: /Email me a sign-in link|Sending…/ });
const snap = (page: Page, info: TestInfo, guard: ConsoleGuard, state: string) =>
  snapSegment(page, info, guard, "03", state);
void snap;

test.describe("magic link, end to end", () => {
  test("sign in from a real link, survive reload, sign out", async ({ page, consoleErrors }) => {
    const meCalls: number[] = [];
    page.on("response", (r) => {
      if (new URL(r.url()).pathname === "/api/me") meCalls.push(r.status());
    });
    await page.goto(callbackPath(magicLink("link"), "/settings/account"));
    await expect(page).toHaveURL(/\/settings\/account$/);
    await expect(page.getByRole("heading", { name: "Account", level: 1 })).toBeVisible();
    await expect(page.getByRole("heading", { name: emailFor("link") })).toBeVisible();
    await expect(page.getByText("Email verified")).toBeVisible();
    expect(page.url()).not.toContain("token_hash");
    expect(meCalls).toContain(200);

    await page.reload();
    await expect(page.getByRole("heading", { name: "Account", level: 1 })).toBeVisible();
    await expect(page).toHaveURL(/\/settings\/account$/);

    await page.getByRole("button", { name: "Sign out", exact: true }).click();
    await expect(page).toHaveURL(/\/sign-in\?signed_out=1/);
    await expect(page.getByText("You are signed out.")).toBeVisible();
    expect(await page.evaluate(() => localStorage.getItem("naadi.auth"))).toBeNull();
    await page.goto("/");
    await expect(page).toHaveURL(/\/sign-in/);
    void consoleErrors;
  });

  test("sign out of all devices revokes the session everywhere", async ({ browser, baseURL }) => {
    // Two sign-ins, the global sign-out, then up to the API's 30 s session cache before device B is refused.
    test.setTimeout(150_000);
    const a = await browser.newContext({ baseURL });
    const b = await browser.newContext({ baseURL });
    const pa = await a.newPage();
    const pb = await b.newPage();
    await pa.goto(callbackPath(magicLink("global"), "/settings/account"));
    await expect(pa.getByRole("heading", { name: "Account", level: 1 })).toBeVisible();
    await pb.goto(callbackPath(magicLink("global"), "/"));
    await expect(pb).toHaveURL(/\/$/);
    const tokenB = await pb.evaluate(() => {
      const raw = localStorage.getItem("naadi.auth");
      return raw ? (JSON.parse(raw) as { access_token: string }).access_token : "";
    });
    const meB = () => b.request.get("/api/me", { headers: { authorization: `Bearer ${tokenB}` } });
    expect((await meB()).status()).toBe(200);

    await pa.getByRole("button", { name: "Sign out of all devices" }).click();
    await pa.getByRole("button", { name: "Sign out everywhere" }).click();
    await expect(pa).toHaveURL(/\/sign-in/);

    // The other device's refresh token is revoked: refreshing fails and the app signs it out.
    const refreshed = await pb.evaluate(async () => {
      const raw = localStorage.getItem("naadi.auth");
      if (!raw) return "no-session";
      const s = JSON.parse(raw) as { refresh_token: string };
      const cfg = await (await fetch("/api/public-config")).json();
      const r = await fetch(`${cfg.supabase_url}/auth/v1/token?grant_type=refresh_token`, {
        method: "POST",
        headers: { apikey: cfg.supabase_anon_key, "content-type": "application/json" },
        body: JSON.stringify({ refresh_token: s.refresh_token }),
      });
      return r.status;
    });
    expect(refreshed).not.toBe(200);
    // And device B's still-unexpired access token no longer opens the API (session lookup, cached <= 30 s).
    await expect
      .poll(
        async () => {
          const r = await meB();
          return r.status() === 401 ? (await r.json()).error.code : r.status();
        },
        { timeout: 45_000, intervals: [2_000] },
      )
      .toBe("session_revoked");
    await a.close();
    await b.close();
  });

  test("a real magic-link email: send, open the link, signed in (PKCE)", async ({ page, request }) => {
    const mailpit = process.env.MAILPIT_URL;
    if (!mailpit) throw new Error("MAILPIT_URL is not set (the webkit-auth-once project needs auth_stack.sh)");
    const address = emailFor("mail");
    await page.goto("/sign-in?redirect=%2Fsettings%2Faccount");
    await emailBox(page).fill(address);
    const sent = page.waitForResponse((r) => new URL(r.url()).pathname.endsWith("/auth/v1/otp"));
    await sendButton(page).click();
    const otp = await sent;
    // Status and error code only (never the body's address): says whether the auth server failed to send.
    const failure = otp.ok()
      ? ""
      : String(((await otp.json().catch(() => ({}))) as { error_code?: string }).error_code);
    expect(otp.status(), `POST /auth/v1/otp failed: ${failure}`).toBe(200);
    await expect(page.getByRole("heading", { name: "Check your email" })).toBeVisible();

    // The email Supabase's auth server actually sent, caught by Mailpit.
    let link = "";
    await expect
      .poll(
        async () => {
          const search = await request.get(`${mailpit}/api/v1/search`, { params: { query: `to:"${address}"` } });
          const found = (await search.json()) as { messages?: { ID: string }[] };
          const id = found.messages?.[0]?.ID;
          if (!id) return "";
          const msg = (await (await request.get(`${mailpit}/api/v1/message/${id}`)).json()) as {
            HTML?: string;
            Text?: string;
          };
          const body = `${msg.HTML ?? ""} ${msg.Text ?? ""}`.replaceAll("&amp;", "&");
          link = /https?:\/\/[^\s"'<>]+\/verify\?[^\s"'<>]+/.exec(body)?.[0] ?? "";
          return link;
        },
        { timeout: 30_000, intervals: [1_000] },
      )
      .not.toBe("");
    expect(new URL(link).searchParams.get("type")).toBe("magiclink");
    expect(new URL(link).searchParams.get("redirect_to")).toContain("/auth/callback");

    // Opening it: /verify -> redirect to /auth/callback?code=... -> the app exchanges the code (PKCE).
    const codes: string[] = [];
    page.on("request", (r) => {
      const u = new URL(r.url());
      if (u.pathname === "/auth/callback" && u.searchParams.has("code")) codes.push("code");
    });
    await page.goto(link);
    await expect(page).toHaveURL(/\/settings\/account$/);
    expect(codes).toEqual(["code"]);
    await expect(page.getByRole("heading", { name: address })).toBeVisible();
  });
});
