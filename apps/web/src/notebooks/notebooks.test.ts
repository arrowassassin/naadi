import "fake-indexeddb/auto";
import { afterEach, describe, expect, it } from "vitest";
import {
  dexieOutbox,
  enqueue,
  flushOutbox,
  type NbOp,
  notebooksDb,
  type QueuedOp,
  resetNotebooksDbForTests,
} from "./outbox";
import {
  applyOrder,
  clampPageNumber,
  insertPage,
  moveItem,
  movePageBy,
  orderPages,
  pageAfterDelete,
  pagePaper,
  removePage,
} from "./pageOps";

const P = (id: string, sort: number) => ({ id, sort });

describe("page order", () => {
  it("orders by sort then id", () => {
    expect(orderPages([P("b", 1), P("c", 0), P("a", 1)]).map((p) => p.id)).toEqual(["c", "a", "b"]);
  });

  it("moves items with clamping", () => {
    expect(moveItem(["a", "b", "c", "d"], 0, 2)).toEqual(["b", "c", "a", "d"]);
    expect(moveItem(["a", "b", "c", "d"], 3, 0)).toEqual(["d", "a", "b", "c"]);
    expect(moveItem(["a", "b"], 1, 9)).toEqual(["a", "b"]);
    expect(moveItem([], 0, 1)).toEqual([]);
    expect(movePageBy(["a", "b", "c"], "b", -1)).toEqual(["b", "a", "c"]);
    expect(movePageBy(["a", "b", "c"], "c", 1)).toEqual(["a", "b", "c"]);
    expect(movePageBy(["a", "b", "c"], "x", 1)).toEqual(["a", "b", "c"]);
  });

  it("applies an id order and renumbers; unknown pages go last", () => {
    const out = applyOrder([P("a", 0), P("b", 1), P("c", 2), P("new", 3)], ["c", "a", "b"]);
    expect(out.map((p) => [p.id, p.sort])).toEqual([
      ["c", 0],
      ["a", 1],
      ["b", 2],
      ["new", 3],
    ]);
  });

  it("inserts and removes pages, renumbering", () => {
    const pages = [P("a", 0), P("b", 1)];
    expect(insertPage(pages, P("x", 0), 1).map((p) => p.id)).toEqual(["a", "x", "b"]);
    expect(insertPage(pages, P("x", 0)).map((p) => p.id)).toEqual(["a", "b", "x"]);
    expect(insertPage(pages, P("x", 0), 99).map((p) => p.id)).toEqual(["a", "b", "x"]);
    expect(removePage(pages, "a")).toEqual([{ id: "b", sort: 0 }]);
  });

  it("opens the next page after a delete, else the previous", () => {
    const pages = [P("a", 0), P("b", 1), P("c", 2)];
    expect(pageAfterDelete(pages, "a")).toBe(1);
    expect(pageAfterDelete(pages, "b")).toBe(2);
    expect(pageAfterDelete(pages, "c")).toBe(2);
    expect(pageAfterDelete([P("a", 0)], "a")).toBeNull();
  });

  it("clamps the route's page number", () => {
    expect(clampPageNumber("4", 23)).toBe(4);
    expect(clampPageNumber("99", 23)).toBe(23);
    expect(clampPageNumber("0", 3)).toBe(1);
    expect(clampPageNumber("abc", 3)).toBe(1);
    expect(clampPageNumber(2.7, 3)).toBe(2);
    expect(clampPageNumber("1", 0)).toBeNull();
  });

  it("resolves the paper: page override, notebook, ruled", () => {
    expect(pagePaper({ paper: "grid" }, "dotted")).toBe("grid");
    expect(pagePaper({ paper: null }, "dotted")).toBe("dotted");
    expect(pagePaper(null, "nope")).toBe("ruled");
  });
});

const NB = "0190a0a0-0000-7000-8000-000000000001";
const PG = "0190a0a0-0000-7000-8000-000000000002";
const PG2 = "0190a0a0-0000-7000-8000-000000000003";

function q(...ops: NbOp[]): QueuedOp[] {
  return ops.reduce<QueuedOp[]>((acc, op, i) => enqueue(acc, op, i + 1, new Set(), 0), []);
}
const kinds = (queue: QueuedOp[]) => queue.map((x) => x.op.kind);

describe("offline queue coalescing", () => {
  it("folds page patches into a queued create and keeps the rest as a patch", () => {
    const out = q(
      { kind: "createPage", id: PG, notebookId: NB, body: { id: PG, canvas: "paper" } },
      { kind: "patchNote", id: PG, notebookId: NB, patch: { paper: "grid" } },
      { kind: "patchNote", id: PG, notebookId: NB, patch: { canvas: "whiteboard", sort: 3 } },
    );
    expect(kinds(out)).toEqual(["createPage", "patchNote"]);
    expect(out[0]?.op).toMatchObject({ body: { id: PG, paper: "grid", canvas: "whiteboard" } });
    expect(out[1]?.op).toMatchObject({ patch: { sort: 3 } });
  });

  it("merges patches of the same note, later fields winning", () => {
    const out = q(
      { kind: "patchNote", id: PG, notebookId: NB, patch: { title: "a", paper: "grid" } },
      { kind: "patchNote", id: PG, notebookId: NB, patch: { title: "b" } },
    );
    expect(out).toHaveLength(1);
    expect(out[0]?.op).toMatchObject({ patch: { title: "b", paper: "grid" } });
  });

  it("never folds into a row that is being sent", () => {
    const first = q({ kind: "patchNote", id: PG, notebookId: NB, patch: { title: "a" } });
    const out = enqueue(first, { kind: "patchNote", id: PG, notebookId: NB, patch: { title: "b" } }, 2, new Set([1]));
    expect(out.map((x) => x.seq)).toEqual([1, 2]);
    expect(out[0]?.op).toMatchObject({ patch: { title: "a" } });
  });

  it("a delete of a page made offline cancels it entirely", () => {
    const out = q(
      { kind: "createPage", id: PG, notebookId: NB, body: { id: PG } },
      { kind: "patchNote", id: PG, notebookId: NB, patch: { paper: "grid" } },
      { kind: "reorder", notebookId: NB, pageIds: [PG2, PG] },
      { kind: "deleteNote", id: PG, notebookId: NB },
    );
    expect(kinds(out)).toEqual(["reorder"]);
    expect(out[0]?.op).toMatchObject({ pageIds: [PG2] });
  });

  it("a delete of a synced page drops its queued patches and is sent", () => {
    const out = q(
      { kind: "patchNote", id: PG, notebookId: NB, patch: { paper: "grid" } },
      { kind: "deleteNote", id: PG, notebookId: NB },
    );
    expect(kinds(out)).toEqual(["deleteNote"]);
  });

  it("a newer order replaces an older one and goes last", () => {
    const out = q(
      { kind: "reorder", notebookId: NB, pageIds: [PG, PG2] },
      { kind: "createPage", id: PG2, notebookId: NB, body: { id: PG2 } },
      { kind: "reorder", notebookId: NB, pageIds: [PG2, PG] },
    );
    expect(kinds(out)).toEqual(["createPage", "reorder"]);
    expect(out[1]?.op).toMatchObject({ pageIds: [PG2, PG] });
  });

  it("notebook: patches fold into the create; deleting an unsent notebook leaves nothing", () => {
    const create: NbOp = {
      kind: "createNotebook",
      id: NB,
      body: { id: NB, first_page_id: PG, title: "Renal", paper: "ruled", cover: "lime" },
    };
    const out = q(create, { kind: "patchNotebook", id: NB, patch: { title: "Renal 2", cover: "violet" } });
    expect(out).toHaveLength(1);
    expect(out[0]?.op).toMatchObject({ body: { title: "Renal 2", cover: "violet", paper: "ruled" } });
    expect(
      q(create, { kind: "createPage", id: PG2, notebookId: NB, body: { id: PG2 } }, { kind: "deleteNotebook", id: NB }),
    ).toEqual([]);
    expect(
      kinds(q({ kind: "patchNotebook", id: NB, patch: { title: "x" } }, { kind: "deleteNotebook", id: NB })),
    ).toEqual(["deleteNotebook"]);
  });
});

describe("offline queue on IndexedDB", () => {
  afterEach(async () => {
    await notebooksDb().delete();
    resetNotebooksDbForTests();
  });

  it("persists per user, replays in order and stops at the first unreachable op", async () => {
    const store = dexieOutbox("u1");
    const other = dexieOutbox("u2");
    const inflight = new Set<number>();
    await store.add({ kind: "createPage", id: PG, notebookId: NB, body: { id: PG } }, inflight);
    await store.add({ kind: "patchNote", id: PG2, notebookId: NB, patch: { paper: "dotted" } }, inflight);
    await other.add({ kind: "deleteNotebook", id: NB }, inflight);
    expect(kinds(await store.read())).toEqual(["createPage", "patchNote"]);
    expect(kinds(await other.read())).toEqual(["deleteNotebook"]);

    const sent: string[] = [];
    let first = await flushOutbox(store, inflight, async (op) => {
      sent.push(op.kind);
      return op.kind === "createPage" ? "sent" : "retry";
    });
    expect(first).toMatchObject({ stopped: true });
    expect(first.done.map((o) => o.kind)).toEqual(["createPage"]);
    expect(kinds(await store.read())).toEqual(["patchNote"]);

    first = await flushOutbox(store, inflight, async () => "dropped");
    expect(first).toMatchObject({ stopped: false });
    expect(first.dropped.map((o) => o.kind)).toEqual(["patchNote"]);
    expect(await store.read()).toEqual([]);
    expect(sent).toEqual(["createPage", "patchNote"]);
    expect(inflight.size).toBe(0);
  });

  it("a send that throws counts as unreachable and keeps the op", async () => {
    const store = dexieOutbox("u1");
    await store.add({ kind: "deleteNote", id: PG, notebookId: NB }, new Set());
    const r = await flushOutbox(store, new Set(), async () => {
      throw new Error("network");
    });
    expect(r.stopped).toBe(true);
    expect(kinds(await store.read())).toEqual(["deleteNote"]);
  });

  it("keeps device copies per user", async () => {
    await dexieOutbox("u1").writeSnapshot("list", { items: [1] });
    expect(await dexieOutbox("u1").readSnapshot("list")).toEqual({ items: [1] });
    expect(await dexieOutbox("u2").readSnapshot("list")).toBeUndefined();
  });
});

describe("ui_copy keys", () => {
  it("every notebooks/pdf/whiteboard key the code uses is required and shipped", async () => {
    const { readdirSync, readFileSync } = await import("node:fs");
    const path = await import("node:path");
    const { REQUIRED_UI_COPY_KEYS, UI_COPY_DEFAULTS } = await import("@naadi/shared");
    const { COVERS, coverKey } = await import("./covers");
    const { PAPERS } = await import("./paper");
    const { NOTEBOOK_ERROR_CODES } = await import("./api");
    const walk = (dir: string): string[] =>
      readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
        e.isDirectory()
          ? walk(path.join(dir, e.name))
          : /\.tsx?$/.test(e.name) && !e.name.includes(".test.")
            ? [path.join(dir, e.name)]
            : [],
      );
    const src = [...walk(import.meta.dirname), ...walk(path.resolve(import.meta.dirname, "../pdf"))]
      .map((f) => readFileSync(f, "utf8"))
      .join("\n");
    const literal = [...src.matchAll(/"((?:notebooks|pdf|whiteboard)\.[a-z0-9_.]+)"/g)].map((m) => m[1] as string);
    const dynamic = [
      ...COVERS.map(coverKey),
      ...PAPERS.map((p) => `notebooks.paper.${p}`),
      ...[...NOTEBOOK_ERROR_CODES].map((c) => `notebooks.error.${c}`),
    ];
    const required = new Set(REQUIRED_UI_COPY_KEYS);
    const missing = [...new Set([...literal, ...dynamic])].filter((k) => !required.has(k) || !UI_COPY_DEFAULTS[k]);
    expect(missing).toEqual([]);
  });
});
