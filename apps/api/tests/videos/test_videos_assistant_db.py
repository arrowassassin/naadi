"""The assistant's video tools against a real Postgres and the real fastembed models: the catalogue is indexed for
hybrid search, `suggest_video` lists candidates and shows only the ids the model picked that the student may see,
`get_page` on a video gives the model its title, channel, subject, topics and time, and `open_in_canvas` opens a
Commons file. Only approved media are visible (RLS)."""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from pydantic import SecretStr

from naadi_api.agent.tools import all_tools
from naadi_api.agent.tools.executor import run_tool_call
from naadi_api.agent.tools.registry import PageRef, ToolContext
from naadi_api.db import Database
from naadi_api.embeddings.engine import Engine
from naadi_api.embeddings.index import reconcile
from naadi_api.llm import CallContext
from naadi_api.videos.query import stem
from tests.db.rls import DB_MARKS
from tests.embeddings.conftest import engine  # noqa: F401
from tests.videos.test_videos_db import VW, vw  # noqa: F401

pytestmark = [*DB_MARKS, pytest.mark.asyncio]


@pytest.fixture
async def db(url: str) -> AsyncIterator[Database]:
    d = Database(SecretStr(url), max_size=2)
    await d.open()
    yield d
    await d.close()


@pytest.fixture(autouse=True)
def _drop_index_rows(pg: Any, vw: VW) -> Iterator[None]:  # noqa: F811
    yield
    pg.execute("delete from public.embeddings where ref_type = 'video' and ref_id like %s", (f"yt-{vw.w.tag}-%",))


def ctx(db: Database, user: uuid.UUID, page: PageRef | None = None, *, videos: bool = True) -> ToolContext:
    return ToolContext(
        user_id=user,
        is_admin=False,
        run_id="test",
        thread_id=None,
        db=db,
        exam_active=False,
        page=page or PageRef(),
        gateway=None,  # type: ignore[arg-type]  # no tool here calls a model
        call=CallContext(purpose="chat"),
        flags={"videos": videos},
        store=None,
    )


async def call(tc: ToolContext, name: str, args: dict[str, Any]) -> Any:
    return await run_tool_call(tc, name, args, {t.name for t in all_tools()})


async def _index(db: Database, engine: Engine, vw: VW) -> None:  # noqa: F811
    await reconcile(db, engine, kinds=["videos"], ref_ids=[vw.mid(i) for i in range(1, 7)])


def _candidates(content: str, vw: VW) -> list[str]:  # noqa: F811
    return re.findall(rf"media_id (yt-{vw.w.tag}-\d+):", content)


async def test_video_docs_hold_catalogue_metadata_and_skip_unapproved(
    vw: VW,  # noqa: F811
    db: Database,
    engine: Engine,  # noqa: F811
    pg: Any,
) -> None:
    await _index(db, engine, vw)
    rows = dict(
        pg.execute(
            "select ref_id, content from public.embeddings where ref_type = 'video' and ref_id like %s",
            (f"yt-{vw.w.tag}-%",),
        ).fetchall()
    )
    assert sorted(rows) == [vw.mid(n) for n in (1, 2, 3, 4, 5)]  # 6 is unapproved: never indexed
    assert rows[vw.mid(1)] == f"Plain video 1\nTopics: Topic {vw.word}\nChapter: Chapter 1\n{vw.subject} · Step 1"
    assert rows[vw.mid(4)].endswith("Step 1 · Dirty Medicine")  # the channel name, from config
    assert "Step 2 CK · Wikimedia Commons" in rows[vw.mid(5)]


async def test_suggest_video_lists_candidates_then_shows_only_visible_picks(
    vw: VW,  # noqa: F811
    db: Database,
    engine: Engine,  # noqa: F811
    pg: Any,
) -> None:
    await _index(db, engine, vw)
    tc = ctx(db, vw.w.a)
    r = await call(tc, "suggest_video", {"query": f"Topic {vw.word}"})
    assert r.status == "ok", r.content
    assert r.blocks == []  # step 1 shows nothing: the model picks
    found = _candidates(r.content, vw)
    assert vw.mid(1) in found and vw.mid(6) not in found
    assert "untrusted_document" in r.content and f"topics: Topic {vw.word}" in r.content

    # step 2: unknown and unapproved ids are dropped, never shown; at most 3, in the model's order
    picked = [vw.mid(2), vw.mid(6), "yt-made-up", vw.mid(1)]
    shown = await call(tc, "suggest_video", {"media_ids": picked})
    assert [b["data"]["media_id"] for b in shown.blocks] == [vw.mid(2)]
    assert shown.blocks[0]["data"] == {
        "provider": "youtube",
        "video_id": f"{vw.w.tag}002",
        "media_id": vw.mid(2),
        "title": f"About {vw.word}",
        "start_s": None,
        "step": "step1",
        "subject": vw.subject,
    }
    assert "2 unknown media id(s) skipped" in shown.content
    refused = await call(tc, "suggest_video", {"media_ids": [vw.mid(6), "yt-made-up"]})
    assert refused.status == "refused" and refused.blocks == []

    # RLS at query time: a video indexed while approved and then withdrawn is no longer a candidate
    pg.execute("update public.media_assets set review_status = 'unreviewed' where id = %s", (vw.mid(1),))
    again = await call(tc, "suggest_video", {"query": f"Topic {vw.word}"})
    assert vw.mid(1) not in _candidates(again.content, vw)


async def test_suggest_video_shows_a_commons_file_with_its_licence(vw: VW, db: Database) -> None:  # noqa: F811
    r = await call(ctx(db, vw.w.a), "suggest_video", {"media_ids": [vw.mid(5)]})
    assert [b["data"]["media_id"] for b in r.blocks] == [vw.mid(5)]
    d = r.blocks[0]["data"]
    assert d["provider"] == "commons" and "video_id" not in d
    assert d["file_url"] == "https://f.test/a.webm" and d["page_url"] == "https://c.test/File:a.webm"
    assert d["attribution"] == "CC BY 4.0 by Someone" and d["duration_s"] == 90
    assert d["licence"] == {"license_short_name": "CC BY 4.0", "license_url": "https://l.test", "artist": "Someone"}
    assert "Wikimedia Commons" in r.content


async def test_suggest_video_needs_the_flag_and_a_query(vw: VW, db: Database) -> None:  # noqa: F811
    off = await call(ctx(db, vw.w.a, videos=False), "suggest_video", {"query": vw.word})
    assert off.blocks == [] and off.content == "No video library available."
    empty = await call(ctx(db, vw.w.a), "suggest_video", {})
    assert empty.status == "refused"


async def test_get_page_on_a_video_gives_title_channel_topics_and_time(vw: VW, db: Database) -> None:  # noqa: F811
    page = PageRef(type="video", id=vw.mid(1), video={"media_id": vw.mid(1), "t": 75})
    r = await call(ctx(db, vw.w.a, page), "get_page", {"type": "current"})
    assert r.status == "ok", r.content
    assert "Video: Plain video 1" in r.content and f"Subject: {vw.subject} (Step 1)" in r.content
    assert f"Topics: Topic {vw.word}" in r.content and "The student is at 1:15." in r.content
    four = await call(ctx(db, vw.w.a), "get_page", {"type": "video", "id": vw.mid(4)})
    assert "Channel: Dirty Medicine" in four.content
    hidden = await call(ctx(db, vw.w.a), "get_page", {"type": "video", "id": vw.mid(6)})
    assert hidden.status == "refused"


async def test_open_in_canvas_opens_a_commons_file_and_refuses_unapproved(vw: VW, db: Database) -> None:  # noqa: F811
    args = {"kind": "video", "title": "Heart", "video_media_id": vw.mid(5)}
    r = await call(ctx(db, vw.w.a), "open_in_canvas", args)
    data = r.canvas[0]["data"]
    assert data["provider"] == "commons" and data["file_url"] == "https://f.test/a.webm"
    hidden = await call(ctx(db, vw.w.a), "open_in_canvas", args | {"video_media_id": vw.mid(6)})
    assert hidden.status == "refused"


async def test_stem_matches_plural_and_singular() -> None:
    assert [stem(w) for w in ("blockers", "diseases", "abscess", "loops", "gas", "murmur")] == [
        "blocker",
        "diseas",
        "abscess",
        "loop",
        "gas",
        "murmur",
    ]
    # "es" only after s, x, z, ch, sh: "genes" must not become "gen" (antigen, general)
    assert [stem(w) for w in ("genes", "viruses", "boxes", "branches", "antigen", "notes", "types")] == [
        "gene",
        "virus",
        "box",
        "branch",
        "antigen",
        "note",
        "type",
    ]
