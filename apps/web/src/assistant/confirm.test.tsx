import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ConfirmChip } from "./ConfirmChip";
import { type ConfirmRequest, parseInterrupt } from "./protocol";

const para = (text: string) => ({ type: "paragraph", content: [{ type: "text", text }] });
const doc = { type: "doc", content: [para("Gap = Na − (Cl + HCO3)")] };

function req(preview: Record<string, unknown>, editable = true): ConfirmRequest {
  const r = parseInterrupt({ type: "confirm", tool: "t", preview, editable });
  if (!r) throw new Error("invalid request");
  return r;
}

function chip(r: ConfirmRequest) {
  const onResume = vi.fn();
  render(<ConfirmChip request={r} busy={false} onResume={onResume} />);
  return onResume;
}

describe("confirm chip", () => {
  it("accept and cancel resume with the decision; the chip then waits", async () => {
    const user = userEvent.setup();
    const onResume = chip(req({ kind: "memory", key: "k", fact: "Exam on 3 March" }));
    expect(screen.getByRole("heading", { name: "Remember this?" })).toBeInTheDocument();
    expect(screen.getByText("Waiting for you")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Accept" }));
    expect(onResume).toHaveBeenCalledWith({ decision: "accept" });
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
  });

  it("cancel resumes with cancel (nothing written)", async () => {
    const user = userEvent.setup();
    const onResume = chip(
      req({
        kind: "note",
        mode: "create",
        note_id: null,
        anchor: { type: "section", id: "s1", label: "Anion gap" },
        doc,
      }),
    );
    expect(screen.getByText("On: Anion gap")).toBeInTheDocument();
    expect(screen.getByText("Gap = Na − (Cl + HCO3)")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onResume).toHaveBeenCalledWith({ decision: "cancel" });
  });

  it("edit cards: the edited cards are the resume args; an empty card blocks saving", async () => {
    const user = userEvent.setup();
    const onResume = chip(
      req({
        kind: "cards",
        cards: [
          { front: "Q1", back: "A1" },
          { front: "Q2", back: "A2" },
        ],
      }),
    );
    await user.click(screen.getByRole("button", { name: "Edit" }));
    const front1 = screen.getByLabelText("Card 1, front");
    await user.clear(front1);
    expect(screen.getByRole("button", { name: "Save and apply" })).toBeDisabled();
    await user.type(front1, "Winters?");
    await user.click(screen.getByRole("button", { name: "Remove card 2" }));
    await user.click(screen.getByRole("button", { name: "Save and apply" }));
    expect(onResume).toHaveBeenCalledWith({ decision: "edit", args: { cards: [{ front: "Winters?", back: "A1" }] } });
  });

  it("edit a plan item and a memory: day/title and fact", async () => {
    const user = userEvent.setup();
    const onPlan = chip(
      req({ kind: "plan_item", day: "2026-11-18", title: "Renal", item_kind: "review", target_minutes: 30 }),
    );
    await user.click(screen.getByRole("button", { name: "Edit" }));
    const title = screen.getByLabelText("What");
    await user.clear(title);
    await user.type(title, "Renal review");
    await user.click(screen.getByRole("button", { name: "Save and apply" }));
    expect(onPlan).toHaveBeenCalledWith({ decision: "edit", args: { day: "2026-11-18", title: "Renal review" } });
  });

  it("edit a note in the TipTap editor; Back discards the edit", async () => {
    const user = userEvent.setup();
    const onResume = chip(
      req({ kind: "note", mode: "append", note_id: "n1", anchor: { type: "note", id: "n1", label: null }, doc }),
    );
    await user.click(screen.getByRole("button", { name: "Edit" }));
    expect(screen.getByRole("textbox", { name: "Edit the note" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Back" }));
    await user.click(screen.getByRole("button", { name: "Edit" }));
    await user.click(screen.getByRole("button", { name: "Save and apply" }));
    expect(onResume).toHaveBeenCalledWith({
      decision: "edit",
      args: { doc: expect.objectContaining({ type: "doc" }) },
    });
  });

  it("no Edit when the server says not editable, or the protocol defines no edit for the kind", () => {
    chip(req({ kind: "memory", key: "k", fact: "x" }, false));
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
  });

  it("chapter edit: a block diff with before/after, target in words, no Edit", () => {
    chip(
      req({
        kind: "chapter_diff",
        section_id: "s1",
        heading: "Anion gap",
        target: "overlay_note",
        ops: [
          { op: "replace", index: 1, before: para("Old text"), after: para("New text") },
          { op: "delete", index: 3, before: para("Gone"), after: null },
        ],
      }),
    );
    expect(screen.getByRole("heading", { name: "Change this section like this?" })).toBeInTheDocument();
    expect(screen.getByText(/Saves your own version of “Anion gap”/)).toBeInTheDocument();
    const ops = screen.getAllByRole("listitem");
    expect(within(ops[0] as HTMLElement).getByText("Replace block 2")).toBeInTheDocument();
    expect(within(ops[0] as HTMLElement).getByText("Old text")).toBeInTheDocument();
    expect(within(ops[0] as HTMLElement).getByText("New text")).toBeInTheDocument();
    expect(within(ops[1] as HTMLElement).getByText("Remove block 4")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
  });

  it("block preview: the criteria in words", () => {
    chip(
      req({ kind: "block", count: 10, mode: "timed", systems: ["Renal"], disciplines: [], status: "incorrect" }, false),
    );
    expect(screen.getByText("Timed")).toBeInTheDocument();
    expect(screen.getByText("Answered wrong")).toBeInTheDocument();
    expect(screen.getByText("Any")).toBeInTheDocument();
  });

  it("knowledge preview (S15 add_to_knowledge): the link or file in words, what happens next, no Edit", async () => {
    const onResume = chip(
      req({ kind: "knowledge", source: "link", label: "https://example.org/renal", host: "example.org" }, false),
    );
    expect(screen.getByText("Add this to your knowledge base?")).toBeInTheDocument();
    expect(screen.getByText("Link")).toBeInTheDocument();
    expect(screen.getByText("https://example.org/renal")).toBeInTheDocument();
    expect(screen.getByText(/You choose where it goes in your Library/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
    await userEvent.setup().click(screen.getByRole("button", { name: "Accept" }));
    expect(onResume).toHaveBeenCalledWith(expect.objectContaining({ decision: "accept" }));
  });

  it("knowledge preview of an uploaded file; a malformed one is not a confirm", () => {
    chip(req({ kind: "knowledge", source: "file", label: "Acid-base lecture.pdf", host: null }, false));
    expect(screen.getByText("File")).toBeInTheDocument();
    expect(screen.getByText("Acid-base lecture.pdf")).toBeInTheDocument();
    expect(
      parseInterrupt({ type: "confirm", tool: "t", preview: { kind: "knowledge", source: "ftp" }, editable: false }),
    ).toBeNull();
  });
});
