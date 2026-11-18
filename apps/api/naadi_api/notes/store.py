"""Notes storage: anchor checks and reads as the user (RLS), writes on the API's privileged connection.

Clients have SELECT only on `notes` / `notebooks` (migration 0017), so every write here runs on the pool's own role
and filters on the caller's id explicitly; the composite foreign key keeps a note in its own user's notebook. Checks
that decide what the caller may anchor to (a section they can read, a question they have a graded attempt on, a
video, their own PDF) run first, as the user, so Postgres RLS decides and a hidden target is a plain 404.

Page order: a notebook's pages (`kind 'page'`) carry a dense `sort` 0..n-1. Every page operation locks the notebook
row first (`for update`), so concurrent inserts, moves, deletes and reorders of one notebook serialise.

Nothing here logs a title, a body or a quote.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from .models import (
    MAX_NOTEBOOKS_PER_USER,
    MAX_NOTES_PER_USER,
    MAX_PAGES_PER_NOTEBOOK,
    AnyPos,
    parse_pos,
)
from .text import excerpt

Row = dict[str, Any]


class NotesError(Exception):
    """An error with a stable code the client words (ui_copy `notes.error.<code>`)."""

    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


def not_found() -> NotesError:
    return NotesError(404, "note_not_found")


def notebook_not_found() -> NotesError:
    return NotesError(404, "notebook_not_found")


async def _rows(conn: psycopg.AsyncConnection[Any], query: str, params: Mapping[str, Any] | Sequence[Any]) -> list[Row]:
    cur = await conn.execute(query, params)
    return [dict(r) for r in await cur.fetchall()]


async def _one(conn: psycopg.AsyncConnection[Any], query: str, params: Mapping[str, Any] | Sequence[Any]) -> Row | None:
    rows = await _rows(conn, query, params)
    return rows[0] if rows else None


def _uuid_text(raw: str) -> str | None:
    try:
        u = uuid.UUID(raw)
    except ValueError:
        return None
    return str(u) if str(u) == raw else None


# ── Anchor checks (as the user, under RLS) ──


async def check_anchor(conn: psycopg.AsyncConnection[Any], anchor_type: str | None, anchor_id: str | None) -> None:
    """Raise NotesError unless the caller may anchor a note here. `conn` runs as the user (auth_db.user_tx)."""
    if anchor_type in (None, "free"):
        return
    assert anchor_id is not None
    if anchor_type == "section":
        row = await _one(conn, "select 1 as ok from public.chapter_sections where id = %s", (anchor_id,))
    elif anchor_type == "question":
        # questions-scope: rls (conn runs as the user)
        row = await _one(conn, "select 1 as ok from public.questions where id = %s", (anchor_id,))
        if row is not None:
            # The flashcard gate of migration 0010: a graded answer of the caller's own (a flag-only row is not).
            gate = await _one(conn, "select public.qbank_has_graded_attempt(%s) as ok", (anchor_id,))
            if not (gate and gate["ok"]):
                raise NotesError(403, "note_question_not_attempted")
    elif anchor_type == "video":
        row = await _one(conn, "select 1 as ok from public.media_assets where id = %s", (anchor_id,))
    elif anchor_type == "pdf":
        upload = _uuid_text(anchor_id)
        row = (
            await _one(conn, "select 1 as ok from public.uploads where id = %s and kind = 'pdf'", (upload,))
            if upload
            else None
        )
    else:
        row = None
    if row is None:
        raise NotesError(404, "note_anchor_not_found")


# ── Reads ──

NOTE_COLS = """
  n.id, n.kind, n.title, n.notebook_id, n.anchor_type, n.anchor_id, n.anchor_pos, n.highlight_color,
  n.body_text, n.paper, n.canvas, n.sort, n.created_at, n.updated_at, n.client_updated_at_ms,
  exists (select 1 from public.ink_layers l where l.user_id = n.user_id and l.surface_type = 'notebook_page'
          and l.surface_id = n.id::text and l.item_count > 0) as has_ink
"""
# Lists leave whiteboard scenes out (they can be large); GET /api/notes/{id} returns them.
LIST_BODY = (
    "case when n.canvas = 'whiteboard' then null else n.body end as body, n.canvas = 'whiteboard' as body_omitted"
)
FULL_BODY = "n.body, false as body_omitted"
NO_BODY = "null::jsonb as body, true as body_omitted"


@dataclass(frozen=True)
class NoteFilter:
    kind: str | None = None
    color: str | None = None
    anchor_type: str | None = None
    anchor_id: str | None = None
    notebook_id: uuid.UUID | None = None
    q: str | None = None


def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def filter_sql(f: NoteFilter, params: dict[str, Any]) -> str:
    """`and ...` clauses for a NoteFilter (fixed fragments; every value is a bind parameter)."""
    parts: list[str] = []
    if f.kind is not None:
        parts.append("n.kind = %(kind)s")
        params["kind"] = f.kind
    if f.color is not None:
        parts.append("n.highlight_color = %(color)s")
        params["color"] = f.color
    if f.anchor_type is not None:
        parts.append("coalesce(n.anchor_type, 'free') = %(anchor_type)s")
        params["anchor_type"] = f.anchor_type
    if f.anchor_id is not None:
        parts.append("n.anchor_id = %(anchor_id)s")
        params["anchor_id"] = f.anchor_id
    if f.notebook_id is not None:
        parts.append("n.notebook_id = %(notebook_id)s")
        params["notebook_id"] = f.notebook_id
    if f.q:
        parts.append("(n.title ilike %(q)s or n.body_text ilike %(q)s or (n.anchor_pos ->> 'quote') ilike %(q)s)")
        params["q"] = _like(f.q)
    return "".join(f" and {p}" for p in parts)


async def list_notes(
    conn: psycopg.AsyncConnection[Any],
    uid: uuid.UUID,
    f: NoteFilter,
    *,
    limit: int,
    offset: int,
    include_body: bool,
) -> tuple[list[Row], bool]:
    params: dict[str, Any] = {"uid": uid, "limit": limit + 1, "offset": offset}
    where = filter_sql(f, params)
    # A notebook's pages in page order; everything else newest first.
    order = "n.sort, n.id" if f.kind == "page" and f.notebook_id is not None else "n.updated_at desc, n.id"
    body = LIST_BODY if include_body else NO_BODY
    rows = await _rows(
        conn,
        f"select {NOTE_COLS}, {body} from public.notes n where n.user_id = %(uid)s{where}"  # noqa: S608 - fixed fragments
        f" order by {order} limit %(limit)s offset %(offset)s",
        params,
    )
    more = len(rows) > limit
    return rows[:limit], more


async def note_counts(conn: psycopg.AsyncConnection[Any], uid: uuid.UUID) -> Row:
    row = await _one(
        conn,
        "select count(*) filter (where kind = 'note')::int as notes,"
        " count(*) filter (where kind = 'highlight')::int as highlights,"
        " count(*) filter (where kind = 'page')::int as pages,"
        " count(*) filter (where kind = 'highlight' and highlight_color = 'lime')::int as lime,"
        " count(*) filter (where kind = 'highlight' and highlight_color = 'violet')::int as violet,"
        " count(*) filter (where kind = 'highlight' and highlight_color = 'amber')::int as amber,"
        " count(*) filter (where kind <> 'page' and anchor_type = 'section')::int as a_section,"
        " count(*) filter (where kind <> 'page' and anchor_type = 'question')::int as a_question,"
        " count(*) filter (where kind <> 'page' and anchor_type = 'video')::int as a_video,"
        " count(*) filter (where kind <> 'page' and anchor_type = 'pdf')::int as a_pdf,"
        " count(*) filter (where kind <> 'page' and coalesce(anchor_type, 'free') = 'free')::int as a_free"
        " from public.notes where user_id = %s",
        (uid,),
    )
    assert row is not None
    return row


async def get_note(
    conn: psycopg.AsyncConnection[Any], uid: uuid.UUID, note_id: uuid.UUID, *, body: str = FULL_BODY
) -> Row | None:
    return await _one(
        conn,
        f"select {NOTE_COLS}, {body} from public.notes n where n.user_id = %s and n.id = %s",  # noqa: S608
        (uid, note_id),
    )


async def load_sources(conn: psycopg.AsyncConnection[Any], rows: Iterable[Row]) -> dict[tuple[str, str], Row]:
    """Chip data for each anchored row, read as the user (RLS): a target the caller can no longer see is absent."""
    want: dict[str, set[str]] = {"section": set(), "question": set(), "video": set(), "pdf": set()}
    for r in rows:
        if r["anchor_type"] in want and r["anchor_id"]:
            want[r["anchor_type"]].add(r["anchor_id"])
    out: dict[tuple[str, str], Row] = {}
    if want["section"]:
        for s in await _rows(
            conn,
            "select s.id, s.heading, s.chapter_id, c.title as chapter_title,"
            " md5(coalesce(s.body::text, s.body_html, '')) as rev"
            " from public.chapter_sections s join public.chapters c on c.id = s.chapter_id where s.id = any(%s)",
            (sorted(want["section"]),),
        ):
            out[("section", s["id"])] = {
                "type": "section",
                "chapter_id": s["chapter_id"],
                "chapter_title": s["chapter_title"],
                "section_id": s["id"],
                "section_heading": s["heading"],
                "section_revision": s["rev"],
            }
    if want["question"]:
        qids = sorted(want["question"])
        latest = {
            a["question_id"]: a
            for a in await _rows(
                conn,
                "select distinct on (a.question_id) a.question_id, a.block_id,"
                " array_position(b.question_ids, a.question_id) as n"
                " from public.attempts a left join public.blocks b on b.id = a.block_id"
                " where a.user_id = (select auth.uid()) and a.question_id = any(%s) and a.chosen is not null"
                " order by a.question_id, a.created_at desc",
                (qids,),
            )
        }
        # questions-scope: rls (conn runs as the user)
        for q in await _rows(conn, "select q.id, q.chapter_id from public.questions q where q.id = any(%s)", (qids,)):
            a = latest.get(q["id"]) or {}
            out[("question", q["id"])] = {
                "type": "question",
                "chapter_id": q["chapter_id"],
                "question_id": q["id"],
                "block_id": str(a["block_id"]) if a.get("block_id") else None,
                "question_number": a.get("n"),
            }
    if want["video"]:
        for m in await _rows(
            conn, "select id, title from public.media_assets where id = any(%s)", (sorted(want["video"]),)
        ):
            out[("video", m["id"])] = {"type": "video", "media_id": m["id"], "title": m["title"]}
    if want["pdf"]:
        ids = [u for u in (_uuid_text(x) for x in want["pdf"]) if u]
        if ids:
            for u in await _rows(
                conn,
                "select id::text as id, original_name from public.uploads where id = any(%s::uuid[]) and kind = 'pdf'",
                (ids,),
            ):
                out[("pdf", u["id"])] = {"type": "pdf", "upload_id": u["id"], "title": u["original_name"]}
    return out


def shape_note(row: Row, sources: Mapping[tuple[str, str], Row]) -> dict[str, Any]:
    src = sources.get((row["anchor_type"], row["anchor_id"])) if row["anchor_id"] else None
    return {
        "id": row["id"],
        "kind": row["kind"],
        "title": row["title"],
        "notebook_id": row["notebook_id"],
        "anchor_type": row["anchor_type"],
        "anchor_id": row["anchor_id"],
        "anchor_pos": parse_pos(row["anchor_pos"]),
        "highlight_color": row["highlight_color"],
        "body": row["body"],
        "body_omitted": bool(row["body_omitted"]) and row["body"] is None,
        "body_text": row["body_text"] or "",
        "excerpt": excerpt(row["body_text"]),
        "paper": row["paper"],
        "canvas": row["canvas"],
        "sort": row["sort"],
        "source": src,
        "has_ink": bool(row["has_ink"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "client_updated_at_ms": row["client_updated_at_ms"],
    }


# ── Writes (privileged connection; every statement filters on the caller) ──


def pos_json(pos: AnyPos | None) -> Jsonb | None:
    return Jsonb(pos.model_dump(by_alias=True, exclude_none=True)) if pos is not None else None


async def _note_count(conn: psycopg.AsyncConnection[Any], uid: uuid.UUID) -> int:
    row = await _one(conn, "select count(*)::int as n from public.notes where user_id = %s", (uid,))
    return int(row["n"]) if row else 0


async def lock_user(conn: psycopg.AsyncConnection[Any], uid: uuid.UUID) -> None:
    """Serialise one student's creates (the per-user limits are counted, not constrained)."""
    await conn.execute("select pg_advisory_xact_lock(hashtextextended('naadi.notes:' || %s::text, 0))", (uid,))


@dataclass(frozen=True)
class NewNote:
    id: uuid.UUID
    kind: str
    title: str | None
    notebook_id: uuid.UUID | None
    anchor_type: str | None
    anchor_id: str | None
    anchor_pos: AnyPos | None
    highlight_color: str | None
    body: dict[str, Any] | None
    body_text: str
    paper: str | None
    canvas: str
    sort: int
    client_updated_at_ms: int | None = None


async def existing_for_create(conn: psycopg.AsyncConnection[Any], uid: uuid.UUID, note: NewNote) -> Row | None:
    """The stored note a replayed create should return: the same id, or the section's one page note."""
    row = await _one(conn, "select n.id, n.user_id = %s as mine from public.notes n where n.id = %s", (uid, note.id))
    if row is not None:
        if not row["mine"]:
            raise NotesError(409, "note_id_conflict")
        return row
    if note.kind == "note" and note.anchor_type == "section":
        return await _one(
            conn,
            "select n.id, true as mine from public.notes n where n.user_id = %s and n.kind = 'note'"
            " and n.anchor_type = 'section' and n.anchor_id = %s",
            (uid, note.anchor_id),
        )
    return None


async def insert_note(conn: psycopg.AsyncConnection[Any], uid: uuid.UUID, note: NewNote) -> bool:
    """Insert unless a replay (same id / the section's note) exists. True when a row was written."""
    await lock_user(conn, uid)
    if await existing_for_create(conn, uid, note) is not None:
        return False
    if await _note_count(conn, uid) >= MAX_NOTES_PER_USER:
        raise NotesError(409, "note_limit")
    try:
        cur = await conn.execute(
            "insert into public.notes (id, user_id, kind, title, notebook_id, anchor_type, anchor_id, anchor_pos,"
            " highlight_color, body, body_text, paper, canvas, sort, client_updated_at_ms)"
            " values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            " on conflict do nothing",
            (
                note.id,
                uid,
                note.kind,
                note.title,
                note.notebook_id,
                note.anchor_type,
                note.anchor_id,
                pos_json(note.anchor_pos),
                note.highlight_color,
                Jsonb(note.body) if note.body is not None else None,
                note.body_text,
                note.paper,
                note.canvas,
                note.sort,
                note.client_updated_at_ms,
            ),
        )
    except psycopg.errors.ForeignKeyViolation as exc:  # a notebook that is not the caller's
        raise notebook_not_found() from exc
    return cur.rowcount == 1


async def resolve_replay(conn: psycopg.AsyncConnection[Any], uid: uuid.UUID, note: NewNote) -> uuid.UUID:
    row = await existing_for_create(conn, uid, note)
    if row is None:
        raise NotesError(409, "note_id_conflict")
    return uuid.UUID(str(row["id"]))


async def lock_note(conn: psycopg.AsyncConnection[Any], uid: uuid.UUID, note_id: uuid.UUID) -> Row:
    row = await _one(
        conn,
        "select id, kind, title, notebook_id, anchor_type, anchor_id, anchor_pos, highlight_color, body, paper,"
        " canvas, sort, updated_at, client_updated_at_ms from public.notes where user_id = %s and id = %s for update",
        (uid, note_id),
    )
    if row is None:
        raise not_found()
    return row


async def update_note(
    conn: psycopg.AsyncConnection[Any], uid: uuid.UUID, note_id: uuid.UUID, values: Mapping[str, Any]
) -> None:
    """Set `values` (column -> value; column names are module constants of the caller). With a
    `client_updated_at_ms` among them the statement is guarded (last writer wins: only an older stored stamp is
    overwritten); the caller holds the row lock and has already decided, so the guard is a second line."""
    if not values:
        await conn.execute("update public.notes set updated_at = now() where user_id = %s and id = %s", (uid, note_id))
        return
    cols = ", ".join(f"{c} = %({c})s" for c in values)
    params: dict[str, Any] = {}
    for c, v in values.items():
        if c == "anchor_pos":
            params[c] = pos_json(v)
        elif c == "body":
            params[c] = Jsonb(v) if v is not None else None
        else:
            params[c] = v
    guard = (
        " and (client_updated_at_ms is null or client_updated_at_ms < %(client_updated_at_ms)s)"
        if "client_updated_at_ms" in values
        else ""
    )
    try:
        await conn.execute(
            f"update public.notes set {cols} where user_id = %(uid)s and id = %(id)s{guard}",  # noqa: S608 - fixed columns
            {**params, "uid": uid, "id": note_id},
        )
    except psycopg.errors.ForeignKeyViolation as exc:
        raise notebook_not_found() from exc
    except psycopg.errors.UniqueViolation as exc:  # re-anchoring a note onto a section that already has one
        raise NotesError(409, "note_section_has_note") from exc


async def delete_embeddings(conn: psycopg.AsyncConnection[Any], uid: uuid.UUID, note_ids: Sequence[str]) -> None:
    """Index rows of these notes and of their notebook-page ink (S07 `ref_type` note / ink)."""
    if not note_ids:
        return
    await conn.execute(
        "delete from public.embeddings where owner_user_id = %s and ("
        " (ref_type = 'note' and ref_id = any(%s))"
        " or (ref_type = 'ink' and ref_id = any(%s)))",
        (uid, list(note_ids), [f"notebook_page:{i}" for i in note_ids]),
    )


async def delete_page_ink(conn: psycopg.AsyncConnection[Any], uid: uuid.UUID, note_ids: Sequence[str]) -> None:
    """A page's ink layer (items cascade): without its page nothing can show or sync it."""
    if note_ids:
        await conn.execute(
            "delete from public.ink_layers where user_id = %s and surface_type = 'notebook_page'"
            " and surface_id = any(%s)",
            (uid, list(note_ids)),
        )


async def delete_note(conn: psycopg.AsyncConnection[Any], uid: uuid.UUID, note_id: uuid.UUID) -> Row:
    sql = "select id, kind, notebook_id, sort from public.notes where user_id = %s and id = %s"
    row = await _one(conn, sql, (uid, note_id))
    if row is None:
        raise not_found()
    if row["kind"] == "page" and row["notebook_id"] is not None:
        await lock_notebook(conn, uid, row["notebook_id"])
        row = await _one(conn, sql, (uid, note_id))  # its sort, now that the notebook's pages cannot move
        if row is None:
            raise not_found()
    await conn.execute("delete from public.notes where user_id = %s and id = %s", (uid, note_id))
    if row["kind"] == "page" and row["notebook_id"] is not None:
        await conn.execute(
            "update public.notes set sort = sort - 1 where user_id = %s and notebook_id = %s and kind = 'page'"
            " and sort > %s",
            (uid, row["notebook_id"], row["sort"]),
        )
    ids = [str(note_id)]
    await delete_page_ink(conn, uid, ids)
    await delete_embeddings(conn, uid, ids)
    return row


# ── Notebooks ──

NOTEBOOK_COLS = """
  b.id, b.title, b.cover, b.paper, b.created_at, b.updated_at, b.client_updated_at_ms,
  (select count(*) from public.notes n where n.notebook_id = b.id and n.user_id = b.user_id and n.kind = 'page')::int
    as page_count,
  (select count(*) from public.notes n where n.notebook_id = b.id and n.user_id = b.user_id and n.kind <> 'page')::int
    as note_count
"""


async def list_notebooks(conn: psycopg.AsyncConnection[Any], uid: uuid.UUID) -> list[Row]:
    return await _rows(
        conn,
        f"select {NOTEBOOK_COLS} from public.notebooks b where b.user_id = %s order by b.updated_at desc, b.id",  # noqa: S608
        (uid,),
    )


async def get_notebook(conn: psycopg.AsyncConnection[Any], uid: uuid.UUID, notebook_id: uuid.UUID) -> Row | None:
    return await _one(
        conn,
        f"select {NOTEBOOK_COLS} from public.notebooks b where b.user_id = %s and b.id = %s",  # noqa: S608
        (uid, notebook_id),
    )


async def lock_notebook(conn: psycopg.AsyncConnection[Any], uid: uuid.UUID, notebook_id: uuid.UUID) -> Row:
    row = await _one(
        conn,
        # NO KEY UPDATE: a concurrent insert of a note filed in this notebook (its foreign key check takes
        # KEY SHARE) does not wait on, or deadlock with, a page operation.
        "select id, paper from public.notebooks where user_id = %s and id = %s for no key update",
        (uid, notebook_id),
    )
    if row is None:
        raise notebook_not_found()
    return row


async def insert_notebook(
    conn: psycopg.AsyncConnection[Any],
    uid: uuid.UUID,
    notebook_id: uuid.UUID,
    title: str,
    cover: str | None,
    paper: str,
    stamp_ms: int | None = None,
) -> bool:
    await lock_user(conn, uid)
    row = await _one(conn, "select user_id = %s as mine from public.notebooks where id = %s", (uid, notebook_id))
    if row is not None:
        if not row["mine"]:
            raise NotesError(409, "notebook_id_conflict")
        return False
    n = await _one(conn, "select count(*)::int as n from public.notebooks where user_id = %s", (uid,))
    if n and n["n"] >= MAX_NOTEBOOKS_PER_USER:
        raise NotesError(409, "notebook_limit")
    await conn.execute(
        "insert into public.notebooks (id, user_id, title, cover, paper, client_updated_at_ms)"
        " values (%s, %s, %s, %s, %s, %s)",
        (notebook_id, uid, title, cover, paper, stamp_ms),
    )
    return True


async def page_ids(conn: psycopg.AsyncConnection[Any], uid: uuid.UUID, notebook_id: uuid.UUID) -> list[str]:
    rows = await _rows(
        conn,
        "select id::text as id from public.notes where user_id = %s and notebook_id = %s and kind = 'page'"
        " order by sort, id",
        (uid, notebook_id),
    )
    return [r["id"] for r in rows]


async def open_slot(
    conn: psycopg.AsyncConnection[Any], uid: uuid.UUID, notebook_id: uuid.UUID, position: int | None
) -> int:
    """Make room for a page at `position` (clamped; default the end) in a locked notebook; returns its sort."""
    count = len(await page_ids(conn, uid, notebook_id))
    if count >= MAX_PAGES_PER_NOTEBOOK:
        raise NotesError(409, "notebook_page_limit")
    pos = count if position is None else max(0, min(position, count))
    await conn.execute(
        "update public.notes set sort = sort + 1 where user_id = %s and notebook_id = %s and kind = 'page'"
        " and sort >= %s",
        (uid, notebook_id, pos),
    )
    return pos


async def renumber(conn: psycopg.AsyncConnection[Any], uid: uuid.UUID, notebook_id: uuid.UUID, ids: list[str]) -> None:
    await conn.execute(
        "update public.notes n set sort = o.i - 1 from unnest(%s::uuid[]) with ordinality as o(id, i)"
        " where n.id = o.id and n.user_id = %s and n.notebook_id = %s and n.kind = 'page' and n.sort <> o.i - 1",
        (ids, uid, notebook_id),
    )


async def move_page(
    conn: psycopg.AsyncConnection[Any], uid: uuid.UUID, notebook_id: uuid.UUID, page_id: uuid.UUID, position: int
) -> None:
    ids = await page_ids(conn, uid, notebook_id)
    pid = str(page_id)
    if pid not in ids:
        raise not_found()
    ids.remove(pid)
    ids.insert(max(0, min(position, len(ids))), pid)
    await renumber(conn, uid, notebook_id, ids)


async def delete_notebook(conn: psycopg.AsyncConnection[Any], uid: uuid.UUID, notebook_id: uuid.UUID) -> list[str]:
    """Delete a notebook with its pages (their ink and index rows); typed notes filed in it stay, unfiled."""
    await lock_notebook(conn, uid, notebook_id)
    pages = await page_ids(conn, uid, notebook_id)
    await conn.execute(
        "delete from public.notes where user_id = %s and notebook_id = %s and kind = 'page'", (uid, notebook_id)
    )
    await conn.execute(
        "update public.notes set notebook_id = null where user_id = %s and notebook_id = %s", (uid, notebook_id)
    )
    await conn.execute("delete from public.notebooks where user_id = %s and id = %s", (uid, notebook_id))
    await delete_page_ink(conn, uid, pages)
    await delete_embeddings(conn, uid, pages)
    return pages


def body_bytes(body: Mapping[str, Any] | None) -> int:
    return len(json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode()) if body is not None else 0


__all__ = [
    "FULL_BODY",
    "LIST_BODY",
    "NewNote",
    "NoteFilter",
    "NotesError",
    "check_anchor",
    "delete_note",
    "delete_notebook",
    "get_note",
    "get_notebook",
    "insert_note",
    "insert_notebook",
    "list_notebooks",
    "list_notes",
    "load_sources",
    "lock_note",
    "lock_notebook",
    "move_page",
    "note_counts",
    "open_slot",
    "page_ids",
    "renumber",
    "shape_note",
    "update_note",
]
