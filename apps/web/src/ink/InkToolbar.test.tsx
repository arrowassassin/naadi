import type { InkEngineState } from "@naadi/ink";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { InkToolbar, StickyInkToolbar } from "./InkToolbar";

// ui_copy comes from /api/public-config; these tests use the shipped defaults (the same text) directly.
vi.mock("../agent/uiCopy", async () => {
  const { uiText } = await import("@naadi/shared");
  return {
    useUiCopy:
      () =>
      (key: string, vars: Record<string, string> = {}) =>
        uiText(null, key, vars),
  };
});

const base: InkEngineState = {
  enabled: true,
  tool: "pen",
  color: "ink",
  eraserMode: "stroke",
  penOnly: true,
  penPresent: false,
  fingersDraw: true,
  canUndo: true,
  canRedo: false,
  count: 3,
  selection: null,
  editingText: false,
  predict: true,
};

function setup(state: Partial<InkEngineState> = {}) {
  const props = {
    onEnabled: vi.fn(),
    onTool: vi.fn(),
    onColor: vi.fn(),
    onEraserMode: vi.fn(),
    onUndo: vi.fn(),
    onRedo: vi.fn(),
    onClear: vi.fn(),
  };
  render(<InkToolbar state={{ ...base, ...state }} theme="paper" {...props} />);
  return props;
}

describe("InkToolbar", () => {
  it("shows the active tool, shortcuts and pen colours", async () => {
    const p = setup();
    expect(screen.getByRole("button", { name: "Pen" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Marker" })).toHaveAttribute("aria-keyshortcuts", "M");
    expect(screen.getByRole("button", { name: "Ink black" })).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(screen.getByRole("button", { name: "Lasso" }));
    expect(p.onTool).toHaveBeenCalledWith("lasso");
    await userEvent.click(screen.getByRole("button", { name: "Ink violet" }));
    expect(p.onColor).toHaveBeenCalledWith("violet");
  });

  it("offers marker colours with their meanings, and eraser modes", async () => {
    setup({ tool: "marker", color: "lime" });
    expect(screen.getByRole("button", { name: "Marker lime, key fact" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Marker amber, my mistake" })).toBeInTheDocument();
  });

  it("eraser shows whole-stroke / partial and hides colours", async () => {
    const p = setup({ tool: "eraser" });
    expect(screen.queryByRole("button", { name: "Ink black" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Partial" }));
    expect(p.onEraserMode).toHaveBeenCalledWith("pixel");
  });

  it("undo/redo follow availability; ink off disables tools", async () => {
    const p = setup({ enabled: false });
    expect(screen.getByRole("button", { name: "Redo" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(p.onUndo).toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Pen" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Ink off" }));
    expect(p.onEnabled).toHaveBeenCalledWith(true);
  });

  it("clear is disabled on an empty page", () => {
    setup({ count: 0 });
    expect(screen.getByRole("button", { name: "Clear page" })).toBeDisabled();
  });
});

describe("StickyInkToolbar", () => {
  it("pins the toolbar on its own compositing layer, top or bottom", () => {
    const noop = () => undefined;
    const handlers = {
      onEnabled: noop,
      onTool: noop,
      onColor: noop,
      onEraserMode: noop,
      onUndo: noop,
      onRedo: noop,
      onClear: noop,
    };
    const { container, rerender } = render(
      <StickyInkToolbar placement="top" state={base} theme="dark" {...handlers} />,
    );
    const wrapper = container.querySelector("[data-sticky-ink-toolbar]");
    expect(wrapper?.className).toMatch(/\bsticky\b/);
    expect(wrapper?.className).toMatch(/\bwill-change-transform\b/);
    expect(wrapper?.className).toMatch(/\btop-/);
    expect(screen.getByRole("toolbar", { name: "Ink tools" })).toBeInTheDocument();
    rerender(<StickyInkToolbar placement="bottom" state={base} theme="dark" {...handlers} />);
    expect(container.querySelector("[data-sticky-ink-toolbar]")?.className).toMatch(/\bbottom-/);
  });
});
