#!/usr/bin/env python3
"""Map allow-listed YouTube channels' uploads to Naadi taxonomy topics (for human approval).

Pipeline (quota-friendly; never calls search.list, which is capped at 100 calls/day):
  1. Read config/youtube_channels.yaml (include: true entries).
  2. Resolve channel IDs: channels.list?part=id,snippet,contentDetails&forHandle=@handle   (1 unit)
     or channels.list?id=UC...                                                          (1 unit)
     -> uploads playlist id (contentDetails.relatedPlaylists.uploads).
  3. List uploads: playlistItems.list?part=contentDetails&maxResults=50 (1 unit/page).
  4. videos.list?part=snippet,contentDetails,status&id=<<=50 ids> (1 unit per 50 videos).
  5. Keep embeddable, public, processed videos. Record madeForKids (policy III.E.4.j).
  6. Score title/description/tags against taxonomy topics (keyword rules shared with
     tools/map_taxonomy.py). Write data/youtube_candidates.json with review_status
     'unreviewed' and metadata_fetched_at for every row.

--refresh re-fetches videos.list for every candidate whose metadata_fetched_at is older than
30 days (YouTube API Developer Policies III.E.4.d: non-authorized data may be stored no longer
than 30 calendar days). Rows that cannot be refreshed (deleted, private, no longer embeddable)
have their cached metadata purged; only the video ID and the human review decision are kept.

Environment: YOUTUBE_API_KEY (required for any network call).

Examples
  YOUTUBE_API_KEY=... python tools/youtube_mapper.py                 # crawl all included channels
  YOUTUBE_API_KEY=... python tools/youtube_mapper.py --channel @medcram --max-videos 200
  YOUTUBE_API_KEY=... python tools/youtube_mapper.py --refresh       # 30-day policy refresh only
  python tools/youtube_mapper.py --resolve-only                      # print handle -> channelId
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import yaml

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import map_taxonomy as mt  # noqa: E402  (shared topic keyword rules)

API = "https://www.googleapis.com/youtube/v3/"
CHANNELS_YAML = ROOT / "config" / "youtube_channels.yaml"
OUT = ROOT / "data" / "youtube_candidates.json"
MAX_METADATA_AGE_DAYS = 30          # hard policy ceiling (III.E.4.d)
DEFAULT_QUOTA_BUDGET = 9000         # default project quota is 10,000 units/day
BATCH = 50                          # videos.list / playlistItems.list max page size
DESC_CHARS = 1200                   # keep only what scoring/review needs


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso(t: dt.datetime) -> str:
    return t.isoformat().replace("+00:00", "Z")


def parse_iso(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


_DUR = re.compile(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


def iso_duration_s(d: str | None) -> int | None:
    if not d:
        return None
    m = _DUR.fullmatch(d)
    if not m:
        return None
    days, h, mi, s = (int(x) if x else 0 for x in m.groups())
    return days * 86400 + h * 3600 + mi * 60 + s


class QuotaExceeded(RuntimeError):
    pass


class YouTube:
    """Minimal YouTube Data API v3 client with unit accounting. search.list is deliberately absent."""

    COST = {"channels": 1, "playlistItems": 1, "videos": 1, "playlists": 1}

    def __init__(self, key: str, budget: int):
        self.key, self.budget, self.used = key, budget, 0

    def _get(self, resource: str, **params) -> dict:
        if resource not in self.COST:
            raise ValueError(f"{resource} not allowed (search.list is quota-capped; do not use)")
        cost = self.COST[resource]
        if self.used + cost > self.budget:
            raise QuotaExceeded(f"quota budget {self.budget} reached")
        params = {k: v for k, v in params.items() if v is not None}
        params["key"] = self.key
        url = API + resource + "?" + urllib.parse.urlencode(params)
        for attempt in range(5):
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": "application/json"}), timeout=30) as r:
                    self.used += cost
                    return json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "replace")
                if e.code == 403 and ("quotaExceeded" in body or "dailyLimitExceeded" in body):
                    raise QuotaExceeded(body[:300]) from e
                if e.code in (429, 500, 502, 503, 504) and attempt < 4:
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError(f"{resource} HTTP {e.code}: {body[:300]}") from e
            except urllib.error.URLError:
                if attempt < 4:
                    time.sleep(2 ** attempt)
                    continue
                raise
        raise RuntimeError("unreachable")

    # channels.list (1 unit)
    def channel(self, handle: str | None = None, channel_id: str | None = None) -> dict | None:
        if channel_id:
            res = self._get("channels", part="id,snippet,contentDetails", id=channel_id)
        elif handle:
            res = self._get("channels", part="id,snippet,contentDetails", forHandle=handle if handle.startswith("@") else "@" + handle)
        else:
            return None
        items = res.get("items") or []
        return items[0] if items else None

    # playlistItems.list (1 unit per page of 50)
    def playlist_video_ids(self, playlist_id: str, limit: int | None) -> list[str]:
        ids, token = [], None
        while True:
            res = self._get("playlistItems", part="contentDetails", playlistId=playlist_id, maxResults=BATCH, pageToken=token)
            for it in res.get("items", []):
                vid = (it.get("contentDetails") or {}).get("videoId")
                if vid:
                    ids.append(vid)
                    if limit and len(ids) >= limit:
                        return ids
            token = res.get("nextPageToken")
            if not token:
                return ids

    # videos.list (1 unit per 50 ids)
    def videos(self, ids: list[str]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for i in range(0, len(ids), BATCH):
            chunk = ids[i:i + BATCH]
            res = self._get("videos", part="snippet,contentDetails,status", id=",".join(chunk), maxResults=BATCH)
            for it in res.get("items", []):
                out[it["id"]] = it
        return out


# ─────────────────────────── taxonomy scoring ───────────────────────────
def topic_candidates(title: str, description: str, tags: list[str], exams: list[str], top_k: int = 3, min_score: float = 2.0) -> list[dict]:
    fields = [(mt.norm(title), 3.0), (mt.norm(" ".join(tags or [])), 2.0), (mt.norm(description), 1.0)]
    scores = mt.score_topics(fields)
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1][0], kv[0]))
    out = []
    for tid, (s, hits) in ranked:
        if s < min_score or len(out) >= top_k:
            break
        dom = mt.domain_of(tid)
        systems = [mt.system_id(e, dom) for e in exams if e in mt.EXAM_INDEX]
        out.append({"topic_id": tid, "score": round(s, 2), "matched": hits[:8], "system_ids": systems})
    return out


def video_row(item: dict, channel: dict, fetched_at: str) -> dict:
    sn, cd, st = item.get("snippet", {}), item.get("contentDetails", {}), item.get("status", {})
    vid = item["id"]
    title, desc = sn.get("title", ""), (sn.get("description") or "")[:DESC_CHARS]
    exams = channel.get("exams") or ["step1"]
    return {
        "media_id": f"yt:{vid}",
        "kind": "video_youtube",
        "youtube_id": vid,
        "channel_id": sn.get("channelId") or channel.get("channel_id"),
        "source_id": f"youtube:{sn.get('channelId') or channel.get('channel_id')}",
        "channel_name": sn.get("channelTitle") or channel.get("name"),
        "title": title,
        "description_excerpt": desc,
        "published_at": sn.get("publishedAt"),
        "duration_s": iso_duration_s(cd.get("duration")),
        "embeddable": bool(st.get("embeddable")),
        "privacy_status": st.get("privacyStatus"),
        "made_for_kids": st.get("madeForKids"),
        "url": f"https://www.youtube.com/watch?v={vid}",
        "embed_url": f"https://www.youtube-nocookie.com/embed/{vid}",
        "attribution": f"Video: \"{title}\" by {sn.get('channelTitle') or channel.get('name')} on YouTube",
        "exams": exams,
        "taxonomy_candidates": topic_candidates(title, desc, sn.get("tags") or [], exams),
        "metadata_fetched_at": fetched_at,
    }


def keep(item: dict) -> bool:
    st = item.get("status", {})
    return bool(st.get("embeddable")) and st.get("privacyStatus") == "public" and st.get("uploadStatus", "processed") == "processed"


# ─────────────────────────── persistence ───────────────────────────
HUMAN_FIELDS = ("review_status", "approved_taxonomy_ids", "reviewer", "reviewed_at", "review_notes")


def load_existing(path: pathlib.Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    return {r["youtube_id"]: r for r in data.get("candidates", [])}


def merge(old: dict | None, new: dict) -> dict:
    row = dict(new)
    for f in HUMAN_FIELDS:
        if old and f in old:
            row[f] = old[f]
    row.setdefault("review_status", "unreviewed")
    return row


def purge_metadata(row: dict, reason: str) -> dict:
    """Keep only the ID and human decision; drop cached YouTube metadata (30-day rule)."""
    kept = {k: row[k] for k in ("media_id", "kind", "youtube_id", "channel_id", "source_id", *HUMAN_FIELDS) if k in row}
    kept.update({"embeddable": False, "metadata_fetched_at": None, "metadata_purged_reason": reason,
                 "metadata_purged_at": iso(now_utc())})
    return kept


def write(path: pathlib.Path, rows: dict[str, dict], quota_used: int) -> None:
    payload = {
        "generated_at": iso(now_utc()),
        "policy": {
            "max_metadata_age_days": MAX_METADATA_AGE_DAYS,
            "note": "Non-authorized YouTube metadata must be refreshed or deleted within 30 days "
                    "(Developer Policies III.E.4.d). Run --refresh at least weekly. Do not place embeds "
                    "behind a paywall (III.F.3.a) pending legal review.",
        },
        "quota_units_used_this_run": quota_used,
        "candidates": sorted(rows.values(), key=lambda r: (r.get("channel_id") or "", r.get("published_at") or "", r["youtube_id"])),
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n")
    tmp.replace(path)


# ─────────────────────────── commands ───────────────────────────
def load_channels(path: pathlib.Path, only: list[str] | None) -> list[dict]:
    chans = (yaml.safe_load(path.read_text()) or {}).get("channels", [])
    sel = []
    for c in chans:
        keys = {c.get("handle"), c.get("channel_id"), c.get("name")}
        if only:
            if keys & set(only):
                sel.append(c)
        elif c.get("include", True):
            sel.append(c)
    return sel


def resolve(yt: YouTube, ch: dict) -> dict | None:
    info = yt.channel(handle=ch.get("handle"), channel_id=ch.get("channel_id"))
    if not info:
        print(f"  ! could not resolve {ch.get('handle') or ch.get('channel_id')} ({ch.get('name')})", file=sys.stderr)
        return None
    resolved = dict(ch)
    resolved["channel_id"] = info["id"]
    resolved["resolved_title"] = info.get("snippet", {}).get("title")
    resolved["uploads_playlist"] = info.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
    if ch.get("channel_id") and ch["channel_id"] != info["id"]:
        print(f"  ! channel id mismatch for {ch.get('name')}: yaml {ch['channel_id']} api {info['id']}", file=sys.stderr)
    return resolved


def cmd_crawl(a, yt: YouTube) -> int:
    rows = load_existing(a.out)
    fetched_at = iso(now_utc())
    for ch in load_channels(a.channels_yaml, a.channel):
        print(f"- {ch.get('name')} ({ch.get('handle') or ch.get('channel_id')})")
        rc = resolve(yt, ch)
        if not rc or not rc.get("uploads_playlist"):
            continue
        if not ch.get("verified"):
            print(f"  resolved to {rc['channel_id']} '{rc['resolved_title']}' - confirm, then set channel_id + verified: true in the YAML")
        vids = yt.playlist_video_ids(rc["uploads_playlist"], a.max_videos)
        meta = yt.videos(vids)
        kept = 0
        for vid in vids:
            item = meta.get(vid)
            if not item or not keep(item):
                if vid in rows:  # previously kept, now unavailable/non-embeddable
                    rows[vid] = purge_metadata(rows[vid], "not embeddable/public at crawl")
                continue
            row = video_row(item, rc, fetched_at)
            if not row["taxonomy_candidates"] and not a.keep_unmatched:
                continue
            rows[vid] = merge(rows.get(vid), row)
            kept += 1
        print(f"  {len(vids)} uploads scanned, {kept} embeddable candidates kept; quota used so far {yt.used}")
        write(a.out, rows, yt.used)  # checkpoint after each channel
    write(a.out, rows, yt.used)
    print(f"wrote {a.out}: {len(rows)} rows; quota units used {yt.used}")
    return 0


def cmd_refresh(a, yt: YouTube) -> int:
    rows = load_existing(a.out)
    max_age = min(a.max_age_days, MAX_METADATA_AGE_DAYS)
    cutoff = now_utc() - dt.timedelta(days=max_age)
    stale = [vid for vid, r in rows.items()
             if r.get("metadata_fetched_at") is not None and parse_iso(r["metadata_fetched_at"]) < cutoff]
    print(f"{len(stale)} of {len(rows)} rows older than {max_age} days")
    chans = {c.get("channel_id"): c for c in load_channels(a.channels_yaml, None) if c.get("channel_id")}
    fetched_at = iso(now_utc())
    refreshed = purged = 0
    try:
        meta = yt.videos(stale)
    except QuotaExceeded:
        # Could not refresh in time: purge stale metadata rather than keep it past 30 days.
        meta = {}
        print("quota exhausted during refresh; purging stale metadata", file=sys.stderr)
    for vid in stale:
        item = meta.get(vid)
        if item and keep(item):
            old = rows[vid]
            ch = chans.get(old.get("channel_id")) or {"channel_id": old.get("channel_id"), "name": old.get("channel_name"), "exams": old.get("exams")}
            rows[vid] = merge(old, video_row(item, ch, fetched_at))
            refreshed += 1
        else:
            rows[vid] = purge_metadata(rows[vid], "refresh failed or video unavailable/non-embeddable")
            purged += 1
    write(a.out, rows, yt.used)
    print(f"refreshed {refreshed}, purged {purged}; quota units used {yt.used}")
    return 0


def cmd_resolve_only(a, yt: YouTube) -> int:
    for ch in load_channels(a.channels_yaml, a.channel):
        rc = resolve(yt, ch)
        if rc:
            print(f"{ch.get('handle') or '-':32s} {rc['channel_id']}  {rc['resolved_title']}  uploads={rc['uploads_playlist']}")
    print(f"quota units used {yt.used}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--channels-yaml", type=pathlib.Path, default=CHANNELS_YAML)
    ap.add_argument("--out", type=pathlib.Path, default=OUT)
    ap.add_argument("--channel", action="append", help="limit to handle/channel_id/name (repeatable; includes include:false)")
    ap.add_argument("--max-videos", type=int, default=None, help="max uploads per channel (newest first)")
    ap.add_argument("--keep-unmatched", action="store_true", help="keep videos with no topic match")
    ap.add_argument("--quota-budget", type=int, default=DEFAULT_QUOTA_BUDGET)
    ap.add_argument("--refresh", action="store_true", help="re-fetch metadata older than --max-age-days (<=30)")
    ap.add_argument("--max-age-days", type=int, default=MAX_METADATA_AGE_DAYS)
    ap.add_argument("--resolve-only", action="store_true", help="resolve handles to channel IDs and exit")
    a = ap.parse_args(argv)

    key = os.environ.get("YOUTUBE_API_KEY")
    if not key:
        print("YOUTUBE_API_KEY is not set", file=sys.stderr)
        return 2
    a.out.parent.mkdir(parents=True, exist_ok=True)
    yt = YouTube(key, a.quota_budget)
    try:
        if a.resolve_only:
            return cmd_resolve_only(a, yt)
        if a.refresh:
            return cmd_refresh(a, yt)
        return cmd_crawl(a, yt)
    except QuotaExceeded as e:
        print(f"stopped: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
