import type { Page, Route, TestInfo } from "@playwright/test";
import { callbackPath, canMintUsers, emailFor, magicLink } from "./auth-users";
import {
  type ConsoleGuard,
  expect,
  expectNoAxeViolations,
  expectNoHorizontalOverflow,
  snapSegment,
  test,
} from "./fixtures";

/**
 * Sign-in, callback and sign-out, signed out (no stored session). Real Supabase Auth throughout, with two
 * deliberate exceptions: the *send* of a magic-link email is intercepted (sending real email to test
 * addresses bounces, and Supabase's free sender allows only a few emails an hour), and Supabase's own
 * redirect for an expired link is replayed as the URL it produces. Real magic-link sign-ins (server-side
 * generated tokens) run in every project against the CI auth container; against the hosted project only in
 * one project per browser engine, because hosted Supabase rate-limits verifications per IP.
 */
const HOSTED_AUTH = (process.env.SUPABASE_URL ?? "").startsWith("https://");
const REAL_LINK_PROJECTS = new Set(["webkit-ipad-portrait", "chromium-laptop-1440", "firefox-phone-390"]);

const snap = (page: Page, info: TestInfo, guard: ConsoleGuard, state: string) =>
  snapSegment(page, info, guard, "03", state);

async function publicConfig(page: Page): Promise<{ google_oauth_enabled: boolean; supabase_url: string }> {
  const res = await page.request.get("/api/public-config");
  expect(res.status()).toBe(200);
  return res.json();
}

const emailBox = (page: Page) => page.getByRole("textbox", { name: "Email" });
const sendButton = (page: Page) => page.getByRole("button", { name: /Email me a sign-in link|Sending…/ });

test.describe("sign-in screen", () => {
  test("default state matches the config and passes axe", async ({ page, consoleErrors }, info) => {
    const cfg = await publicConfig(page);
    await page.goto("/sign-in");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(/Welcome\s*back\./);
    await expect(emailBox(page)).toBeVisible();
    await expect(sendButton(page)).toBeEnabled();
    const google = page.getByRole("button", { name: "Continue with Google" });
    await expect(google).toHaveCount(cfg.google_oauth_enabled ? 1 : 0);
    // the one lime action on the view
    await expect(page.locator('[data-variant="primary"]')).toHaveCount(1);
    await expectNoHorizontalOverflow(page);
    await expectNoAxeViolations(page);
    await snap(page, info, consoleErrors, "signin-default");
  });

  test("Google button is absent when Google sign-in is off", async ({ page }) => {
    await page.route("**/api/public-config", async (route) => {
      const res = await route.fetch();
      const body = await res.json();
      await route.fulfill({ response: res, json: { ...body, google_oauth_enabled: false } });
    });
    await page.goto("/sign-in");
    await expect(emailBox(page)).toBeVisible();
    await expect(page.getByRole("button", { name: "Continue with Google" })).toHaveCount(0);
  });

  test("Google starts the Supabase PKCE redirect back to /auth/callback", async ({ page }) => {
    const cfg = await publicConfig(page);
    test.skip(!cfg.google_oauth_enabled, "Google sign-in is not enabled on this deployment");
    let authorize: URL | null = null;
    await page.route(`${cfg.supabase_url}/auth/v1/authorize**`, async (route) => {
      authorize = new URL(route.request().url());
      await route.fulfill({ status: 204, body: "" });
    });
    await page.goto("/sign-in?redirect=%2Fsettings%2Faccount");
    await page.getByRole("button", { name: "Continue with Google" }).click();
    await expect.poll(() => authorize?.searchParams.get("provider") ?? null).toBe("google");
    const u = authorize as unknown as URL;
    expect(u.searchParams.get("code_challenge_method")).toBe("s256");
    expect(u.searchParams.get("code_challenge")).toBeTruthy();
    const back = new URL(u.searchParams.get("redirect_to") ?? "");
    expect(back.pathname).toBe("/auth/callback");
    expect(back.searchParams.get("next")).toBe("/settings/account");
  });

  test("an invalid address is caught before sending", async ({ page }) => {
    await page.goto("/sign-in");
    await emailBox(page).fill("not-an-email");
    await sendButton(page).click();
    await expect(emailBox(page)).toHaveAttribute("aria-invalid", "true");
    await expect(page.getByText("Enter an email address like name@example.com.")).toBeVisible();
    await expect(emailBox(page)).toBeFocused();
  });

  test("sending, then sent with a resend cooldown", async ({ page, consoleErrors }, info) => {
    const cfg = await publicConfig(page);
    let release: () => void = () => {};
    const gate = new Promise<void>((r) => {
      release = r;
    });
    let sent: Record<string, unknown> | null = null;
    await page.route(`${cfg.supabase_url}/auth/v1/otp**`, async (route: Route) => {
      sent = route.request().postDataJSON();
      await gate;
      await route.fulfill({ status: 200, json: {} });
    });
    await page.goto("/sign-in?redirect=%2Fsettings%2Faccount");
    await emailBox(page).fill("naadi-e2e-nobody@example.com");
    await sendButton(page).click();
    await expect(page.getByRole("button", { name: "Sending…" })).toBeDisabled();
    await expect(page.locator("form")).toHaveAttribute("aria-busy", "true");
    await expectNoAxeViolations(page);
    await snap(page, info, consoleErrors, "signin-sending");
    release();
    await expect(page.getByRole("heading", { name: "Check your email" })).toBeFocused();
    await expect(page.getByText("naadi-e2e-nobody@example.com")).toBeVisible();
    await expect(page.getByRole("button", { name: /Resend in \d+ s/ })).toBeDisabled();
    expect(sent).not.toBeNull();
    const body = sent as unknown as { email: string; code_challenge?: string; create_user?: boolean };
    expect(body.email).toBe("naadi-e2e-nobody@example.com");
    expect(body.code_challenge).toBeTruthy(); // PKCE
    expect(page.url()).toContain("/sign-in");
    await expectNoAxeViolations(page);
    await snap(page, info, consoleErrors, "signin-sent");
    await page.getByRole("button", { name: "Use a different email" }).click();
    await expect(emailBox(page)).toBeFocused();
  });

  test("send failure shows a clear error", async ({ page, consoleErrors }, info) => {
    const cfg = await publicConfig(page);
    consoleErrors.allowHttpError({ urlPart: "/auth/v1/otp", status: 429 });
    await page.route(`${cfg.supabase_url}/auth/v1/otp**`, (route) =>
      route.fulfill({
        status: 429,
        json: { code: 429, error_code: "over_email_send_rate_limit", msg: "email rate limit exceeded" },
      }),
    );
    await page.goto("/sign-in");
    await emailBox(page).fill("naadi-e2e-nobody@example.com");
    await sendButton(page).click();
    const alert = page.getByRole("alert");
    await expect(alert).toContainText("No email sent");
    await expect(alert).toContainText("Too many sign-in emails were requested");
    await expect(sendButton(page)).toBeEnabled();
    await expectNoAxeViolations(page);
    await snap(page, info, consoleErrors, "signin-error");
  });

  test("offline disables sending and says why", async ({ page, context, consoleErrors }, info) => {
    await page.goto("/sign-in");
    await expect(emailBox(page)).toBeVisible();
    // Faces the page has not needed yet, fetched while online (a returning visitor's cache has them).
    await page.evaluate(() =>
      Promise.all([...document.fonts].filter((f) => f.family.includes("JetBrains")).map((f) => f.load())),
    );
    await context.setOffline(true);
    await expect(page.getByRole("status").filter({ hasText: "Offline" })).toContainText(
      "Connect to the internet to sign in.",
    );
    await expect(sendButton(page)).toBeDisabled();
    await expectNoAxeViolations(page);
    await snap(page, info, consoleErrors, "signin-offline");
    await context.setOffline(false);
    await expect(sendButton(page)).toBeEnabled();
  });
});

test.describe("callback", () => {
  test("Supabase's expired-link redirect lands on sign-in with a notice", async ({ page, consoleErrors }, info) => {
    await page.goto(
      "/auth/callback?next=%2Fsettings%2Faccount#error=access_denied&error_code=otp_expired&error_description=Email+link+is+invalid+or+has+expired",
    );
    await expect(page).toHaveURL(/\/sign-in\?.*error=expired/);
    expect(new URL(page.url()).searchParams.get("redirect")).toBe("/settings/account");
    await expect(page.getByRole("status").filter({ hasText: "That sign-in link has expired" })).toBeVisible();
    await expectNoAxeViolations(page);
    await snap(page, info, consoleErrors, "signin-expired");
  });

  test("processing, then a used token is reported as expired", async ({ page, consoleErrors }, info) => {
    const cfg = await publicConfig(page);
    consoleErrors.allowHttpError({ urlPart: "/auth/v1/verify", status: 403 });
    let release: () => void = () => {};
    const gate = new Promise<void>((r) => {
      release = r;
    });
    await page.route(`${cfg.supabase_url}/auth/v1/verify**`, async (route) => {
      await gate;
      await route.fulfill({
        status: 403,
        json: { code: 403, error_code: "otp_expired", msg: "Email link is invalid or has expired" },
      });
    });
    await page.goto(
      "/auth/callback?token_hash=0123456789abcdef0123456789abcdef0123456789abcdef01234567&type=magiclink",
    );
    await expect(page.getByRole("heading", { name: "Signing you in…" })).toBeVisible();
    await expect(page.locator("[data-state=processing]")).toHaveAttribute("aria-busy", "true");
    await expectNoAxeViolations(page);
    await snap(page, info, consoleErrors, "callback-processing");
    release();
    await expect(page).toHaveURL(/\/sign-in\?.*error=expired/);
  });

  test("a link with nothing to redeem shows the error state", async ({ page, consoleErrors }, info) => {
    await page.goto("/auth/callback");
    await expect(page.getByRole("heading", { name: "Sign-in did not finish" })).toBeVisible();
    await expect(page.getByRole("alert")).toContainText("incomplete or not valid");
    await expectNoAxeViolations(page);
    await snap(page, info, consoleErrors, "callback-error");
    await page.getByRole("button", { name: "Back to sign in" }).click();
    await expect(page).toHaveURL(/\/sign-in/);
  });

  test("an open redirect in next is ignored", async ({ page }) => {
    await page.goto("/auth/callback?next=https%3A%2F%2Fevil.example%2F#error=access_denied&error_code=otp_expired");
    await expect(page).toHaveURL(/\/sign-in/);
    expect(new URL(page.url()).searchParams.get("redirect")).toBe("/");
  });
});

test.describe("route protection", () => {
  for (const path of ["/", "/settings/account", "/settings"]) {
    test(`${path} redirects to sign-in when signed out`, async ({ page }) => {
      await page.goto(path);
      await expect(page).toHaveURL(/\/sign-in/);
      const want = path === "/settings" ? "/settings" : path;
      expect(new URL(page.url()).searchParams.get("redirect") ?? "/").toBe(want);
      await expect(emailBox(page)).toBeVisible();
    });
  }

  test("protected API routes reject missing and forged tokens", async ({ request }) => {
    for (const path of ["/api/me", "/api/nav", "/api/admin/overview"]) {
      const none = await request.get(path);
      expect(none.status()).toBe(401);
      // a private Space's proxy forwards the HF token header, which the API then rejects as invalid
      expect((await none.json()).error.code).toMatch(/^(unauthorized|invalid_token)$/);
      const forged = await request.get(path, {
        headers: {
          authorization: "Bearer eyJhbGciOiJFUzI1NiIsImtpZCI6Im5vcGUiLCJ0eXAiOiJKV1QifQ.eyJzdWIiOiJ4In0.c2lnbmF0dXJl",
        },
      });
      expect(forged.status()).toBe(401);
    }
  });
});

test.describe("magic link, end to end", () => {
  test("sign in from a real link, survive reload, sign out", async ({ page, consoleErrors }, info) => {
    test.skip(!canMintUsers(), "needs the service role (live: pnpm e2e:live-auth)");
    test.skip(HOSTED_AUTH && !REAL_LINK_PROJECTS.has(info.project.name), "hosted: once per browser engine");
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

  test("sign out of all devices revokes the session everywhere", async ({ browser, baseURL }, info) => {
    test.skip(!canMintUsers(), "needs the service role (live: pnpm e2e:live-auth)");
    test.skip(info.project.name !== "chromium-laptop-1440", "global sign-out runs once");
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

  test("a real magic-link email: send, open the link, signed in (PKCE)", async ({ page, request }, info) => {
    const mailpit = process.env.MAILPIT_URL;
    test.skip(!mailpit, "needs the local auth stack's mail catcher (auth_stack.sh)");
    test.skip(info.project.name !== "webkit-ipad-portrait", "one WebKit run: the email path is browser-independent");
    const address = emailFor("mail");
    await page.goto("/sign-in?redirect=%2Fsettings%2Faccount");
    await emailBox(page).fill(address);
    await sendButton(page).click();
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
