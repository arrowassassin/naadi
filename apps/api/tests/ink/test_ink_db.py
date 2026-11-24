"""Integration (real Postgres, migrated schema, full HTTP path): load/sync, the merge upsert against the golden
vectors shared with mergeInkItems, idempotency, clock clamp, limits, surface validation for every type, RLS between
users, the "content changed" count, tombstone purge and `reset`, settings.ink."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from naadi_api.db import Database
from naadi_api.ink import routes as ink_routes
from naadi_api.ink import store
from naadi_api.ink.store import compact_tombstone, layer_hash
from tests.auth.jwt_helpers import SigningKey

from ..conftest import REPO_ROOT
from .conftest import Ink

pytestmark = [pytest.mark.integration, pytest.mark.db]

VECTORS: list[dict[str, Any]] = json.loads(
    (REPO_ROOT / "packages" / "shared" / "fixtures" / "ink" / "merge-vectors.json").read_text(encoding="utf-8")
)["cases"]

_seq = 0


def uid7(ms: int | None = None) -> str:
    """A UUIDv7 (ms timestamp + counter), unique per test run."""
    global _seq
    _seq += 1
    ms = ms if ms is not None else int(time.time() * 1000)
    rand = uuid.uuid4().int & ((1 << 62) - 1)
    value = (ms << 80) | (0x7 << 76) | ((_seq & 0xFFF) << 64) | (0b10 << 62) | rand
    return str(uuid.UUID(int=value))


def stroke(updated: int | None = None, **over: Any) -> dict[str, Any]:
    now = int(time.time() * 1000)
    return {
        "id": uid7(),
        "tool": "pen",
        "color": "ink",
        "size": 0.002,
        "points": [[0.1, 0.1, 0.5, 0], [0.2, 0.2, 0.5, 8]],
        "createdAt": now,
        "updatedAt": updated if updated is not None else now,
        **over,
    }


def path(kind: str, sid: str) -> str:
    return f"/api/ink/{kind}/{sid}"


def post(client: TestClient, h: dict[str, str], kind: str, sid: str, items: list[Any], base: int = 0) -> Any:
    return client.post(path(kind, sid) + "/changes", json={"base_rev": base, "items": items}, headers=h)


def get(client: TestClient, h: dict[str, str], kind: str, sid: str, since: int | None = None) -> dict[str, Any]:
    res = client.get(path(kind, sid), params={} if since is None else {"since": since}, headers=h)
    assert res.status_code == 200, res.text
    out: dict[str, Any] = res.json()
    return out


# ── Auth ──


def test_every_endpoint_requires_sign_in(client: TestClient, ink: Ink) -> None:
    assert client.get(path("section", ink.section)).status_code == 401
    assert client.post(path("section", ink.section) + "/changes", json={"base_rev": 0, "items": []}).status_code == 401


# ── Load and sync ──


def test_empty_surface_then_save_reload_and_incremental_sync(client: TestClient, key: SigningKey, ink: Ink) -> None:
    h = ink.h(key)
    empty = get(client, h, "section", ink.section)
    assert empty["layer_id"] is None and empty["items"] == [] and empty["rev"] == 0 and not empty["reset"]
    a, b = stroke(), stroke(color="violet")
    res = post(client, h, "section", ink.section, [a, b])
    assert res.status_code == 200, res.text
    out = res.json()
    assert sorted(out["applied"]) == sorted([a["id"], b["id"]]) and out["rejected"] == [] and out["remote"] == []
    assert out["item_count"] == 2 and out["hash"] == layer_hash([a, b])
    full = get(client, h, "section", ink.section)
    assert full["items"] == sorted([a, b], key=lambda i: i["id"])  # stored exactly as sent
    assert full["rev"] == out["rev"] and full["hash"] == out["hash"] and full["outdated"] == 0
    # A delete from "another device": the tombstone shows up incrementally, never in a full load.
    dead = {**a, "deleted": True, "updatedAt": a["updatedAt"] + 1}
    other_device = post(client, h, "section", ink.section, [dead], base=full["rev"]).json()
    inc = get(client, h, "section", ink.section, since=full["rev"])
    assert [i["id"] for i in inc["items"]] == [a["id"]] and inc["items"][0]["deleted"] is True
    assert inc["rev"] == other_device["rev"]
    assert [i["id"] for i in get(client, h, "section", ink.section)["items"]] == [b["id"]]
    assert get(client, h, "section", ink.section, since=inc["rev"])["items"] == []


def test_remote_carries_other_devices_changes_and_not_my_own(client: TestClient, key: SigningKey, ink: Ink) -> None:
    h = ink.h(key)
    first = post(client, h, "question", ink.question, [stroke()]).json()
    theirs = stroke()
    post(client, h, "question", ink.question, [theirs], base=first["rev"])  # device B
    mine = stroke()
    out = post(client, h, "question", ink.question, [mine], base=first["rev"]).json()  # device A, behind by one
    assert [i["id"] for i in out["remote"]] == [theirs["id"]]
    assert out["applied"] == [mine["id"]]


def test_resending_is_a_no_op(client: TestClient, key: SigningKey, ink: Ink) -> None:
    h = ink.h(key)
    items = [stroke(), stroke()]
    first = post(client, h, "section", ink.section, items).json()
    again = post(client, h, "section", ink.section, items, base=first["rev"]).json()
    assert again["applied"] == [] and again["rev"] == first["rev"] and again["hash"] == first["hash"]
    assert sorted(r["id"] for r in again["rejected"]) == sorted(i["id"] for i in items)
    assert {r["reason"] for r in again["rejected"]} == {"stale"}
    assert sorted(i["id"] for i in again["remote"]) == sorted(i["id"] for i in items)  # the server's copy


def _sql_state(pg: psycopg.Connection[Any], layer_id: str) -> list[dict[str, Any]]:
    rows = pg.execute("select item from public.ink_items where layer_id = %s order by item_id", (layer_id,)).fetchall()
    return [r[0] for r in rows]


@pytest.mark.parametrize("case", VECTORS, ids=[c["name"] for c in VECTORS])
def test_sql_upsert_matches_merge_ink_items_golden_vectors(
    client: TestClient, key: SigningKey, ink: Ink, pg: psycopg.Connection[Any], case: dict[str, Any]
) -> None:
    """The same vectors mergeInkItems is checked against (ink.fixtures.test.ts), in both write orders."""
    h = ink.h(key)
    expected = [compact_tombstone(i) for i in case["expected"]]
    for n, (first, second) in enumerate(((case["a"], case["b"]), (case["b"], case["a"]))):
        sid = f"{ink.block}:{n + 1}"  # exam_scratch: two clean surfaces of the owner's block
        r1 = post(client, h, "exam_scratch", sid, first)
        assert r1.status_code == 200, r1.text
        r2 = post(client, h, "exam_scratch", sid, second, base=r1.json()["rev"])
        assert r2.status_code == 200, r2.text
        assert _sql_state(pg, r2.json()["layer_id"]) == expected
        assert r2.json()["hash"] == layer_hash(case["expected"])


def test_full_tie_keeps_the_stored_row(client: TestClient, key: SigningKey, ink: Ink) -> None:
    h = ink.h(key)
    a = stroke(updated=1_763_000_000_000)
    post(client, h, "section", ink.section, [a])
    tie = {**a, "color": "violet"}  # same updatedAt, different content: the client's JSON order is not ours
    out = post(client, h, "section", ink.section, [tie]).json()
    assert out["applied"] == [] and out["rejected"] == [{"id": a["id"], "reason": "stale"}]
    assert out["remote"] == [a]


def test_clock_more_than_five_minutes_ahead_is_clamped(client: TestClient, key: SigningKey, ink: Ink) -> None:
    h = ink.h(key)
    far = int(time.time() * 1000) + 3600_000
    near = int(time.time() * 1000) + 120_000
    a, b = stroke(updated=far), stroke(updated=near)
    out = post(client, h, "section", ink.section, [a, b]).json()
    items = {i["id"]: i for i in get(client, h, "section", ink.section)["items"]}
    assert items[a["id"]]["updatedAt"] <= out["server_now"]
    assert items[b["id"]]["updatedAt"] == near  # inside the window: kept


def test_a_clamped_item_comes_back_in_remote_with_the_server_stamp(
    client: TestClient, key: SigningKey, ink: Ink
) -> None:
    """Review P1: a device whose clock runs ahead must adopt the server's stamp, or a later delete elsewhere loses
    to its future copy. The clamped item is applied *and* echoed; one inside the window is not echoed."""
    h = ink.h(key)
    ahead = int(time.time() * 1000) + 600_000
    x, near = stroke(updated=ahead), stroke(updated=int(time.time() * 1000) + 60_000)
    out = post(client, h, "section", ink.section, [x, near]).json()
    assert sorted(out["applied"]) == sorted([x["id"], near["id"]])
    assert [i["id"] for i in out["remote"]] == [x["id"]]
    echoed = out["remote"][0]
    assert echoed["updatedAt"] == out["server_now"] and echoed["points"] == x["points"]
    # Another device erases X after the clamp: the tombstone wins, and device A's next sync carries it.
    dead = {**echoed, "deleted": True, "updatedAt": echoed["updatedAt"] + 1}
    b = post(client, h, "section", ink.section, [dead], base=out["rev"]).json()
    assert b["applied"] == [x["id"]]
    inc = get(client, h, "section", ink.section, since=out["rev"])
    assert [(i["id"], i.get("deleted")) for i in inc["items"]] == [(x["id"], True)]
    # Device A re-sends the version it adopted (a lost response): stale, and X stays deleted.
    again = post(client, h, "section", ink.section, [echoed], base=out["rev"]).json()
    assert again["rejected"] == [{"id": x["id"], "reason": "stale"}]
    assert x["id"] not in {i["id"] for i in get(client, h, "section", ink.section)["items"]}


def test_integral_floats_are_stored_as_integers(client: TestClient, key: SigningKey, ink: Ink) -> None:
    """Review P2: `1794883405009.0` is an integer to zod and pydantic; it used to reach Postgres' ::bigint as text
    and fail the whole batch with 500. Now it is stored as 1794883405009 (and t / anchor.pm likewise), and a
    non-integral stamp is refused for that item alone."""
    h = ink.h(key)
    now = int(time.time() * 1000)
    a = stroke(updated=now)
    raw = {
        **a,
        "updatedAt": float(now),
        "createdAt": float(now),
        "points": [[0.1, 0.1, 0.5, 0.0], [0.2, 0.2, 0.5, 8.0]],
        "anchor": {"pm": 12.0, "dy": 0.01},
    }
    bad = stroke(updated=now)
    bad["updatedAt"] = now + 0.5
    res = post(client, h, "section", ink.section, [raw, bad])
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["applied"] == [a["id"]] and out["rejected"] == [{"id": bad["id"], "reason": "invalid"}]
    stored = get(client, h, "section", ink.section)["items"][0]
    assert stored["updatedAt"] == now and isinstance(stored["updatedAt"], int)
    assert isinstance(stored["createdAt"], int) and stored["anchor"] == {"pm": 12, "dy": 0.01}
    assert [p[3] for p in stored["points"]] == [0, 8] and all(isinstance(p[3], int) for p in stored["points"])
    body = (
        '{"base_rev": 0, "items": ['
        + json.dumps({**stroke(), "updatedAt": 0}).replace('"updatedAt": 0', '"updatedAt": 1.79e12')
        + "]}"
    )
    res = client.post(
        path("section", ink.section) + "/changes", content=body, headers={**h, "content-type": "application/json"}
    )
    assert res.status_code == 200 and len(res.json()["applied"]) == 1, res.text


def test_invalid_and_duplicate_items_are_rejected_individually(client: TestClient, key: SigningKey, ink: Ink) -> None:
    h = ink.h(key)
    good = stroke()
    newer = {**good, "color": "lime", "updatedAt": good["updatedAt"] + 5}
    bad = {**stroke(), "color": "#ff00"}  # hex colours are valid (full-colour wheel); this one is malformed
    out = post(client, h, "section", ink.section, [good, bad, None, newer]).json()
    assert out["applied"] == [good["id"]]
    assert {"id": bad["id"], "reason": "invalid"} in out["rejected"]
    assert {"id": None, "reason": "invalid"} in out["rejected"]
    assert {"id": good["id"], "reason": "duplicate"} in out["rejected"]
    assert get(client, h, "section", ink.section)["items"] == [newer]


def test_limits(client: TestClient, key: SigningKey, ink: Ink, monkeypatch: pytest.MonkeyPatch) -> None:
    h = ink.h(key)
    too_many = post(client, h, "section", ink.section, [stroke() for _ in range(501)])
    assert too_many.status_code == 422
    big = stroke(points=[[0.123456, 0.123456, 0.5, i] for i in range(10_000)])
    huge = client.post(
        path("section", ink.section) + "/changes",
        content=json.dumps({"base_rev": 0, "items": [big] * 30}),
        headers={**h, "content-type": "application/json"},
    )
    assert huge.status_code == 413
    monkeypatch.setattr(store, "INK_MAX_ITEMS", 3)
    first = post(client, h, "section", ink.section, [stroke(), stroke(), stroke()])
    assert first.status_code == 200
    over = post(client, h, "section", ink.section, [stroke()], base=first.json()["rev"])
    assert over.status_code == 409 and over.json()["error"]["code"] == "ink_item_limit"
    assert get(client, h, "section", ink.section)["item_count"] == 3  # rolled back
    # Deleting makes room again (a tombstone is not a live item).
    gone = {**get(client, h, "section", ink.section)["items"][0], "deleted": True}
    gone["updatedAt"] += 1
    assert post(client, h, "section", ink.section, [gone, stroke()]).status_code == 200


# ── Surfaces ──


def test_surface_validation_for_every_type(client: TestClient, key: SigningKey, ink: Ink) -> None:
    h = ink.h(key)
    ok = [
        ("section", ink.section),
        ("question", ink.question),
        ("flashcard", ink.card),
        ("notebook_page", ink.note),
        ("exam_scratch", f"{ink.block}:1"),
        ("exam_scratch", f"{ink.block}:2"),
        ("pdf_page", f"{ink.upload}:1"),
        ("pdf_page", f"{ink.upload}:10000"),
    ]
    for kind, sid in ok:
        assert client.get(path(kind, sid), headers=h).status_code == 200, (kind, sid)
        assert post(client, h, kind, sid, [stroke()]).status_code == 200, (kind, sid)
    missing = [
        ("section", ink.draft_section),  # exists, hidden
        ("section", "no-such-section"),
        ("question", ink.draft_question),
        ("flashcard", ink.private_card),  # someone else's card
        ("flashcard", "not-a-uuid"),
        ("flashcard", str(uuid.uuid4()).upper()),
        ("notebook_page", ink.other_note),
        ("exam_scratch", f"{ink.block}:3"),  # past the block's questions
        ("exam_scratch", f"{ink.block}:0"),
        ("exam_scratch", ink.block),
        ("pdf_page", f"{ink.upload}:10001"),
        ("pdf_page", f"{uuid.uuid4()}:1"),
        ("canvas", ink.section),  # unknown type
        ("section", "bad id with spaces"),
    ]
    for kind, sid in missing:
        g = client.get(path(kind, sid), headers=h)
        p = post(client, h, kind, sid, [stroke()])
        assert g.status_code == 404 and p.status_code == 404, (kind, sid, g.status_code, p.status_code)
        assert g.json()["error"]["code"] == "not_found"
    # Another student's block or upload is a 404 too, and the same text as a missing one.
    hb = ink.h(key, ink.other)
    assert client.get(path("exam_scratch", f"{ink.block}:1"), headers=hb).status_code == 404
    assert client.get(path("pdf_page", f"{ink.upload}:1"), headers=hb).status_code == 404


def test_users_never_see_each_others_ink(client: TestClient, key: SigningKey, ink: Ink, url: str) -> None:
    a_item = stroke()
    post(client, ink.h(key), "section", ink.section, [a_item])
    hb = ink.h(key, ink.other)
    assert get(client, hb, "section", ink.section)["items"] == []
    b_item = {**a_item, "color": "violet", "updatedAt": a_item["updatedAt"] + 10}  # same id from B
    out = post(client, hb, "section", ink.section, [b_item]).json()
    assert out["applied"] == [b_item["id"]]  # B's own layer; A's untouched
    assert get(client, ink.h(key), "section", ink.section)["items"] == [a_item]
    # Directly, under RLS: B reads none of A's rows.
    from tests.db.rls import as_user

    with psycopg.connect(url, autocommit=True) as conn, as_user(conn, ink.other) as c:
        rows = c.execute("select user_id from public.ink_items").fetchall()
        assert {r[0] for r in rows} == {ink.other}
        layers = c.execute("select user_id from public.ink_layers").fetchall()
        assert {r[0] for r in layers} == {ink.other}


def test_outdated_counts_items_drawn_on_an_older_section(
    client: TestClient, key: SigningKey, ink: Ink, pg: psycopg.Connection[Any]
) -> None:
    h = ink.h(key)
    old = stroke()
    post(client, h, "section", ink.section, [old])
    assert get(client, h, "section", ink.section)["outdated"] == 0
    body = {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Two"}]}]}
    pg.execute("update public.chapter_sections set body = %s where id = %s", (Jsonb(body), ink.section))
    after = get(client, h, "section", ink.section)
    assert after["outdated"] == 1 and after["items"] == [old]  # never dropped
    post(client, h, "section", ink.section, [stroke()])
    assert get(client, h, "section", ink.section)["outdated"] == 1  # only the old one
    moved = {**old, "points": [[0.3, 0.3, 0.5, 0]], "updatedAt": old["updatedAt"] + 1}
    post(client, h, "section", ink.section, [moved])
    assert get(client, h, "section", ink.section)["outdated"] == 0


# ── Tombstones ──


def test_purge_removes_old_tombstones_and_resets_old_cursors(
    client: TestClient, key: SigningKey, ink: Ink, pg: psycopg.Connection[Any], url: str
) -> None:
    h = ink.h(key)
    keep, dead_old, dead_new = stroke(), stroke(), stroke()
    base = post(client, h, "section", ink.section, [keep, dead_old, dead_new]).json()
    cursor = base["rev"]
    t1 = {**dead_old, "deleted": True, "updatedAt": dead_old["updatedAt"] + 1}
    t2 = {**dead_new, "deleted": True, "updatedAt": dead_new["updatedAt"] + 1}
    after = post(client, h, "section", ink.section, [t1, t2], base=cursor).json()
    stored = pg.execute("select item from public.ink_items where item_id = %s", (t1["id"],)).fetchone()
    assert stored is not None and len(stored[0]["points"]) == 1  # tombstones are compacted
    pg.execute("update public.ink_items set written_at = now() - interval '91 days' where item_id = %s", (t1["id"],))

    async def purge() -> int | None:
        from pydantic import SecretStr

        db = Database(SecretStr(url))
        await db.open()
        try:
            return await ink_routes.run_purge(db)
        finally:
            await db.close()

    removed = asyncio.run(purge())
    assert removed is not None and removed >= 1
    ids = [
        r[0]
        for r in pg.execute(
            "select item_id::text from public.ink_items where layer_id = %s", (after["layer_id"],)
        ).fetchall()
    ]
    assert t1["id"] not in ids and t2["id"] in ids and keep["id"] in ids
    # A device that last synced before the purged deletion cannot be told about it: reset (full load, no tombstones).
    stale = get(client, h, "section", ink.section, since=cursor)
    assert stale["reset"] is True and sorted(i["id"] for i in stale["items"]) == sorted([keep["id"]])
    fresh = get(client, h, "section", ink.section, since=after["rev"])
    assert fresh["reset"] is False and fresh["items"] == []
    late = post(client, h, "section", ink.section, [stroke()], base=cursor).json()
    assert late["reset"] is True and keep["id"] in {i["id"] for i in late["remote"]}
    # A cursor from the future (restored database) also resets.
    assert get(client, h, "section", ink.section, since=10**12)["reset"] is True
    # Review P3: the purge horizon. A long-offline device's edit of dead_old, older than its (purged) deletion, is
    # stale and forces a full list without it; it must not bring the stroke back.
    horizon = pg.execute(
        "select purged_before_ms from public.ink_layers where id = %s", (after["layer_id"],)
    ).fetchone()
    assert horizon is not None and horizon[0] == t1["updatedAt"]
    old_edit = {**dead_old, "color": "violet", "updatedAt": dead_old["updatedAt"]}
    back = post(client, h, "section", ink.section, [old_edit], base=late["rev"]).json()
    assert back["applied"] == [] and back["rejected"] == [{"id": dead_old["id"], "reason": "stale"}]
    assert back["reset"] is True and dead_old["id"] not in {i["id"] for i in back["remote"]}
    assert dead_old["id"] not in {i["id"] for i in get(client, h, "section", ink.section)["items"]}
    # An item the table still holds merges as usual below the horizon, and anything newer than it is applied.
    newer = {**dead_old, "updatedAt": t1["updatedAt"] + 5}
    assert post(client, h, "section", ink.section, [newer], base=back["rev"]).json()["applied"] == [dead_old["id"]]


def test_request_bodies_are_never_logged(
    client: TestClient, key: SigningKey, ink: Ink, capfd: pytest.CaptureFixture[str]
) -> None:
    marker = f"PRIVATE-NOTE-{uuid.uuid4().hex}"
    text = {
        "id": uid7(),
        "tool": "text",
        "color": "ink",
        "size": 0.02,
        "x": 0.1,
        "y": 0.1,
        "w": 0.3,
        "text": marker,
        "createdAt": 1,
        "updatedAt": 2,
    }
    assert post(client, ink.h(key), "section", ink.section, [text, {"text": marker}]).status_code == 200
    out, err = capfd.readouterr()
    assert marker not in out and marker not in err


# ── settings.ink ──


def test_settings_ink_is_validated(client: TestClient, key: SigningKey, ink: Ink) -> None:
    h = ink.h(key)
    assert client.get("/api/me", headers=h).status_code == 200
    ok = client.patch(
        "/api/me", json={"settings": {"ink": {"pen_only": False, "predict": True, "tool": "marker"}}}, headers=h
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["profile"]["settings"]["ink"] == {"pen_only": False, "predict": True, "tool": "marker"}
    for bad in ({"pen_only": "no"}, {"tool": "brush"}, {"color": "#fff"}, {"unknown": True}):
        res = client.patch("/api/me", json={"settings": {"ink": bad}}, headers=h)
        assert res.status_code == 422, bad
    cleared = client.patch("/api/me", json={"settings": {"ink": None}}, headers=h)
    assert "ink" not in cleared.json()["profile"]["settings"]
