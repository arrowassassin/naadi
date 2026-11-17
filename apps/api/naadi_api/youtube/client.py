"""The YouTube Data API v3 through the official `google-api-python-client`, with a per-run quota ledger.

Only the three cheap list calls are used (1 unit each): `playlistItems.list` for the uploads of a channel and
`videos.list` for details. `search.list` (100 units) is never called. The uploads playlist id of a channel is its
channel id with the `UC` prefix replaced by `UU`, so `channels.list` is not needed either.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

BATCH = 50
_DUR = re.compile(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


class QuotaExceeded(RuntimeError):
    """The run's unit budget (or YouTube's own daily quota) is spent: stop early, keep what was done."""


class YouTubeError(RuntimeError):
    """The API refused or failed; the message is a status class, never a key or a URL with one."""


def uploads_playlist(channel_id: str) -> str | None:
    return "UU" + channel_id[2:] if channel_id.startswith("UC") and len(channel_id) > 2 else None


def iso_duration_s(d: str | None) -> int | None:
    m = _DUR.fullmatch(d or "")
    if not d or not m:
        return None
    days, h, mi, s = (int(x) if x else 0 for x in m.groups())
    return days * 86400 + h * 3600 + mi * 60 + s


@dataclass(slots=True)
class Quota:
    budget: int
    used: int = 0

    def spend(self, units: int = 1) -> None:
        if self.used + units > self.budget:
            raise QuotaExceeded(f"budget {self.budget} reached")
        self.used += units


class YouTubeApi(Protocol):
    quota: Quota

    def playlist_video_ids(self, playlist_id: str, limit: int) -> list[str]:
        """Newest uploads first, at most `limit` ids (1 unit per page of 50)."""
        ...

    def videos(self, ids: list[str]) -> dict[str, dict[str, Any]]:
        """`snippet`, `contentDetails`, `status` per id; unknown / deleted / private ids are simply absent."""
        ...


class GoogleYouTube:
    """Blocking client: call it from a worker thread (`asyncio.to_thread`)."""

    def __init__(self, api_key: str, quota: Quota) -> None:
        from googleapiclient.discovery import build

        # cache_discovery=False: the discovery document is bundled; nothing is written to disk.
        self._svc = build("youtube", "v3", developerKey=api_key, cache_discovery=False)
        self.quota = quota

    def _execute(self, request: Any, units: int = 1) -> dict[str, Any]:
        from googleapiclient.errors import HttpError

        self.quota.spend(units)
        try:
            out: dict[str, Any] = request.execute(num_retries=2)
        except HttpError as exc:
            status = getattr(exc.resp, "status", 0)
            reason = str(getattr(exc, "reason", "") or "")
            text = str(exc.content[:400]) if exc.content else ""
            if status in (403, 429) and ("quota" in reason.lower() or "quotaExceeded" in text or "rateLimit" in text):
                raise QuotaExceeded("youtube quota") from None
            raise YouTubeError(f"http_{status}") from None
        return out

    def playlist_video_ids(self, playlist_id: str, limit: int) -> list[str]:
        ids: list[str] = []
        token: str | None = None
        while len(ids) < limit:
            res = self._execute(
                self._svc.playlistItems().list(
                    part="contentDetails", playlistId=playlist_id, maxResults=min(BATCH, limit), pageToken=token
                )
            )
            for it in res.get("items", []):
                vid = (it.get("contentDetails") or {}).get("videoId")
                if vid:
                    ids.append(vid)
            token = res.get("nextPageToken")
            if not token:
                break
        return ids[:limit]

    def videos(self, ids: list[str]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for i in range(0, len(ids), BATCH):
            res = self._execute(
                self._svc.videos().list(
                    part="snippet,contentDetails,status", id=",".join(ids[i : i + BATCH]), maxResults=BATCH
                )
            )
            for it in res.get("items", []):
                out[it["id"]] = it
        return out


def keep(item: dict[str, Any]) -> bool:
    """Embeddable, public and processed: the only videos that can play in the lesson."""
    st = item.get("status") or {}
    return (
        bool(st.get("embeddable"))
        and st.get("privacyStatus") == "public"
        and st.get("uploadStatus", "processed") == "processed"
    )


def media_id(youtube_id: str) -> str:
    """`media_assets.id`: lower case (the content schema's id pattern); `media_assets.youtube_id` keeps the real id."""
    return "yt-" + youtube_id.lower()
