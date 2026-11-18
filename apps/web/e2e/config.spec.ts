import type { APIRequestContext, Browser, Page } from "@playwright/test";
import { canMintUsers, type E2ERole, isLive } from "./auth-users";
import { expect, test } from "./fixtures";
import { signedInState } from "./session";

/**
 * Runtime configuration (S05): an admin edits app_config through the admin API and the app picks the change
 * up with no redeploy: UI copy on the sign-in page, a feature flag + navigation item in /api/nav.
 *
 * The values are global, so this spec runs in one project only (WebKit on iPad portrait, the primary target)
 * and restores every key it touched. The keys it edits (signin.pitch, a throwaway `config_probe` flag and a
 * `config-probe` nav item that no screen uses) are not asserted by any other spec, so parallel specs see no
 * difference. The nav item hangs on the shipped `today` flag (the cross-key check reads the shipped flags). It
 * never touches a real feature's flag or nav item: Pathway is a real route now.
 */
// Local stack only: it rewrites global app_config rows, which must never happen on a deployed Space.
test.skip(isLive(), "edits global app_config: local stack only, never against a deployed Space");
test.skip(!canMintUsers(), "needs the local auth stack's service role to mint the admin and the student");

const roleState = async (browser: Browser, baseURL: string, role: E2ERole) => signedInState(browser, baseURL, role);

async function tokenOf(page: Page): Promise<string> {
  const token = await page.evaluate(() => {
    const raw = localStorage.getItem("naadi.auth");
    return raw ? (JSON.parse(raw) as { access_token: string }).access_token : null;
  });
  expect(token).toBeTruthy();
  return `Bearer ${token}`;
}

interface Entry {
  value: unknown;
  updated_at: string | null;
}

async function getKey(request: APIRequestContext, auth: string, key: string): Promise<Entry> {
  const res = await request.get(`/api/admin/config/${key}`, { headers: { authorization: auth } });
  expect(res.status()).toBe(200);
  return (await res.json()) as Entry;
}

async function putKey(
  request: APIRequestContext,
  auth: string,
  key: string,
  value: unknown,
  at: string | null,
  adminEdited = true,
) {
  const res = await request.put(`/api/admin/config/${key}`, {
    headers: { authorization: auth },
    data: { value, expected_updated_at: at, admin_edited: adminEdited },
  });
  expect(res.status(), await res.text()).toBe(200);
  return (await res.json()) as Entry;
}

test("an admin's flag, navigation and copy edits show up without a redeploy", async ({ browser, baseURL }, info) => {
  test.skip(info.project.name !== "webkit-ipad-portrait", "global config: runs in one project");
  const origin = baseURL as string;
  const adminCtx = await browser.newContext({
    baseURL: origin,
    storageState: await roleState(browser, origin, "admin"),
  });
  const studentCtx = await browser.newContext({
    baseURL: origin,
    storageState: await roleState(browser, origin, "student"),
  });
  const anonCtx = await browser.newContext({ baseURL: origin });
  const adminPage = await adminCtx.newPage();
  await adminPage.goto("/settings/account");
  const admin = await tokenOf(adminPage);
  const studentPage = await studentCtx.newPage();
  await studentPage.goto("/settings/account");
  const student = await tokenOf(studentPage);

  const flags = await getKey(adminCtx.request, admin, "feature_flags");
  const nav = await getKey(adminCtx.request, admin, "ui.nav");
  const copy = await getKey(adminCtx.request, admin, "ui_copy");
  const pitch = "Study for USMLE anywhere: iPad, phone or laptop.";
  let saved: Record<string, Entry> = {};
  try {
    saved = {
      feature_flags: await putKey(
        adminCtx.request,
        admin,
        "feature_flags",
        { ...(flags.value as object), config_probe: { enabled: true } },
        flags.updated_at,
      ),
      "ui.nav": await putKey(
        adminCtx.request,
        admin,
        "ui.nav",
        [
          ...(nav.value as object[]),
          {
            id: "config-probe",
            label: "Config probe",
            path: "/config-probe",
            icon: "calendar",
            feature: "today",
          },
        ],
        nav.updated_at,
      ),
      ui_copy: await putKey(
        adminCtx.request,
        admin,
        "ui_copy",
        { ...(copy.value as object), "signin.pitch": pitch },
        copy.updated_at,
      ),
    };

    // The student's navigation and flags change at once (the API drops its cache on write).
    const navRes = await studentCtx.request.get("/api/nav", { headers: { authorization: student } });
    const studentNav = (await navRes.json()) as { items: { id: string }[]; features: Record<string, boolean> };
    expect(studentNav.items.map((i) => i.id)).toContain("config-probe");
    expect(studentNav.features.config_probe).toBe(true);

    // A signed-out visitor's sign-in page renders the new copy (fresh browser cache, same server).
    const anon = await anonCtx.newPage();
    await anon.goto("/sign-in");
    await expect(anon.getByText(pitch)).toBeVisible();
  } finally {
    // Put every key back as it was (each with the updated_at of our own write).
    const restore: [string, Entry][] = [
      ["feature_flags", flags],
      ["ui.nav", nav],
      ["ui_copy", copy],
    ];
    for (const [key, before] of restore) {
      const mine = saved[key];
      if (!mine) continue;
      // Hand the key back to the seed: no admin_edited_at mark is left behind by the test.
      const restored = await putKey(adminCtx.request, admin, key, before.value, mine.updated_at, false);
      expect((restored as Entry & { admin_edited_at: string | null }).admin_edited_at).toBeNull();
    }
    await Promise.all([adminCtx.close(), studentCtx.close(), anonCtx.close()]);
  }
});
