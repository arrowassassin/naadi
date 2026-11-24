"""The catalogue search shared by the Videos routes and the assistant's `suggest_video`: one ranking, one SQL.

Everything here runs on a caller's connection (RLS: only approved media are visible)."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import psycopg
import yaml

COMMONS = "commons"
MAX_WORDS = 6


# the channel key of a video: its channel id, or "commons"
CH = "(case when c.provider = 'commons' then 'commons' else m.channel_id end)"

ITEM_COLS = """
c.media_id, c.provider, c.subject, c.step, c.style, c.core, m.title, m.youtube_id, m.url as file_url, m.page_url,
m.licence, m.attribution, m.duration_s, m.channel_id, (w.media_id is not null) as watched, tp.topics,
count(*) over () as total
"""
ITEM_FROM = """
from public.video_catalog c
join public.media_assets m on m.id = c.media_id
left join public.video_watches w on w.media_id = c.media_id and w.user_id = (select auth.uid())
cross join lateral (
  select coalesce(jsonb_agg(jsonb_build_object('id', t.id, 'title', t.title, 'chapter', t.chapter) order by t.sort),
                  '[]'::jsonb) as topics
  from public.video_topics t where t.id = any(c.topic_ids)
) tp
"""


@lru_cache(maxsize=4)
def channel_names(config_dir: Path) -> dict[str, str]:
    try:
        data = yaml.safe_load((config_dir / "video_channels.yaml").read_text(encoding="utf-8")) or {}
    except OSError:
        return {}
    return {c["channel_id"]: c["name"] for c in data.get("channels", []) if c.get("channel_id") and c.get("name")}


def names_for(config_dir: Path) -> dict[str, str]:
    """Channel id -> display name, plus the "commons" pseudo-channel."""
    return {**channel_names(config_dir), COMMONS: "Wikimedia Commons"}


def stem(word: str) -> str:
    """A plural and its singular match each other: "blockers" -> "blocker", "diseases" -> "diseas" (both are then
    matched as substrings). shortcut: English plurals only, upgrade to FTS stemming if recall still falls short."""
    if len(word) <= 3 or not word.endswith("s") or word.endswith("ss"):
        return word
    return word[:-2] if word.endswith("es") and len(word) > 4 else word[:-1]


def query_words(q: str) -> list[str]:
    return [stem(w) for w in re.findall(r"[^\W_]+", q.lower())][:MAX_WORDS]


def search_filter(q: str, names: dict[str, str], *, any_word: bool = False) -> tuple[str, str, dict[str, Any]]:
    """(filter, rank expression, params) for a free-text query: every word must match the video title, subject,
    a direct topic's title or chapter, or the channel name. Rank: topic title > video title > chapter > channel.
    With `any_word`, one matching word is enough and the rank is the number of words matched (most first).
    shortcut: ILIKE scans the filtered catalogue (a few thousand rows), upgrade to trigram/FTS if it grows 10x."""
    words = query_words(q)
    if not words:
        return "true", "0", {}
    params: dict[str, Any] = {}
    hit, topic, title, chapter, channel = [], [], [], [], []
    for i, word in enumerate(words):
        params[f"p{i}"] = f"%{word}%"
        params[f"c{i}"] = [cid for cid, name in names.items() if word in name.lower()]
        p, ch = (
            f"%(p{i})s",
            f"{CH} = any(%(c{i})s::text[])",
        )
        topic.append(f"t.title ilike {p}")
        chapter.append(f"t.chapter ilike {p}")
        title.append(f"m.title ilike {p}")
        channel.append(ch)
        hit.append(
            f"(m.title ilike {p} or c.subject ilike {p} or {ch} or exists (select 1 from public.video_topics t "  # noqa: S608
            f"where t.id = any(c.topic_ids) and (t.title ilike {p} or t.chapter ilike {p})))"  # placeholders only
        )
    if any_word:
        return " or ".join(hit), "-(" + " + ".join(f"{h}::int" for h in hit) + ")", params
    sub = "exists (select 1 from public.video_topics t where t.id = any(c.topic_ids) and {})"
    rank = (
        f"case when {sub.format(' and '.join(topic))} then 0 when {' and '.join(title)} then 1 "
        f"when {sub.format(' and '.join(chapter))} then 2 when {' and '.join(channel)} then 3 else 4 end"
    )
    return " and ".join(hit), rank, params


async def search_videos(
    conn: psycopg.AsyncConnection[Any],
    q: str,
    names: dict[str, str],
    *,
    step: str | None,
    limit: int,
    any_word: bool = False,
) -> list[dict[str, Any]]:
    """The best-ranked catalogue videos for a free-text query (optionally one step)."""
    where, rank, params = search_filter(q, names, any_word=any_word)
    sql = (
        f"select {ITEM_COLS}, {rank} as rank {ITEM_FROM} where ({where})"
        " and (%(step)s::text is null or c.step = %(step)s) order by rank, c.sort, c.media_id limit %(limit)s"
    )
    cur = await conn.execute(sql, params | {"step": step, "limit": limit})
    return [dict(r) for r in await cur.fetchall()]
