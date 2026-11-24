import { act, renderHook, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { z } from "zod";
import { registerCanvasKind, resetCanvasForTests, useCanvas } from "../canvas/registry";
import { renderInApp } from "../test/assistant";
import { BlockView } from "./blocks";
import type { Block } from "./protocol";
import { parseCustomEvent } from "./protocol";

vi.mock("../content/mermaidHost", () => ({
  renderMermaid: vi.fn(async () => ({ id: "m1", svg: "<svg xmlns='http://www.w3.org/2000/svg'><text>A</text></svg>" })),
  adoptSvg: (host: HTMLElement) => {
    host.innerHTML = "<svg data-test-svg></svg>";
  },
  releaseDiagramStyles: () => {},
}));

function block(raw: Record<string, unknown>): Block {
  const ev = parseCustomEvent({ type: "block", block: { id: "b1", message_id: null, ...raw } });
  if (ev?.type !== "block") throw new Error(`invalid block ${JSON.stringify(raw)}`);
  return ev.block;
}

function show(b: Block, ask = vi.fn()) {
  return renderInApp(
    () => <BlockView block={b} handlers={{ ask }} busy={false} />,
    ["/cards", "/practice/blocks/$blockId/$n", "/learn/library", "/videos"],
  );
}

const citation = (n: number, over: Record<string, unknown> = {}) => ({
  id: `c${n}`,
  message_id: null,
  title: `Source ${n}`,
  source: "Naadi",
  layer: "course",
  url: null,
  target: { chapter_id: "ch05", section_id: "s3" },
  license: null,
  n,
  ...over,
});

afterEach(() => resetCanvasForTests());

describe("answer blocks", () => {
  it("sources: the count is singular for one source and plural otherwise (review14 r3)", async () => {
    const one = show(block({ type: "sources", data: { items: [citation(1)] } }));
    expect(await screen.findByText("From 1 source")).toBeInTheDocument();
    expect(screen.queryByText(/From 1 sources/)).toBeNull();
    one.unmount();
    show(block({ type: "sources", data: { items: [citation(1), citation(2)] } }));
    expect(await screen.findByText("From 2 sources")).toBeInTheDocument();
  });

  it("flowchart: label + the sandboxed diagram", async () => {
    show(block({ type: "flowchart", data: { mermaid: "flowchart TD\nA-->B" } }));
    expect(await screen.findByRole("region", { name: "Flowchart" })).toBeInTheDocument();
    expect(await screen.findByRole("img", { name: /diagram/i })).toBeInTheDocument();
  });

  it("image: only from the CSP's hosts, with title and credit", async () => {
    const ok = show(
      block({
        type: "image",
        data: {
          url: "https://upload.wikimedia.org/n.png",
          title: "Nephron",
          license: "CC BY-SA",
          credit: "Jane",
          link: "https://commons.wikimedia.org/x",
        },
      }),
    );
    expect(await screen.findByRole("img", { name: "Nephron" })).toHaveAttribute(
      "src",
      "https://upload.wikimedia.org/n.png",
    );
    expect(screen.getByText("Credit: Jane · CC BY-SA")).toBeInTheDocument();
    ok.unmount();
    show(
      block({
        type: "image",
        data: { url: "https://evil.example/n.png", title: "Nope", license: null, credit: null, link: null },
      }),
    );
    await screen.findByText("destination", {}, { timeout: 50 }).catch(() => null);
    expect(screen.queryByRole("img", { name: "Nope" })).toBeNull();
  });

  it("video: the existing click-to-load YouTube node (no iframe until asked)", async () => {
    const user = userEvent.setup();
    show(
      block({
        type: "video",
        data: { provider: "youtube", video_id: "dQw4w9WgXcQ", media_id: "yt:1", title: "RTA explained", start_s: 30 },
      }),
    );
    const play = await screen.findByRole("button", { name: /RTA explained/ });
    expect(document.querySelector("iframe")).toBeNull();
    await user.click(play);
    const frame = document.querySelector("iframe");
    // S20's player page (ContentRenderer.test.tsx pins its origin and flags): never YouTube framed directly; the
    // video id and start reach it over the typed bridge.
    const src = new URL(frame?.getAttribute("src") ?? "", window.location.href);
    expect(src.hostname).not.toMatch(/youtube/);
    expect(frame?.getAttribute("sandbox")).toContain("allow-scripts");
  });

  it("video: a catalogue video links to its place on the Videos page", async () => {
    show(
      block({
        type: "video",
        data: {
          provider: "youtube",
          video_id: "dQw4w9WgXcQ",
          media_id: "yt-rta",
          title: "RTA explained",
          start_s: null,
          step: "step1",
          subject: "Renal",
        },
      }),
    );
    const link = await screen.findByRole("link", { name: "Open in Videos" });
    const href = new URL(link.getAttribute("href") ?? "", window.location.href);
    expect(href.pathname).toBe("/videos");
    expect(href.searchParams.get("v")).toBe("yt-rta");
    expect(JSON.parse(href.searchParams.get("vs") ?? "null")).toEqual({ step: "step1", subject: "Renal" });
  });

  it("video: a Commons file plays in the browser's player only when asked, with its credit and licence", async () => {
    const user = userEvent.setup();
    show(
      block({
        type: "video",
        data: {
          provider: "commons",
          media_id: "cm-heart",
          title: "Heart valves",
          start_s: null,
          file_url: "https://upload.wikimedia.org/heart.webm",
          page_url: "https://commons.wikimedia.org/wiki/File:Heart.webm",
          attribution: "Heart valves by Someone, CC BY 4.0",
          duration_s: 90,
          licence: { license_short_name: "CC BY 4.0", license_url: "https://creativecommons.org/licenses/by/4.0" },
        },
      }),
    );
    expect(await screen.findByText("Heart valves by Someone, CC BY 4.0")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "CC BY 4.0" })).toHaveAttribute("rel", "noopener noreferrer");
    // a chapter video outside the catalogue: no Videos link
    expect(screen.queryByRole("link", { name: "Open in Videos" })).toBeNull();
    expect(document.querySelector("video")).toBeNull();
    await user.click(screen.getByRole("button", { name: /Heart valves/ }));
    expect(document.querySelector("video")).toHaveAttribute("src", "https://upload.wikimedia.org/heart.webm");
    expect(document.querySelector("iframe")).toBeNull();
  });

  it("sources: chips with the layer in words, course links into the reader, web links out", async () => {
    show(
      block({
        type: "sources",
        data: {
          items: [
            citation(1),
            citation(2, { layer: "web", source: "Tavily", url: "https://www.cdc.gov/x", target: null }),
          ],
        },
      }),
    );
    const list = await screen.findByRole("region", { name: "Sources" });
    expect(within(list).getByText("Searched the web · 2 sources")).toBeInTheDocument();
    expect(within(list).getByText("(Naadi course)")).toBeInTheDocument();
    expect(within(list).getByText("(Web · Tavily)")).toBeInTheDocument();
    const links = within(list).getAllByRole("link");
    expect(links[0]).toHaveAttribute("href", "/learn/ch05/s3");
    expect(links[1]).toHaveAttribute("href", "https://www.cdc.gov/x");
    expect(links[1]).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("actions: known labels only; ask sends; open_block navigates; open_canvas opens the canvas", async () => {
    const user = userEvent.setup();
    const off = registerCanvasKind({
      kind: "diagram",
      schema: z.object({ mermaid: z.string() }),
      load: async () => ({ default: () => null }),
      label_key: "canvas.kind.diagram",
      icon: "workflow",
    });
    const ask = vi.fn();
    const canvasRef = {
      key: "cv1",
      kind: "diagram",
      origin: "assistant",
      title_key: null,
      title: "Gap",
      ref: null,
      data: { mermaid: "x" },
    };
    const { router } = show(
      block({
        type: "actions",
        data: {
          items: [
            { action: "ask", label_key: "agent.chip.quiz_me", payload: { kind: "quiz" } },
            { action: "ask", label_key: "agent.not_a_key", payload: {} },
            { action: "open_canvas", label_key: "agent.block.canvas", payload: canvasRef },
            { action: "open_block", label_key: "agent.action.open_block", payload: { block_id: "b7", n: 3 } },
            { action: "open_block", label_key: "agent.action.open_block", payload: { block_id: "b7", n: "x" } },
          ],
        },
      }),
      ask,
    );
    await screen.findByRole("button", { name: "Quiz me" });
    expect(screen.getAllByRole("button")).toHaveLength(3); // unknown label and bad payload are not shown
    await user.click(screen.getByRole("button", { name: "Quiz me" }));
    expect(ask).toHaveBeenCalledWith("Quiz me", "quiz");
    const canvas = renderHook(() => useCanvas());
    await user.click(screen.getByRole("button", { name: "Open in canvas" }));
    expect(canvas.result.current.items).toHaveLength(1);
    await user.click(screen.getByRole("button", { name: "Open the questions" }));
    await act(async () => {});
    expect(router.state.location.pathname).toBe("/practice/blocks/b7/3");
    off();
  });

  it("sources: a page of the student's PDF is worded from ui_copy and opens the viewer at that page", async () => {
    const upload = "0b9a7c8e-1f2d-4c3b-9a8e-7d6c5b4a3f21";
    show(
      block({
        type: "sources",
        data: {
          items: [
            citation(1, {
              title: "Renal.pdf",
              source: "Renal.pdf",
              layer: "notes",
              target: { type: "pdf_page", upload_id: upload, page: "2" },
            }),
            citation(2, { title: "", layer: "notes", target: { type: "pdf_page", upload_id: upload, page: "5" } }),
            citation(3, { title: "Odd", layer: "notes", target: { type: "pdf_page", upload_id: "../x", page: "1" } }),
          ],
        },
      }),
    );
    const list = await screen.findByRole("region", { name: "Sources" });
    expect(within(list).getByText("Renal.pdf, page 2")).toBeInTheDocument();
    expect(within(list).getByText("Your PDF, page 5")).toBeInTheDocument();
    const links = within(list).getAllByRole("link");
    expect(links[0]).toHaveAttribute("href", `/learn/library/pdf/${upload}?page=2`);
    expect(links[1]).toHaveAttribute("href", `/learn/library/pdf/${upload}?page=5`);
    // A malformed target is plain text: no link built from it.
    expect(within(list).getByText("Odd").closest("a")).toBeNull();
  });

  it("actions: open_library (S15 add_to_knowledge) goes to the Library", async () => {
    const user = userEvent.setup();
    const { router } = show(
      block({
        type: "actions",
        data: {
          items: [{ action: "open_library", label_key: "agent.action.open_library", payload: { item_id: "i1" } }],
        },
      }),
    );
    await user.click(await screen.findByRole("button", { name: "Open Library" }));
    await act(async () => {});
    expect(router.state.location.pathname).toBe("/learn/library");
  });

  it("card preview, table, chart and notices word everything from ui_copy", async () => {
    show(block({ type: "card_preview", data: { cards: [{ front: "Winters?", back: "1.5 × HCO3 + 8" }] } }));
    expect(await screen.findByText("Flashcards · 1")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Open Cards/ })).toHaveAttribute("href", "/cards");

    show(
      block({
        type: "table",
        data: {
          columns: [
            { key: "system", label_key: "agent.analytics.system", label: null },
            { key: "percent", label_key: null, label: "Score" },
          ],
          rows: [{ system: "Renal", percent: 72 }],
        },
      }),
    );
    const table = await screen.findByRole("table", { name: "Table" });
    expect(within(table).getByRole("columnheader", { name: "System" })).toBeInTheDocument();
    expect(within(table).getByRole("columnheader", { name: "Score" })).toBeInTheDocument();

    show(
      block({
        type: "chart",
        data: {
          chart: "bar",
          x: "system",
          series: [{ key: "percent", label_key: "agent.analytics.percent", label: null }],
          rows: [{ system: "Renal", percent: 72 }],
          unit: "percent",
        },
      }),
    );
    expect(await screen.findByRole("img", { name: "Bar chart" })).toBeInTheDocument();
    expect(screen.getAllByText("72%").length).toBeGreaterThan(0);

    show(block({ type: "notice", data: { kind: "budget_exhausted", reset_at: new Date().toISOString() } }));
    expect(await screen.findByText(/AI quota resets at/)).toBeInTheDocument();
    expect(screen.getByText("Quota used")).toBeInTheDocument();

    show(block({ type: "notice", data: { kind: "disclaimer" } }));
    expect(await screen.findByText("For exam study only, not for patient care.")).toBeInTheDocument();
  });
});
