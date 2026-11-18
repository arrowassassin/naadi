import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { useMemo } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ContentRenderer } from "../content/ContentRenderer";
import { htmlTextRuns, SafeHtml } from "../content/SafeHtml";
import { type HighlightColor, makeAnchor, type PaintRange, sectionRuns } from "./anchor";
import { rangeToPositions } from "./dom";
import { useHighlightDecorator } from "./Mark";
import { SelectionToolbar, type ToolbarActions } from "./SelectionToolbar";

const doc = {
  type: "doc",
  content: [
    { type: "heading", attrs: { level: 2 }, content: [{ type: "text", text: "Renal tubular acidosis" }] },
    {
      type: "paragraph",
      content: [
        { type: "text", text: "Every RTA is a " },
        { type: "text", text: "normal anion gap", marks: [{ type: "bold" }] },
        { type: "text", text: " metabolic acidosis." },
      ],
    },
    {
      type: "callout",
      attrs: { variant: "pearl" },
      content: [{ type: "paragraph", content: [{ type: "text", text: "Type 4 is the one with hyperkalemia." }] }],
    },
  ],
};

const EX_HTML = "<p>Type 4 RTA is the one with <b>hyperkalemia</b>.</p>";

function Explanation() {
  const decorate = useHighlightDecorator([], null);
  return <SafeHtml html={EX_HTML} decorateText={decorate} />;
}

function Section({ ranges = [] as PaintRange[] }) {
  const decorate = useHighlightDecorator(ranges, null);
  return (
    <div data-testid="root">
      <ContentRenderer doc={doc} sectionId="s1" mode="prod" decorateText={decorate} />
    </div>
  );
}

/** A DOM range over `text` (inside one text node) under `root`. */
function rangeOver(root: Element, text: string, from = 0, len = text.length): Range {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const i = (n as Text).data.indexOf(text);
    if (i >= 0) {
      const r = document.createRange();
      r.setStart(n, i + from);
      r.setEnd(n, i + from + len);
      return r;
    }
  }
  throw new Error(`no text ${text}`);
}

describe("selection → anchor positions", () => {
  it("maps a selection across marks to ProseMirror positions and the exact quote", () => {
    render(<Section />);
    const root = screen.getByTestId("root");
    const range = document.createRange();
    const start = rangeOver(root, "Every RTA is a ", 6, 3); // "RTA"
    const end = rangeOver(root, " metabolic acidosis.", 0, 10); // " metabolic"
    range.setStart(start.startContainer, start.startOffset);
    range.setEnd(end.endContainer, end.endOffset);
    const pos = rangeToPositions(root, range);
    expect(pos).not.toBeNull();
    const anchor = makeAnchor(sectionRuns(doc), pos?.from ?? 0, pos?.to ?? 0);
    expect(anchor?.quote).toBe("RTA is a normal anion gap metabolic");
  });

  it("ignores labels that are not section text (the callout's label)", () => {
    render(<Section />);
    const root = screen.getByTestId("root");
    const label = root.querySelector("[data-node=callout] > span") as HTMLElement;
    const range = document.createRange();
    range.setStart(label.firstChild as Node, 0);
    const end = rangeOver(root, "Type 4 is", 0, 6);
    range.setEnd(end.endContainer, end.endOffset);
    const pos = rangeToPositions(root, range);
    const anchor = makeAnchor(sectionRuns(doc), pos?.from ?? 0, pos?.to ?? 0);
    expect(anchor?.quote).toBe("Type 4");
  });

  it("returns null for a selection outside the runs", () => {
    render(
      <div data-testid="root">
        <p>not content</p>
      </div>,
    );
    const root = screen.getByTestId("root");
    expect(rangeToPositions(root, rangeOver(root, "not content"))).toBeNull();
  });

  it("paints highlights as marks with the word for screen readers, once per highlight", () => {
    const runs = sectionRuns(doc);
    const a = makeAnchor(runs, 21, 52);
    if (!a) throw new Error("anchor");
    render(<Section ranges={[{ id: "h1", from: a.from, to: a.to, color: "violet" }]} />);
    const marks = document.querySelectorAll("mark[data-highlight-id=h1]");
    expect(marks.length).toBeGreaterThan(1); // the bold run splits it
    expect(marks[0]?.getAttribute("data-color")).toBe("violet");
    expect(document.querySelectorAll("[data-sr-word]")).toHaveLength(1);
    expect(marks[0]?.textContent?.startsWith("Formula: ")).toBe(true);
  });

  it("maps explanation HTML to text offsets", () => {
    const html = EX_HTML;
    const { container } = render(<Explanation />);
    const pos = rangeToPositions(container, rangeOver(container, "hyperkalemia"));
    expect(makeAnchor(htmlTextRuns(html), pos?.from ?? 0, pos?.to ?? 0)?.quote).toBe("hyperkalemia");
  });
});

function Harness({ actions, enabled = true }: { actions: ToolbarActions; enabled?: boolean }) {
  const runs = useMemo(() => sectionRuns(doc), []);
  const decorate = useHighlightDecorator([{ id: "h9", from: 1, to: 6, color: "amber" }], null);
  const ref = { current: null as HTMLDivElement | null };
  return (
    <div>
      <input aria-label="field" />
      <div
        ref={(el) => {
          ref.current = el;
        }}
        data-testid="surface"
        className="relative"
      >
        <ContentRenderer doc={doc} sectionId="s1" mode="prod" decorateText={decorate} />
        <SelectionToolbar surfaceRef={ref} hostRef={ref} runs={runs} actions={actions} enabled={enabled} />
      </div>
    </div>
  );
}

function makeActions(over: Partial<ToolbarActions> = {}): ToolbarActions {
  return {
    onHighlight: vi.fn(),
    onNote: vi.fn(),
    onRecolor: vi.fn(),
    onRemove: vi.fn(),
    colorOf: (id) => (id === "h9" ? ("amber" as HighlightColor) : null),
    ...over,
  };
}

function select(root: Element, text: string) {
  const sel = document.getSelection();
  sel?.removeAllRanges();
  sel?.addRange(rangeOver(root, text));
  act(() => {
    document.dispatchEvent(new Event("selectionchange"));
    vi.advanceTimersByTime(200);
  });
}

describe("SelectionToolbar", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    return () => vi.useRealTimers();
  });

  it("opens on a selection with the three words, Note and Card; Ask only when given", () => {
    const actions = makeActions({ onCard: vi.fn() });
    render(<Harness actions={actions} />);
    select(screen.getByTestId("surface"), "metabolic acidosis");
    const bar = screen.getByRole("toolbar", { name: "Selection tools" });
    expect(within(bar).getByRole("button", { name: "Key fact" })).toBeVisible();
    expect(within(bar).getByRole("button", { name: "Formula" })).toBeVisible();
    expect(within(bar).getByRole("button", { name: "My mistake" })).toBeVisible();
    expect(within(bar).getByRole("button", { name: "Note" })).toBeVisible();
    expect(within(bar).getByRole("button", { name: "Card" })).toBeVisible();
    expect(within(bar).queryByRole("button", { name: "Ask" })).toBeNull();
    fireEvent.click(within(bar).getByRole("button", { name: "Formula" }));
    expect(actions.onHighlight).toHaveBeenCalledWith(
      "violet",
      expect.objectContaining({ anchor: expect.objectContaining({ quote: "metabolic acidosis" }) }),
    );
    expect(screen.queryByRole("toolbar", { name: "Selection tools" })).toBeNull();
  });

  it("has no Card without onCard (question explanations) and shows Ask when the assistant answers", () => {
    render(<Harness actions={makeActions({ onAsk: vi.fn() })} />);
    select(screen.getByTestId("surface"), "hyperkalemia");
    const bar = screen.getByRole("toolbar");
    expect(within(bar).queryByRole("button", { name: "Card" })).toBeNull();
    expect(within(bar).getByRole("button", { name: "Ask" })).toBeVisible();
  });

  it("moves between buttons with the arrow keys and closes on Escape", () => {
    render(<Harness actions={makeActions()} />);
    select(screen.getByTestId("surface"), "hyperkalemia");
    const bar = screen.getByRole("toolbar");
    fireEvent.keyDown(document.body, { key: "Tab" });
    const buttons = within(bar).getAllByRole("button");
    expect(buttons[0]).toHaveFocus();
    fireEvent.keyDown(bar, { key: "ArrowRight" });
    expect(buttons[1]).toHaveFocus();
    fireEvent.keyDown(bar, { key: "End" });
    expect(buttons.at(-1)).toHaveFocus();
    fireEvent.keyDown(bar, { key: "Escape" });
    expect(screen.queryByRole("toolbar")).toBeNull();
  });

  it("does not take keys typed in a field", () => {
    render(<Harness actions={makeActions()} />);
    select(screen.getByTestId("surface"), "hyperkalemia");
    const field = screen.getByRole("textbox", { name: "field" });
    field.focus();
    fireEvent.keyDown(field, { key: "Escape" });
    fireEvent.keyDown(field, { key: "Tab" });
    expect(screen.getByRole("toolbar")).toBeVisible();
    expect(field).toHaveFocus();
  });

  it("closes when the selection goes away, and stays off while ink is on", () => {
    const { rerender } = render(<Harness actions={makeActions()} />);
    const surface = screen.getByTestId("surface");
    select(surface, "hyperkalemia");
    act(() => {
      document.getSelection()?.removeAllRanges();
      document.dispatchEvent(new Event("selectionchange"));
      vi.advanceTimersByTime(200);
    });
    expect(screen.queryByRole("toolbar")).toBeNull();
    rerender(<Harness actions={makeActions()} enabled={false} />);
    select(surface, "hyperkalemia");
    expect(screen.queryByRole("toolbar")).toBeNull();
  });

  it("tapping one of the student's highlights offers its colours and Remove", () => {
    const actions = makeActions();
    render(<Harness actions={actions} />);
    const mark = document.querySelector("mark[data-highlight-id=h9]") as HTMLElement;
    fireEvent.click(mark);
    const bar = screen.getByRole("toolbar");
    expect(within(bar).getByRole("button", { name: "My mistake" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(within(bar).getByRole("button", { name: "Key fact" }));
    expect(actions.onRecolor).toHaveBeenCalledWith("h9", "lime");
    fireEvent.click(mark);
    fireEvent.click(within(screen.getByRole("toolbar")).getByRole("button", { name: "Remove" }));
    expect(actions.onRemove).toHaveBeenCalledWith("h9");
  });
});
