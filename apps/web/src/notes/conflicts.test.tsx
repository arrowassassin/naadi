import "fake-indexeddb/auto";
import type { NoteOut } from "@naadi/shared";
import { QueryClient } from "@tanstack/react-query";
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from "@tanstack/react-router";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  ConflictNotices,
  conflictCopyTitle,
  conflictNotices,
  loadConflictNotices,
  noteConflictHandler,
  resetConflictsForTests,
} from "./conflicts";
import { editStamp, resetEditClockForTests } from "./editClock";
import { type NoteOp, pendingOps, queueUpdate, resetNotesDbForTests } from "./outbox";

vi.mock("../auth/session", () => ({
  useAuth: () => ({ status: "signed-in", session: { user: { id: "u1" } }, config: { ui_copy: null } }),
}));

const U = "u1";
const t = (k: string, v?: Readonly<Record<string, string>>) => (v ? `${k}${JSON.stringify(v)}` : k);

function server(over: Partial<NoteOut> = {}): NoteOut {
  return {
    id: "n1",
    kind: "note",
    title: null,
    notebook_id: null,
    anchor_type: "section",
    anchor_id: "ch07-s3",
    anchor_pos: null,
    highlight_color: null,
    body: { type: "doc", content: [{ type: "paragraph", content: [{ type: "text", text: "B text" }] }] },
    body_omitted: false,
    body_text: "B text",
    excerpt: "B text",
    paper: null,
    canvas: "paper",
    sort: 0,
    source: { type: "section", section_heading: "Three leaks" },
    has_ink: false,
    created_at: "2026-11-17T09:00:00Z",
    updated_at: "2026-11-17T11:00:00Z",
    edited_at: "2026-11-17T11:00:00Z",
    ...over,
  } as NoteOut;
}

function local(fields: Record<string, unknown>, over: Partial<NoteOp> = {}): NoteOp {
  return {
    user: U,
    id: "n1",
    v: 2,
    create: false,
    attempted: true,
    deleted: false,
    fields,
    queuedAt: 1,
    refused: null,
    ...over,
  };
}

const MINE = { type: "doc", content: [{ type: "paragraph", content: [{ type: "text", text: "A text" }] }] };

beforeEach(async () => {
  resetNotesDbForTests();
  resetConflictsForTests();
  resetEditClockForTests();
  await new Promise<void>((r) => {
    const req = indexedDB.deleteDatabase("naadi-notes");
    req.onsuccess = () => r();
    req.onerror = () => r();
  });
});

describe("note conflicts", () => {
  it("keeps a page note's version as a free conflict copy and adopts the server copy", async () => {
    const qc = new QueryClient();
    qc.setQueryData(["notes", U, "note", null, "section", "ch07-s3", null, null, 1], {
      data: { items: [server({ body_text: "old" })] },
      fromCache: false,
    });
    await noteConflictHandler(U, qc, t)(local({ body: MINE }), server());
    const [copy] = pendingOps(U);
    expect(copy).toMatchObject({
      create: true,
      fields: {
        kind: "note",
        anchor_type: "free",
        body: MINE,
        title: 'notes.conflict_copy_title{"title":"Three leaks"}',
      },
    });
    expect(copy?.fields).not.toHaveProperty("anchor_id");
    const cached = qc.getQueryData<{ data: { items: NoteOut[] } }>([
      "notes",
      U,
      "note",
      null,
      "section",
      "ch07-s3",
      null,
      null,
      1,
    ]);
    expect(cached?.data.items[0]?.body_text).toBe("B text");
    expect(conflictNotices(U)).toMatchObject([
      { kind: "note_copy", noteId: "n1", copyId: copy?.id, anchorId: "ch07-s3" },
    ]);
    // The next edit of that note is stamped no earlier than the adopted copy.
    expect(editStamp("n1")).toBeGreaterThanOrEqual(Date.parse("2026-11-17T11:00:00Z"));
  });

  it("highlight conflicts only adopt; a stale delete keeps the note with a notice", async () => {
    const qc = new QueryClient();
    const handle = noteConflictHandler(U, qc, t);
    await handle(local({ highlight_color: "amber" }), server({ kind: "highlight", highlight_color: "lime" }));
    await handle(local({}, { deleted: true }), server({ kind: "highlight" }));
    expect(pendingOps(U)).toEqual([]);
    expect(conflictNotices(U)).toEqual([]);
    await handle(local({}, { deleted: true }), server({ title: "Renal" }));
    await handle(local({ notebook_id: "nb1" }), server());
    expect(
      conflictNotices(U)
        .map((n) => n.kind)
        .sort(),
    ).toEqual(["note_delete_kept", "note_kept"]);
    expect(pendingOps(U)).toEqual([]);
  });

  it("notices survive a reload until dismissed", async () => {
    const qc = new QueryClient();
    await noteConflictHandler(
      U,
      qc,
      t,
    )(local({ title: "Mine", body: MINE }), server({ anchor_type: "free", anchor_id: null }));
    const copyId = pendingOps(U)[0]?.id;
    resetConflictsForTests(); // a reload: memory is gone, IndexedDB is not
    expect(conflictNotices(U)).toEqual([]);
    await loadConflictNotices(U);
    expect(conflictNotices(U)).toHaveLength(1);

    const root = createRootRoute({ component: () => <ConflictNotices filter={{ scope: "notes" }} /> });
    const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: ["/"] }) });
    render(<RouterProvider router={router} />);
    const notice = await screen.findByRole("status");
    // Worded with the shipped ui_copy defaults (config/app_config.yaml).
    expect(notice.textContent).toContain("Changed on another device");
    expect(notice.textContent).toContain("“Three leaks”"); // an untitled page note is named by its section
    expect(notice.textContent).toContain("Open your copy");
    const link = notice.querySelector("a");
    expect(link?.getAttribute("href")).toBe(`/notes?note=${copyId}`);
    expect(notice.querySelector("svg")?.getAttribute("aria-hidden")).toBe("true");
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Dismiss this message" }));
    });
    await waitFor(() => expect(screen.queryByRole("status")).toBeNull());
    resetConflictsForTests();
    await loadConflictNotices(U);
    expect(conflictNotices(U)).toEqual([]);
  });

  it("words the copy's title within the API's 200 characters", () => {
    expect(conflictCopyTitle(t, null)).toBe("notes.conflict_copy_title_untitled");
    expect(conflictCopyTitle(t, "  Renal  ")).toBe('notes.conflict_copy_title{"title":"Renal"}');
    expect(conflictCopyTitle(t, "x".repeat(400)).length).toBe(200);
  });

  it("a conflict copy is queued like any write (stamped, sent later)", async () => {
    await queueUpdate(U, "other", { title: "x" });
    await noteConflictHandler(U, new QueryClient(), t)(local({ body: MINE }), server());
    expect(pendingOps(U).map((o) => [o.create, typeof o.editedAt])).toEqual([
      [false, "number"],
      [true, "number"],
    ]);
  });
});

describe("conflict ui_copy", () => {
  it("every key a notice or a copy title can use is required and shipped", async () => {
    const { REQUIRED_UI_COPY_KEYS, UI_COPY_DEFAULTS } = await import("@naadi/shared");
    const kinds = {
      notes: ["note_copy", "note_kept", "note_delete_kept"],
      notebooks: ["page_copy", "page_kept", "page_delete_kept", "notebook_kept", "notebook_delete_kept"],
    };
    const keys = Object.entries(kinds).flatMap(([scope, ks]) => [
      ...ks.map((k) => `${scope}.conflict_${k}`),
      ...["label", "dismiss", "untitled", "open_copy"].map((k) => `${scope}.conflict_${k}`),
    ]);
    keys.push(
      "notes.conflict_open_note",
      "notes.conflict_copy_title",
      "notes.conflict_copy_title_untitled",
      "notebooks.conflict_open_notebook",
      "notebooks.conflict_page_title",
    );
    const required = new Set(REQUIRED_UI_COPY_KEYS);
    expect(keys.filter((k) => !required.has(k) || !UI_COPY_DEFAULTS[k])).toEqual([]);
  });
});
