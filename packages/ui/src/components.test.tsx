import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import {
  AssistantBar,
  Button,
  Chip,
  ErrorState,
  Highlight,
  NavPill,
  OfflineBanner,
  Skeleton,
  TabBar,
  Tile,
  TileLabel,
} from "./index";

describe("Button", () => {
  it("defaults to a 48 px secondary button of type=button", () => {
    render(<Button>Go</Button>);
    const b = screen.getByRole("button", { name: "Go" });
    expect(b).toHaveAttribute("type", "button");
    expect(b).toHaveAttribute("data-variant", "secondary");
    expect(b.className).toContain("min-h-12");
  });

  it.each(["primary", "strong", "secondary", "assistant"] as const)("renders the %s variant", (variant) => {
    render(<Button variant={variant}>{variant}</Button>);
    expect(screen.getByRole("button", { name: variant })).toHaveAttribute("data-variant", variant);
  });

  it("uses the 56 px CTA size", () => {
    render(<Button size="cta">Start</Button>);
    expect(screen.getByRole("button").className).toContain("min-h-14");
  });

  it("is inert and dashed when disabled", async () => {
    const onClick = vi.fn();
    render(
      <Button variant="primary" disabled onClick={onClick}>
        Nope
      </Button>,
    );
    const b = screen.getByRole("button");
    expect(b).toBeDisabled();
    expect(b.className).toContain("border-dashed");
    expect(b.className).not.toContain("bg-lime");
    await userEvent.click(b);
    expect(onClick).not.toHaveBeenCalled();
  });

  it("hides the icon from assistive tech", () => {
    render(<Button icon={<svg data-testid="i" />}>Retry</Button>);
    expect(screen.getByTestId("i").parentElement).toHaveAttribute("aria-hidden", "true");
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});

describe("Chip", () => {
  it("always carries its word and an optional screen-reader prefix", () => {
    render(
      <Chip tone="weak" srPrefix="Renal">
        Weak area
      </Chip>,
    );
    const chip = screen.getByText("Weak area");
    expect(chip).toHaveAttribute("data-tone", "weak");
    expect(chip).toHaveTextContent("Renal: Weak area");
  });
});

describe("Tile", () => {
  it.each(["default", "assistant", "paper"] as const)("renders the %s variant as the requested element", (v) => {
    render(
      <Tile variant={v} as="section" aria-label={`tile ${v}`}>
        <TileLabel variant={v}>Label</TileLabel>
      </Tile>,
    );
    const el = screen.getByRole("region", { name: `tile ${v}` });
    expect(el.tagName).toBe("SECTION");
    expect(el).toHaveAttribute("data-variant", v);
  });
});

describe("NavPill", () => {
  const items = [
    { id: "today", label: "Today" },
    { id: "learn", label: "Learn" },
    { id: "docs", label: "Docs", href: "/system" },
  ];

  it("marks the current item and reports selection", async () => {
    const onSelect = vi.fn();
    render(<NavPill label="Main" items={items} currentId="today" onSelect={onSelect} />);
    expect(screen.getByRole("navigation", { name: "Main" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Today" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("button", { name: "Learn" })).not.toHaveAttribute("aria-current");
    await userEvent.click(screen.getByRole("button", { name: "Learn" }));
    expect(onSelect).toHaveBeenCalledWith("learn");
    expect(screen.getByRole("link", { name: "Docs" })).toHaveAttribute("href", "/system");
  });
});

describe("TabBar", () => {
  it("puts the assistant button in the centre", async () => {
    const onAssistant = vi.fn();
    const items = ["a", "b", "c", "d"].map((id) => ({ id, label: id.toUpperCase() }));
    render(<TabBar label="Tabs" items={items} currentId="c" onAssistant={onAssistant} />);
    const buttons = screen.getAllByRole("button");
    expect(buttons.map((b) => b.textContent || b.getAttribute("aria-label"))).toEqual([
      "A",
      "B",
      "Ask Naadi",
      "C",
      "D",
    ]);
    expect(screen.getByRole("button", { name: "C" })).toHaveAttribute("aria-current", "page");
    await userEvent.click(screen.getByRole("button", { name: "Ask Naadi" }));
    expect(onAssistant).toHaveBeenCalledOnce();
  });
});

describe("AssistantBar", () => {
  it("enables send only with text, submits trimmed text and clears", async () => {
    const onSubmit = vi.fn();
    render(<AssistantBar onSubmit={onSubmit} />);
    const send = screen.getByRole("button", { name: "Send" });
    expect(send).toBeDisabled();
    const input = screen.getByLabelText("Ask Naadi");
    await userEvent.type(input, "  why is the gap raised?  ");
    expect(send).toBeEnabled();
    await userEvent.click(send);
    expect(onSubmit).toHaveBeenCalledWith("why is the gap raised?");
    expect(input).toHaveValue("");
  });

  it("submits with Enter", async () => {
    const onSubmit = vi.fn();
    render(<AssistantBar onSubmit={onSubmit} />);
    await userEvent.type(screen.getByLabelText("Ask Naadi"), "hello{Enter}");
    expect(onSubmit).toHaveBeenCalledWith("hello");
  });

  it("is inert with a stated reason when disabled (exam mode)", async () => {
    const onSubmit = vi.fn();
    render(
      <AssistantBar onSubmit={onSubmit} onAttach={() => {}} disabled disabledReason="Assistant is off in exam mode" />,
    );
    const input = screen.getByLabelText("Ask Naadi");
    expect(input).toBeDisabled();
    expect(input).toHaveAccessibleDescription("Assistant is off in exam mode");
    expect(screen.getByRole("button", { name: "Attach a file" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
  });

  it("disables attach when no handler is given", () => {
    render(<AssistantBar onSubmit={() => {}} />);
    expect(screen.getByRole("button", { name: "Attach a file" })).toBeDisabled();
  });

  it("keeps the text if sending fails", async () => {
    const failure = new Error("offline");
    const onSubmit = vi.fn().mockRejectedValue(failure);
    const onError = vi.fn();
    render(<AssistantBar onSubmit={onSubmit} onError={onError} />);
    const input = screen.getByLabelText("Ask Naadi");
    await userEvent.type(input, "keep me");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(onError).toHaveBeenCalledWith(failure);
    expect(input).toHaveValue("keep me");
  });
});

describe("Highlight", () => {
  it.each([
    ["key-fact", "Key fact"],
    ["formula", "Formula"],
    ["mistake", "My mistake"],
  ] as const)("%s carries its meaning in text", (kind, label) => {
    render(<Highlight kind={kind}>anion gap</Highlight>);
    const mark = screen.getByText((_, el) => el?.tagName === "MARK");
    expect(mark).toHaveAttribute("data-kind", kind);
    expect(mark).toHaveTextContent(`${label}: anion gap`);
  });
});

describe("states", () => {
  it("Skeleton is decorative", () => {
    const { container } = render(<Skeleton className="h-4" />);
    expect(container.firstElementChild).toHaveAttribute("aria-hidden", "true");
  });

  it("ErrorState announces and retries", async () => {
    const onRetry = vi.fn();
    render(<ErrorState title="Failed" message="Because." requestId="rid-1" onRetry={onRetry} />);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Failed");
    expect(alert).toHaveTextContent("Request ID rid-1");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("ErrorState specimen is not live", () => {
    render(<ErrorState live={false} title="Specimen" message="m" />);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("ErrorState shows a retrying label", () => {
    render(<ErrorState title="t" message="m" onRetry={() => {}} retrying />);
    expect(screen.getByRole("button", { name: "Retrying…" })).toBeDisabled();
  });

  it("OfflineBanner says Offline in words", () => {
    render(<OfflineBanner message="Showing saved data." />);
    expect(screen.getByRole("status")).toHaveTextContent("OfflineShowing saved data.");
  });
});
