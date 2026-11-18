import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Markdown } from "./Markdown";

describe("Markdown", () => {
  it("renders headings, lists, emphasis and https links that open in a new tab", () => {
    render(
      <Markdown
        source={
          "# Title\n\nSome **bold** and *soft* text with [ECFMG](https://ecfmg.org/x).\n\n- one\n- two\n\n1. first\n2. second"
        }
      />,
    );
    expect(screen.getByRole("heading", { level: 2, name: "Title" })).toBeInTheDocument();
    expect(screen.getByText("bold").tagName).toBe("STRONG");
    const link = screen.getByRole("link", { name: "ECFMG" });
    expect(link).toHaveAttribute("href", "https://ecfmg.org/x");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link.getAttribute("rel")).toContain("noopener");
    expect(screen.getAllByRole("listitem")).toHaveLength(4);
  });

  it("never turns a non-https link, a script or raw HTML into markup", () => {
    const { container } = render(
      <Markdown
        source={
          "[x](javascript:alert(1)) [y](http://a.b) <img src=x onerror=alert(1)> <script>alert(2)</script> [z](data:text/html,hi)"
        }
      />,
    );
    expect(container.querySelector("a")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect(container.textContent).toContain("<script>alert(2)</script>");
  });

  it("renders a table in a focusable scroll region", () => {
    const { container } = render(
      <Markdown source={"| # | Task |\n|---|---|\n| `a-01` | Apply [here](https://usmle.org) |\n| `a-02` | Book |"} />,
    );
    expect(screen.getAllByRole("columnheader")).toHaveLength(2);
    expect(screen.getAllByRole("row")).toHaveLength(3);
    expect(container.querySelector("[tabindex='0']")?.querySelector("table")).not.toBeNull();
    expect(screen.getByRole("link", { name: "here" })).toBeInTheDocument();
  });

  it("copes with empty text, a quote and a rule", () => {
    const { container } = render(<Markdown source={"\n\n> quoted\n\n---\n\nend"} />);
    expect(container.querySelector("blockquote")?.textContent).toBe("quoted");
    expect(container.querySelector("hr")).not.toBeNull();
    render(<Markdown source="" />);
  });
});
