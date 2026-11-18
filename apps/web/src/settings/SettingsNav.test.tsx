import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
  features: { today: true, export: true } as Record<string, boolean> | undefined,
  routes: {} as Record<string, unknown>,
}));
vi.mock("@tanstack/react-router", () => ({
  Link: ({ to, children, ...rest }: { to: string; children: ReactNode }) => (
    <a href={to} {...rest}>
      {children}
    </a>
  ),
  useRouter: () => ({ routesByPath: state.routes }),
}));
vi.mock("../auth/api", () => ({ useMe: () => ({ data: state.features ? { features: state.features } : undefined }) }));
vi.mock("../copy/useUiText", () => ({ useUiText: () => (k: string) => k }));

import { SettingsNav } from "./SettingsNav";

const ROUTES = Object.fromEntries(
  ["account", "ink", "plan", "appearance", "data", "about"].map((g) => [`/settings/${g}`, {}]),
);

describe("SettingsNav", () => {
  it("is a labelled nav with one link per visible group and marks the current page", () => {
    state.routes = ROUTES;
    state.features = { today: true, export: true };
    render(<SettingsNav current="data" />);
    const nav = screen.getByRole("navigation", { name: "settings.nav_label" });
    const links = nav.querySelectorAll("a");
    expect([...links].map((a) => a.getAttribute("href"))).toEqual([
      "/settings/account",
      "/settings/ink",
      "/settings/plan",
      "/settings/appearance",
      "/settings/data",
      "/settings/about",
    ]);
    expect(screen.getByRole("link", { name: "settings.nav_data" })).toHaveAttribute("aria-current", "page");
    expect(nav.querySelectorAll("[aria-current]")).toHaveLength(1);
  });

  it("drops groups whose flag is off or whose route is missing", () => {
    state.routes = { ...ROUTES };
    delete state.routes["/settings/appearance"];
    state.features = { today: false, export: false };
    render(<SettingsNav current="account" />);
    const hrefs = [...screen.getByRole("navigation").querySelectorAll("a")].map((a) => a.getAttribute("href"));
    expect(hrefs).toEqual(["/settings/account", "/settings/ink", "/settings/about"]);
  });

  it("scrolls sideways on phone instead of truncating (nowrap row, wraps from tablet up)", () => {
    state.routes = ROUTES;
    state.features = { today: true, export: true };
    render(<SettingsNav current="account" />);
    const row = screen.getByRole("list");
    expect(row.className).toContain("overflow-x-auto");
    expect(row.className).toContain("flex-nowrap");
    expect(row.className).toContain("tablet:flex-wrap");
    for (const li of row.querySelectorAll("li")) expect(li.className).toContain("shrink-0");
  });
});
