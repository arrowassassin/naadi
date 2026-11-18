import { act, render, screen } from "@testing-library/react";
import { Component, type ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../copy/useUiText", () => ({ useUiText: () => (key: string) => key }));

import { isChunkLoadError, lazyWithFallback } from "./lazyPage";

const chunkError = () => new TypeError("Failed to fetch dynamically imported module: https://x.test/assets/A.js");

function Hello({ name }: { name: string }) {
  return <p>hello {name}</p>;
}

class Boundary extends Component<{ children: ReactNode }, { error: string | null }> {
  override state = { error: null as string | null };
  static getDerivedStateFromError(e: Error) {
    return { error: e.message };
  }
  override render() {
    return this.state.error ? <p>boundary: {this.state.error}</p> : this.props.children;
  }
}

afterEach(() => vi.restoreAllMocks());

describe("isChunkLoadError", () => {
  it.each([
    ["Failed to fetch dynamically imported module: /assets/a.js"],
    ["Importing a module script failed."],
    ["error loading dynamically imported module: /assets/a.js"],
    ["Unable to preload CSS for /assets/a.css"],
  ])("knows %s", (message) => {
    expect(isChunkLoadError(new TypeError(message))).toBe(true);
  });

  it("does not swallow other errors", () => {
    expect(isChunkLoadError(new TypeError("Cannot read properties of undefined"))).toBe(false);
    expect(isChunkLoadError("Failed to fetch dynamically imported module")).toBe(false);
  });
});

describe("lazyWithFallback", () => {
  it("renders the loaded component with its props", async () => {
    const Lazy = lazyWithFallback(async () => Hello);
    render(<Lazy name="Ada" />);
    expect(await screen.findByText("hello Ada")).toBeInTheDocument();
  });

  it("a chunk that cannot load shows the notice, logs nothing, and loads once back online", async () => {
    const error = vi.spyOn(console, "error");
    const load = vi.fn<() => Promise<typeof Hello>>().mockRejectedValueOnce(chunkError()).mockResolvedValue(Hello);
    const Lazy = lazyWithFallback(load);
    render(<Lazy name="Ada" />);
    expect(await screen.findByRole("heading", { name: "shell.chunk_offline_title" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("shell.chunk_offline_body");
    expect(error).not.toHaveBeenCalled();
    await act(async () => {
      window.dispatchEvent(new Event("online"));
    });
    expect(await screen.findByText("hello Ada")).toBeInTheDocument();
    expect(load).toHaveBeenCalledTimes(2);
  });

  it("Try again retries on demand; the inline notice has no heading", async () => {
    const load = vi
      .fn<() => Promise<typeof Hello>>()
      .mockRejectedValueOnce(new TypeError("Importing a module script failed."))
      .mockResolvedValue(Hello);
    const Lazy = lazyWithFallback(load, { fallback: null, unavailable: "inline" });
    render(<Lazy name="Bo" />);
    const button = await screen.findByRole("button", { name: "shell.chunk_retry" });
    expect(screen.queryByRole("heading")).toBeNull();
    await act(async () => button.click());
    expect(await screen.findByText("hello Bo")).toBeInTheDocument();
  });

  it("every instance recovers together after one retry", async () => {
    const load = vi.fn<() => Promise<typeof Hello>>().mockRejectedValueOnce(chunkError()).mockResolvedValue(Hello);
    const Lazy = lazyWithFallback(load, { unavailable: "inline" });
    render(
      <>
        <Lazy name="one" />
        <Lazy name="two" />
      </>,
    );
    expect(await screen.findAllByRole("status")).toHaveLength(2);
    await act(async () => {
      window.dispatchEvent(new Event("online"));
    });
    expect(await screen.findByText("hello one")).toBeInTheDocument();
    expect(screen.getByText("hello two")).toBeInTheDocument();
  });

  it("any other failure still reaches the error boundary", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const Lazy = lazyWithFallback(async (): Promise<typeof Hello> => {
      throw new Error("bug in the page module");
    });
    render(
      <Boundary>
        <Lazy name="x" />
      </Boundary>,
    );
    expect(await screen.findByText("boundary: bug in the page module")).toBeInTheDocument();
  });
});
