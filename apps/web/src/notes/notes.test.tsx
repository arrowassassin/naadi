import type { NoteOut } from "@naadi/shared";
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from "@tanstack/react-router";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createRef, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { editedLabel, isDetached, noteTitle, sourceLabel } from "./format";
import { type HighlightFilters, HighlightsRail } from "./HighlightsRail";
import type { NoteFilters } from "./types";

const state = vi.hoisted(() => ({
  items: [] as unknown[],
  pending: false,
  error: null as unknown,
  calls: [] as unknown[],
  writes: [] as { op: string; id?: string; fields?: Record<string, unknown> }[],
  ops: [] as { id: string; create: boolean; refused: string | null }[],
}));

vi.mock("./api", () => ({
  useNotes: (filters: NoteFilters) => {
    state.calls.push(filters);
    const items = (state.items as NoteOut[]).filter(
      (n) =>
        (!filters.kind || n.kind === filters.kind) &&
        (!filters.color || n.highlight_color === filters.color) &&
        (!filters.anchor_type || n.anchor_type === filters.anchor_type) &&
        (!filters.anchor_id || n.anchor_id === filters.anchor_id),
    );
    return {
      items,
      data: state.pending || state.error ? undefined : { data: { items } },
      isPending: state.pending,
      isError: Boolean(state.error),
      error: state.error,
      isFetching: false,
      fromCache: false,
      counts: {
        notes: 1,
        highlights: 3,
        pages: 0,
        by_color: { lime: 1, violet: 1, amber: 1 },
        by_anchor: { section: 2, question: 1, video: 0, pdf: 0, free: 0 },
      },
      refetch: vi.fn(),
    };
  },
  useNoteWriter: () => ({
    create: (fields: Record<string, unknown>) => {
      state.writes.push({ op: "create", fields });
      return "new-id";
    },
    update: (id: string, fields: Record<string, unknown>) => state.writes.push({ op: "update", id, fields }),
    remove: (id: string) => state.writes.push({ op: "remove", id }),
  }),
  usePendingOps: () => state.ops,
  useNotesSync: () => ({ flush: vi.fn(), pending: 0, refused: [] }),
  useUserId: () => "u1",
  errorKey: () => "notes.error.generic",
  refusedKey: () => "notes.error.generic",
}));

vi.mock("./NoteInk", () => ({
  NoteInk: ({ noteId }: { noteId: string }) => <div data-testid="ink">{noteId}</div>,
}));

function note(over: Partial<NoteOut>): NoteOut {
  return {
    id: "n",
    kind: "highlight",
    title: null,
    notebook_id: null,
    anchor_type: "section",
    anchor_id: "s1",
    anchor_pos: { from: 1, to: 5, quote: "quote", prefix: "", suffix: "" },
    highlight_color: "lime",
    body: null,
    body_omitted: false,
    body_text: "",
    excerpt: "",
    paper: null,
    canvas: "paper",
    sort: 0,
    source: {
      type: "section",
      chapter_id: "ch07",
      chapter_title: "Renal",
      section_id: "ch07-s3",
      section_heading: "Three leaks",
    },
    has_ink: false,
    created_at: "2026-11-16T10:00:00Z",
    updated_at: "2026-11-16T10:00:00Z",
    ...over,
  } as NoteOut;
}

/** A router around the component, so <Link>s render. */
function withRouter(ui: ReactNode) {
  const root = createRootRoute({ component: () => <>{ui}</> });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: ["/"] }) });
  return render(<RouterProvider router={router} />);
}

beforeEach(() => {
  state.items = [];
  state.pending = false;
  state.error = null;
  state.calls = [];
  state.writes = [];
  state.ops = [];
});

describe("format", () => {
  const t = (k: string, v?: Readonly<Record<string, string>>) => `${k}${v ? JSON.stringify(v) : ""}`;
  it("words the edit date", () => {
    const now = new Date("2026-11-17T12:00:00");
    expect(editedLabel(t, "2026-11-17T08:00:00", now)).toBe("notes.edited_today");
    expect(editedLabel(t, "2026-11-16T23:00:00", now)).toBe("notes.edited_yesterday");
    expect(editedLabel(t, "2026-11-01T08:00:00", now)).toMatch(/^notes\.edited_on/);
  });
  it("titles and sources", () => {
    expect(noteTitle(t, note({ title: " Mine " }))).toBe("Mine");
    expect(noteTitle(t, note({ source: null, body_text: "\nFirst line\nsecond" }))).toBe("First line");
    expect(noteTitle(t, note({ source: null }))).toBe("notes.untitled");
    expect(sourceLabel(t, note({}))).toContain("Three leaks");
    expect(sourceLabel(t, note({ source: { type: "question", question_number: 4 } as NoteOut["source"] }))).toBe(
      'notes.source_question_n{"n":"4"}',
    );
  });
  it("detached: flagged by the reader, or its source is gone (unless it was just made here)", () => {
    expect(isDetached(note({}))).toBe(false);
    expect(isDetached(note({ anchor_pos: { from: 0, to: 1, quote: "q", detached: true } }))).toBe(true);
    expect(isDetached(note({ source: null }))).toBe(true);
    expect(isDetached(note({ source: null }), true)).toBe(false);
    expect(isDetached(note({ kind: "note", source: null }))).toBe(false);
  });
});

function Rail({ filters, onFilters }: { filters: HighlightFilters; onFilters: (f: HighlightFilters) => void }) {
  return <HighlightsRail filters={filters} onFilters={onFilters} />;
}

describe("HighlightsRail", () => {
  const items = [
    note({
      id: "a",
      highlight_color: "lime",
      anchor_pos: { from: 1, to: 2, quote: "Key one", prefix: "", suffix: "" },
    }),
    note({
      id: "b",
      highlight_color: "violet",
      anchor_type: "question",
      anchor_id: "q1",
      anchor_pos: { from: 1, to: 2, quote: "Formula one", prefix: "", suffix: "" },
      source: { type: "question", question_id: "q1", block_id: "b1", question_number: 8 } as NoteOut["source"],
    }),
    note({
      id: "c",
      highlight_color: "amber",
      anchor_pos: { from: 1, to: 2, quote: "Gone one", prefix: "", suffix: "", detached: true },
    }),
  ];

  it("lists highlights with their word and source, and keeps detached ones in their own group", async () => {
    state.items = items;
    withRouter(<Rail filters={{}} onFilters={vi.fn()} />);
    const list = await screen.findByRole("list", { name: "Recent highlights" });
    const rows = within(list).getAllByRole("listitem");
    expect(rows).toHaveLength(2);
    expect(within(rows[0] as HTMLElement).getByText("Key fact")).toBeVisible();
    expect(within(rows[0] as HTMLElement).getByRole("link", { name: /Three leaks · Renal/ })).toHaveAttribute(
      "href",
      "/learn/ch07/ch07-s3",
    );
    expect(within(rows[1] as HTMLElement).getByRole("link", { name: /Question 8/ })).toHaveAttribute(
      "href",
      "/practice/blocks/b1/8",
    );
    const detached = screen.getByRole("region", { name: /Detached · 1/ });
    expect(within(detached).getByText("Gone one")).toBeVisible();
    expect(within(detached).getByText("My mistake")).toBeVisible();
  });

  it("filters by colour and by anchor through the URL state, counts on the chips", async () => {
    state.items = items;
    const onFilters = vi.fn();
    withRouter(<Rail filters={{}} onFilters={onFilters} />);
    const formula = await screen.findByRole("button", { name: /^Formula/ });
    expect(formula).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(formula);
    expect(onFilters).toHaveBeenLastCalledWith({ color: "violet" });
    fireEvent.click(screen.getByRole("button", { name: /^Questions/ }));
    expect(onFilters).toHaveBeenLastCalledWith({ anchor: "question" });
  });

  it("asks the server for the filtered list", async () => {
    state.items = items;
    withRouter(<Rail filters={{ color: "violet", anchor: "question" }} onFilters={vi.fn()} />);
    await screen.findByRole("list", { name: "Recent highlights" });
    expect(state.calls).toContainEqual(
      expect.objectContaining({ kind: "highlight", color: "violet", anchor_type: "question" }),
    );
    expect(screen.getAllByText("Formula one")).toHaveLength(1);
    expect(screen.queryByText("Key one")).toBeNull();
  });

  it("detached filter shows only the detached group", async () => {
    state.items = items;
    withRouter(<Rail filters={{ anchor: "detached" }} onFilters={vi.fn()} />);
    await screen.findByRole("region", { name: /Detached · 1/ });
    expect(screen.queryByRole("list", { name: "Recent highlights" })).toBeNull();
  });

  it("empty, loading and error states", async () => {
    const { unmount } = withRouter(<Rail filters={{}} onFilters={vi.fn()} />);
    expect(await screen.findByText(/No highlights yet/)).toBeVisible();
    unmount();
    state.pending = true;
    const second = withRouter(<Rail filters={{}} onFilters={vi.fn()} />);
    expect(await screen.findByText("Loading your highlights")).toBeInTheDocument();
    second.unmount();
    state.pending = false;
    state.error = new Error("x");
    state.items = [];
    withRouter(<Rail filters={{}} onFilters={vi.fn()} />);
    expect(await screen.findByText("Your highlights could not be loaded")).toBeVisible();
  });

  it("remove sends a delete", async () => {
    state.items = items;
    withRouter(<Rail filters={{}} onFilters={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: /Remove the highlight “Key one”/ }));
    expect(state.writes).toContainEqual({ op: "remove", id: "a" });
  });
});

describe("PageNotesPanel", () => {
  it("Type / Write / Both, and a quote from the toolbar makes the page note", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { PageNotesPanel } = await import("./PageNotesPanel");
    const ref = createRef<import("./PageNotesPanel").PageNotesHandle>();
    const onInk = vi.fn();
    render(<PageNotesPanel ref={ref} sectionId="s1" sectionHeading="Three leaks" onInkChange={onInk} />);
    expect(screen.getByRole("heading", { name: "Page notes" })).toBeVisible();
    expect(screen.getByText(/Pinned to Three leaks/)).not.toBeVisible();
    fireEvent.click(screen.getByLabelText(/Pinned to Three leaks/));
    expect(screen.getByText(/Pinned to Three leaks/)).toBeVisible();
    const editor = await screen.findByRole("textbox", { name: "Your notes on Three leaks" });
    expect(editor).toBeVisible();
    // Selecting Write creates the section note and enables ink in one action.
    fireEvent.click(screen.getByRole("button", { name: "Write" }));
    expect(screen.queryByRole("textbox", { name: "Your notes on Three leaks" })).toBeNull();
    expect(onInk).toHaveBeenCalledWith(true);
    expect(state.writes[0]).toMatchObject({
      op: "create",
      fields: { kind: "note", anchor_type: "section", anchor_id: "s1" },
    });
    // Both shows the typed note and the ink
    fireEvent.click(screen.getByRole("button", { name: "Both" }));
    expect(screen.getByRole("button", { name: "Both" })).toHaveAttribute("aria-pressed", "true");
    expect(await screen.findByRole("textbox", { name: "Your notes on Three leaks" })).toBeVisible();
    // Quote → typed into the note, saved after the pause
    act(() => ref.current?.addQuote("Type 4 is the one with hyperkalemia."));
    await act(async () => {
      vi.advanceTimersByTime(1000);
    });
    await waitFor(() =>
      expect(state.writes.some((w) => w.op === "update" && JSON.stringify(w.fields).includes("hyperkalemia"))).toBe(
        true,
      ),
    );
    vi.useRealTimers();
  });
});
