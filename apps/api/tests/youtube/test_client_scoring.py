"""YouTube client (official SDK shape), quota ledger, keep filter, id parsing, offline scoring and assignment."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from naadi_api.youtube import client as yt
from naadi_api.youtube.mapper import pick
from naadi_api.youtube.queue import parse_video_id
from naadi_api.youtube.scoring import Match, Section, score_videos

from .fakes import WordEmbedder, item


def test_uploads_playlist_and_durations() -> None:
    assert yt.uploads_playlist("UC6QYFutt9cluQ3uSM963_KQ") == "UU6QYFutt9cluQ3uSM963_KQ"
    assert yt.uploads_playlist("xx") is None
    assert yt.iso_duration_s("PT1H2M3S") == 3723
    assert yt.iso_duration_s("PT45S") == 45
    assert yt.iso_duration_s("P1DT1M") == 86460
    assert yt.iso_duration_s("nope") is None and yt.iso_duration_s(None) is None


def test_keep_needs_embeddable_public_and_processed() -> None:
    assert yt.keep(item("a", "t"))
    assert not yt.keep(item("a", "t", embeddable=False))
    assert not yt.keep(item("a", "t", privacy="unlisted"))
    assert not yt.keep(item("a", "t", upload="uploaded"))


def test_media_id_is_lowercase_but_keeps_the_real_id_elsewhere() -> None:
    assert yt.media_id("AbC_-123xYz") == "yt-abc_-123xyz"


def test_quota_ledger_stops_the_run() -> None:
    q = yt.Quota(2)
    q.spend()
    q.spend()
    with pytest.raises(yt.QuotaExceeded):
        q.spend()
    assert q.used == 2


class _Req:
    def __init__(self, out: Any) -> None:
        self.out = out

    def execute(self, num_retries: int = 0) -> Any:
        if isinstance(self.out, Exception):
            raise self.out
        return self.out


def _client(pages: list[Any], videos: list[Any]) -> yt.GoogleYouTube:
    c = yt.GoogleYouTube.__new__(yt.GoogleYouTube)
    c.quota = yt.Quota(100)
    plist, vlist = iter(pages), iter(videos)
    c._svc = SimpleNamespace(  # type: ignore[attr-defined]
        playlistItems=lambda: SimpleNamespace(list=lambda **kw: _Req(next(plist))),
        videos=lambda: SimpleNamespace(list=lambda **kw: _Req(next(vlist))),
    )
    return c


def test_playlist_pages_and_batches_cost_one_unit_each() -> None:
    page = lambda ids, nxt: {"items": [{"contentDetails": {"videoId": i}} for i in ids], "nextPageToken": nxt}  # noqa: E731
    c = _client([page(["a", "b"], "t2"), page(["c"], None)], [{"items": [item("a", "x")]}, {"items": []}])
    assert c.playlist_video_ids("UUx", 10) == ["a", "b", "c"]
    assert c.quota.used == 2
    ids = [f"v{i:02d}" for i in range(60)]
    assert set(c.videos(ids)) == {"a"}
    assert c.quota.used == 4  # two batches of 50


def test_http_quota_error_becomes_quota_exceeded_and_others_a_code() -> None:
    from googleapiclient.errors import HttpError

    def err(status: int, body: bytes, reason: str) -> HttpError:
        return HttpError(SimpleNamespace(status=status, reason=reason), body)  # type: ignore[arg-type]

    c = _client([err(403, b'{"error":{"errors":[{"reason":"quotaExceeded"}]}}', "Forbidden")], [])
    with pytest.raises(yt.QuotaExceeded):
        c.playlist_video_ids("UUx", 5)
    c = _client([err(500, b"{}", "Server Error")], [])
    with pytest.raises(yt.YouTubeError, match="http_500"):
        c.playlist_video_ids("UUx", 5)


@pytest.mark.parametrize(
    ("raw", "vid"),
    [
        ("dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=30s", "dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ?si=abc", "dQw4w9WgXcQ"),
        ("https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://evil.example/watch?v=dQw4w9WgXcQ", None),
        ("javascript:alert(1)", None),
        ("short", None),
    ],
)
def test_parse_video_id(raw: str, vid: str | None) -> None:
    assert parse_video_id(raw) == vid


SECTIONS = [
    Section(
        "s-hf",
        "Heart failure and cardiac output",
        "ch1",
        ("step1:cardio",),
        "Heart failure and cardiac output cardiovascular",
    ),
    Section(
        "s-ph",
        "Pharmacokinetics of drug clearance",
        "ch2",
        ("step1:pharm",),
        "Pharmacokinetics of drug clearance pharmacology",
    ),
    Section("s-kid", "Nephron physiology", "ch3", ("step1:renal",), "Nephron physiology renal"),
]


def test_scoring_ranks_by_similarity_applies_min_score_and_subject_boost() -> None:
    vids = [("Heart failure explained: cardiac output", "preload afterload"), ("Cooking pasta", "boil water")]
    out = score_videos(WordEmbedder(), SECTIONS, vids, ["all"], min_score=0.3)
    assert [m.section_id for m in out[0]][:1] == ["s-hf"] and out[1] == []
    assert "heart" in out[0][0].terms and "failure" in out[0][0].terms
    plain = score_videos(WordEmbedder(), SECTIONS, vids[:1], [], min_score=0.0)[0]
    boosted = score_videos(WordEmbedder(), SECTIONS, vids[:1], ["cardio"], min_score=0.0)[0]
    s = {m.section_id: m.score for m in plain}
    b = {m.section_id: m.score for m in boosted}
    assert b["s-hf"] == pytest.approx(s["s-hf"] + 0.05, abs=1e-3) and b["s-ph"] == s["s-ph"]


def test_scoring_handles_empty_inputs() -> None:
    assert score_videos(WordEmbedder(), [], [("t", "d")], [], min_score=0.1) == [[]]
    assert score_videos(WordEmbedder(), SECTIONS, [], [], min_score=0.1) == []


def test_pick_respects_room_and_three_sections_per_video() -> None:
    ms = [
        (f"v{i}", [Match("s1", 0.9 - i / 100, ()), Match("s2", 0.5, ()), Match("s3", 0.4, ()), Match("s4", 0.3, ())])
        for i in range(3)
    ]
    room = {"s1": 2, "s2": 5, "s3": 5, "s4": 5}
    got = pick(ms, room)
    assert [(v, m.section_id) for v, m in got if m.section_id == "s1"] == [("v0", "s1"), ("v1", "s1")]  # room 2
    per_video: dict[str, int] = {}
    for v, _ in got:
        per_video[v] = per_video.get(v, 0) + 1
    assert max(per_video.values()) == 3
