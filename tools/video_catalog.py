#!/usr/bin/env python3
"""Build the Videos catalogue: `data/video_topics.json` (topic tree) and `data/videos.json` (video -> topics).

    uv --directory apps/api run python ../../tools/video_catalog.py --topic-map <topics.json> \
        --atlas <atlas_youtube.json>

Sources: YouTube uploads of the channels in `config/video_channels.yaml` (official Data API, embeddable only) and
open-licence Wikimedia Commons videos. Titles are matched to topic titles with the local fastembed engine,
precision first: a gap is better than a wrong video. YouTube titles / durations are used in memory only and are never
written; the JSON holds ids plus Naadi-authored labels. Commons metadata (open licence) may be written.
Environment: YOUTUBE_API_KEY (through `naadi_api.settings`, never printed).
"""

from __future__ import annotations

import argparse
import html
import json
import re
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import yaml

from naadi_api.youtube.client import (
    QuotaExceeded,
    YouTubeApi,
    YouTubeError,
    iso_duration_s,
    keep,
    media_id,
    uploads_playlist,
)

STYLE_RANK = {"sketch": 3, "whiteboard": 2, "animation": 1, "lecture": 0}
MIN_S, MAX_S = 120, 75 * 60  # drops Shorts and very long lectures
TOP_PER_TOPIC = 3
MAX_TOPICS_PER_VIDEO = 3
THRESHOLD = 0.85  # cosine on titles; tuned by eyeballing the printed sample
CHAPTER_MARGIN = 0.06  # a chapter title must match this much better than a topic title
UA = "MedEduCatalogBuilder/1.0 (educational video catalog)"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
COMMONS_QUERIES = ["Osmosis filetype:video", "VideoWiki Osmosis filetype:video"]
ATTRIBUTION = '"{title}" by {artist}, via Wikimedia Commons ({file_url}), {license_short_name} ({license_url})'
LICENCE_OK = re.compile(r"^(CC0.*|Public domain|CC BY(-SA)? .+)$")
GENERIC = {
    "and", "the", "of", "in", "to", "for", "with", "from", "that", "this", "what", "your", "how", "why", "vs", "versus",
    "disease", "diseases", "syndrome", "syndromes", "disorder", "disorders", "anatomy", "physiology", "pathology",
    "pharmacology", "introduction", "overview", "basics", "lecture", "explained", "clearly", "usmle", "step",
    "medicine", "clinical", "review", "part", "system", "human", "body", "mnemonic", "mechanism", "action", "causes",
    "symptoms", "signs", "diagnosis", "treatment", "management", "etiology", "presentation", "workup",
    "pathophysiology",
    "ddx", "high", "yield", "approach", "understanding", "guide", "simple", "made", "easy",
}  # fmt: skip
NOISE = re.compile(r"(?i)podcast|nclex|nurs(e|ing)\b|#shorts|\bq\s*&\s*a\b|live ?stream|\bquiz\b|\bmcq")
# Step 1 subject of a taxonomy leaf by id keyword, then by family; Step 2 CK subject by family.
S1_KEY = [("pharm", "Pharmacology"), ("physiology", "Physiology"), ("embryology", "Anatomy"), ("anatomy", "Anatomy")]
S1_FAMILY = {
    "biostat": "Biostatistics & Epidemiology", "social": "Behavioral Science", "dev": "Behavioral Science",
    "psych": "Behavioral Science", "neuro": "Neurology", "immune": "Immunology", "nutrition": "Biochemistry",
}  # fmt: skip
S1_LEAF = {
    "bacteria": "Microbiology", "viruses_hiv": "Microbiology", "fungi_parasites": "Microbiology",
    "antimicrobials": "Pharmacology", "pharm_principles": "Pharmacology", "toxicology_environmental": "Pharmacology",
    "genetics": "Biochemistry", "metabolism_inborn_errors": "Biochemistry", "molecular_cell_biology": "Biochemistry",
}  # fmt: skip
S2_FAMILY = {
    "dev": "Pediatrics", "repro": "Obstetrics & Gynecology", "preg": "Obstetrics & Gynecology", "male": "Surgery",
    "neuro": "Neurology", "psych": "Psychiatry", "biostat": "Biostatistics & Epidemiology",
    "social": "Behavioral Science",
}  # fmt: skip
S2_LEAF = {
    "trauma_surgery_periop": "Surgery", "toxicology_environmental": "Emergency & Family Medicine",
    "well_care": "Emergency & Family Medicine", "shock_syncope": "Emergency & Family Medicine",
    "acute_abdomen": "Surgery", "burns_wounds": "Surgery", "orthopedics_trauma": "Surgery",
}  # fmt: skip
STEP1_ONLY = {"molecular_cell_biology", "metabolism_inborn_errors", "genetics"}  # no Step 2 CK twin

Embed = Callable[[Sequence[str]], list[list[float]]]


@dataclass(slots=True)
class Channel:
    handle: str | None
    channel_id: str | None
    name: str
    style: str
    steps: list[str]
    subjects: list[str]


@dataclass(slots=True)
class Cand:
    """A video that can be attached to topics. `title` is used for matching only and is never written."""

    id: str
    provider: str
    title: str
    channel_name: str
    style: str
    steps: list[str]
    subjects: list[str]
    youtube_id: str | None = None
    channel_id: str | None = None
    pinned: list[str] = field(default_factory=list)  # atlas chapter topic ids
    extra: dict[str, Any] = field(default_factory=dict)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def load_channels(path: Path) -> list[Channel]:
    rows = yaml.safe_load(path.read_text())["channels"]
    return [Channel(r["handle"], r["channel_id"], r["name"], r["style"], r["steps"], r["subjects"]) for r in rows]


# ---------------------------------------------------------------- topics


def taxonomy_topics(taxonomy: list[dict[str, Any]], systems: dict[str, str]) -> list[dict[str, Any]]:
    """One Step 1 topic per taxonomy leaf and a Step 2 CK twin for the clinical ones, from Naadi's own outline."""
    out: list[dict[str, Any]] = []
    for leaf in (x for x in taxonomy if x["kind"] == "topic"):
        fam, name = leaf["id"].split(".")[1:3]
        unit = systems.get(leaf["parent_id"], "")
        for step in ("step1", "step2ck"):
            if step not in leaf["exam"] or (step == "step2ck" and name in STEP1_ONLY):
                continue
            if step == "step1":
                subj = S1_LEAF.get(name) or next((s for k, s in S1_KEY if k in name), None) or S1_FAMILY.get(fam)
                subj = subj or "Pathology"
            else:
                subj = S2_LEAF.get(name) or S2_FAMILY.get(fam) or "Internal Medicine"
            out.append(
                {"id": slug(leaf["label"]) + ("-step2ck" if step == "step2ck" else ""), "title": leaf["label"],
                 "subject": subj, "unit": unit, "chapter": leaf["label"], "step": step, "core": True}
            )  # fmt: skip
    return out


def build_topics(
    atlas_topics: list[dict[str, Any]], subjects: list[str], taxonomy: list[dict[str, Any]]
) -> tuple[list[str], list[dict[str, Any]]]:
    systems = {x["id"]: x["label"].split(" (")[0] for x in taxonomy if x["kind"] == "system"}
    topics = [
        {**{k: t[k] for k in ("id", "title", "subject", "chapter", "core")},
         "unit": "Clinical Pearls" if t["unit"].endswith("Pearls") else t["unit"],  # atlas unit label is brand-named
         "step": "step1" if t["step"] == "S1" else "step2ck"}
        for t in atlas_topics
    ]  # fmt: skip
    seen = {t["id"] for t in topics}
    for t in taxonomy_topics(taxonomy, systems):
        if t["id"] not in seen:
            seen.add(t["id"])
            topics.append(t)
    order = list(subjects) + sorted({t["subject"] for t in topics} - set(subjects))
    topics.sort(key=lambda t: (order.index(t["subject"]), t["step"], t["unit"], t["chapter"], t["title"], t["id"]))
    for i, t in enumerate(topics, 1):
        t["sort"] = i
        t["media_ids"] = []
    return order, topics


# ---------------------------------------------------------------- YouTube


def crawl_youtube(api: YouTubeApi, channels: list[Channel], per_channel: int, log: Callable[[str], None]) -> list[Cand]:
    out: list[Cand] = []
    for ch in channels:
        pl = uploads_playlist(ch.channel_id or "")
        if not pl:
            log(f"skip {ch.name}: no channel id")
            continue
        try:
            ids = api.playlist_video_ids(pl, per_channel)
            got = api.videos(ids)
        except YouTubeError as exc:
            log(f"skip {ch.name}: {exc}")
            continue
        n = 0
        for vid in ids:
            c = video_cand(got.get(vid), ch)
            if c:
                out.append(c)
                n += 1
        log(f"{ch.name}: {len(ids)} uploads, {n} kept")
    return out


def video_cand(item: dict[str, Any] | None, ch: Channel) -> Cand | None:
    if not item or not keep(item):
        return None
    dur = iso_duration_s((item.get("contentDetails") or {}).get("duration"))
    snip = item.get("snippet") or {}
    if dur is None or not MIN_S <= dur <= MAX_S or snip.get("liveBroadcastContent") not in (None, "none"):
        return None
    return Cand(
        media_id(item["id"]), "youtube", snip.get("title", ""), ch.name, ch.style, ch.steps, ch.subjects,
        youtube_id=item["id"], channel_id=snip.get("channelId") or ch.channel_id,
    )  # fmt: skip


# ---------------------------------------------------------------- Commons


class CommonsBlocked(RuntimeError):
    pass


class Commons:
    def __init__(self, client: httpx.Client | None = None, sleep: Callable[[float], None] = time.sleep) -> None:
        self.http = client or httpx.Client(headers={"User-Agent": UA}, timeout=30)
        self.sleep = sleep

    def get(self, **params: Any) -> dict[str, Any]:
        """One MediaWiki call: >= 1 s apart, maxlag=5, exponential backoff on 429 / maxlag / 'too many requests'."""
        q = {"format": "json", "maxlag": 5, **params}
        for attempt in range(6):
            self.sleep(1.0 + (2**attempt if attempt else 0) * 2)
            r = self.http.get(COMMONS_API, params=q)
            text = r.text.lower()
            if r.status_code == 200 and '"maxlag"' not in text[:200] and "too many requests" not in text[:600]:
                return r.json()  # type: ignore[no-any-return]
            if r.status_code not in (200, 429, 503):
                break
        raise CommonsBlocked("commons rate limit")

    def search(self, query: str, limit: int) -> list[str]:
        titles: list[str] = []
        cont: dict[str, Any] = {}
        while len(titles) < limit:
            res = self.get(action="query", list="search", srsearch=query, srnamespace=6, srlimit=50, **cont)
            titles += [h["title"] for h in res["query"]["search"]]
            if "continue" not in res:
                break
            cont = {"sroffset": res["continue"]["sroffset"]}
        return titles[:limit]

    def info(self, titles: list[str]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for i in range(0, len(titles), 50):
            res = self.get(
                action="query",
                prop="imageinfo",
                iiprop="url|size|mime|extmetadata",
                titles="|".join(titles[i : i + 50]),
            )
            for p in (res.get("query", {}).get("pages") or {}).values():
                if p.get("imageinfo"):
                    out[p["title"]] = p["imageinfo"][0]
        return out


def _meta(info: dict[str, Any], key: str) -> str:
    return html.unescape(
        re.sub(r"<[^>]+>", "", str((info.get("extmetadata") or {}).get(key, {}).get("value", "")))
    ).strip()


def commons_cand(title: str, info: dict[str, Any]) -> Cand | None:
    licence = _meta(info, "LicenseShortName")
    if not LICENCE_OK.match(licence) or not str(info.get("mime", "")).startswith("video/"):
        return None
    name = title.removeprefix("File:")
    text = re.sub(r"\.\w+$", "", name)
    text = re.sub(r"(?i)[-_ ]*osmosis[-_ ]*video|videowiki|\(osmosis\)|[-_]", " ", text).strip()
    artist = _meta(info, "Artist") or "Unknown"
    page = "https://commons.wikimedia.org/wiki/" + title.replace(" ", "_")
    attribution = ATTRIBUTION.format(
        title=name, artist=artist, file_url=page, license_short_name=licence, license_url=_meta(info, "LicenseUrl")
    )
    return Cand(
        "wm-" + slug(re.sub(r"\.\w+$", "", name)),
        "commons",
        text,
        "Osmosis" if "osmos" in (name + artist).lower() else artist,
        "animation", ["step1", "step2ck"], ["all"],
        extra={"file_url": info["url"].split("?")[0], "file_page": page, "licence": licence, "attribution": attribution,
               "duration_s": int(info.get("duration") or 0) or None},
    )  # fmt: skip


def crawl_commons(c: Commons, limit: int, log: Callable[[str], None]) -> list[Cand]:
    titles = sorted({t for q in COMMONS_QUERIES for t in c.search(q, limit)})
    log(f"commons: {len(titles)} search hits")
    infos = c.info(titles)
    out = [x for t in titles if t in infos and (x := commons_cand(t, infos[t]))]
    log(f"commons: {len(out)} with an allowed licence")
    return out


# ---------------------------------------------------------------- matching


def clean_title(title: str) -> str:
    return re.split(r"\s[|:\-–]\s", title)[0].strip() or title


def tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w[:-1] if len(w) > 3 and w.endswith("s") else w for w in words if len(w) > 1 and w not in GENERIC}


def compatible(a: set[str], b: set[str]) -> bool:
    """One title's content words contain the other's: 'Coronary Artery Disease' is not 'Coronary Circulation'."""
    return bool(a and b and (a <= b or b <= a))


def norm(m: Any) -> Any:
    a = np.asarray(m, dtype=float)
    return a / np.maximum(np.linalg.norm(a, axis=1, keepdims=True), 1e-9)


def match(
    topics: list[dict[str, Any]], cands: list[Cand], embed: Embed, threshold: float = THRESHOLD
) -> dict[str, list[tuple[str, float]]]:
    """video id -> [(topic id, score)], best first. A topic keeps its 3 best videos, a video its 3 best topics.

    The gate is the raw cosine of titles plus one shared content word; style and channel fit only reorder."""
    cands = [c for c in cands if not NOISE.search(c.title)]
    if not topics or not cands:
        return {}
    # a video may match a topic's own title or its chapter title (a chapter video serves every topic in it)
    keys = [(ti, text) for ti, t in enumerate(topics) for text in dict.fromkeys([t["title"], t["chapter"]])]
    sims = norm(embed([clean_title(c.title) for c in cands])) @ norm(embed([k[1] for k in keys])).T
    ktoks = [tokens(k[1]) for k in keys]
    vtoks = [tokens(clean_title(c.title)) for c in cands]
    best: dict[tuple[int, int], float] = {}
    for vi, ki in np.argwhere(sims >= threshold):  # the cosine gate first: few pairs survive
        ti = keys[ki][0]
        if keys[ki][1] != topics[ti]["title"] and sims[vi, ki] < threshold + CHAPTER_MARGIN:
            continue
        c, t = cands[vi], topics[ti]
        if t["step"] not in c.steps or ("all" not in c.subjects and t["subject"] not in c.subjects):
            continue
        if compatible(vtoks[vi], ktoks[ki]):
            best[(int(vi), ti)] = max(best.get((int(vi), ti), 0.0), float(sims[vi, ki]))
    per_topic: dict[int, list[tuple[float, int]]] = {}
    for (vi, ti), sc in best.items():
        c = cands[vi]
        bonus = 0.01 * STYLE_RANK[c.style] + (0.02 if topics[ti]["subject"] in c.subjects else 0)
        per_topic.setdefault(ti, []).append((sc + bonus, vi))
    out: dict[str, list[tuple[str, float]]] = {}
    for ti, rows in per_topic.items():
        rows.sort(key=lambda r: (-r[0], cands[r[1]].id))
        for score, vi in rows[:TOP_PER_TOPIC]:
            out.setdefault(cands[vi].id, []).append((topics[ti]["id"], score))
    return {k: sorted(v, key=lambda r: (-r[1], r[0]))[:MAX_TOPICS_PER_VIDEO] for k, v in out.items()}


def pin_atlas(topics: list[dict[str, Any]], atlas: list[dict[str, Any]], embed: Embed) -> dict[str, list[str]]:
    """Atlas video (youtube id) -> its topic: the topic of its subject and step closest to the atlas chapter or title.

    The atlas groups YouTube videos under chapter names of its own, so there is no id to follow; every atlas video
    gets one topic (the best available, however weak) because the atlas already placed it in that subject."""
    out: dict[str, list[str]] = {}
    for a in atlas:
        step = "step1" if a["step"] == "S1" else "step2ck"
        group = [t for t in topics if t["subject"] == a["subject"] and t["step"] == step] or [
            t for t in topics if t["step"] == step
        ]
        if not group:
            continue
        s = norm(embed([a["chapter"], clean_title(a["title"])])) @ norm(embed([t["title"] for t in group])).T
        out[a["youtube_id"]] = [group[int(np.argmax(s.max(axis=0)))]["id"]]
    return out


def assemble(
    topics: list[dict[str, Any]], cands: list[Cand], hits: dict[str, list[tuple[str, float]]]
) -> list[dict[str, Any]]:
    """Fill `topics[*].media_ids` and return the video rows (sorted by id). Pinned topics come first."""
    tmap = {t["id"]: t for t in topics}
    rows: list[dict[str, Any]] = []
    for c in sorted(cands, key=lambda c: c.id):
        tids = list(dict.fromkeys(c.pinned + [t for t, _ in hits.get(c.id, [])]))
        if not tids:
            continue
        first = tmap[tids[0]]
        row = {
            "id": c.id, "provider": c.provider, "youtube_id": c.youtube_id, "file_url": c.extra.get("file_url"),
            "file_page": c.extra.get("file_page"), "channel_id": c.channel_id, "channel_name": c.channel_name,
            "style": c.style, "subject": first["subject"], "step": first["step"], "topic_ids": tids,
            "core": any(tmap[t]["core"] for t in tids), "licence": c.extra.get("licence"),
            "attribution": c.extra.get("attribution"), "label": first["title"],
        }  # fmt: skip
        if c.provider == "commons":
            row["duration_s"] = c.extra.get("duration_s")
        rows.append(row)
        for t in tids:
            tmap[t]["media_ids"].append(c.id)
    return rows


def report(topics: list[dict[str, Any]], videos: list[dict[str, Any]], log: Callable[[str], None]) -> float:
    for key in ("provider", "channel_name", "step", "subject"):
        log(f"videos by {key}: {dict(sorted(Counter(v[key] for v in videos).items()))}")
    gaps = [t for t in topics if not t["media_ids"]]
    cov = 100 * (1 - len(gaps) / max(len(topics), 1))
    log(f"topics covered {len(topics) - len(gaps)}/{len(topics)} ({cov:.1f}%); gaps by subject: "
        f"{dict(sorted(Counter(t['subject'] for t in gaps).items()))}")  # fmt: skip
    return cov


# ---------------------------------------------------------------- CLI


def memoized(embed: Embed, path: Path) -> Embed:
    store: dict[str, list[float]] = json.loads(path.read_text()) if path.exists() else {}

    def run(texts: Sequence[str]) -> list[list[float]]:
        todo = sorted({t for t in texts if t not in store})
        if todo:
            store.update(zip(todo, embed(todo), strict=True))
            path.write_text(json.dumps(store))
        return [store[t] for t in texts]

    return run


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--topic-map", type=Path, required=True, help="atlas topic map JSON (subjects + topics)")
    ap.add_argument("--atlas", type=Path, required=True, help="atlas YouTube videos JSON")
    ap.add_argument("--cache", type=Path, help="JSON cache of the crawl (skips the network when it exists)")
    ap.add_argument("--embed-cache", type=Path, help="JSON of text -> vector, speeds up re-tuning")
    ap.add_argument("--quota", type=int, default=3000, help="YouTube quota budget in units")
    ap.add_argument("--per-channel", type=int, default=1500, help="newest uploads read per channel")
    ap.add_argument("--commons-limit", type=int, default=600)
    ap.add_argument("--threshold", type=float, default=THRESHOLD)
    ap.add_argument("--sample", type=int, default=40)
    args = ap.parse_args()

    from naadi_api.embeddings.engine import get_engine
    from naadi_api.settings import REPO_ROOT, get_settings

    def log(msg: str) -> None:
        print(msg, flush=True)

    s = get_settings()
    base = json.loads(args.topic_map.read_text())
    atlas = json.loads(args.atlas.read_text())
    taxonomy = json.loads((REPO_ROOT / "data" / "taxonomy.json").read_text())
    subjects, topics = build_topics(base["topics"], base["subjects"], taxonomy)
    channels = load_channels(REPO_ROOT / "config" / "video_channels.yaml")
    engine = get_engine(s.config_dir)
    embed: Embed = engine.embed_passages
    if args.embed_cache:
        embed = memoized(embed, args.embed_cache)

    def cached(name: str, fetch: Callable[[], list[Cand]]) -> list[Cand]:
        f = args.cache.with_suffix(f".{name}.json") if args.cache else None
        if f and f.exists():
            return [Cand(**c) for c in json.loads(f.read_text())]
        got = fetch()
        if f and got:
            f.write_text(json.dumps([asdict(c) for c in got]))
        return got

    def youtube() -> list[Cand]:
        from naadi_api.youtube.client import GoogleYouTube, Quota

        if s.youtube_api_key is None:
            raise SystemExit("YOUTUBE_API_KEY is not set")
        api = GoogleYouTube(s.youtube_api_key.get_secret_value(), Quota(args.quota))
        out: list[Cand] = []
        try:
            for ch in channels:
                if not ch.channel_id and ch.handle:
                    found = api.channel_by_handle(ch.handle)
                    ch.channel_id = found["id"] if found else None
                    log(f"resolve {ch.handle}: {'ok' if found else 'not found, skipped'}")
            out = crawl_youtube(api, channels, args.per_channel, log)
            # atlas videos are verified too, whatever channel they came from
            have = {c.youtube_id for c in out}
            miss = [a["youtube_id"] for a in atlas if a["youtube_id"] not in have]
            got = api.videos(miss)
            by_name = {c.name.lower(): c for c in channels}
            for a in atlas:
                if a["youtube_id"] in miss:
                    ch = by_name.get(a["channel"].lower()) or Channel(
                        None, None, a["channel"], "lecture", ["step1", "step2ck"], ["all"]
                    )
                    if c := video_cand(got.get(a["youtube_id"]), ch):
                        out.append(c)
        except QuotaExceeded:
            log("quota budget reached: continuing with what was crawled")
        log(f"youtube quota used: {api.quota.used} of {args.quota}")
        return out

    def commons() -> list[Cand]:
        try:
            return crawl_commons(Commons(), args.commons_limit, log)
        except CommonsBlocked:
            log("commons: still rate limited after retries, catalogue is PARTIAL (no commons videos)")
            return []

    cands = cached("youtube", youtube) + cached("commons", commons)
    log(f"candidates: {len(cands)}")

    pins = pin_atlas(topics, atlas, embed)
    for c in cands:
        c.pinned = pins.get(c.youtube_id or "", [])
    hits = match(topics, cands, embed, args.threshold)
    videos = assemble(topics, cands, hits)
    cov = report(topics, videos, log)

    (REPO_ROOT / "data" / "video_topics.json").write_text(
        json.dumps({"version": 1, "subjects": subjects, "topics": topics}, indent=1, ensure_ascii=False) + "\n"
    )
    (REPO_ROOT / "data" / "videos.json").write_text(
        json.dumps({"version": 1, "videos": videos}, indent=1, ensure_ascii=False) + "\n"
    )
    # sample for eyeballing: the YouTube title is printed here only, never stored
    title = {c.id: c.title for c in cands}
    tt = {t["id"]: t["title"] for t in topics}
    pairs = sorted((t, v["id"]) for v in videos for t in v["topic_ids"])
    for t, vid in pairs[:: max(len(pairs) // max(args.sample, 1), 1)][: args.sample]:  # evenly spaced
        log(f"SAMPLE {tt[t]!r} <- {title[vid]!r} [{vid}]")
    return 0 if cov >= 90 else 1


if __name__ == "__main__":
    raise SystemExit(main())
