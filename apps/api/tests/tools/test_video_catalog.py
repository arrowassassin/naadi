"""tools/video_catalog.py: filtering, Commons, topic keys, matching tiers, pins and output (no network)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

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


def topic(tid: str, title: str, chapter: str = "Ch", step: str = "step1", subject: str = "Pathology") -> dict[str, Any]:
    return {"id": tid, "title": title, "chapter": chapter, "subject": subject, "step": step, "core": True,
            "media_ids": [], "chapter_media_ids": []}  # fmt: skip


def cand(vid: str, title: str, style: str = "sketch", **kw: Any) -> Any:
    return T.Cand(f"yt-{vid}", "youtube", title, "Fake", style, ["step1"], ["all"], youtube_id=vid, **kw)


# ---------------------------------------------------------------- YouTube crawl


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
    catalog["ok"]["snippet"].update(tags=["kidney", "proteinuria"], description="Edema and proteinuria. " * 40)
    api = FakeYouTube({"UUfake0000000000000000aa": ids}, catalog)
    got = T.crawl_youtube(api, [CH], 50, lambda m: None)
    assert [c.id for c in got] == ["yt-ok"]
    assert got[0].text.startswith("kidney proteinuria Edema") and len(got[0].text) < 350  # transient context only
    assert api.quota.used == 2


# ---------------------------------------------------------------- Commons


def _info(licence: str, mime: str = "video/webm") -> dict[str, Any]:
    ext = {
        "LicenseShortName": {"value": licence},
        "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-sa/4.0"},
        "Artist": {"value": "<a href='x'>Osmosis</a>"},
    }
    return {"url": "https://upload.wikimedia.org/a.webm?utm=1", "mime": mime, "extmetadata": ext, "duration": 61.4}


def test_commons_licence_allowlist_attribution_and_snapshot() -> None:
    ok = ["CC0", "Public domain", "CC BY 4.0", "CC BY-SA 3.0", "CC BY-SA 4.0"]
    bad = ["CC BY-NC 4.0", "CC BY-ND 4.0", "GFDL", "Fair use", ""]
    assert all(T.commons_cand("File:A-osmosis-video.webm", _info(x)) for x in ok)
    assert not any(T.commons_cand("File:A-osmosis-video.webm", _info(x)) for x in bad)
    assert not T.commons_cand("File:A.png", _info("CC0", "image/png"))
    c = T.commons_cand("File:Gallstones-osmosis-video.webm", _info("CC BY-SA 4.0"))
    assert c.id == "wm-gallstones-osmosis-video" and c.title == "Gallstones" and c.extra["duration_s"] == 61
    assert c.extra["file_url"] == "https://upload.wikimedia.org/a.webm"
    assert c.extra["licence_snapshot"] == {
        "license_short_name": "CC BY-SA 4.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/4.0",
        "artist": "Osmosis",
    }
    assert c.extra["attribution"].startswith('"Gallstones-osmosis-video.webm" by Osmosis, via Wikimedia Commons (')
    assert c.extra["attribution"].endswith("(https://creativecommons.org/licenses/by-sa/4.0). none")


def test_attribution_template_is_read_from_sources_yaml() -> None:
    assert "{license_short_name}" in T.attribution_template() and "{modifications}" in T.attribution_template()


def _commons(handler: Any) -> tuple[Any, list[float]]:
    sleeps: list[float] = []
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return T.Commons(client, sleeps.append), sleeps


def test_commons_get_sends_maxlag_and_waits_between_requests() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"ok": 1})

    c, sleeps = _commons(handler)
    assert c.get(action="query") == {"ok": 1}
    assert seen[0].url.params["maxlag"] == "5" and seen[0].url.params["action"] == "query"
    assert sleeps and sleeps[0] >= 1.0


def test_commons_get_backs_off_on_429_and_maxlag_then_succeeds() -> None:
    replies = iter(
        [httpx.Response(429, text="Too Many Requests"), httpx.Response(200, json={"error": {"code": "maxlag"}})]
    )

    def handler(req: httpx.Request) -> httpx.Response:
        return next(replies, httpx.Response(200, json={"ok": 1}))

    c, sleeps = _commons(handler)
    assert c.get(action="query") == {"ok": 1}
    assert len(sleeps) == 3 and sleeps[1] > sleeps[0] and sleeps[2] > sleeps[1]  # exponential


def test_commons_get_gives_up_with_commons_blocked_and_raises_real_errors() -> None:
    c, sleeps = _commons(lambda req: httpx.Response(429, text="slow down"))
    with pytest.raises(T.CommonsBlocked):
        c.get(action="query")
    assert len(sleeps) == 6
    c2, _ = _commons(lambda req: httpx.Response(404, text="nope"))
    with pytest.raises(httpx.HTTPStatusError):
        c2.get(action="query")


def test_crawl_commons_keeps_one_file_per_film() -> None:
    class Fake:
        def search(self, query: str, limit: int) -> list[str]:
            return ["File:A-osmosis-video.webm", "File:A-osmosis-video.ogv", "File:B-osmosis-video.ogv"]

        def info(self, titles: list[str]) -> dict[str, dict[str, Any]]:
            return {t: _info("CC BY-SA 4.0") for t in titles}

    got = T.crawl_commons(Fake(), 10, lambda m: None)
    assert sorted(c.id for c in got) == ["wm-a-osmosis-video", "wm-b-osmosis-video"]


# ---------------------------------------------------------------- topics


def test_topic_keys_strip_noise_and_split_facets() -> None:
    keys = dict(T.topic_keys("Cardiomyopathies 1: Dilated & Restrictive SOAPs Part 2"))
    assert (
        "Cardiomyopathies : Dilated & Restrictive" in keys
        and keys["Cardiomyopathies : Dilated & Restrictive"] == "main"
    )
    assert {"Dilated", "Restrictive"} <= set(keys)
    adrenal = [k for k, _ in T.topic_keys("Adrenal disorders (Cushing, aldosteronism, CAH)")]
    assert adrenal[0] == "Adrenal disorders" and "Adrenal disorders Cushing" in adrenal
    assert [k for k, _ in T.topic_keys("Hyponatremia (Workup & Management)")] == ["Hyponatremia"]  # generic parts only


def _leaf(key: str, label: str, exam: list[str]) -> dict[str, Any]:
    return {"id": f"topic.{key}", "kind": "topic", "parent_id": "sys", "label": label, "exam": exam}


TAX = [
    _leaf("cardio.cardio_pharm", "Cardiovascular pharmacology", ["step1", "step2ck"]),
    _leaf("psych.mood", "Mood", ["step1", "step2ck"]),
    _leaf("multi.genetics", "Genetics", ["step1", "step2ck"]),
    _leaf("preg.labor", "Labor", ["step2ck"]),
]


def test_taxonomy_topics_subject_and_step_mapping() -> None:
    got = {t["id"]: (t["subject"], t["step"], t["unit"]) for t in T.taxonomy_topics(TAX, {"sys": "Cardiovascular"})}
    assert got == {
        "cardiovascular-pharmacology": ("Pharmacology", "step1", "Cardiovascular"),
        "cardiovascular-pharmacology-step2ck": ("Internal Medicine", "step2ck", "Cardiovascular"),
        "mood": ("Behavioral Science", "step1", "Cardiovascular"),
        "mood-step2ck": ("Psychiatry", "step2ck", "Cardiovascular"),
        "genetics": ("Biochemistry", "step1", "Cardiovascular"),  # no Step 2 CK twin
        "labor-step2ck": ("Obstetrics & Gynecology", "step2ck", "Cardiovascular"),
    }


def test_build_topics_adds_taxonomy_leaves_with_unique_ids_and_tier_fields() -> None:
    taxonomy = json.loads((REPO_ROOT / "data" / "taxonomy.json").read_text())
    base = [{"id": "x", "title": "X", "subject": "Anatomy", "unit": "U", "chapter": "C", "step": "step1", "core": True}]
    subjects, topics = T.build_topics(base, ["Anatomy"], taxonomy)
    ids = [t["id"] for t in topics]
    assert len(ids) == len(set(ids)) and "x" in ids
    assert {"Immunology", "Psychiatry", "Emergency & Family Medicine"} <= set(subjects)
    assert [t["sort"] for t in topics] == list(range(1, len(topics) + 1))
    assert all(t["media_ids"] == [] and t["chapter_media_ids"] == [] for t in topics)


def test_committed_topic_map_and_pins_are_clean() -> None:
    topic_map = json.loads((REPO_ROOT / "data" / "video_topic_map.json").read_text())
    pins = (REPO_ROOT / "config" / "video_pins.yaml").read_text()
    assert all(t["step"] in ("step1", "step2ck") for t in topic_map["topics"])
    assert pins.count("youtube_id:") == 158


# ---------------------------------------------------------------- matching


TOPICS = [
    topic("nephrotic", "Nephrotic syndrome"),
    topic("gout", "Gout and pseudogout"),
    topic("ck-neph", "Nephrotic syndrome", step="step2ck"),
]


def test_match_threshold_step_style_noise_and_lexical_gate() -> None:
    cands = [
        cand("a", "Nephrotic syndrome | Pathology", "lecture"),
        cand("b", "Nephrotic syndrome", "sketch"),
        cand("c", "Cooking pasta"),
        cand("d", "Gout"),  # shares a word but is too dissimilar for the threshold
        cand("e", "Nephrotic syndrome | Podcast"),  # noise titles never match
    ]
    m = T.match(TOPICS, cands, EMBED, 0.7)
    assert not {"yt-c", "yt-d", "yt-e"} & set(m.direct)
    assert m.direct["yt-b"][0][0] == "nephrotic" and m.direct["yt-a"][0][0] == "nephrotic"
    assert all(t != "ck-neph" for rows in m.direct.values() for t, _ in rows)  # channel is Step 1 only
    assert m.direct["yt-b"][0][1] > m.direct["yt-a"][0][1]  # sketch beats lecture


def test_lexical_gate_rejects_a_near_title_with_a_different_subject() -> None:
    topics = [topic("circ", "Coronary Circulation")]
    # cosine alone passes (threshold 0): the content words {coronary, artery} vs {coronary, circulation} do not
    m = T.match(topics, [cand("a", "Coronary Artery Disease")], EMBED, 0.0)
    assert m.direct == {}
    assert T.match(topics, [cand("b", "Coronary Circulation")], EMBED, 0.0).direct["yt-b"][0][0] == "circ"


def test_description_rescues_a_partial_title_only_when_it_names_the_key() -> None:
    topics = [topic("azoles", "Azole antifungals")]
    cands = [cand("a", "Antifungal drugs"), cand("b", "Antifungal drugs", text="fluconazole azoles voriconazole")]
    m = T.match(topics, cands, lambda ts: [[1.0, 0.0] for _ in ts], 0.8)  # same cosine for both
    assert "yt-a" not in m.direct  # title overlap is only 1/3
    assert m.direct["yt-b"][0][0] == "azoles"  # the tags / description name "azole"


def test_facet_matches_a_compound_topic_with_a_lower_score() -> None:
    topics = [topic("hemo", "Cardiac physiology & hemodynamics")]
    m = T.match(topics, [cand("a", "Hemodynamics")], EMBED, 0.7)
    assert m.direct["yt-a"][0][0] == "hemo"


def test_relative_margin_keeps_only_topics_near_the_best() -> None:
    topics = [topic("t1", "Nephrotic syndrome"), topic("t2", "Nephrotic syndrome Minimal change disease")]
    m = T.match(topics, [cand("a", "Nephrotic syndrome")], EMBED, 0.5)
    assert [t for t, _ in m.direct["yt-a"]] == ["t1"]  # t2 matches too but scores far below the best


def _pin(c: Any, chapter: str, subject: str = "Pathology", step: str = "step1") -> Any:
    return T.apply_pins([c], [{"youtube_id": c.youtube_id, "subject": subject, "step": step, "chapter": chapter}])[0]


def test_pin_scoring_direct_when_it_passes_otherwise_chapter_tier_only() -> None:
    topics = [
        topic("neph", "Nephrotic syndrome", chapter="Kidney"),
        topic("aki", "Acute kidney injury", chapter="Kidney"),
    ]
    good = _pin(cand("good", "Nephrotic syndrome overview"), "Kidney")
    weak = _pin(cand("weak", "Hypertension introduction"), "Kidney")
    m = T.match(topics, [good, weak], EMBED, 0.85)
    assert [t for t, _ in m.direct["yt-good"]] == ["neph"]
    assert "yt-weak" not in m.direct and m.chapter["yt-weak"][0][0] == ("Pathology", "step1", "Kidney")
    rows = T.assemble(topics, [good, weak], m)
    by_id = {r["id"]: r for r in rows}
    assert by_id["yt-weak"]["topic_ids"] == [] and by_id["yt-weak"]["label"] == "Kidney"  # never an unscored link
    assert topics[0]["media_ids"] == ["yt-good"] and "yt-weak" not in topics[0]["media_ids"]
    assert topics[1]["media_ids"] == [] and set(topics[1]["chapter_media_ids"]) == {"yt-good", "yt-weak"}


def test_pins_are_limited_to_their_subject_and_step_and_skip_nursing() -> None:
    topics = [topic("neph", "Nephrotic syndrome", subject="Pathology")]
    wrong_subject = _pin(cand("a", "Nephrotic syndrome"), "Kidney", subject="Physiology")
    nursing = _pin(cand("b", "Nephrotic syndrome"), "Kidney")
    nursing.channel_name = "Ninja Nerd Nursing"
    m = T.match(topics, [wrong_subject, nursing], EMBED, 0.5)
    assert m.direct == {} and m.chapter == {}
    assert (
        T.apply_pins([cand("z", "x")], [{"youtube_id": "gone", "subject": "s", "step": "step1", "chapter": "c"}])[0].pin
        is False
    )


def test_chapter_tier_siblings_and_chapter_title_matches() -> None:
    topics = [
        topic("t1", "Nephrotic syndrome", chapter="Glomerular disease"),
        topic("t2", "Nephritic syndrome", chapter="Glomerular disease"),
        topic("t3", "Gout", chapter="Crystals"),
    ]
    cands = [cand("a", "Nephrotic syndrome"), cand("b", "Glomerular disease")]
    m = T.match(topics, cands, EMBED, 0.8)
    rows = {r["id"]: r for r in T.assemble(topics, cands, m)}
    assert rows["yt-a"]["topic_ids"] == ["t1"]
    assert rows["yt-b"]["topic_ids"] == [] and rows["yt-b"]["label"] == "Glomerular disease"
    assert topics[0]["media_ids"] == ["yt-a"] and topics[0]["chapter_media_ids"] == [
        "yt-b"
    ]  # no repeat of a direct link
    assert topics[1]["media_ids"] == [] and set(topics[1]["chapter_media_ids"]) == {"yt-a", "yt-b"}
    assert topics[2]["chapter_media_ids"] == []  # another chapter
    assert T.coverage(topics) == (pytest.approx(100 / 3), pytest.approx(200 / 3))


def test_video_step_and_subject_come_from_the_best_scored_topic() -> None:
    topics = [
        topic("ps", "Nephrotic syndrome", subject="Physiology"),
        topic("pa", "Nephrotic syndrome", subject="Pathology"),
    ]
    c = cand("a", "Nephrotic syndrome")
    c.subjects = ["Pathology", "Physiology"]
    m = T.match(topics, [c], EMBED, 0.8)
    rows = T.assemble(topics, [c], m)
    assert rows[0]["topic_ids"][0] == "pa" and rows[0]["subject"] == "Pathology"  # subject fit ranks first


def test_assemble_is_deterministic_and_has_no_youtube_text() -> None:
    cands = [cand("b", "Nephrotic syndrome", text="secret description"), cand("a", "Nephrotic syndrome")]
    topics = [dict(t) for t in TOPICS]
    m = T.match(topics, cands, EMBED, 0.7)
    rows = T.assemble(topics, list(reversed(cands)), m)
    assert [r["id"] for r in rows] == ["yt-a", "yt-b"]
    assert topics[0]["media_ids"] == ["yt-a", "yt-b"]
    assert "title" not in rows[0] and "duration_s" not in rows[0] and "secret" not in json.dumps(rows)
    topics2 = [dict(t) for t in TOPICS]
    assert json.dumps(rows) == json.dumps(T.assemble(topics2, cands, T.match(topics2, cands, EMBED, 0.7)))


def test_coverage_and_exit_code_min_coverage() -> None:
    topics = [topic("a", "A"), topic("b", "B"), topic("c", "C"), topic("d", "D")]
    topics[0]["media_ids"] = ["v"]
    topics[1]["chapter_media_ids"] = ["v"]
    assert T.coverage(topics) == (25.0, 50.0)
    assert T.exit_code(50.0, 0) == 0 and T.exit_code(50.0, 50) == 0 and T.exit_code(50.0, 90) == 1


# ---------------------------------------------------------------- committed data


def test_committed_data_precision_on_the_labelled_eval_set() -> None:
    """video_eval.json: Naadi-authored (topic id, video id, ok) labels. Direct links must be >= 95% right."""
    labels = json.loads((Path(__file__).parent / "video_eval.json").read_text())["labels"]
    topics = {t["id"]: t for t in json.loads((REPO_ROOT / "data" / "video_topics.json").read_text())["topics"]}
    assert len(labels) >= 140
    predicted = [x for x in labels if x["video_id"] in topics[x["topic_id"]]["media_ids"]]
    right = [x for x in predicted if x["ok"]]
    assert len(predicted) >= 90
    assert len(right) / len(predicted) >= 0.95
    wanted = [x for x in labels if x["ok"]]
    assert len(right) / len(wanted) >= 0.6  # recall on the labelled good pairs


def test_committed_data_shape() -> None:
    topics = json.loads((REPO_ROOT / "data" / "video_topics.json").read_text())["topics"]
    videos = {v["id"]: v for v in json.loads((REPO_ROOT / "data" / "videos.json").read_text())["videos"]}
    for t in topics:
        assert not set(t["media_ids"]) & set(t["chapter_media_ids"])
        assert set(t["media_ids"] + t["chapter_media_ids"]) <= set(videos)
        assert all(t["id"] in videos[v]["topic_ids"] for v in t["media_ids"])
    for v in videos.values():
        assert (
            v["licence_snapshot"] is None if v["provider"] == "youtube" else v["licence_snapshot"]["license_short_name"]
        )
    assert ("sket" + "chy") not in json.dumps(topics).lower()  # no brand names in the data
