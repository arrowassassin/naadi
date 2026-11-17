"""Fakes for the YouTube tests: a recorded-shape Data API and a deterministic bag-of-words embedder. No network."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Any

from naadi_api.youtube.client import Quota, QuotaExceeded


def item(
    vid: str,
    title: str,
    description: str = "",
    *,
    embeddable: bool = True,
    privacy: str = "public",
    upload: str = "processed",
    duration: str = "PT12M30S",
    channel: str = "UCfake0000000000000000aa",
    channel_title: str = "Fake Channel",
) -> dict[str, Any]:
    return {
        "id": vid,
        "snippet": {
            "title": title,
            "description": description,
            "channelId": channel,
            "channelTitle": channel_title,
        },
        "contentDetails": {"duration": duration},
        "status": {"embeddable": embeddable, "privacyStatus": privacy, "uploadStatus": upload},
    }


class FakeYouTube:
    """`playlists`: uploads playlist id -> video ids (newest first); `catalog`: video id -> `videos.list` item."""

    def __init__(self, playlists: dict[str, list[str]], catalog: dict[str, dict[str, Any]], budget: int = 2000) -> None:
        self.playlists = playlists
        self.catalog = catalog
        self.quota = Quota(budget)
        self.calls: list[tuple[str, int]] = []

    def playlist_video_ids(self, playlist_id: str, limit: int) -> list[str]:
        self.quota.spend(1)
        ids = self.playlists.get(playlist_id, [])[:limit]
        self.calls.append(("playlist", len(ids)))
        return ids

    def videos(self, ids: list[str]) -> dict[str, dict[str, Any]]:
        for _ in range(0, max(len(ids), 1), 50):
            self.quota.spend(1)
        self.calls.append(("videos", len(ids)))
        return {i: self.catalog[i] for i in ids if i in self.catalog}


class Exhausted(FakeYouTube):
    def videos(self, ids: list[str]) -> dict[str, dict[str, Any]]:
        raise QuotaExceeded("spent")


class WordEmbedder:
    """Hashes words into 64 buckets: texts that share words are similar, unrelated texts are not."""

    def embed_passages(self, texts: Sequence[str], *, batch_size: int = 32) -> list[list[float]]:
        out: list[list[float]] = []
        for t in texts:
            v = [0.0] * 64
            for w in "".join(c.lower() if c.isalnum() else " " for c in t).split():
                v[int(hashlib.sha256(w.encode()).hexdigest(), 16) % 64] += 1.0
            out.append(v)
        return out
