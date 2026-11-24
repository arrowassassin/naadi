"""The assistant's video tools against a real Postgres: `suggest_video` ranks the catalogue like the Videos page and
shows both providers, `get_page` on a video gives the model its title, channel, subject, topics and time, and
`open_in_canvas` opens a Commons file. Only approved media are visible (RLS)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from pydantic import SecretStr

from naadi_api.agent.tools import all_tools
from naadi_api.agent.tools.executor import run_tool_call
from naadi_api.agent.tools.registry import PageRef, ToolContext
from naadi_api.db import Database
from naadi_api.llm import CallContext
from tests.db.rls import DB_MARKS
from tests.videos.test_videos_db import VW, vw  # noqa: F401

pytestmark = [*DB_MARKS, pytest.mark.asyncio]


@pytest.fixture
async def db(url: str) -> AsyncIterator[Database]:
    d = Database(SecretStr(url), max_size=2)
    await d.open()
    yield d
    await d.close()


def ctx(db: Database, user: uuid.UUID, page: PageRef | None = None) -> ToolContext:
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
        flags={},
        store=None,
    )


async def call(tc: ToolContext, name: str, args: dict[str, Any]) -> Any:
    return await run_tool_call(tc, name, args, {t.name for t in all_tools()})


async def test_suggest_video_ranks_like_the_videos_page_and_hides_unapproved(vw: VW, db: Database) -> None:  # noqa: F811
    r = await call(ctx(db, vw.w.a), "suggest_video", {"topic": vw.word})
    assert r.status == "ok", r.content
    # topic title > video title > chapter (the channel-only match 4 and the unapproved 6 are left out: max 3)
    assert [b["data"]["media_id"] for b in r.blocks] == [vw.mid(1), vw.mid(2), vw.mid(3)]
    first = r.blocks[0]["data"]
    assert first == {
        "provider": "youtube",
        "video_id": f"{vw.w.tag}001",
        "media_id": vw.mid(1),
        "title": "Plain video 1",
        "start_s": None,
        "step": "step1",
        "subject": vw.subject,
    }
    # the model reads a short framed list it can cite by media id
    assert "untrusted_document" in r.content and f"media_id {vw.mid(1)}" in r.content
    assert f"topics: Topic {vw.word}" in r.content


async def test_suggest_video_shows_a_commons_file_with_its_licence(vw: VW, db: Database) -> None:  # noqa: F811
    r = await call(ctx(db, vw.w.a), "suggest_video", {"topic": vw.subject, "step": "step2ck"})
    assert [b["data"]["media_id"] for b in r.blocks] == [vw.mid(5)]
    d = r.blocks[0]["data"]
    assert d["provider"] == "commons" and "video_id" not in d
    assert d["file_url"] == "https://f.test/a.webm" and d["page_url"] == "https://c.test/File:a.webm"
    assert d["attribution"] == "CC BY 4.0 by Someone" and d["duration_s"] == 90
    assert d["licence"] == {"license_short_name": "CC BY 4.0", "license_url": "https://l.test", "artist": "Someone"}
    assert "Wikimedia Commons" in r.content
    none = await call(ctx(db, vw.w.a), "suggest_video", {"topic": f"nothing{vw.w.tag}"})
    assert none.blocks == [] and "No approved video" in none.content


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
