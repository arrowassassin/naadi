import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Markdown } from "./Markdown";
import type { Citation } from "./protocol";

const cite = (n: number, url: string | null): Citation => ({
  id: `c${n}`,
  message_id: null,
  title: `Source ${n}`,
  source: "course",
  layer: "course",
  url,
  target: null,
  license: null,
  n,
});

describe("assistant markdown (Streamdown)", () => {
  it("renders GFM, and math as MathML only (no inline styles)", async () => {
    const { container } = render(
      <Markdown text={"**Gap** is $Na^+ - (Cl^- + HCO_3^-)$.\n\n| a | b |\n|---|---|\n| 1 | 2 |"} streaming={false} />,
    );
    await waitFor(() => expect(container.querySelector("math")).not.toBeNull());
    expect(container.querySelector('[data-streamdown="strong"]')).toHaveTextContent("Gap");
    expect(container.querySelector(".katex-html")).toBeNull();
    expect(screen.getByRole("table")).toBeInTheDocument();
  });

  it("keeps https links, turns other links into text, drops raw HTML and foreign images", async () => {
    const { container } = render(
      <Markdown
        text={
          "[ok](https://www.cdc.gov/x) [bad](javascript:alert(1)) [plain](http://example.com)\n\n<script>alert(1)</script><b>raw</b>\n\n![x](https://evil.example/x.png) ![y](https://upload.wikimedia.org/a.png)"
        }
        streaming={false}
      />,
    );
    await waitFor(() => expect(screen.getByRole("link", { name: "ok" })).toBeInTheDocument());
    const ok = screen.getByRole("link", { name: "ok" });
    expect(ok).toHaveAttribute("href", "https://www.cdc.gov/x");
    expect(ok).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.queryByRole("link", { name: "bad" })).toBeNull();
    expect(screen.queryByRole("link", { name: "plain" })).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("b")).toBeNull();
    const imgs = [...container.querySelectorAll("img")].map((i) => i.getAttribute("src"));
    expect(imgs).toEqual(["https://upload.wikimedia.org/a.png"]);
  });

  it("turns [n] into a chip for a known citation and leaves unknown markers as text", async () => {
    const { container } = render(
      <Markdown
        text="Winters' formula [1] and something else [7]."
        streaming={false}
        citations={[cite(1, "https://www.ncbi.nlm.nih.gov/x")]}
      />,
    );
    await waitFor(() => expect(screen.getByRole("link", { name: /Source 1/ })).toBeInTheDocument());
    expect(screen.getByRole("link", { name: /Source 1/ })).toHaveAttribute("href", "https://www.ncbi.nlm.nih.gov/x");
    expect(container).toHaveTextContent("[7]");
  });
});
