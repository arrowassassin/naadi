"""Offline scoring of a video against lesson sections: local fastembed vectors, cosine similarity, no LLM call."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import numpy as np

DESC_CHARS = 600
SUBJECT_BOOST = 0.05


class Embedder(Protocol):
    def embed_passages(self, texts: Sequence[str], *, batch_size: int = 32) -> list[list[float]]: ...


@dataclass(frozen=True, slots=True)
class Section:
    id: str
    heading: str
    chapter_id: str
    taxonomy_ids: tuple[str, ...]
    text: str  # heading + taxonomy labels


@dataclass(frozen=True, slots=True)
class Match:
    section_id: str
    score: float
    terms: tuple[str, ...]


def video_text(title: str, description: str) -> str:
    return f"{title}. {description[:DESC_CHARS]}".strip()


def _unit(rows: list[list[float]]) -> np.ndarray:
    m = np.asarray(rows, dtype=np.float32)
    n = np.linalg.norm(m, axis=1, keepdims=True)
    n[n == 0] = 1.0
    out: np.ndarray = m / n
    return out


def terms_in_common(a: str, b: str, limit: int = 6) -> tuple[str, ...]:
    """Words (4+ letters) the video text shares with the section text: shown to the reviewer as the evidence."""
    wb = {w for w in _words(b)}
    seen: list[str] = []
    for w in _words(a):
        if w in wb and w not in seen:
            seen.append(w)
        if len(seen) >= limit:
            break
    return tuple(seen)


def _words(s: str) -> list[str]:
    cur = []
    out = []
    for ch in s.lower():
        if ch.isalnum():
            cur.append(ch)
        else:
            if len(cur) >= 4:
                out.append("".join(cur))
            cur = []
    if len(cur) >= 4:
        out.append("".join(cur))
    return out


def score_videos(
    embedder: Embedder,
    sections: Sequence[Section],
    videos: Sequence[tuple[str, str]],
    subjects: Sequence[str],
    *,
    min_score: float,
    per_video: int = 5,
) -> list[list[Match]]:
    """For each `(title, description)`: its best sections above `min_score`, best first.

    A channel's `subjects` (taxonomy domains such as `cardio`) lift a section whose taxonomy ids name one of them
    by a small fixed amount; `all` lifts nothing. The cosine is clamped at 0.
    """
    if not sections or not videos:
        return [[] for _ in videos]
    sec = _unit(embedder.embed_passages([s.text for s in sections]))
    vid = _unit(embedder.embed_passages([video_text(t, d) for t, d in videos]))
    sims = vid @ sec.T
    wanted = {s.lower() for s in subjects if s.lower() != "all"}
    out: list[list[Match]] = []
    for i, (title, desc) in enumerate(videos):
        row: list[Match] = []
        for j, s in enumerate(sections):
            score = max(0.0, float(sims[i, j]))
            if wanted and any(w in tid.lower() for tid in s.taxonomy_ids for w in wanted):
                score += SUBJECT_BOOST
            if score >= min_score and not math.isnan(score):
                row.append(Match(s.id, round(score, 4), terms_in_common(video_text(title, desc), s.text)))
        row.sort(key=lambda m: (-m.score, m.section_id))
        out.append(row[:per_video])
    return out
