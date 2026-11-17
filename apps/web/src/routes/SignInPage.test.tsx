import { UI_COPY_DEFAULTS } from "@naadi/shared";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const auth = vi.hoisted(() => ({ value: { status: "loading" } as Record<string, unknown> }));
vi.mock("../auth/session", () => ({ useAuth: () => auth.value }));

import { SignInPage } from "./SignInPage";

function signedOut(uiCopy: Record<string, string>) {
  auth.value = {
    status: "signed-out",
    client: { auth: { signInWithOtp: vi.fn(), signInWithOAuth: vi.fn() } },
    config: {
      auth_configured: true,
      supabase_url: "https://project.supabase.test",
      supabase_anon_key: "public-anon-key",
      google_oauth_enabled: true,
      app_base_url: null,
      app_version: "test",
      video_player_origin: "https://arrowassassin-naadi-player.static.hf.space",
      features: {},
      nav: [],
      ui_copy: uiCopy,
    },
  };
}

afterEach(() => vi.restoreAllMocks());

describe("SignInPage copy", () => {
  it("renders the configured strings", () => {
    signedOut({ ...UI_COPY_DEFAULTS, "signin.submit": "Send me a link", "signin.heading": "Log in" });
    render(<SignInPage next="/" />);
    expect(screen.getByRole("button", { name: "Send me a link" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Log in" })).toBeInTheDocument();
  });

  it("keeps accessible names when a key is missing from the config (shipped default, logged)", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const { "signin.submit": _drop, "signin.google": _drop2, ...rest } = UI_COPY_DEFAULTS;
    signedOut(rest);
    render(<SignInPage next="/" />);
    expect(screen.getByRole("button", { name: UI_COPY_DEFAULTS["signin.submit"] })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: UI_COPY_DEFAULTS["signin.google"] })).toBeInTheDocument();
    expect(warn).toHaveBeenCalled();
  });
});
