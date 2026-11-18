"""Integration (real Postgres, migrated schema, full HTTP path): GET /api/offline/week.

The manifest never holds an answer, explanation, why-not or objective of a question the student has not answered
(scanned over the whole JSON), shows a graded tutor answer's explanation the way Qbank does, leaves timed/exam
blocks out, drops every explanation while an exam session runs, applies the flashcard attempt gate, and carries
only the caller's own notes, notebooks and ink.
"""

from __future__ import annotations

import datetime as dt
import json
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from tests.auth.jwt_helpers import SigningKey
from tests.db.fixtures import insert_row

from .conftest import Ink

pytestmark = [pytest.mark.integration, pytest.mark.db]

HIDDEN = ("answer", "answer_html", "explanation_html", "why_not_html", "educational_objective")


def keys_of(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in keys_of(v)}
    if isinstance(value, list):
        return {k for v in value for k in keys_of(v)}
    return set()


def config(mode: str, **extra: Any) -> str:
    return json.dumps(
        {
            "exam": "step1",
            "mode": mode,
            "status": "all",
            "difficulty": None,
            "systems": [],
            "disciplines": [],
            "seed": 1,
            "weak_spot": False,
            **extra,
        }
    )


@dataclass
class Week:
    ink: Ink
    start: dt.date
    q2: str  # in the tutor block, never answered
    q3: str  # only in a timed block
    tutor: str
    timed: str
    gated_card: str  # shared card linked to q2: hidden until q2 is answered
    figure: str
    video: str


@pytest.fixture
def week(pg: psycopg.Connection[Any], ink: Ink) -> Iterator[Week]:
    tag = ink.tag
    src = f"t{tag}-src"
    start = dt.datetime.now(dt.UTC).date()
    w = Week(
        ink=ink,
        start=start,
        q2=f"t{tag}-q-secret2",
        q3=f"t{tag}-q-secret3",
        tutor="",
        timed="",
        gated_card=str(uuid.uuid4()),
        figure=f"t{tag}-fig",
        video=f"t{tag}-yt",
    )
    base = {
        "exam": "step1",
        "stem_html": "<p>stem</p>",
        "options": Jsonb([{"label": "A", "html": "a"}, {"label": "B", "html": "b"}]),
        "answer": "B",
        "source_id": src,
    }
    for qid, word in ((w.q2, "secret-two"), (w.q3, "secret-three")):
        insert_row(
            pg,
            "questions",
            {
                "id": qid,
                **base,
                "answer_html": f"<p>{word}-answer</p>",
                "explanation_html": f"<p>{word}-explanation</p>",
                "why_not_html": f"<p>{word}-whynot</p>",
                "educational_objective": f"{word}-objective",
            },
        )
    insert_row(
        pg,
        "media_assets",
        {
            "id": w.figure,
            "kind": "svg",
            "source_id": src,
            "url": f"content-public/t{tag}/fig.svg",
            "review_status": "approved",
            "width": 10,
            "height": 10,
        },
    )
    insert_row(
        pg,
        "media_assets",
        {
            "id": w.video,
            "kind": "video_youtube",
            "source_id": src,
            "youtube_id": "abcdefghijk",
            "review_status": "approved",
        },
    )
    for i, mid in enumerate((w.figure, w.video)):
        insert_row(pg, "section_media", {"section_id": ink.section, "media_id": mid, "sort": i})
    row = pg.execute(
        "insert into public.blocks (user_id, config, question_ids, started_at) values (%s, %s::jsonb, %s, now())"
        " returning id",
        (ink.owner, config("tutor"), [ink.question, w.q2]),
    ).fetchone()
    assert row is not None
    w.tutor = str(row[0])
    row = pg.execute(
        "insert into public.blocks (user_id, config, question_ids, started_at) values (%s, %s::jsonb, %s, now())"
        " returning id",
        (
            ink.owner,
            config("timed", sections=[{"n": 1, "first_index": 0, "count": 1, "time_limit_s": 3600}], break_s=0),
            [w.q3],
        ),
    ).fetchone()
    assert row is not None
    w.timed = str(row[0])
    insert_row(
        pg,
        "flashcards",
        {"id": w.gated_card, "front_html": "gated", "back_html": "B is right", "origin": "seed", "question_id": w.q2},
    )
    insert_row(
        pg,
        "plan_items",
        {
            "user_id": ink.owner,
            "day": start + dt.timedelta(days=1),
            "kind": "read",
            "title": "Read",
            "ref_type": "chapter",
            "ref_id": ink.chapter,
            "origin": "user",
        },
    )
    yield w
    pg.execute("delete from public.flashcards where id = %s", (w.gated_card,))
    pg.execute("delete from public.blocks where user_id = %s", (ink.owner,))
    pg.execute("delete from public.attempts where user_id = %s", (ink.owner,))
    pg.execute("delete from public.section_media where section_id = %s", (ink.section,))
    pg.execute("delete from public.media_assets where id = any(%s)", ([w.figure, w.video],))
    pg.execute("delete from public.questions where id = any(%s)", ([w.q2, w.q3],))


def manifest(client: TestClient, key: SigningKey, w: Week, who: uuid.UUID | None = None) -> Any:
    res = client.get("/api/offline/week", params={"start": w.start.isoformat()}, headers=w.ink.h(key, who))
    assert res.status_code == 200, res.text
    assert res.headers["cache-control"] == "private, no-store"
    return res


def views(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {q["view"]["question"]["id"]: q["view"] for q in body["questions"]}


def test_the_week_holds_no_hidden_field_before_an_answer(client: TestClient, key: SigningKey, week: Week) -> None:
    w, ink = week, week.ink
    h = ink.h(key)
    # the student answers the first question of the tutor block (wrong): graded, so its explanation may come along
    res = client.post(f"/api/blocks/{w.tutor}/attempts", json={"question_id": ink.question, "chosen": "A"}, headers=h)
    assert res.status_code == 200, res.text
    stroke = {
        "id": "019a8c4e-0000-7000-8000-0000000000aa",
        "tool": "pen",
        "color": "ink",
        "size": 0.002,
        "points": [[0.1, 0.1, 0.5, 0], [0.2, 0.2, 0.5, 8]],
        "createdAt": int(time.time() * 1000),
        "updatedAt": int(time.time() * 1000),
    }
    assert (
        client.post(f"/api/ink/section/{ink.section}/changes", json={"base_rev": 0, "items": [stroke]}, headers=h)
    ).status_code == 200
    mine = client.post(
        "/api/notes",
        json={"kind": "note", "anchor_type": "section", "anchor_id": ink.section, "title": "my section note"},
        headers=h,
    )
    assert mine.status_code == 201
    client.post("/api/notebooks", json={"title": "Book"}, headers=h)
    client.post(
        "/api/notes",
        json={"kind": "note", "anchor_type": "section", "anchor_id": ink.section, "title": "their note"},
        headers=ink.h(key, ink.other),
    )

    res = manifest(client, key, w)
    raw = res.text
    body = res.json()
    # dates and plan
    assert body["start"] == w.start.isoformat() and body["end"] == (w.start + dt.timedelta(days=6)).isoformat()
    assert body["plan"]["days"][0]["date"] == w.start.isoformat() and len(body["plan"]["days"]) == 7
    assert body["plan"]["days"][1]["items"][0]["ref_id"] == ink.chapter
    # chapters through the reader's own loader (identical to GET /api/chapters/{id}); positions; media rules
    assert [c["id"] for c in body["chapters"]] == [ink.chapter]
    assert body["chapters"][0] == client.get(f"/api/chapters/{ink.chapter}", headers=h).json()
    assert {r["section_id"] for r in body["section_revisions"]} == {ink.section}
    assert body["positions"] == [{"chapter_id": ink.chapter, "last": None, "sections": []}]
    assert [f["media_id"] for f in body["figures"]] == [w.figure] and body["videos"] == []
    assert {s["media_id"]: s["reason"] for s in body["skipped_media"]} == {w.video: "youtube"}
    # questions: the tutor block only; q1 answered and explained, q2 bare
    assert [b["id"] for b in body["blocks"]] == [w.tutor] and body["exam_active"] is False
    # The block itself as GET /api/blocks/{id} serves it (the device opens the block offline from this copy).
    assert body["blocks"][0] == client.get(f"/api/blocks/{w.tutor}", headers=h).json()
    assert [(q["block_id"], q["n"]) for q in body["questions"]] == [(w.tutor, 1), (w.tutor, 2)]
    for q in body["questions"]:  # the same serializer as the online question endpoint
        online = client.get(f"/api/blocks/{q['block_id']}/questions/{q['n']}", headers=h).json()
        assert q["view"] == online
    v = views(body)
    assert set(v) == {ink.question, w.q2}
    assert v[ink.question]["attempt"]["chosen"] == "A" and v[ink.question]["attempt"]["correct"] is False
    assert v[ink.question]["explanation"]["explanation_html"] == "<p>because</p>"
    assert v[w.q2]["attempt"] is None and v[w.q2]["explanation"] is None
    assert not keys_of(v[w.q2]) & set(HIDDEN)
    # the scan: no hidden key anywhere but inside the answered question's explanation, no secret text at all
    rest = {**body, "questions": []}
    others = [view for qid, view in v.items() if qid != ink.question]
    assert not keys_of(rest) & set(HIDDEN)
    assert not keys_of(others) & set(HIDDEN)
    for secret in ("secret-two", "secret-three", w.q3, "B is right"):
        assert secret not in raw, secret
    # cards: the gate holds (q2 not answered)
    card_ids = {c["id"] for c in body["due"]["cards"]}
    assert ink.card in card_ids and w.gated_card not in card_ids
    # own notes, notebooks and ink only
    assert [n["title"] for n in body["notes"]["items"]] == ["my section note"]
    assert set(body["notes"]["counts"]) == {"notes", "highlights", "pages", "by_color", "by_anchor"}
    assert [b["title"] for b in body["notebooks"]] == ["Book"] and len(body["notebooks"][0]["pages"]) == 1
    layers = body["ink"]
    assert [(lay["surface_type"], lay["surface_id"]) for lay in layers] == [("section", ink.section)]
    assert layers[0]["layer"]["items"][0]["id"] == stroke["id"]
    # byte estimates and counts
    sizes = {s["part"]: s for s in body["parts"]}
    assert set(sizes) == {
        "plan",
        "chapters",
        "figures",
        "videos",
        "questions",
        "due",
        "notes",
        "notebooks",
        "ink",
        "pathway",
    }
    assert sizes["questions"]["count"] == 2 and sizes["figures"]["count"] == 1 and sizes["videos"]["count"] == 0
    assert all(s["bytes"] > 0 for s in body["parts"])
    assert body["bytes"] == sum(s["bytes"] for s in body["parts"])

    # the other student's week has none of it
    theirs = manifest(client, key, w, ink.other).json()
    assert theirs["questions"] == [] and theirs["blocks"] == [] and theirs["ink"] == []
    assert theirs["notes"]["items"] == []  # no chapter planned for them


def test_an_active_exam_session_removes_every_explanation(
    client: TestClient, key: SigningKey, week: Week, pg: psycopg.Connection[Any]
) -> None:
    w, ink = week, week.ink
    assert (
        client.post(
            f"/api/blocks/{w.tutor}/attempts", json={"question_id": ink.question, "chosen": "B"}, headers=ink.h(key)
        ).status_code
        == 200
    )
    assert views(manifest(client, key, w).json())[ink.question]["explanation"] is not None
    pg.execute(
        "insert into public.block_sections (block_id, user_id, n, first_index, count, time_limit_s, started_at,"
        " deadline_at) values (%s, %s, 1, 0, 1, 3600, now(), now() + interval '1 hour')",
        (w.timed, ink.owner),
    )
    before = pg.execute(
        "select n, started_at, deadline_at, ended_at, paused_at from public.block_sections where block_id = %s",
        (w.timed,),
    ).fetchall()
    body = manifest(client, key, w).json()
    assert body["exam_active"] is True
    v = views(body)
    assert v[ink.question]["explanation"] is None
    assert not keys_of(body) & set(HIDDEN)
    assert all(b["id"] != w.timed for b in body["blocks"])
    # the manifest wrote nothing: the session's clock rows are as they were
    after = pg.execute(
        "select n, started_at, deadline_at, ended_at, paused_at from public.block_sections where block_id = %s",
        (w.timed,),
    ).fetchall()
    assert after == before


def test_start_is_validated_and_the_read_is_rate_limited_like_the_others(
    client: TestClient, key: SigningKey, week: Week
) -> None:
    h = week.ink.h(key)
    far = client.get("/api/offline/week", params={"start": "2001-01-01"}, headers=h)
    assert far.status_code == 422 and far.json()["error"]["code"] == "offline_start_out_of_range"
    assert client.get("/api/offline/week", params={"start": "not-a-date"}, headers=h).status_code == 422
    assert client.get("/api/offline/week").status_code == 401
    default = client.get("/api/offline/week", headers=h)
    assert default.status_code == 200 and default.json()["timezone"] == "UTC"


def test_the_manifest_writes_nothing(
    client: TestClient, key: SigningKey, week: Week, pg: psycopg.Connection[Any]
) -> None:
    """An expired exam session would be graded by any timed request; the manifest leaves it (and all else) alone."""
    w, ink = week, week.ink
    pg.execute(
        "insert into public.block_sections (block_id, user_id, n, first_index, count, time_limit_s, started_at,"
        " deadline_at) values (%s, %s, 1, 0, 1, 60, now() - interval '2 hours', now() - interval '1 hour')",
        (w.timed, ink.owner),
    )
    tables = ("blocks", "attempts", "block_sections", "plan_items", "profiles", "sync_ops", "notes", "notebooks")

    def snapshot() -> dict[str, Any]:
        out: dict[str, Any] = {
            t: pg.execute(f"select count(*) from public.{t} where user_id = %s", (ink.owner,)).fetchone()  # noqa: S608
            for t in tables
        }
        out["timed"] = pg.execute(
            "select b.finished_at, b.score, s.ended_at from public.blocks b join public.block_sections s"
            " on s.block_id = b.id where b.id = %s",
            (w.timed,),
        ).fetchall()
        return out

    before = snapshot()
    manifest(client, key, w)
    assert snapshot() == before
    assert before["timed"][0][0] is None  # still not graded
