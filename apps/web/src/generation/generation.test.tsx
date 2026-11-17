import { uiText } from "@naadi/shared";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { type GeneratedItem, generationErrorKey } from "./api";
import { GeneratedLibrary } from "./GeneratedLibrary";
import { GeneratedMedia } from "./GeneratedMedia";
import { GenerationProgress } from "./Progress";

const hooks = vi.hoisted(() => ({
  list: {} as Record<string, unknown>,
  remove: vi.fn(),
}));
vi.mock("./api", async (orig) => ({
  ...(await orig<typeof import("./api")>()),
  useGeneratedList: () => hooks.list,
  useDeleteGenerated: () => ({ mutateAsync: hooks.remove, isPending: false, error: null, reset: vi.fn() }),
}));
vi.mock("../copy/useUiText", () => ({
  useUiText: () => (key: string, vars?: Record<string, string>) => uiText(null, key, vars),
}));

const LABEL = "AI-generated illustration — not clinical reference";

function item(over: Partial<GeneratedItem> = {}): GeneratedItem {
  return {
    id: "6f1e5a2c-0c1b-4c55-9a55-0e8f2b6f7a10",
    kind: "image",
    status: "ready",
    title: null,
    prompt: "Nephron cross-section",
    model: "m",
    width: 1024,
    height: 768,
    duration_s: null,
    url: "http://storage.test/a.webp?token=t",
    ai_generated: true,
    error: null,
    paused_until: null,
    published: false,
    created_at: "2026-11-17T04:00:00Z",
    ...over,
  };
}

function wrap(ui: ReactNode) {
  return render(<QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
  hooks.remove.mockReset();
  HTMLDialogElement.prototype.showModal = function showModal() {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function close() {
    this.removeAttribute("open");
  };
});

describe("GeneratedMedia", () => {
  it("shows a ready image with the AI chip, a label-first alt text and a caption", () => {
    wrap(<GeneratedMedia item={item()} />);
    expect(screen.getByText(LABEL)).toBeVisible();
    const img = screen.getByRole("img");
    expect(img).toHaveAttribute("alt", `${LABEL}: Nephron cross-section`);
    expect(screen.getByText("Prompt: Nephron cross-section")).toBeVisible();
  });

  it("labels a clip the same way", () => {
    wrap(<GeneratedMedia item={item({ kind: "video", url: "http://storage.test/a.mp4", duration_s: 3 })} />);
    expect(screen.getByText(LABEL)).toBeVisible();
    expect(screen.getByLabelText(`${LABEL}: Nephron cross-section`).tagName).toBe("VIDEO");
  });

  it("shows words and a progressbar while it is made, never only a bar", () => {
    wrap(<GeneratedMedia item={item({ status: "generating", url: null })} />);
    expect(screen.getByText(LABEL)).toBeVisible();
    const bar = screen.getByRole("progressbar", { name: "Generation progress" });
    expect(bar).toHaveAttribute("aria-valuetext", "Generating");
    expect(screen.getByRole("status")).toHaveTextContent("Generating");
  });

  it("states the pause with the reset time", () => {
    wrap(<GeneratedMedia item={item({ status: "paused", url: null, paused_until: "2026-11-18T00:00:00Z" })} />);
    expect(screen.getByText("Generation is paused")).toBeVisible();
    expect(screen.getByText(/Generation resumes at/)).toBeVisible();
    expect(screen.getByText(LABEL)).toBeVisible();
  });

  it("offers a retry on failure only when a handler exists, with the error worded from its code", async () => {
    const onRetry = vi.fn();
    const failed = item({ status: "failed", url: null, error: "generation_failed" });
    const { unmount } = wrap(<GeneratedMedia item={failed} />);
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
    unmount();
    wrap(<GeneratedMedia item={failed} onRetry={onRetry} />);
    expect(screen.getByRole("alert")).toHaveTextContent("The generation failed. Try again in a moment.");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("has no open button unless there is something to open into", () => {
    wrap(<GeneratedMedia item={item()} />);
    expect(screen.queryByRole("button", { name: "Open in the side panel" })).toBeNull();
  });
});

describe("GenerationProgress", () => {
  it("words the queue position", () => {
    wrap(<GenerationProgress stage="queued" queuePosition={2} />);
    expect(screen.getByRole("status")).toHaveTextContent("Waiting for a free GPU · 2 ahead");
  });
});

describe("error codes", () => {
  it("maps known codes to ui_copy keys and anything else to the generic one", () => {
    expect(generationErrorKey("prompt_real_person")).toBe("generation.error_prompt_real_person");
    expect(generationErrorKey("whatever")).toBe("generation.error_generic");
    expect(generationErrorKey(null)).toBe("generation.error_generic");
  });
});

describe("GeneratedLibrary", () => {
  it("lists own items with the label, marks published ones and confirms before deleting", async () => {
    hooks.list = {
      isPending: false,
      isError: false,
      data: {
        items: [item(), item({ id: "7f1e5a2c-0c1b-4c55-9a55-0e8f2b6f7a10", published: true })],
        next_before: null,
      },
    };
    hooks.remove.mockResolvedValue(undefined);
    wrap(<GeneratedLibrary />);
    expect(screen.getAllByText(LABEL)).toHaveLength(2);
    expect(screen.getByText("Published as a chapter figure")).toBeVisible();
    const first = within(screen.getAllByTestId("generated-card")[0] as HTMLElement);
    await userEvent.click(first.getByRole("button", { name: /Delete/ }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("Delete this image?")).toBeVisible();
    expect(hooks.remove).not.toHaveBeenCalled(); // nothing is deleted before the confirm
    await userEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
    expect(hooks.remove).toHaveBeenCalledWith("6f1e5a2c-0c1b-4c55-9a55-0e8f2b6f7a10");
  });

  it("shows the empty state and a retryable error", () => {
    hooks.list = { isPending: false, isError: false, data: { items: [], next_before: null } };
    const { unmount } = wrap(<GeneratedLibrary />);
    expect(screen.getByTestId("generated-empty")).toBeVisible();
    unmount();
    hooks.list = { isPending: false, isError: true, error: new Error("x"), refetch: vi.fn() };
    wrap(<GeneratedLibrary />);
    expect(screen.getByText("Your generated items could not be loaded.")).toBeVisible();
  });
});
