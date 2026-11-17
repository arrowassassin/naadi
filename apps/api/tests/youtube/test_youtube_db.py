"""The YouTube mapper, refresh/drop, approval queue and admin routes against a real Postgres (fake Data API, fake
embedder: no network, no key). Students must never see an unapproved video or a candidate."""

from __future__ import annotations

import datetime as dt
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from naadi_api.config_api.schemas import YoutubeRules
from naadi_api.db import Database
from naadi_api.youtube import mapper, queue
from naadi_api.youtube.client import media_id
from tests.db.rls import DB_MARKS, as_user
from tests.mediadb import World, admin_headers, student

from .fakes import Exhausted, FakeYouTube, WordEmbedder, item

pytestmark = DB_MARKS

CH = mapper.Channel("UCfake0000000000000000aa", "Fake Channel", ("cardio",))
PLAYLIST = "UUfake0000000000000000aa"


def vid(world: World, n: int) -> str:
    # 11 characters, unique per test world (the tag is hex)
    return f"{world.tag}{n:03d}"[:11]


def media(world: World, n: int) -> str:
    return media_id(vid(world, n))


def api_for(world: World, **extra: Any) -> FakeYouTube:
    heart, nephron = f"Cardiac {world.tag} failure", f"Nephron {world.tag} physiology"
    catalog = {
        vid(world, 1): item(vid(world, 1), f"{heart} explained", "preload afterload"),
        vid(world, 2): item(vid(world, 2), f"{nephron} basics", ""),
        vid(world, 3): item(vid(world, 3), f"{heart} in depth", "", embeddable=False),
        vid(world, 4): item(vid(world, 4), f"{heart} private", "", privacy="private"),
        vid(world, 5): item(vid(world, 5), "Cooking pasta at home", "boil water salt"),
    }
    return FakeYouTube({PLAYLIST: [vid(world, n) for n in range(1, 6)]}, catalog, **extra)


@pytest.fixture
def db(url: str) -> Database:
    return Database(SecretStr(url))


def rows(pg: psycopg.Connection[Any], world: World, status: str | None = None) -> list[tuple[Any, ...]]:
    q = "select media_id, section_id, status, score from public.video_candidates where media_id like %s"
    args: list[Any] = [f"yt-{world.tag}%"]
    if status:
        q += " and status = %s"
        args.append(status)
    return pg.execute(q + " order by media_id, section_id", args).fetchall()


async def run(db: Database, api: Any, **rules: Any) -> mapper.Report:
    return await mapper.run(db, api, WordEmbedder(), YoutubeRules(**rules), [CH])


async def test_run_keeps_embeddable_public_videos_and_writes_pending_candidates(
    db: Database, world: World, pg: psycopg.Connection[Any]
) -> None:
    api = api_for(world)
    rep = await run(db, api)
    assert rep.listed == 5 and rep.new_videos == 3 and not rep.stopped_early  # 3 and 4 are not playable; 5 is public
    got = {(m, s) for m, s, st, _ in rows(pg, world, "pending")}
    assert (media(world, 1), world.section) in got and (media(world, 2), world.section2) in got
    assert not any(m in (media(world, 3), media(world, 4)) for m, *_ in rows(pg, world))
    m = pg.execute(
        "select kind, source_id, review_status, youtube_id, embeddable, title is not null,"
        " metadata_fetched_at is not null from public.media_assets where id = %s",
        (media(world, 1),),
    ).fetchone()
    assert m == ("video_youtube", "youtube", "unreviewed", vid(world, 1), True, True, True)
    stored = {r[0] for r in pg.execute("select id from public.media_assets where id like %s", (f"yt-{world.tag}%",))}
    assert {media(world, 1), media(world, 2)} <= stored and not {media(world, 3), media(world, 4)} & stored
    # a student sees neither the unapproved video nor any candidate
    with as_user(pg, world.a) as conn:
        assert conn.execute(
            "select count(*) from public.media_assets where id like %s", (f"yt-{world.tag}%",)
        ).fetchone() == (0,)
        with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
            conn.execute("select 1 from public.video_candidates")


async def test_a_second_run_asks_the_api_about_nothing_it_already_knows(db: Database, world: World) -> None:
    api = api_for(world)
    await run(db, api)
    api.calls.clear()
    rep = await run(db, api)
    assert rep.candidates_added == 0 and ("videos", 5) not in api.calls
    # only the three that were stored are skipped; unmatched ids may be looked at again, never stored
    assert all(n <= 5 for k, n in api.calls if k == "videos")


async def test_room_per_section_is_respected(db: Database, world: World, pg: psycopg.Connection[Any]) -> None:
    extra = api_for(world)
    heart = f"Cardiac {world.tag} failure"
    for n in (6, 7):
        extra.catalog[vid(world, n)] = item(vid(world, n), f"{heart} part {n}", "")
    extra.playlists[PLAYLIST] += [vid(world, 6), vid(world, 7)]
    await run(db, extra, max_candidates_per_section=2)
    per_section = [r for r in rows(pg, world, "pending") if r[1] == world.section]
    assert len(per_section) == 2


async def test_quota_exhaustion_stops_early_and_reports_units(db: Database, world: World) -> None:
    rep = await run(db, Exhausted({PLAYLIST: [vid(world, 1)]}, {}, budget=5))
    assert rep.stopped_early and rep.quota_used == 1 and rep.candidates_added == 0


async def test_approve_links_the_video_and_students_then_see_it(
    db: Database, world: World, pg: psycopg.Connection[Any]
) -> None:
    await run(db, api_for(world))
    out = await queue.approve(db, media(world, 1), reviewer=world.admin, start_s=30, end_s=95)
    assert (out.section_id, out.start_s, out.end_s) == (world.section, 30, 95)
    sm = pg.execute(
        "select start_s, end_s from public.section_media where media_id = %s", (media(world, 1),)
    ).fetchall()
    assert sm == [(30, 95)]
    with as_user(pg, world.b) as conn:
        assert conn.execute(
            "select count(*) from public.media_assets where id = %s", (media(world, 1),)
        ).fetchone() == (1,)
        assert conn.execute(
            "select count(*) from public.media_assets where id = %s", (media(world, 2),)
        ).fetchone() == (0,)
    st = pg.execute(
        "select status, reviewed_by from public.video_candidates where media_id = %s and section_id = %s",
        (media(world, 1), world.section),
    ).fetchone()
    assert st == ("approved", world.admin)
    with pytest.raises(queue.QueueError) as e:
        await queue.approve(db, media(world, 2), reviewer=world.admin, start_s=50, end_s=10)
    assert e.value.code == "video_bad_range"
    with pytest.raises(queue.QueueError) as e2:
        await queue.approve(db, "yt-nope", reviewer=world.admin)
    assert e2.value.code == "video_candidate_not_found"


async def test_reject_keeps_the_id_drops_metadata_and_is_never_proposed_again(
    db: Database, world: World, pg: psycopg.Connection[Any]
) -> None:
    api = api_for(world)
    await run(db, api)
    await queue.reject(db, media(world, 1), reviewer=world.admin)
    m = pg.execute(
        "select review_status, title, attribution, metadata_fetched_at from public.media_assets where id = %s",
        (media(world, 1),),
    ).fetchone()
    assert m == ("rejected", None, None, None)
    assert {s for _, _, s, _ in rows(pg, world) if True} >= {"rejected"}
    before = len(rows(pg, world))
    await run(db, api)
    assert len(rows(pg, world)) == before
    with pytest.raises(queue.QueueError) as e:
        await queue.approve(db, media(world, 1), reviewer=world.admin)
    assert e.value.code == "video_rejected"


async def test_refresh_updates_fresh_metadata_and_drops_what_is_gone(
    db: Database, world: World, pg: psycopg.Connection[Any]
) -> None:
    api = api_for(world)
    await run(db, api)
    await queue.approve(db, media(world, 1), reviewer=world.admin)
    await queue.approve(db, media(world, 2), reviewer=world.admin)
    old = dt.datetime.now(dt.UTC) - dt.timedelta(days=26)
    pg.execute("update public.media_assets set metadata_fetched_at = %s where id like %s", (old, f"yt-{world.tag}%"))
    # video 1 got a new title; video 2 is now private
    api.catalog[vid(world, 1)] = item(vid(world, 1), "Renamed lecture", "", duration="PT5M")
    api.catalog[vid(world, 2)] = item(vid(world, 2), "Nephron basics", "", privacy="private")
    rep = await run(db, api)
    assert rep.refreshed >= 1 and rep.dropped >= 1
    one = pg.execute(
        "select title, duration_s, review_status, metadata_fetched_at > %s from public.media_assets where id = %s",
        (old, media(world, 1)),
    ).fetchone()
    assert one == ("Renamed lecture", 300, "approved", True)
    two = pg.execute(
        "select title, caption, attribution, review_status, embeddable, metadata_fetched_at"
        " from public.media_assets where id = %s",
        (media(world, 2),),
    ).fetchone()
    assert two == (None, None, None, "rejected", False, None)
    assert pg.execute(
        "select count(*) from public.section_media where media_id = %s", (media(world, 2),)
    ).fetchone() == (0,)
    assert pg.execute(
        "select status from public.video_candidates where media_id = %s", (media(world, 2),)
    ).fetchone() == ("stale",)
    with as_user(pg, world.a) as conn:  # a dropped video is gone for students at once
        assert conn.execute(
            "select count(*) from public.media_assets where id = %s", (media(world, 2),)
        ).fetchone() == (0,)


async def test_fresh_metadata_is_not_refetched(db: Database, world: World) -> None:
    api = api_for(world)
    await run(db, api)
    api.asked.clear()
    await run(db, api)
    # nothing stored is older than refresh_days: only never-stored ids (3, 4, 5) are looked at again
    assert not {vid(world, 1), vid(world, 2)} & set(api.asked)


def test_admin_routes_gate_list_approve_reject_and_add(
    client: TestClient, key: Any, world: World, pg: psycopg.Connection[Any]
) -> None:
    api = api_for(world)
    client.app.state.youtube_api = api  # type: ignore[attr-defined]
    admin, stu = admin_headers(key, world.admin), student(key, world.a)
    for method, path in (("get", "/api/admin/youtube/candidates"), ("post", "/api/admin/youtube/add")):
        assert getattr(client, method)(path).status_code == 401
        assert getattr(client, method)(path, headers=stu).status_code == 403
    body = {"url": f"https://youtu.be/{vid(world, 1)}", "section_id": world.section}
    r = client.post("/api/admin/youtube/add", json=body, headers=admin)
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "pending" and r.json()["title"].startswith("Cardiac")
    bad = client.post(
        "/api/admin/youtube/add", json={**body, "url": f"https://youtu.be/{vid(world, 3)}"}, headers=admin
    )
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "video_not_embeddable"
    lst = client.get("/api/admin/youtube/candidates", headers=admin).json()
    assert any(c["media_id"] == media(world, 1) for c in lst["items"]) and lst["pending"] >= 1
    ok = client.post(
        f"/api/admin/youtube/candidates/{media(world, 1)}/approve", json={"start_s": 5, "end_s": 50}, headers=admin
    )
    assert ok.status_code == 200 and ok.json()["section_id"] == world.section
    assert (
        client.post(f"/api/admin/youtube/candidates/{media(world, 1)}/approve", json={}, headers=stu).status_code == 403
    )
    rej = client.post(f"/api/admin/youtube/candidates/{media(world, 1)}/reject", headers=admin)
    assert rej.status_code == 204
    gone = client.post("/api/admin/youtube/add", json=body, headers=admin)
    assert gone.status_code == 409 and gone.json()["error"]["code"] == "video_rejected"
