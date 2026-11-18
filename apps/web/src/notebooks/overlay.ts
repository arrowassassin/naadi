/**
 * The optimistic view (pure): what the server last said, with this device's queued edits applied on top, so a
 * new page, a rename or a reorder shows at once and survives an offline reload. Once an op is confirmed it leaves
 * the queue and the refetched server data already contains it.
 */
import type { NotebookDetail, NotebookOut, NoteOut } from "@naadi/shared";
import type { QueuedOp } from "./outbox";
import { applyOrder, insertPage, orderPages, removePage } from "./pageOps";

function iso(ms: number): string {
  return new Date(ms).toISOString();
}

/** A page made on this device, shaped like the server's NoteOut until the server confirms it. */
export function localPage(
  id: string,
  notebookId: string,
  body: { title?: string | null; paper?: NoteOut["paper"]; canvas?: NoteOut["canvas"]; body?: NoteOut["body"] },
  at: number,
): NoteOut {
  return {
    id,
    kind: "page",
    title: body.title ?? null,
    notebook_id: notebookId,
    anchor_type: null,
    anchor_id: null,
    anchor_pos: null,
    highlight_color: null,
    body: body.body ?? null,
    body_omitted: false,
    body_text: "",
    excerpt: "",
    paper: body.paper ?? null,
    canvas: body.canvas ?? "paper",
    sort: 0,
    source: null,
    has_ink: false,
    created_at: iso(at),
    updated_at: iso(at),
  };
}

function patchPage(page: NoteOut, patch: Record<string, unknown>, at: number): NoteOut {
  const next = { ...page, updated_at: iso(at) } as NoteOut & Record<string, unknown>;
  for (const k of ["title", "paper", "canvas", "body"] as const) if (k in patch) next[k] = patch[k] as never;
  if ("body" in patch) next.body_omitted = false;
  return next;
}

/** The notebook list with queued creates, renames, cover/paper changes, deletes and page counts applied. */
export function overlayList(items: readonly NotebookOut[], queue: readonly QueuedOp[]): NotebookOut[] {
  let out = items.map((n) => ({ ...n }));
  for (const { op, queuedAt } of queue) {
    switch (op.kind) {
      case "createNotebook":
        if (!out.some((n) => n.id === op.id))
          out = [
            {
              id: op.id,
              title: op.body.title,
              cover: op.body.cover ?? null,
              paper: op.body.paper ?? "ruled",
              page_count: 1,
              note_count: 0,
              created_at: iso(queuedAt),
              updated_at: iso(queuedAt),
            },
            ...out,
          ];
        break;
      case "patchNotebook":
        out = out.map((n) =>
          n.id === op.id
            ? {
                ...n,
                ...(op.patch.title != null ? { title: op.patch.title } : {}),
                ...(op.patch.cover !== undefined ? { cover: op.patch.cover } : {}),
                ...(op.patch.paper != null ? { paper: op.patch.paper } : {}),
                updated_at: iso(queuedAt),
              }
            : n,
        );
        break;
      case "deleteNotebook":
        out = out.filter((n) => n.id !== op.id);
        break;
      case "createPage":
        out = out.map((n) => (n.id === op.notebookId ? { ...n, page_count: n.page_count + 1 } : n));
        break;
      case "deleteNote":
        out = out.map((n) => (n.id === op.notebookId ? { ...n, page_count: Math.max(0, n.page_count - 1) } : n));
        break;
      default:
        break;
    }
  }
  return out;
}

/**
 * One notebook with its pages, queued ops applied. `detail` may be undefined (never loaded, or made offline):
 * a queued create then provides it. Returns null when there is nothing to show (unknown or deleted here).
 */
export function overlayDetail(
  id: string,
  detail: NotebookDetail | undefined | null,
  queue: readonly QueuedOp[],
): NotebookDetail | null {
  let nb: NotebookDetail | null = detail ? { ...detail, pages: orderPages(detail.pages) } : null;
  for (const { op, queuedAt } of queue) {
    switch (op.kind) {
      case "createNotebook":
        if (op.id === id && !nb)
          nb = {
            id,
            title: op.body.title,
            cover: op.body.cover ?? null,
            paper: op.body.paper ?? "ruled",
            page_count: 1,
            note_count: 0,
            created_at: iso(queuedAt),
            updated_at: iso(queuedAt),
            pages: [localPage(op.body.first_page_id, id, {}, queuedAt)],
          };
        break;
      case "patchNotebook":
        if (op.id === id && nb)
          nb = {
            ...nb,
            ...(op.patch.title != null ? { title: op.patch.title } : {}),
            ...(op.patch.cover !== undefined ? { cover: op.patch.cover } : {}),
            ...(op.patch.paper != null ? { paper: op.patch.paper } : {}),
          };
        break;
      case "deleteNotebook":
        if (op.id === id) nb = null;
        break;
      case "createPage":
        if (op.notebookId === id && nb && !nb.pages.some((p) => p.id === op.id))
          nb = { ...nb, pages: insertPage(nb.pages, localPage(op.id, id, op.body, queuedAt), op.body.position) };
        break;
      case "patchNote":
        if (op.notebookId === id && nb) {
          nb = { ...nb, pages: nb.pages.map((p) => (p.id === op.id ? patchPage(p, op.patch, queuedAt) : p)) };
          if (op.patch.sort != null) {
            const ids = nb.pages.map((p) => p.id).filter((x) => x !== op.id);
            ids.splice(op.patch.sort, 0, op.id);
            nb = { ...nb, pages: applyOrder(nb.pages, ids) };
          }
        }
        break;
      case "deleteNote":
        if (op.notebookId === id && nb) nb = { ...nb, pages: removePage(nb.pages, op.id) };
        break;
      case "reorder":
        if (op.notebookId === id && nb) nb = { ...nb, pages: applyOrder(nb.pages, op.pageIds) };
        break;
    }
  }
  return nb ? { ...nb, page_count: nb.pages.length } : null;
}

/** A single note (a whiteboard page with its body) with queued patches applied. */
export function overlayNote(id: string, note: NoteOut | undefined | null, queue: readonly QueuedOp[]): NoteOut | null {
  let n = note ?? null;
  for (const { op, queuedAt } of queue) {
    if (op.kind === "createPage" && op.id === id && !n) n = localPage(id, op.notebookId, op.body, queuedAt);
    else if (op.kind === "patchNote" && op.id === id && n) n = patchPage(n, op.patch, queuedAt);
    else if (op.kind === "deleteNote" && op.id === id) n = null;
    else if (op.kind === "deleteNotebook" && n?.notebook_id === op.id) n = null;
  }
  return n;
}

/** How many queued ops touch this notebook (for "will sync"). */
export function pendingFor(id: string | null, queue: readonly QueuedOp[]): number {
  if (id === null) return queue.length;
  return queue.filter((q) => ("notebookId" in q.op ? q.op.notebookId : q.op.id) === id).length;
}
