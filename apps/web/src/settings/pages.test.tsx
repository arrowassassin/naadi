import { fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  online: true,
  onboarding: undefined as unknown,
  save: vi.fn(),
  config: { app_version: "1.2.3" },
}));
vi.mock("../copy/useUiText", () => ({
  useUiText: () => (k: string, v?: Record<string, string>) => {
    const urls: Record<string, string> = {
      "video.terms_url": "https://www.youtube.com/t/terms",
      "privacy.youtube_google_url": "https://policies.google.com/privacy",
      "privacy.youtube_revoke_url": "https://myaccount.google.com/permissions",
    };
    return urls[k] ?? (v ? `${k} ${JSON.stringify(v)}` : k);
  },
}));
vi.mock("../health/useOnlineStatus", () => ({ useOnlineStatus: () => h.online }));
vi.mock("../auth/session", () => ({ useAuth: () => ({ status: "signed-in", config: h.config }) }));
vi.mock("./SettingsPage", () => ({ SettingsPage: ({ children }: { children: ReactNode }) => <div>{children}</div> }));
vi.mock("@tanstack/react-router", () => ({
  Link: ({ to, children }: { to: string; children: ReactNode }) => <a href={to}>{children}</a>,
}));
vi.mock("../today/api", () => ({
  errorCode: () => null,
  useOnboarding: () => ({ isPending: false, data: h.onboarding }),
  usePlanPreview: (i: unknown) => ({
    data: i
      ? { ok: true, summary: { weeks: 11, study_days: 60, first_day: "2026-11-18", last_day: "2027-02-01" } }
      : undefined,
    isPlaceholderData: false,
    isFetching: false,
  }),
  useSaveOnboarding: () => ({ mutate: h.save, isPending: false, error: null }),
}));

import { AboutPage } from "./AboutPage";
import { AppearancePage } from "./AppearancePage";
import { StudyPlanPage } from "./StudyPlanPage";

const state = (completed: string | null) => ({
  completed_at: completed,
  today: "2026-11-17",
  max_days: 400,
  min_study_days: 3,
  hours: { min: 1, max: 12, step: 0.5, default: 6 },
  exams: [
    { id: "step1", label: "Step 1" },
    { id: "step2ck", label: "Step 2 CK" },
  ],
  materials: ["qbank"],
  values: {
    exam: "step1",
    exam_date: "2027-03-01",
    daily_hours: 6,
    study_days: [1, 2, 3, 4, 5],
    materials: ["qbank"],
    medical_school: "X",
  },
});

beforeEach(() => {
  h.online = true;
  h.save.mockReset();
});

describe("Study plan", () => {
  it("saves only when something changed, keeping the onboarding answers it does not show", async () => {
    h.onboarding = state("2026-11-01T00:00:00Z");
    render(<StudyPlanPage />);
    const save = screen.getByRole("button", { name: "account.save" });
    expect(save).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "onboarding.day_6" }));
    await vi.waitFor(() => expect(screen.getByRole("button", { name: "account.save" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "account.save" }));
    expect(h.save.mock.calls[0]?.[0]).toEqual({
      exam: "step1",
      exam_date: "2027-03-01",
      daily_hours: 6,
      study_days: [1, 2, 3, 4, 5, 6],
      materials: ["qbank"],
      medical_school: "X",
    });
  });

  it("cannot save offline", async () => {
    h.onboarding = state("2026-11-01T00:00:00Z");
    h.online = false;
    render(<StudyPlanPage />);
    fireEvent.click(screen.getByRole("button", { name: "onboarding.day_6" }));
    await vi.waitFor(() => expect(screen.getByText("account.offline_save")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "account.save" })).toBeDisabled();
  });

  it("sends a student with no plan to set one up", () => {
    h.onboarding = state(null);
    render(<StudyPlanPage />);
    expect(screen.getByRole("link", { name: "settings.plan_setup" })).toHaveAttribute("href", "/welcome");
    expect(screen.queryByRole("button", { name: "account.save" })).toBeNull();
  });
});

describe("Appearance", () => {
  it("states the dark theme and has no control at all", () => {
    render(<AppearancePage />);
    expect(screen.getByText("settings.theme_dark")).toBeInTheDocument();
    expect(screen.queryAllByRole("button")).toHaveLength(0);
    expect(screen.queryAllByRole("switch")).toHaveLength(0);
    expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
    expect(screen.queryAllByRole("radio")).toHaveLength(0);
    expect(screen.queryAllByRole("combobox")).toHaveLength(0);
  });
});

describe("About & privacy", () => {
  it("shows the version, the storage and AI statements and the YouTube links in a new tab", () => {
    render(<AboutPage />);
    expect(screen.getByText(/settings\.about_version.*1\.2\.3/)).toBeInTheDocument();
    expect(screen.getAllByRole("listitem").length).toBeGreaterThanOrEqual(8);
    for (const href of ["https://www.youtube.com/t/terms", "https://policies.google.com/privacy"]) {
      const a = document.querySelector(`a[href="${href}"]`) as HTMLAnchorElement;
      expect(a).not.toBeNull();
      expect(a.target).toBe("_blank");
      expect(a.rel).toBe("noopener noreferrer");
      expect(a.querySelector("svg")).not.toBeNull();
    }
  });
});
