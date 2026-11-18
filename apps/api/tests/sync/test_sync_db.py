"""Integration (real Postgres, migrated schema, full HTTP path): offline sync.

RLS on `sync_tombstones` / `sync_ops`; last writer wins on notes and notebooks through the real endpoints (older,
tie, newer, clamp, other users' keys, tombstone versus edit in both orders); Idempotency-Key replays return the
stored response without writing twice; the daily clean-up.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import errors
from psycopg.types.json import Jsonb
from pydantic import SecretStr

from naadi_api.db import Database
from naadi_api.sync.gc import run_gc
from naadi_api.sync.lww import FUTURE_SKEW_MS
from tests.auth.jwt_helpers import SigningKey
from tests.db.rls import as_anon, as_user

from .conftest import Ink

pytestmark = [pytest.mark.integration, pytest.mark.db]


def doc(text: str) -> dict[str, Any]:
    return {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}]}


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def text_of(note: dict[str, Any]) -> str:
    return str(note["body_text"])


# ── RLS ──


def test_tombstones_are_select_only_for_their_owner(pg: psycopg.Connection[Any], ink: Ink) -> None:
    pg.execute(
        "insert into public.sync_tombstones (user_id, entity, key, client_updated_at_ms) values (%s, 'note', %s, 1)",
        (ink.owner, str(uuid.uuid4())),
    )
    q = "select count(*) from public.sync_tombstones where user_id = %s"
    with as_user(pg, ink.owner) as conn:
        assert conn.execute(q, (ink.owner,)).fetchone() == (1,)
        for stmt in (
            "update public.sync_tombstones set client_updated_at_ms = 2 where user_id = %s",
            "delete from public.sync_tombstones where user_id = %s",
            "insert into public.sync_tombstones (user_id, entity, key, client_updated_at_ms)"
            " values (%s, 'note', 'k', 1)",
        ):
            with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
                conn.execute(stmt, (ink.owner,))
    with as_user(pg, ink.other) as conn:
        assert conn.execute(q, (ink.owner,)).fetchone() == (0,)
    with as_anon(pg) as conn, pytest.raises(errors.InsufficientPrivilege):
        conn.execute(q, (ink.owner,))


def test_sync_ops_are_closed_to_every_client(pg: psycopg.Connection[Any], ink: Ink) -> None:
    pg.execute(
        "insert into public.sync_ops (user_id, op_id, method, path, status, body) values (%s, %s, 'POST', '/x', 201,"
        " %s)",
        (ink.owner, uuid.uuid4(), Jsonb({"secret": "stored body"})),
    )
    for role in (lambda: as_user(pg, ink.owner), lambda: as_anon(pg)):
        with role() as conn:
            for stmt in (
                "select count(*) from public.sync_ops",
                "delete from public.sync_ops",
                "update public.sync_ops set status = 200",
            ):
                with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
                    conn.execute(stmt)
    row = pg.execute(
        "select relrowsecurity, relforcerowsecurity, (select count(*) from pg_policies where tablename = 'sync_ops')"
        " from pg_class where oid = 'public.sync_ops'::regclass"
    ).fetchone()
    assert row == (True, True, 0)


# ── LWW on notes ──


def test_note_patch_last_writer_wins(client: TestClient, key: SigningKey, ink: Ink) -> None:
    h = ink.h(key)
    t = now_ms() - 60_000
    made = client.post("/api/notes", json={"title": "v1", "body": doc("one"), "client_updated_at_ms": t}, headers=h)
    assert made.status_code == 201, made.text
    note = made.json()
    assert note["applied"] is True and note["client_updated_at_ms"] == t
    nid = note["id"]
    older = client.patch(f"/api/notes/{nid}", json={"body": doc("older"), "client_updated_at_ms": t - 1}, headers=h)
    assert older.status_code == 200 and older.json()["applied"] is False and text_of(older.json()) == "one"
    tie = client.patch(f"/api/notes/{nid}", json={"body": doc("tie"), "client_updated_at_ms": t}, headers=h)
    assert tie.json()["applied"] is False and text_of(tie.json()) == "one"
    newer = client.patch(f"/api/notes/{nid}", json={"body": doc("newer"), "client_updated_at_ms": t + 1}, headers=h)
    assert newer.json()["applied"] is True and text_of(newer.json()) == "newer"
    assert newer.json()["client_updated_at_ms"] == t + 1
    # a device clock far ahead is clamped to server time + 5 min, so a later honest write can still win
    far = client.patch(f"/api/notes/{nid}", json={"title": "far", "client_updated_at_ms": t + 10**12}, headers=h)
    stamp = far.json()["client_updated_at_ms"]
    assert far.json()["applied"] is True and stamp <= now_ms() + FUTURE_SKEW_MS
    # a client that sends no stamp always applies, above what is stored
    plain = client.patch(f"/api/notes/{nid}", json={"title": "plain"}, headers=h)
    assert plain.json()["applied"] is True and plain.json()["title"] == "plain"
    assert plain.json()["client_updated_at_ms"] > stamp


def test_lww_endpoints_refuse_other_users_keys(client: TestClient, key: SigningKey, ink: Ink) -> None:
    h, them = ink.h(key), ink.h(key, ink.other)
    t = now_ms()
    nid = client.post("/api/notes", json={"title": "mine", "client_updated_at_ms": t}, headers=h).json()["id"]
    for res in (
        client.patch(f"/api/notes/{nid}", json={"title": "x", "client_updated_at_ms": t + 5}, headers=them),
        client.delete(f"/api/notes/{nid}", params={"client_updated_at_ms": t + 5}, headers=them),
    ):
        assert res.status_code == 404 and res.json()["error"]["code"] == "note_not_found"
    stolen = client.post("/api/notes", json={"id": nid, "title": "x", "client_updated_at_ms": t + 5}, headers=them)
    assert stolen.status_code == 409 and stolen.json()["error"]["code"] == "note_id_conflict"
    book = client.post("/api/notebooks", json={"title": "B", "client_updated_at_ms": t}, headers=h).json()
    for res in (
        client.patch(f"/api/notebooks/{book['id']}", json={"title": "x", "client_updated_at_ms": t + 5}, headers=them),
        client.delete(f"/api/notebooks/{book['id']}", params={"client_updated_at_ms": t + 5}, headers=them),
    ):
        assert res.status_code == 404 and res.json()["error"]["code"] == "notebook_not_found"
    # another user's tombstone decides nothing for me
    assert client.delete(f"/api/notes/{nid}", params={"client_updated_at_ms": t + 1}, headers=h).status_code == 204
    again = client.post("/api/notes", json={"id": nid, "title": "theirs", "client_updated_at_ms": t}, headers=them)
    assert again.status_code == 201  # ids are global: the other user's create is theirs, my tombstone is mine
    assert client.get(f"/api/notes/{nid}", headers=h).status_code == 404


def test_delete_then_an_older_edit_is_refused_and_a_newer_one_recreates(
    client: TestClient, key: SigningKey, ink: Ink, pg: psycopg.Connection[Any]
) -> None:
    h = ink.h(key)
    t = now_ms() - 60_000
    nid = client.post("/api/notes", json={"title": "a", "client_updated_at_ms": t}, headers=h).json()["id"]
    gone = client.delete(f"/api/notes/{nid}", params={"client_updated_at_ms": t + 10}, headers=h)
    assert gone.status_code == 204
    row = pg.execute(
        "select entity, client_updated_at_ms from public.sync_tombstones where user_id = %s and key = %s",
        (ink.owner, nid),
    ).fetchone()
    assert row == ("note", t + 10)
    # the edit made offline before the delete loses: 409 sync_deleted with the delete's stamp
    stale = client.patch(f"/api/notes/{nid}", json={"title": "b", "client_updated_at_ms": t + 5}, headers=h)
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "sync_deleted"
    assert stale.json()["tombstone"] == {"entity": "note", "key": nid, "client_updated_at_ms": t + 10}
    # even a newer PATCH cannot bring back a row it does not hold: the client re-creates with its stamp
    newer_patch = client.patch(f"/api/notes/{nid}", json={"title": "c", "client_updated_at_ms": t + 20}, headers=h)
    assert newer_patch.status_code == 409 and newer_patch.json()["tombstone"]["client_updated_at_ms"] == t + 10
    tie = client.post("/api/notes", json={"id": nid, "title": "tie", "client_updated_at_ms": t + 10}, headers=h)
    assert tie.status_code == 409 and tie.json()["error"]["code"] == "sync_deleted"
    back = client.post("/api/notes", json={"id": nid, "title": "back", "client_updated_at_ms": t + 20}, headers=h)
    assert back.status_code == 201 and back.json()["title"] == "back" and back.json()["applied"] is True
    assert pg.execute("select count(*) from public.sync_tombstones where key = %s", (nid,)).fetchone() == (0,)
    # a replayed delete of an already-deleted row (with its stamp) is done, not an error; without one it is a 404
    assert client.delete(f"/api/notes/{nid}", params={"client_updated_at_ms": t + 30}, headers=h).status_code == 204
    assert client.delete(f"/api/notes/{nid}", params={"client_updated_at_ms": t + 30}, headers=h).status_code == 204
    assert client.delete(f"/api/notes/{nid}", headers=h).status_code == 404


def test_edit_then_an_older_delete_keeps_the_note(client: TestClient, key: SigningKey, ink: Ink) -> None:
    h = ink.h(key)
    t = now_ms() - 60_000
    nid = client.post("/api/notes", json={"title": "a", "client_updated_at_ms": t}, headers=h).json()["id"]
    assert client.patch(f"/api/notes/{nid}", json={"title": "e", "client_updated_at_ms": t + 20}, headers=h).json()[
        "applied"
    ]
    kept = client.delete(f"/api/notes/{nid}", params={"client_updated_at_ms": t + 10}, headers=h)
    assert kept.status_code == 200 and kept.json()["applied"] is False and kept.json()["title"] == "e"
    tie = client.delete(f"/api/notes/{nid}", params={"client_updated_at_ms": t + 20}, headers=h)
    assert tie.status_code == 200 and tie.json()["applied"] is False
    assert client.get(f"/api/notes/{nid}", headers=h).status_code == 200
    assert client.delete(f"/api/notes/{nid}", params={"client_updated_at_ms": t + 30}, headers=h).status_code == 204


def test_two_devices_create_the_section_note(client: TestClient, key: SigningKey, ink: Ink) -> None:
    """Both devices made the section's one page note offline (different ids): the newer content wins on both."""
    h = ink.h(key)
    t = now_ms() - 60_000
    base = {"kind": "note", "anchor_type": "section", "anchor_id": ink.section}
    a = client.post("/api/notes", json={**base, "body": doc("A"), "client_updated_at_ms": t}, headers=h)
    assert a.status_code == 201
    b = client.post(
        "/api/notes", json={**base, "id": str(uuid.uuid4()), "body": doc("B"), "client_updated_at_ms": t + 1}, headers=h
    )
    assert b.status_code == 200 and b.json()["id"] == a.json()["id"]
    assert b.json()["applied"] is True and text_of(b.json()) == "B"
    late = client.post(
        "/api/notes", json={**base, "id": str(uuid.uuid4()), "body": doc("old"), "client_updated_at_ms": t}, headers=h
    )
    assert late.status_code == 200 and late.json()["applied"] is False and text_of(late.json()) == "B"
    # a replay without a stamp is the S13 replay: the stored note, unchanged
    plain = client.post("/api/notes", json={**base, "body": doc("plain")}, headers=h)
    assert plain.status_code == 200 and plain.json()["applied"] is False and text_of(plain.json()) == "B"


def test_notebook_lww_and_tombstones(
    client: TestClient, key: SigningKey, ink: Ink, pg: psycopg.Connection[Any]
) -> None:
    h = ink.h(key)
    t = now_ms() - 60_000
    book = client.post("/api/notebooks", json={"title": "B1", "client_updated_at_ms": t}, headers=h)
    assert book.status_code == 201 and book.json()["client_updated_at_ms"] == t
    bid = book.json()["id"]
    page_id = book.json()["pages"][0]["id"]
    old = client.patch(f"/api/notebooks/{bid}", json={"title": "old", "client_updated_at_ms": t - 1}, headers=h)
    assert old.status_code == 200 and old.json()["applied"] is False and old.json()["title"] == "B1"
    new = client.patch(f"/api/notebooks/{bid}", json={"title": "B2", "client_updated_at_ms": t + 1}, headers=h)
    assert new.json()["applied"] is True and new.json()["title"] == "B2"
    replay = client.post("/api/notebooks", json={"id": bid, "title": "B3", "client_updated_at_ms": t + 2}, headers=h)
    assert replay.status_code == 200 and replay.json()["applied"] is True and replay.json()["title"] == "B3"
    kept = client.delete(f"/api/notebooks/{bid}", params={"client_updated_at_ms": t}, headers=h)
    assert kept.status_code == 200 and kept.json()["applied"] is False and len(kept.json()["pages"]) == 1
    assert client.delete(f"/api/notebooks/{bid}", params={"client_updated_at_ms": t + 9}, headers=h).status_code == 204
    stones = dict(
        pg.execute(
            "select entity, client_updated_at_ms from public.sync_tombstones where user_id = %s and key = any(%s)",
            (ink.owner, [bid, page_id]),
        ).fetchall()
    )
    assert stones == {"notebook": t + 9, "page": t + 9}
    after = client.patch(f"/api/notebooks/{bid}", json={"title": "x", "client_updated_at_ms": t + 5}, headers=h)
    assert after.status_code == 409 and after.json()["tombstone"]["entity"] == "notebook"
    page = client.patch(f"/api/notes/{page_id}", json={"title": "x", "client_updated_at_ms": t + 5}, headers=h)
    assert page.status_code == 409 and page.json()["tombstone"]["entity"] == "page"


# ── Idempotency-Key ──


def test_a_replayed_create_returns_the_stored_note_and_writes_nothing(
    client: TestClient, key: SigningKey, ink: Ink, pg: psycopg.Connection[Any]
) -> None:
    h = ink.h(key)
    op = str(uuid.uuid4())
    first = client.post("/api/notes", json={"title": "once"}, headers={**h, "Idempotency-Key": op})
    assert first.status_code == 201
    second = client.post(
        "/api/notes", json={"id": str(uuid.uuid4()), "title": "twice"}, headers={**h, "Idempotency-Key": op}
    )
    assert second.status_code == 201 and second.json() == first.json()
    assert second.headers["idempotent-replayed"] == "true"
    assert pg.execute(
        "select count(*) from public.notes where user_id = %s and title in ('once', 'twice')", (ink.owner,)
    ).fetchone() == (1,)
    stored = pg.execute(
        "select method, path, status, body ->> 'id' from public.sync_ops where user_id = %s and op_id = %s",
        (ink.owner, op),
    ).fetchone()
    assert stored == ("POST", "/api/notes", 201, first.json()["id"])
    # the same key on another route is refused; another user's same key is their own
    nid = first.json()["id"]
    reused = client.patch(f"/api/notes/{nid}", json={"title": "x"}, headers={**h, "Idempotency-Key": op})
    assert reused.status_code == 422 and reused.json()["error"]["code"] == "idempotency_key_reused"
    theirs = client.post(
        "/api/notes", json={"title": "theirs"}, headers={**ink.h(key, ink.other), "Idempotency-Key": op}
    )
    assert theirs.status_code == 201 and theirs.json()["id"] != nid
    bad = client.post("/api/notes", json={"title": "x"}, headers={**h, "Idempotency-Key": "not-a-uuid"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "idempotency_key_invalid"


def test_a_replayed_delete_and_a_final_error_are_stored(client: TestClient, key: SigningKey, ink: Ink) -> None:
    h = ink.h(key)
    nid = client.post("/api/notes", json={"title": "d"}, headers=h).json()["id"]
    op = str(uuid.uuid4())
    assert client.delete(f"/api/notes/{nid}", headers={**h, "Idempotency-Key": op}).status_code == 204
    again = client.delete(f"/api/notes/{nid}", headers={**h, "Idempotency-Key": op})
    assert again.status_code == 204 and again.headers["idempotent-replayed"] == "true"
    missing = str(uuid.uuid4())
    op2 = str(uuid.uuid4())
    first = client.patch(f"/api/notes/{missing}", json={"title": "x"}, headers={**h, "Idempotency-Key": op2})
    assert first.status_code == 404
    second = client.patch(f"/api/notes/{missing}", json={"title": "x"}, headers={**h, "Idempotency-Key": op2})
    assert second.status_code == 404 and second.json() == first.json()
    assert second.headers["idempotent-replayed"] == "true"


def _tutor_block(pg: psycopg.Connection[Any], ink: Ink, questions: list[str]) -> str:
    import json

    cfg = {
        "exam": "step1",
        "mode": "tutor",
        "status": "all",
        "difficulty": None,
        "systems": [],
        "disciplines": [],
        "seed": 1,
        "weak_spot": False,
    }
    row = pg.execute(
        "insert into public.blocks (user_id, config, question_ids, started_at) values (%s, %s::jsonb, %s, now())"
        " returning id",
        (ink.owner, json.dumps(cfg), questions),
    ).fetchone()
    assert row is not None
    return str(row[0])


def test_a_replayed_attempt_is_counted_once(
    client: TestClient, key: SigningKey, ink: Ink, pg: psycopg.Connection[Any]
) -> None:
    h = ink.h(key)
    bid = _tutor_block(pg, ink, [ink.question])
    op = str(uuid.uuid4())
    body = {"question_id": ink.question, "chosen": "A", "seconds": 12}
    first = client.post(f"/api/blocks/{bid}/attempts", json=body, headers={**h, "Idempotency-Key": op})
    assert first.status_code == 200, first.text
    second = client.post(f"/api/blocks/{bid}/attempts", json=body, headers={**h, "Idempotency-Key": op})
    assert second.json() == first.json() and second.headers["idempotent-replayed"] == "true"
    assert pg.execute(
        "select count(*) from public.attempts where user_id = %s and block_id = %s", (ink.owner, bid)
    ).fetchone() == (1,)


def test_reading_position_tie_keeps_the_stored_position(client: TestClient, key: SigningKey, ink: Ink) -> None:
    h = ink.h(key)
    base = {"chapter_id": ink.chapter, "section_id": ink.section, "client_updated_at": "2026-11-16T09:00:00Z"}
    assert client.put("/api/reading-position", json={**base, "offset": 1.0}, headers=h).json()["applied"] is True
    tie = client.put("/api/reading-position", json={**base, "offset": 2.0}, headers=h).json()
    assert tie["applied"] is False and tie["position"]["offset"] == 1.0
    op = str(uuid.uuid4())
    newer = {**base, "offset": 3.0, "client_updated_at": "2026-11-16T09:00:01Z"}
    one = client.put("/api/reading-position", json=newer, headers={**h, "Idempotency-Key": op})
    two = client.put("/api/reading-position", json=newer, headers={**h, "Idempotency-Key": op})
    assert one.json()["applied"] is True and two.json() == one.json()
    assert two.headers["idempotent-replayed"] == "true"


# ── Clean-up ──


def test_gc_removes_old_ops_and_tombstones_only(pg: psycopg.Connection[Any], ink: Ink, url: str) -> None:
    old_op, new_op = uuid.uuid4(), uuid.uuid4()
    pg.execute(
        "insert into public.sync_ops (user_id, op_id, method, path, status, created_at) values"
        " (%s, %s, 'POST', '/a', 200, now() - interval '25 hours'), (%s, %s, 'POST', '/a', 200, now())",
        (ink.owner, old_op, ink.owner, new_op),
    )
    pg.execute(
        "insert into public.sync_tombstones (user_id, entity, key, client_updated_at_ms, created_at) values"
        " (%s, 'note', 'old', 1, now() - interval '91 days'), (%s, 'note', 'new', 1, now())",
        (ink.owner, ink.owner),
    )

    async def run() -> Any:
        db = Database(SecretStr(url))
        await db.open()
        try:
            return await run_gc(db)
        finally:
            await db.close()

    done = asyncio.run(run())
    assert done is not None and done.ops >= 1 and done.tombstones >= 1
    ops = {r[0] for r in pg.execute("select op_id from public.sync_ops where user_id = %s", (ink.owner,)).fetchall()}
    assert ops == {new_op}
    keys = {r[0] for r in pg.execute("select key from public.sync_tombstones where user_id = %s", (ink.owner,))}
    assert keys == {"new"}
