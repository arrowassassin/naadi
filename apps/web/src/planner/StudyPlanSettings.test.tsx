import { uiText } from "@naadi/shared";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { StudyPlanSettings } from "./StudyPlanSettings";

const h = vi.hoisted(() => ({
  feed: {
    data: { active: false, created_at: null } as unknown,
    isPending: false,
    isError: false,
    isFetching: false,
    refetch: vi.fn(),
  },
  rotate: { mutate: vi.fn(), isPending: false },
  revoke: { mutate: vi.fn(), isPending: false },
  key: {
    data: { configured: true, public_key: "AQID" } as unknown,
    isPending: false,
    isError: false,
    isFetching: false,
    refetch: vi.fn(),
  },
  devices: { data: { configured: true, subscriptions: [] } as unknown, isPending: false },
  add: { mutate: vi.fn(), isPending: false },
  remove: { mutate: vi.fn(), isPending: false },
  test: { mutate: vi.fn(), isPending: false },
  support: "ready" as "ready" | "unsupported" | "not_standalone",
  online: true,
  subscribed: null as string | null,
}));
vi.mock("./studyPlanApi", () => ({
  useCalendarFeed: () => h.feed,
  useRotateFeed: () => h.rotate,
  useRevokeFeed: () => h.revoke,
  usePushKey: () => h.key,
  useDevices: () => h.devices,
  useAddDevice: () => h.add,
  useRemoveDevice: () => h.remove,
  useSendTest: () => h.test,
}));
vi.mock("./pushClient", () => ({
  pushSupport: () => h.support,
  permissionState: () => "default",
  currentEndpoint: async () => h.subscribed,
  subscribeThisDevice: vi.fn(async () => ({
    ok: true,
    subscription: { endpoint: "https://push.example/x", keys: { p256dh: "P", auth: "A" } },
  })),
  unsubscribeThisDevice: vi.fn(async () => true),
  webcalUrl: (u: string) => u.replace(/^https?:\/\//, "webcal://"),
}));
vi.mock("../health/useOnlineStatus", () => ({ useOnlineStatus: () => h.online }));
vi.mock("../copy/useUiText", () => ({
  useUiText:
    () =>
    (key: string, vars: Record<string, string> = {}) =>
      uiText(null, key, vars),
}));

const t = (k: string, v: Record<string, string> = {}) => uiText(null, k, v);

beforeEach(() => {
  h.feed.data = { active: false, created_at: null };
  h.feed.isError = false;
  h.key.data = { configured: true, public_key: "AQID" };
  h.devices.data = { configured: true, subscriptions: [] };
  h.support = "ready";
  h.online = true;
  h.subscribed = null;
  for (const m of [h.rotate, h.revoke, h.add, h.remove, h.test]) m.mutate.mockReset();
});

describe("calendar feed", () => {
  it("creates a link and shows it once with a webcal subscribe link", async () => {
    h.rotate.mutate.mockImplementation((_v: unknown, o: { onSuccess: (r: { url: string }) => void }) =>
      o.onSuccess({ url: "https://naadi.example/api/plan/ics/tok" }),
    );
    render(<StudyPlanSettings />);
    await userEvent.click(screen.getByRole("button", { name: t("settings.plan.feed_create") }));
    expect(await screen.findByDisplayValue("https://naadi.example/api/plan/ics/tok")).toBeInTheDocument();
    expect(screen.getByText(t("settings.plan.feed_once"))).toBeInTheDocument();
    expect(screen.getByRole("link", { name: t("settings.plan.feed_subscribe") })).toHaveAttribute(
      "href",
      "webcal://naadi.example/api/plan/ics/tok",
    );
  });

  it("asks inline before rotating or turning off an active link", async () => {
    h.feed.data = { active: true, created_at: "2026-11-01T10:00:00Z" };
    render(<StudyPlanSettings />);
    expect(screen.getByText(/It is not shown again|not shown again/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: t("settings.plan.feed_revoke") }));
    expect(h.revoke.mutate).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: t("settings.plan.feed_confirm_yes") }));
    expect(h.revoke.mutate).toHaveBeenCalledTimes(1);
  });

  it("disables changes offline", () => {
    h.online = false;
    render(<StudyPlanSettings />);
    expect(screen.getByRole("button", { name: t("settings.plan.feed_create") })).toBeDisabled();
  });
});

describe("reminders", () => {
  it("explains the missing public address when the server has no keys (no switch)", () => {
    h.key.data = { configured: false, public_key: null };
    render(<StudyPlanSettings />);
    expect(screen.getByText(t("settings.plan.reminders_server_off_title"))).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: t("settings.plan.reminders_enable") })).toBeNull();
  });
  it("shows the Home Screen steps when the app is not installed", () => {
    h.support = "not_standalone";
    render(<StudyPlanSettings />);
    expect(screen.getByText(t("settings.plan.reminders_install_title"))).toBeInTheDocument();
    expect(screen.getByText(t("settings.plan.reminders_install_2"))).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: t("settings.plan.reminders_enable") })).toBeNull();
  });
  it("says so when the browser cannot receive pushes", () => {
    h.support = "unsupported";
    render(<StudyPlanSettings />);
    expect(screen.getByText(t("settings.plan.reminders_unsupported"))).toBeInTheDocument();
  });
  it("turns reminders on: permission, subscribe, register with the server", async () => {
    render(<StudyPlanSettings />);
    await userEvent.click(screen.getByRole("button", { name: t("settings.plan.reminders_enable") }));
    await waitFor(() =>
      expect(h.add.mutate).toHaveBeenCalledWith(
        { endpoint: "https://push.example/x", keys: { p256dh: "P", auth: "A" } },
        expect.any(Object),
      ),
    );
  });
  it("lists devices, sends a test and removes one", async () => {
    h.devices.data = {
      configured: true,
      subscriptions: [{ id: "d1", ua_label: "iPad · Safari", created_at: "2026-11-01T10:00:00Z", last_ok_at: null }],
    };
    render(<StudyPlanSettings />);
    await userEvent.click(screen.getByRole("button", { name: t("settings.plan.reminders_test") }));
    expect(h.test.mutate).toHaveBeenCalled();
    await userEvent.click(
      screen.getByRole("button", { name: t("settings.plan.device_remove", { label: "iPad · Safari" }) }),
    );
    expect(h.remove.mutate).toHaveBeenCalledWith("d1");
  });
});
