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

describe("markdown renderer loading (offline-safe)", () => {
  it("never fetches while offline, shows plain text, retries on `online` and after a failed load", async () => {
    const { act } = await import("@testing-library/react");
    const { vi } = await import("vitest");
    const { setMarkdownLoaderForTests } = await import("./Markdown");
    const real = () =>
      Promise.all([import("streamdown"), import("@streamdown/math"), import("@streamdown/code")]) as Promise<
        [typeof import("streamdown"), typeof import("@streamdown/math"), typeof import("@streamdown/code")]
      >;
    const errors = vi.spyOn(console, "error");
    const online = vi.spyOn(navigator, "onLine", "get").mockReturnValue(false);
    let calls = 0;
    let fail = true;
    setMarkdownLoaderForTests(() => {
      calls += 1;
      if (fail) return Promise.reject(new TypeError("Failed to fetch dynamically imported module"));
      return real();
    });
    try {
      const { container } = render(<Markdown text="**Gap** is high" streaming={false} />);
      // offline: plain text, and nothing was fetched
      expect(container.querySelector('[data-markdown="plain"]')).toHaveTextContent("**Gap** is high");
      expect(calls).toBe(0);
      // back online: one load, which fails (the connection dropped mid-fetch): still plain, no error
      online.mockReturnValue(true);
      await act(async () => {
        window.dispatchEvent(new Event("online"));
        await Promise.resolve();
      });
      expect(calls).toBe(1);
      expect(container.querySelector('[data-markdown="plain"]')).not.toBeNull();
      // the next `online` retries, and the answer renders
      fail = false;
      await act(async () => {
        window.dispatchEvent(new Event("online"));
      });
      await waitFor(() => expect(container.querySelector('[data-streamdown="strong"]')).toHaveTextContent("Gap"));
      expect(calls).toBe(2);
      expect(errors).not.toHaveBeenCalled();
    } finally {
      online.mockRestore();
      errors.mockRestore();
      setMarkdownLoaderForTests(null);
    }
  });
});
