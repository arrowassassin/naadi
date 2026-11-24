"""The Videos API against a real Postgres: filters, search ranking, coverage, RLS (unapproved media hidden, own
watches only) and the idempotent watched toggle. Rows are tagged per test and removed afterwards."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from tests.auth.jwt_helpers import SigningKey
from tests.db.fixtures import insert_row
from tests.db.rls import DB_MARKS
from tests.mediadb import World, student

pytestmark = DB_MARKS

DIRTY = "UCZaDAUF7UEcRXIFvGZu3O9Q"  # Dirty Medicine in config/video_channels.yaml


@dataclass
class VW:
    w: World
    word: str  # a search word unique to this test
    subject: str

    def mid(self, n: int) -> str:
        return f"yt-{self.w.tag}-{n}"


def _media(pg: psycopg.Connection[Any], vw: VW, n: int, **over: Any) -> None:
    row: dict[str, Any] = {
        "id": vw.mid(n),
        "kind": "video_youtube",
        "source_id": "youtube",
        "title": f"Plain video {n}",
        "youtube_id": f"{vw.w.tag}{n:03d}",
        "review_status": "approved",
        "attribution": "Video by someone on YouTube",
    }
    row |= over
    insert_row(pg, "media_assets", row)


def _catalog(pg: psycopg.Connection[Any], vw: VW, n: int, **over: Any) -> None:
    row: dict[str, Any] = {
        "media_id": vw.mid(n),
        "subject": vw.subject,
        "step": "step1",
        "style": "sketch",
        "provider": "youtube",
        "sort": n,
        "topic_ids": [],
    }
    row |= over
    insert_row(pg, "video_catalog", row)


def _topic(pg: psycopg.Connection[Any], vw: VW, n: int, **over: Any) -> None:
    row: dict[str, Any] = {
        "id": f"{vw.w.tag}-t{n}",
        "title": f"Topic {n}",
        "subject": vw.subject,
        "step": "step1",
        "chapter": f"Chapter {n}",
        "sort": n,
        "media_ids": [],
        "chapter_media_ids": [],
    }
    row |= over
    insert_row(pg, "video_topics", row)


@pytest.fixture
def vw(world: World, pg: psycopg.Connection[Any]) -> Iterator[VW]:
    v = VW(world, f"w{world.tag}", f"Subj{world.tag}")

    def t(n: int) -> str:
        return f"{world.tag}-t{n}"

    # 1: topic title has the word (rank 0); 2: video title (1); 3: topic chapter (2); 4: only the channel matches
    _media(pg, v, 1)
    _media(pg, v, 2, title=f"About {v.word}")
    _media(pg, v, 3)
    _media(pg, v, 4, channel_id=DIRTY)
    _media(
        pg,
        v,
        5,
        kind="video_file",
        source_id=f"t{world.tag}-msrc",
        youtube_id=None,
        url="https://f.test/a.webm",
        page_url="https://c.test/File:a.webm",
        duration_s=90,
        attribution="CC BY 4.0 by Someone",
        licence=Jsonb({"license_short_name": "CC BY 4.0", "license_url": "https://l.test", "artist": "Someone"}),
    )
    _media(pg, v, 6, review_status="unreviewed")
    _catalog(pg, v, 1, topic_ids=[t(1)], core=True)
    _catalog(pg, v, 2, topic_ids=[t(2)], style="whiteboard")
    _catalog(pg, v, 3, topic_ids=[t(3)])
    _catalog(pg, v, 4)
    _catalog(pg, v, 5, provider="commons", step="step2ck", style="animation", topic_ids=[t(6)])
    _catalog(pg, v, 6, topic_ids=[t(5)])
    _topic(pg, v, 1, title=f"Topic {v.word}", media_ids=[v.mid(1)])
    _topic(pg, v, 2, media_ids=[v.mid(2)])
    _topic(pg, v, 3, chapter=f"Chapter {v.word}", media_ids=[v.mid(3)], chapter_media_ids=[v.mid(2)])
    _topic(pg, v, 4)  # a gap
    _topic(pg, v, 5, media_ids=[v.mid(6)], chapter_media_ids=[v.mid(6)])  # only an unapproved video: a gap
    _topic(pg, v, 6, step="step2ck", media_ids=[v.mid(5)])
    _topic(pg, v, 7, chapter_media_ids=[v.mid(1)])  # covered by chapter only
    yield v
    pg.execute("delete from public.video_topics where id like %s", (f"{world.tag}-t%",))


def ids(res: Any, v: VW) -> list[str]:
    assert res.status_code == 200, res.text
    return [i["media_id"].removeprefix(f"yt-{v.w.tag}-") for i in res.json()["items"]]


def get(client: TestClient, key: SigningKey, v: VW, path: str = "", **params: Any) -> Any:
    params = {"subject": v.subject, **params} if path == "" else params
    return client.get(f"/api/videos{path}", params=params, headers=student(key, v.w.a))


def test_list_hides_unapproved_and_shapes_both_providers(client: TestClient, key: SigningKey, vw: VW) -> None:
    res = get(client, key, vw)
    assert ids(res, vw) == ["1", "2", "3", "4", "5"]  # 6 is unapproved: invisible
    assert res.headers["cache-control"] == "private, no-store"
    assert res.json()["total"] == 5 and res.json()["next"] is None
    by = {i["media_id"].rsplit("-", 1)[1]: i for i in res.json()["items"]}
    yt, cm = by["1"], by["5"]
    assert yt["provider"] == "youtube" and yt["youtube_id"] == f"{vw.w.tag}001" and yt["file_url"] is None
    assert yt["topics"] == [{"id": f"{vw.w.tag}-t1", "title": f"Topic {vw.word}", "chapter": "Chapter 1"}]
    assert yt["core"] is True and yt["watched"] is False
    assert by["4"]["channel_name"] == "Dirty Medicine"
    assert cm["provider"] == "commons" and cm["file_url"] == "https://f.test/a.webm" and cm["youtube_id"] is None
    assert cm["page_url"] == "https://c.test/File:a.webm" and cm["licence"]["license_short_name"] == "CC BY 4.0"
    assert cm["attribution"] == "CC BY 4.0 by Someone" and cm["channel_name"] == "Wikimedia Commons"
    assert cm["duration_s"] == 90 and cm["step"] == "step2ck"


def test_filters_and_paging(client: TestClient, key: SigningKey, vw: VW) -> None:
    assert ids(get(client, key, vw, step="step2ck"), vw) == ["5"]
    assert ids(get(client, key, vw, step="step1"), vw) == ["1", "2", "3", "4"]
    assert ids(get(client, key, vw, style="whiteboard"), vw) == ["2"]
    assert ids(get(client, key, vw, core="true"), vw) == ["1"]
    assert ids(get(client, key, vw, channel=DIRTY), vw) == ["4"]
    assert ids(get(client, key, vw, channel="commons"), vw) == ["5"]
    first = get(client, key, vw, step="step1", limit=3)
    assert ids(first, vw) == ["1", "2", "3"] and first.json()["next"] == "3" and first.json()["total"] == 4
    last = get(client, key, vw, step="step1", limit=3, cursor="3")
    assert ids(last, vw) == ["4"] and last.json()["next"] is None
    assert get(client, key, vw, limit=201).status_code == 422
    assert get(client, key, vw, step="step3").status_code == 422


def test_search_ranks_topic_then_title_then_chapter(client: TestClient, key: SigningKey, vw: VW) -> None:
    # video 3's chapter, video 2's title and video 1's topic title all carry the word; sort order would be 1,2,3
    # so shuffle it: give video 3 the best sort and check rank still wins
    assert ids(get(client, key, vw, q=vw.word.upper()), vw) == ["1", "2", "3"]
    assert ids(get(client, key, vw, q=f"{vw.word} nomatch"), vw) == []  # every word must match somewhere
    assert ids(get(client, key, vw, q="dirty medicine"), vw) == ["4"]  # channel name
    assert ids(get(client, key, vw, q=vw.subject.lower()), vw) == ["1", "2", "3", "4", "5"]  # subject


def test_search_rank_beats_catalogue_order(
    client: TestClient, key: SigningKey, vw: VW, pg: psycopg.Connection[Any]
) -> None:
    pg.execute("update public.video_catalog set sort = 10 - sort where media_id like %s", (f"yt-{vw.w.tag}-%",))
    assert ids(get(client, key, vw, q=vw.word), vw) == ["1", "2", "3"]
    assert ids(get(client, key, vw), vw) == ["5", "4", "3", "2", "1"]


def test_topics_coverage_ignores_unapproved_videos(client: TestClient, key: SigningKey, vw: VW) -> None:
    res = get(client, key, vw, "/topics", subject=vw.subject)
    assert res.status_code == 200 and res.headers["cache-control"] == "private, no-store"
    by = {t["id"].rsplit("-t", 1)[1]: t for t in res.json()["items"]}
    assert {n: t["coverage"] for n, t in by.items()} == {
        "1": "topic",
        "2": "topic",
        "3": "topic",
        "4": "gap",
        "5": "gap",
        "6": "topic",
        "7": "chapter",
    }
    assert by["5"]["media_ids"] == [] and by["5"]["chapter_media_ids"] == []
    assert by["3"]["chapter_media_ids"] == [vw.mid(2)] and by["1"]["media_ids"] == [vw.mid(1)]
    step2 = get(client, key, vw, "/topics", subject=vw.subject, step="step2ck").json()["items"]
    assert [t["id"].rsplit("-t", 1)[1] for t in step2] == ["6"]


def test_overview_counts_and_channels(client: TestClient, key: SigningKey, vw: VW) -> None:
    def mine(res: Any) -> dict[str, dict[str, int]]:
        assert res.status_code == 200 and res.headers["cache-control"] == "private, no-store"
        return {s["step"]: s for s in res.json()["subjects"] if s["subject"] == vw.subject}

    assert client.put(f"/api/videos/{vw.mid(1)}/watched", headers=student(key, vw.w.a)).status_code == 204
    res = get(client, key, vw, "/overview")
    s = mine(res)
    assert (s["step1"]["videos"], s["step1"]["topics"], s["step1"]["watched"]) == (4, 6, 1)
    assert (s["step1"]["topics_direct"], s["step1"]["topics_chapter"]) == (3, 1)
    assert (s["step2ck"]["videos"], s["step2ck"]["topics_direct"], s["step2ck"]["watched"]) == (1, 1, 0)
    chans = {c["id"]: c for c in res.json()["channels"]}
    assert chans[DIRTY]["name"] == "Dirty Medicine" and chans["commons"]["name"] == "Wikimedia Commons"
    other = client.get("/api/videos/overview", headers=student(key, vw.w.b))
    assert mine(other)["step1"]["watched"] == 0  # another student's ticks do not show


def test_watched_toggle_is_idempotent_and_private(
    client: TestClient, key: SigningKey, vw: VW, pg: psycopg.Connection[Any]
) -> None:
    a, b = student(key, vw.w.a), student(key, vw.w.b)
    url = f"/api/videos/{vw.mid(2)}/watched"
    assert client.put(url, headers=a).status_code == 204 and client.put(url, headers=a).status_code == 204
    assert pg.execute("select count(*) from public.video_watches where media_id = %s", (vw.mid(2),)).fetchone() == (1,)
    assert ids(get(client, key, vw, unwatched="true"), vw) == ["1", "3", "4", "5"]
    mine = {
        i["media_id"]: i["watched"]
        for i in client.get("/api/videos", params={"subject": vw.subject}, headers=a).json()["items"]
    }
    assert mine[vw.mid(2)] is True
    theirs = client.get("/api/videos", params={"subject": vw.subject}, headers=b).json()["items"]
    assert not any(i["watched"] for i in theirs)
    assert client.delete(url, headers=b).status_code == 204  # B's delete cannot touch A's row
    assert pg.execute("select count(*) from public.video_watches where media_id = %s", (vw.mid(2),)).fetchone() == (1,)
    assert client.delete(url, headers=a).status_code == 204 and client.delete(url, headers=a).status_code == 204
    assert pg.execute("select count(*) from public.video_watches where media_id = %s", (vw.mid(2),)).fetchone() == (0,)


def test_watching_an_unapproved_unknown_or_non_catalogue_video_is_404(
    client: TestClient, key: SigningKey, vw: VW, pg: psycopg.Connection[Any]
) -> None:
    a = student(key, vw.w.a)
    _media(pg, vw, 7)  # approved but never put in the catalogue
    for target in (vw.mid(6), vw.mid(7), f"yt-{vw.w.tag}-nope"):
        res = client.put(f"/api/videos/{target}/watched", headers=a)
        assert res.status_code == 404 and res.json()["error"]["code"] == "video_not_found", target
    assert pg.execute(
        "select count(*) from public.video_watches where media_id like %s", (f"yt-{vw.w.tag}-%",)
    ).fetchone() == (0,)
    assert client.put(f"/api/videos/{vw.mid(1)}/watched").status_code == 401
