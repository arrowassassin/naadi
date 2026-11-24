"""tools/video_catalog.py: filtering, Commons licences, matching and deterministic output (no network)."""

from __future__ import annotations

import importlib.util
import json
import sys
from typing import Any

from naadi_api.settings import REPO_ROOT
from tests.youtube.fakes import FakeYouTube, WordEmbedder, item


def _tool() -> Any:
    spec = importlib.util.spec_from_file_location("video_catalog_tool", REPO_ROOT / "tools" / "video_catalog.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses look the module up
    spec.loader.exec_module(mod)
    return mod


T = _tool()
CH = T.Channel("@x", "UCfake0000000000000000aa", "Fake Channel", "sketch", ["step1"], ["all"])
EMBED = WordEmbedder().embed_passages


def test_crawl_filters_shorts_live_long_private_and_not_embeddable() -> None:
    ids = ["ok", "short", "long", "live", "priv", "noembed"]
    catalog = {
        "ok": item("ok", "Nephrotic syndrome", duration="PT10M"),
        "short": item("short", "Nephrotic syndrome", duration="PT59S"),
        "long": item("long", "Nephrotic syndrome", duration="PT1H20M"),
        "live": item("live", "Nephrotic syndrome", duration="PT10M"),
        "priv": item("priv", "Nephrotic syndrome", privacy="private"),
        "noembed": item("noembed", "Nephrotic syndrome", embeddable=False),
    }
    catalog["live"]["snippet"]["liveBroadcastContent"] = "live"
    api = FakeYouTube({"UUfake0000000000000000aa": ids}, catalog)
    got = T.crawl_youtube(api, [CH], 50, lambda m: None)
    assert [c.id for c in got] == ["yt-ok"]
    assert api.quota.used == 2


def _info(licence: str, mime: str = "video/webm") -> dict[str, Any]:
    ext = {"LicenseShortName": {"value": licence}, "Artist": {"value": "<a href='x'>Osmosis</a>"}}
    return {"url": "https://upload.wikimedia.org/a.webm", "mime": mime, "extmetadata": ext, "duration": 61.4}


def test_commons_licence_allowlist() -> None:
    ok = ["CC0", "Public domain", "CC BY 4.0", "CC BY-SA 3.0", "CC BY-SA 4.0"]
    bad = ["CC BY-NC 4.0", "CC BY-ND 4.0", "GFDL", "Fair use", ""]
    assert all(T.commons_cand("File:A-osmosis-video.webm", _info(x)) for x in ok)
    assert not any(T.commons_cand("File:A-osmosis-video.webm", _info(x)) for x in bad)
    assert not T.commons_cand("File:A.png", _info("CC0", "image/png"))
    c = T.commons_cand("File:Gallstones-osmosis-video.webm", _info("CC BY-SA 4.0"))
    assert c.id == "wm-gallstones-osmosis-video" and c.title == "Gallstones" and c.extra["duration_s"] == 61
    assert "Osmosis" in c.extra["attribution"] and "<a" not in c.extra["attribution"]


TOPICS = [
    {
        "id": "nephrotic",
        "title": "Nephrotic syndrome",
        "chapter": "Ch",
        "subject": "Pathology",
        "step": "step1",
        "core": True,
    },
    {
        "id": "gout",
        "title": "Gout and pseudogout",
        "chapter": "Ch",
        "subject": "Pathology",
        "step": "step1",
        "core": False,
    },
    {
        "id": "ck-neph",
        "title": "Nephrotic syndrome",
        "chapter": "Ch",
        "subject": "Pathology",
        "step": "step2ck",
        "core": True,
    },
]


def _cand(vid: str, title: str, style: str = "sketch") -> Any:
    return T.Cand(f"yt-{vid}", "youtube", title, "Fake", style, ["step1"], ["all"], youtube_id=vid)


def test_match_threshold_step_and_style_order() -> None:
    cands = [
        _cand("a", "Nephrotic syndrome | Pathology", "lecture"),
        _cand("b", "Nephrotic syndrome", "sketch"),
        _cand("c", "Cooking pasta"),
        _cand("d", "Gout"),  # shares a word but is too dissimilar for the threshold
        _cand("e", "Nephrotic syndrome | Podcast"),  # noise titles never match
    ]
    hits = T.match(TOPICS, cands, EMBED, 0.7)
    assert not {"yt-c", "yt-d", "yt-e"} & set(hits)
    assert hits["yt-b"][0][0] == "nephrotic" and hits["yt-a"][0][0] == "nephrotic"
    assert all(t != "ck-neph" for rows in hits.values() for t, _ in rows)  # channel is Step 1 only
    assert hits["yt-b"][0][1] > hits["yt-a"][0][1]  # sketch beats lecture


def test_assemble_is_deterministic_and_has_no_youtube_title() -> None:
    cands = [_cand("b", "Nephrotic syndrome"), _cand("a", "Nephrotic syndrome")]
    topics = [dict(t, media_ids=[]) for t in TOPICS]
    hits = T.match(topics, cands, EMBED, 0.7)
    rows = T.assemble(topics, list(reversed(cands)), hits)
    assert [r["id"] for r in rows] == ["yt-a", "yt-b"]
    assert topics[0]["media_ids"] == ["yt-a", "yt-b"]
    assert rows[0]["label"] == "Nephrotic syndrome" and rows[0]["youtube_id"] == "a"
    assert "title" not in rows[0] and "duration_s" not in rows[0]
    rows2 = T.assemble([dict(t, media_ids=[]) for t in TOPICS], cands, hits)
    assert json.dumps(rows) == json.dumps(rows2)


def test_build_topics_adds_taxonomy_leaves_with_unique_ids() -> None:
    taxonomy = json.loads((REPO_ROOT / "data" / "taxonomy.json").read_text())
    atlas = [{"id": "x", "title": "X", "subject": "Anatomy", "unit": "U", "chapter": "C", "step": "S1", "core": True}]
    subjects, topics = T.build_topics(atlas, ["Anatomy"], taxonomy)
    ids = [t["id"] for t in topics]
    assert len(ids) == len(set(ids)) and "x" in ids
    assert {"Immunology", "Psychiatry", "Emergency & Family Medicine"} <= set(subjects)
    assert [t["sort"] for t in topics] == list(range(1, len(topics) + 1))
