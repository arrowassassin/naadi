import { uiText } from "@naadi/shared";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { handwritingSearchOn } from "./handwritingSearch";
import { InkSettingsPage } from "./InkSettingsPage";

/**
 * Settings → Pencil & ink → Handwriting search (opt-in). The real page, hooks, query cache, `authed` and error
 * normalisation run; only the generated SDK's getMe / updateMe (the network) and the auth session are replaced.
 */
const h = vi.hoisted(() => ({
  online: true,
  settings: {} as Record<string, unknown>,
  getMe: vi.fn(),
  updateMe: vi.fn(),
}));

vi.mock("@naadi/shared", async (importOriginal) => {
  const real = await importOriginal<typeof import("@naadi/shared")>();
  return { ...real, getMe: h.getMe, updateMe: h.updateMe };
});
vi.mock("../../auth/session", () => ({
  useAuth: () => ({ status: "signed-in", session: { user: { id: "u-1" } }, config: { ui_copy: null } }),
  accessToken: async () => "token",
  refreshAccessToken: async () => null,
  signOut: async () => undefined,
}));
vi.mock("../../health/useOnlineStatus", () => ({ useOnlineStatus: () => h.online }));
vi.mock("../../routes/SettingsNav", () => ({ SettingsNav: () => null }));

const t = (k: string) => uiText(null, k);

function me(settings: Record<string, unknown>) {
  return {
    email: "student@example.test",
    email_verified: true,
    features: {},
    is_admin: false,
    profile: { user_id: "u-1", display_name: null, timezone: "UTC", settings },
  };
}

const ok = (data: unknown) => ({ data, response: new Response(null, { status: 200 }) });
const fail = () => ({
  error: { error: { code: "internal_error", message: "The server is having trouble.", request_id: "rid-7" } },
  response: new Response(null, { status: 500 }),
});

function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <InkSettingsPage />
    </QueryClientProvider>,
  );
  return qc;
}

const hwSwitch = () => screen.findByRole("switch", { name: t("ink.ocr.setting_label") });

/** The status line of the handwriting tile (the drawing tile has its own). */
function hwStatus() {
  const tile = screen.getByRole("region", { name: t("ink.ocr.group") });
  return within(tile).getByRole("status");
}

beforeEach(() => {
  localStorage.clear();
  h.online = true;
  h.settings = {};
  h.getMe.mockReset();
  h.updateMe.mockReset();
  h.getMe.mockImplementation(async () => ok(me(h.settings)));
});

describe("handwritingSearchOn", () => {
  it("is on only for an explicit true", () => {
    expect(handwritingSearchOn({ handwriting_search: true })).toBe(true);
    for (const v of [{}, { handwriting_search: null }, { handwriting_search: false }, { handwriting_search: "true" }])
      expect(handwritingSearchOn(v)).toBe(false);
    expect(handwritingSearchOn(null)).toBe(false);
    expect(handwritingSearchOn(undefined)).toBe(false);
  });
});

describe("Settings → Pencil & ink → Handwriting search", () => {
  it("is off by default, says so in words and explains what would be sent", async () => {
    renderPage();
    const sw = await hwSwitch();
    expect(sw).toHaveAttribute("aria-checked", "false");
    expect(sw).toHaveTextContent(t("settings.off"));
    expect(sw).toHaveAccessibleDescription(expect.stringContaining(t("ink.ocr.setting_note")));
    expect(sw).toHaveAccessibleDescription(expect.stringContaining(t("ink.ocr.privacy_note")));
    expect(sw.className).toContain("min-h-11"); // 44 px touch target
    expect(hwStatus()).toHaveTextContent(t("ink.ocr.state_off"));
    expect(h.updateMe).not.toHaveBeenCalled(); // nothing is saved (or sent) by opening the page
  });

  it("reads an explicit true as on", async () => {
    h.settings = { handwriting_search: true, ink: { pen_only: true, predict: true } };
    renderPage();
    const sw = await hwSwitch();
    await waitFor(() => expect(sw).toHaveAttribute("aria-checked", "true"));
    expect(sw).toHaveTextContent(t("settings.on"));
    expect(hwStatus()).toHaveTextContent(t("ink.ocr.state_on"));
  });

  it("turns on optimistically with PATCH /api/me {settings: {handwriting_search: true}} only", async () => {
    h.settings = { ink: { pen_only: false, predict: true } };
    const reply = deferred<unknown>();
    h.updateMe.mockImplementation(() => reply.promise);
    renderPage();
    const sw = await hwSwitch();
    await waitFor(() => expect(sw).toBeEnabled());
    await userEvent.click(sw);
    expect(h.updateMe).toHaveBeenCalledTimes(1);
    const call = h.updateMe.mock.calls[0]?.[0] as { body: unknown; headers: Record<string, string> };
    expect(call.body).toEqual({ settings: { handwriting_search: true } }); // never inside settings.ink
    expect(call.headers.authorization).toBe("Bearer token");
    await waitFor(() => expect(sw).toHaveAttribute("aria-checked", "true")); // before the server answered
    expect(sw).toHaveTextContent(t("settings.on"));
    expect(sw).toBeDisabled(); // one change at a time
    await act(async () => reply.resolve(ok(me({ ...h.settings, handwriting_search: true }))));
    await waitFor(() => expect(sw).toBeEnabled());
    expect(sw).toHaveAttribute("aria-checked", "true");
    expect(hwStatus()).toHaveTextContent(t("ink.ocr.state_on"));
  });

  it("rolls back and shows the error when saving fails", async () => {
    h.updateMe.mockImplementation(async () => fail());
    renderPage();
    const sw = await hwSwitch();
    await waitFor(() => expect(sw).toBeEnabled());
    await userEvent.click(sw);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(t("ink.ocr.save_error"));
    expect(alert).toHaveTextContent("rid-7");
    expect(sw).toHaveAttribute("aria-checked", "false");
    expect(sw).toHaveTextContent(t("settings.off"));
  });

  it("shows the cleared confirmation after turning it off", async () => {
    h.settings = { handwriting_search: true };
    h.updateMe.mockImplementation(async () => ok(me({ handwriting_search: false })));
    renderPage();
    const sw = await hwSwitch();
    await waitFor(() => expect(sw).toHaveAttribute("aria-checked", "true"));
    await userEvent.click(sw);
    expect(h.updateMe.mock.calls[0]?.[0]).toMatchObject({ body: { settings: { handwriting_search: false } } });
    await waitFor(() => expect(hwStatus()).toHaveTextContent(t("ink.ocr.cleared")));
    expect(sw).toHaveAttribute("aria-checked", "false");
    expect(sw).toHaveTextContent(t("settings.off"));
  });

  it("is disabled offline with the reason shown", async () => {
    h.online = false;
    renderPage();
    const sw = await hwSwitch();
    expect(sw).toBeDisabled();
    expect(hwStatus()).toHaveTextContent(t("settings.offline"));
    await userEvent.click(sw);
    expect(h.updateMe).not.toHaveBeenCalled();
  });

  it("the ink saver sends only settings.ink, so the flag is never overwritten from the web", async () => {
    h.settings = { handwriting_search: true, ink: { pen_only: true, predict: true } };
    h.updateMe.mockImplementation(async ({ body }: { body: { settings: Record<string, unknown> } }) =>
      ok(me({ ...h.settings, ...body.settings })),
    );
    renderPage();
    const pen = await screen.findByRole("switch", { name: t("settings.ink_pen_only") });
    await waitFor(() => expect(pen).toBeEnabled());
    await userEvent.click(pen);
    const call = h.updateMe.mock.calls[0]?.[0] as { body: { settings: Record<string, unknown> } } | undefined;
    expect(Object.keys(call?.body.settings ?? {})).toEqual(["ink"]);
    expect(await hwSwitch()).toHaveAttribute("aria-checked", "true");
  });
});
