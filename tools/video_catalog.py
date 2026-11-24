#!/usr/bin/env python3
"""Build the Videos catalogue: `data/video_topics.json` (topic tree) and `data/videos.json` (video -> topics).

    uv --directory apps/api run python ../../tools/video_catalog.py [--cache DIR] [--min-coverage 90]

Inputs in the repo: `data/video_topic_map.json` (topic map), `config/video_pins.yaml` (curated YouTube ids),
`config/video_channels.yaml`, `config/sources.yaml` (Commons attribution template), `data/taxonomy.json`.

Sources: YouTube uploads of the channels in `config/video_channels.yaml` (official Data API, embeddable only) and
open-licence Wikimedia Commons videos. Titles are matched to topic titles with the local fastembed engine,
precision first: a gap is better than a wrong video. YouTube titles, descriptions, tags and durations are used in
memory only and are never written; the JSON holds ids plus Naadi-authored labels. Commons metadata (open licence) may
be written. Output is tiered: `media_ids` are direct, precise links; `chapter_media_ids` are related videos of the same
subject, step and chapter (the UI shows them as "related", never as the primary video).
Environment: YOUTUBE_API_KEY (through `naadi_api.settings`, never printed).
"""

from __future__ import annotations

import argparse
import html
import json
import re
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import yaml

from naadi_api.settings import REPO_ROOT
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
THRESHOLD = 0.83  # title cosine for a direct link; tuned on apps/api/tests/tools/video_eval.json
PIN_RELAX = 0.05  # curated pins may pass this much below the threshold, with the same lexical gate
MIN_JACCARD = 0.4  # content-word overlap between a video title and a topic key
REL_MARGIN = 0.08  # a video keeps only topics scoring within this of its best topic
FACET_PENALTY = 0.02
RESCUE_BAND = 0.0  # at the threshold, a key fully named in tags/description may pass with less title overlap
RESCUE_JACCARD = 0.25
FACET_DF = 8  # a single-word facet used by more topic keys than this ('cancer') is too common to match on
RERANK_BAND = 0.10  # borderline cosines the optional cross-encoder may rescue
RERANK_MIN = 2.0  # cross-encoder logit that rescues a borderline pair
FACET_RERANK_MIN = 0.0  # ... and that a facet match needs to count at all
CHAPTER_CAP = 6
UA = "MedEduCatalogBuilder/1.0 (educational video catalog)"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
COMMONS_QUERIES = ["Osmosis filetype:video", "VideoWiki Osmosis filetype:video"]
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
# a video about another context ("... Histology", "... in Children") is not the plain topic unless the topic says so
CONTEXT = re.compile(
    r"(?i)\b(histology|embryology|pediatrics?|paediatrics?|children|child|dogs?|cats?|veterinary|mcat)\b"
)
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
    """A video that can be attached to topics. `title` and `text` are used for matching only, never written."""

    id: str
    provider: str
    title: str
    channel_name: str
    style: str
    steps: list[str]
    subjects: list[str]
    youtube_id: str | None = None
    channel_id: str | None = None
    text: str = ""  # tags + description start: matching only, never written
    hint: str = ""  # a pin's chapter hint
    pin: bool = False
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
         "step": t["step"]}
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
        t["chapter_media_ids"] = []
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
        text=" ".join((snip.get("tags") or [])[:12]) + " " + (snip.get("description") or "")[:300],
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
                r.raise_for_status()  # a real client / server error is not a rate limit
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


def attribution_template() -> str:
    """The `wikimedia-commons` row of config/sources.yaml is the single source of the attribution string."""
    rows = yaml.safe_load((REPO_ROOT / "config" / "sources.yaml").read_text())["sources"]
    return str(next(r for r in rows if r["id"] == "wikimedia-commons")["attribution_template"])


def commons_cand(title: str, info: dict[str, Any]) -> Cand | None:
    licence = _meta(info, "LicenseShortName")
    if not LICENCE_OK.match(licence) or not str(info.get("mime", "")).startswith("video/"):
        return None
    name = title.removeprefix("File:")
    text = re.sub(r"\.\w+$", "", name)
    text = re.sub(r"(?i)[-_ ]*osmosis[-_ ]*video|videowiki|\(osmosis\)|[-_]", " ", text).strip()
    artist = _meta(info, "Artist") or "Unknown"
    page = "https://commons.wikimedia.org/wiki/" + title.replace(" ", "_")
    snapshot = {"license_short_name": licence, "license_url": _meta(info, "LicenseUrl"), "artist": artist}
    attribution = attribution_template().format(title=name, file_url=page, modifications="", **snapshot).rstrip()
    return Cand(
        "wm-" + slug(re.sub(r"\.\w+$", "", name)),
        "commons",
        text,
        "Osmosis" if "osmos" in (name + artist).lower() else artist,
        "animation", ["step1", "step2ck"], ["all"],
        extra={"file_url": info["url"].split("?")[0], "file_page": page, "licence": licence, "attribution": attribution,
               "licence_snapshot": snapshot, "duration_s": int(info.get("duration") or 0) or None},
    )  # fmt: skip


def crawl_commons(c: Commons, limit: int, log: Callable[[str], None]) -> list[Cand]:
    hits = {t for q in COMMONS_QUERIES for t in c.search(q, limit)}
    # the same film is often uploaded as .webm and .ogv: keep the .webm
    titles = sorted(t for t in hits if t.endswith(".webm") or re.sub(r"\.\w+$", ".webm", t) not in hits)
    log(f"commons: {len(titles)} search hits")
    infos = c.info(titles)
    out = [x for t in titles if t in infos and (x := commons_cand(t, infos[t]))]
    log(f"commons: {len(out)} with an allowed licence")
    return out


# ---------------------------------------------------------------- matching

FACET_SPLIT = re.compile(r"\s*(?:,|&|/|\band\b|\bvs\.?|:)\s*", re.I)
TOPIC_NOISE = re.compile(r"(?i)\bSOAPs?\b|\bSOA\b|\bpart\s*\d+\b")

Embed = Callable[[Sequence[str]], list[list[float]]]
Rerank = Callable[[str, Sequence[str]], list[float]]
Group = tuple[str, str, str]  # subject, step, chapter


@dataclass(frozen=True, slots=True)
class Key:
    """One matchable text of a topic: its cleaned title, a facet of a compound title, or its chapter title."""

    ti: int
    text: str
    kind: str  # main | facet | chapter


@dataclass(slots=True)
class Matches:
    direct: dict[str, list[tuple[str, float]]]  # video id -> [(topic id, score)], best first
    chapter: dict[str, list[tuple[Group, float]]]  # video id -> chapter groups it is related to


def clean_title(title: str) -> str:
    return re.split(r"\s[|:\-–]\s", title)[0].strip() or title


def tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w[:-1] if len(w) > 3 and w.endswith("s") else w for w in words if len(w) > 1 and w not in GENERIC}


def jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def topic_keys(title: str) -> list[tuple[str, str]]:
    """(text, kind) keys of a topic title: the title without SOAP / Part N / parentheses, plus one facet per part of a
    compound ("A, B & C", "A vs B") and per item of a parenthesis (prefixed with the head)."""
    head = re.sub(r"\s+", " ", re.sub(r"\b\d+\s*:", ":", TOPIC_NOISE.sub(" ", re.sub(r"\([^)]*\)", " ", title))))
    head = head.strip(" :-,&")
    keys = [(head, "main")] if tokens(head) else [(title, "main")]
    seen = {keys[0][0].lower()}

    def add(text: str, facet: str) -> None:
        text = re.sub(r"\s+", " ", text).strip(" :-,&")
        if text.lower() not in seen and any(len(w) >= 5 for w in tokens(facet)):
            seen.add(text.lower())
            keys.append((text, "facet"))

    for part in FACET_SPLIT.split(head):
        add(part, part)
    for group in re.findall(r"\(([^)]*)\)", title):
        for part in FACET_SPLIT.split(TOPIC_NOISE.sub(" ", group)):
            add(f"{head} {part}", part)
    return keys[:7]


def group_of(t: dict[str, Any]) -> Group:
    return (t["subject"], t["step"], t["chapter"])


def norm(m: Any) -> Any:
    a = np.asarray(m, dtype=np.float32)
    return a / np.maximum(np.linalg.norm(a, axis=1, keepdims=True), 1e-9)


def match(
    topics: list[dict[str, Any]],
    cands: list[Cand],
    embed: Embed,
    threshold: float = THRESHOLD,
    rerank: Rerank | None = None,
) -> Matches:
    """Score every (video, topic key) pair; a pair is a direct link when the title cosine passes the threshold and the
    content words overlap (Jaccard). Below the threshold a key that the tags / description name in full may still
    pass (rescue), as may a borderline pair the cross-encoder approves. A video keeps only topics scoring within
    REL_MARGIN of its best, a topic its 3 best videos. A chapter-title match is a chapter-tier link, never direct.
    Curated pins pass the same tests with the threshold relaxed by PIN_RELAX; a pin that fails is attached to the
    chapter of its nearest key instead of any topic."""
    cands = [c for c in cands if not NOISE.search(c.title) and "nursing" not in c.channel_name.lower()]
    empty = Matches({}, {})
    if not topics or not cands:
        return empty
    groups: dict[Group, list[int]] = {}
    for ti, t in enumerate(topics):
        groups.setdefault(group_of(t), []).append(ti)
    glist = list(groups)
    keys = [Key(ti, text, kind) for ti, t in enumerate(topics) for text, kind in topic_keys(t["title"])]
    df = Counter(w for k in keys for w in tokens(k.text))
    keys = [
        k
        for k in keys
        if not (k.kind == "facet" and len(tokens(k.text)) == 1 and df[next(iter(tokens(k.text)))] > FACET_DF)
    ]
    nt = len(keys)
    keys += [Key(groups[g][0], g[2], "chapter") for g in glist]
    kvec = norm(embed([k.text for k in keys]))
    sims = norm(embed([clean_title(c.title) for c in cands])) @ kvec.T
    hinted = [i for i, c in enumerate(cands) if c.hint]
    if hinted:
        sims[hinted] = np.maximum(sims[hinted], norm(embed([cands[i].hint for i in hinted])) @ kvec.T)
    ktok = [tokens(k.text) for k in keys]
    vtok = [tokens(clean_title(c.title)) | tokens(c.hint) for c in cands]
    vctx = [{w.lower() for w in CONTEXT.findall(c.title)} for c in cands]
    vrich = [vtok[i] | tokens(c.text) for i, c in enumerate(cands)]

    direct: dict[tuple[int, int], float] = {}
    chap: dict[tuple[int, int], float] = {}
    border: dict[int, list[tuple[int, float]]] = {}
    facets: dict[int, list[tuple[int, float]]] = {}
    low = threshold - PIN_RELAX - max(RESCUE_BAND, RERANK_BAND)
    for vi, ki in np.argwhere(sims >= low):
        c, k = cands[vi], keys[ki]
        t = topics[k.ti]
        if t["step"] not in c.steps or ("all" not in c.subjects and t["subject"] not in c.subjects):
            continue
        if vctx[vi] - (
            {w.lower() for w in CONTEXT.findall(k.text)}
            | ({"pediatrics", "children"} if t["subject"] == "Pediatrics" else set())
        ):
            continue
        s, thr = float(sims[vi, ki]), threshold - (PIN_RELAX if c.pin else 0.0)
        jac = jaccard(vtok[vi], ktok[ki])
        score: float | None = None
        if s >= thr and jac >= MIN_JACCARD:
            score = s + 0.10 * jac - (FACET_PENALTY if k.kind == "facet" else 0.0)
        elif k.kind != "chapter" and s >= thr - RESCUE_BAND and jac >= RESCUE_JACCARD and ktok[ki] <= vrich[vi]:
            score = s - 0.03
        elif rerank and k.kind != "chapter" and s >= thr - RERANK_BAND and jac >= MIN_JACCARD:
            border.setdefault(int(vi), []).append((int(ki), s))
        if score is None:
            continue
        if k.kind == "chapter":
            gi = ki - nt
            chap[(int(vi), gi)] = max(chap.get((int(vi), gi), 0.0), score)
        elif k.kind == "facet" and rerank:
            facets.setdefault(int(vi), []).append((int(ki), score))  # a facet match is confirmed below
        else:
            direct[(int(vi), k.ti)] = max(direct.get((int(vi), k.ti), 0.0), score)
    if rerank:
        for vi, frows in facets.items():
            for (ki, sc), r in zip(
                frows, rerank(clean_title(cands[vi].title), [keys[ki].text for ki, _ in frows]), strict=True
            ):
                if r >= FACET_RERANK_MIN:
                    direct[(vi, keys[ki].ti)] = max(direct.get((vi, keys[ki].ti), 0.0), sc)
        for vi, brows in border.items():
            thr = threshold - (PIN_RELAX if cands[vi].pin else 0.0)
            for (ki, _), r in zip(
                brows, rerank(clean_title(cands[vi].title), [keys[ki].text for ki, _ in brows]), strict=True
            ):
                if r >= RERANK_MIN:
                    direct[(vi, keys[ki].ti)] = max(direct.get((vi, keys[ki].ti), 0.0), thr)

    per_v: dict[int, list[tuple[float, int]]] = defaultdict(list)
    for (vi, ti), sc in direct.items():
        per_v[vi].append((sc, ti))
    per_t: dict[int, list[tuple[float, float, int]]] = defaultdict(list)
    for vi, rows in per_v.items():
        c = cands[vi]
        top = max(sc for sc, _ in rows)
        for sc, ti in rows:
            if sc >= top - REL_MARGIN:
                rank = sc + 0.01 * STYLE_RANK[c.style] + (0.02 if topics[ti]["subject"] in c.subjects else 0)
                per_t[ti].append((rank, sc, vi))
    kept: dict[int, list[tuple[float, float, int]]] = defaultdict(list)  # video -> (rank, score, topic)
    for ti, trows in per_t.items():
        trows.sort(key=lambda r: (-r[0], cands[r[2]].id))
        for rank, sc, vi in trows[:TOP_PER_TOPIC]:
            kept[vi].append((rank, sc, ti))
    out = Matches({}, {})
    for vi, krows in kept.items():
        krows.sort(key=lambda r: (-r[0], topics[r[2]]["id"]))
        out.direct[cands[vi].id] = [(topics[ti]["id"], rank) for rank, _, ti in krows[:MAX_TOPICS_PER_VIDEO]]
    for (vi, gi), sc in sorted(chap.items()):
        out.chapter.setdefault(cands[vi].id, []).append((glist[gi], sc))
    for vi, c in enumerate(cands):  # a pin that matched nothing still joins the chapter of its nearest key
        if c.pin and c.id not in out.direct and c.id not in out.chapter:
            ok = [
                ki for ki, k in enumerate(keys)
                if topics[k.ti]["step"] in c.steps and topics[k.ti]["subject"] in c.subjects
            ]  # fmt: skip
            if ok:
                ki = max(ok, key=lambda i: float(sims[vi, i]))
                out.chapter[c.id] = [(group_of(topics[keys[ki].ti]), float(sims[vi, ki]))]
    return out if (out.direct or out.chapter) else empty


def apply_pins(
    cands: list[Cand], pins: list[dict[str, Any]], by_channel: dict[str, Channel] | None = None
) -> list[Cand]:
    """Mark the curated videos: they are limited to their pin's subject and step and carry its chapter hint."""
    by_yt = {c.youtube_id: c for c in cands}
    for p in pins:
        c = by_yt.get(p["youtube_id"])
        if c is None:
            continue  # unavailable today (not embeddable, deleted, too short ...): the catalogue skips it
        c.pin, c.hint, c.steps, c.subjects = True, p["chapter"], [p["step"]], [p["subject"]]
        c.extra["core"] = bool(p.get("core", True))
    return cands


def assemble(topics: list[dict[str, Any]], cands: list[Cand], m: Matches) -> list[dict[str, Any]]:
    """Fill `media_ids` / `chapter_media_ids` of the topics and return the video rows (sorted by id).

    Direct links come from the matcher. A topic's chapter tier holds the videos linked to another topic of the same
    subject, step and chapter, plus chapter-title matches and weak pins; it never repeats a direct link."""
    tmap = {t["id"]: t for t in topics}
    members: dict[Group, list[str]] = defaultdict(list)
    for topic in topics:
        members[group_of(topic)].append(topic["id"])
    direct_links: dict[str, list[tuple[float, str]]] = defaultdict(list)
    chapter_links: dict[str, dict[str, float]] = defaultdict(dict)
    rows: list[dict[str, Any]] = []
    for c in sorted(cands, key=lambda c: c.id):
        d = m.direct.get(c.id, [])
        groups: dict[Group, float] = {group_of(tmap[t]): sc for t, sc in reversed(d)}
        for g, sc in m.chapter.get(c.id, []):
            groups.setdefault(g, sc)
        if not d and not groups:
            continue
        if d:
            first = tmap[d[0][0]]
            subject, step = first["subject"], first["step"]
            labels = [tmap[t]["title"] for t, _ in d]  # best-scored direct topic first
            core = any(tmap[t]["core"] for t, _ in d)
        else:
            g0 = next(iter(groups))
            subject, step = g0[0], g0[1]
            labels = [g[2] for g in groups]  # chapter-tier topics' chapters
            core = bool(c.extra.get("core", any(tmap[t]["core"] for t in members[g0])))
        label = next((x for x in (*labels, subject) if x.strip()), "")
        assert label.strip(), f"blank label for {c.id}"
        rows.append(
            {
                "id": c.id, "provider": c.provider, "youtube_id": c.youtube_id, "file_url": c.extra.get("file_url"),
                "file_page": c.extra.get("file_page"), "channel_id": c.channel_id, "channel_name": c.channel_name,
                "style": c.style, "subject": subject, "step": step, "topic_ids": [t for t, _ in d], "core": core,
                "licence": c.extra.get("licence"), "licence_snapshot": c.extra.get("licence_snapshot"),
                "attribution": c.extra.get("attribution"), "label": label,
            }
        )  # fmt: skip
        if c.provider == "commons":
            rows[-1]["duration_s"] = c.extra.get("duration_s")
        for tid, sc in d:
            direct_links[tid].append((sc, c.id))
        own = {tid for tid, _ in d}
        for g, sc in groups.items():
            for tid in members[g]:
                if tid not in own:
                    chapter_links[tid][c.id] = max(chapter_links[tid].get(c.id, 0.0), sc)
    for topic in topics:
        topic["media_ids"] = [v for _, v in sorted(direct_links[topic["id"]], key=lambda r: (-r[0], r[1]))]
        rel = sorted(chapter_links[topic["id"]].items(), key=lambda r: (-r[1], r[0]))
        topic["chapter_media_ids"] = [v for v, _ in rel[:CHAPTER_CAP]]
    return rows


def coverage(topics: list[dict[str, Any]]) -> tuple[float, float]:
    """(% of topics with a direct video, % with a direct or chapter-tier video)."""
    n = max(len(topics), 1)
    return (
        100 * sum(bool(t["media_ids"]) for t in topics) / n,
        100 * sum(bool(t["media_ids"] or t["chapter_media_ids"]) for t in topics) / n,
    )


def exit_code(total_coverage: float, min_coverage: float) -> int:
    return 0 if total_coverage >= min_coverage else 1


def report(topics: list[dict[str, Any]], videos: list[dict[str, Any]], log: Callable[[str], None]) -> float:
    for key in ("provider", "channel_name", "step", "subject"):
        log(f"videos by {key}: {dict(sorted(Counter(v[key] for v in videos).items()))}")
    log(f"videos with a direct link: {sum(bool(v['topic_ids']) for v in videos)}, chapter tier only: "
        f"{sum(not v['topic_ids'] for v in videos)}")  # fmt: skip
    direct, total = coverage(topics)
    gaps = [t for t in topics if not (t["media_ids"] or t["chapter_media_ids"])]
    log(f"direct coverage {direct:.1f}%; direct + chapter tier {total:.1f}% ({len(topics) - len(gaps)}/{len(topics)})")
    log(f"gaps by subject: {dict(sorted(Counter(t['subject'] for t in gaps).items()))}")
    return total


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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--topic-map", type=Path, default=REPO_ROOT / "data" / "video_topic_map.json")
    ap.add_argument("--pins", type=Path, default=REPO_ROOT / "config" / "video_pins.yaml")
    ap.add_argument("--cache", type=Path, help="crawl cache (holds YouTube titles: keep it OUTSIDE the repo)")
    ap.add_argument("--embed-cache", type=Path, help="JSON of text -> vector (holds YouTube titles: outside the repo)")
    ap.add_argument("--quota", type=int, default=3000, help="YouTube quota budget in units")
    ap.add_argument("--per-channel", type=int, default=6000, help="newest uploads read per channel")
    ap.add_argument("--commons-limit", type=int, default=700)
    ap.add_argument("--threshold", type=float, default=THRESHOLD)
    ap.add_argument("--min-coverage", type=float, default=0, help="exit 1 below this direct + chapter coverage %%")
    ap.add_argument(
        "--no-rerank", action="store_true", help="skip the local cross-encoder (borderline rescue, facet check)"
    )
    ap.add_argument("--offline", action="store_true", help="never crawl: needs --cache and --embed-cache files")
    ap.add_argument("--sample", type=int, default=40)
    args = ap.parse_args(argv)
    if args.offline and not (args.cache and args.embed_cache):
        ap.error("--offline needs --cache and --embed-cache")

    from naadi_api.embeddings.engine import get_engine
    from naadi_api.settings import get_settings

    def log(msg: str) -> None:
        print(msg, flush=True)

    s = get_settings()
    base = json.loads(args.topic_map.read_text())
    pins = yaml.safe_load(args.pins.read_text())["pins"]
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
        if args.offline:
            raise SystemExit(f"--offline: cache {f} is missing")
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
            have = {c.youtube_id for c in out}
            miss = [p["youtube_id"] for p in pins if p["youtube_id"] not in have]
            got = api.videos(miss)  # pinned videos of channels outside the list are verified too
            by_id = {c.channel_id: c for c in channels}
            for vid in miss:
                snip = (got.get(vid) or {}).get("snippet") or {}
                ch = by_id.get(snip.get("channelId")) or Channel(
                    None, snip.get("channelId"), snip.get("channelTitle", ""), "lecture", ["step1", "step2ck"], ["all"]
                )
                if c := video_cand(got.get(vid), ch):
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

    commons_cands = cached("commons", commons)
    for c in commons_cands:  # caches from before the empty-modifications fix end in ". none"
        c.extra["attribution"] = c.extra["attribution"].removesuffix(" none")
    cands = apply_pins(cached("youtube", youtube) + commons_cands, pins)
    log(f"candidates: {len(cands)}, pins applied: {sum(c.pin for c in cands)} of {len(pins)}")
    rerank = None if args.no_rerank else engine.rerank
    m = match(topics, cands, embed, args.threshold, rerank)
    videos = assemble(topics, cands, m)
    total = report(topics, videos, log)

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
    for t, vid in pairs[:: max(len(pairs) // max(args.sample, 1), 1)][: args.sample]:
        log(f"SAMPLE {tt[t]!r} <- {title[vid]!r} [{vid}]")
    return exit_code(total, args.min_coverage)


if __name__ == "__main__":
    raise SystemExit(main())
