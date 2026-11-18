"""In-memory retrieval over `data/chapters.json` for the local answerer.

Sections are turned into plain text (`naadi_api.embeddings.text.html_to_text`) and chunked with the app's
chunker (`naadi_api.embeddings.chunker.chunk_text`, heading-prefixed), embedded locally with fastembed through
`naadi_api.embeddings.engine.Engine` (model ids from config/models.yaml), ranked by cosine and re-ranked with the
cross-encoder. Chunk ids are `<section id>#<n>`; the answerer cites them as `[ctx:<id>]`.

Held-out questions never enter the index: any chunk that shares a 12-word run with a held-out stem, option or
explanation is dropped before embedding (`build_corpus`), and so is any chunk that reads like an answer key
(mock exam instructions list "question number, letter, topic"); both drop counts are reported.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from naadi_api.embeddings.chunker import chunk_text
from naadi_api.embeddings.text import html_to_text

SHINGLE_WORDS = 12
_WORD = re.compile(r"[a-z0-9]+")
_FIGURE_REF = re.compile(r"\[\[figure:[^\]]+\]\]")
# One answer-key row: question number, option letter, topic word ("14 B Immunology").
_ANSWER_KEY_ROW = re.compile(r"(?<![\w.])\d{1,3}\s+[A-J]\s+[A-Z][a-z]")
ANSWER_KEY_MIN_ROWS = 3


class Embedder(Protocol):
    def embed_passages(self, texts: Sequence[str], *, batch_size: int = 32) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...

    def rerank(self, query: str, docs: Sequence[str]) -> list[float]: ...


@dataclass(frozen=True)
class Chunk:
    id: str
    section_id: str
    chapter_id: str
    heading: str
    text: str


@dataclass(frozen=True)
class Hit:
    chunk: Chunk
    score: float


def _words(text: str) -> list[str]:
    return _WORD.findall(text.casefold())


def shingles(text: str, n: int = SHINGLE_WORDS) -> set[str]:
    w = _words(text)
    return {" ".join(w[i : i + n]) for i in range(len(w) - n + 1)}


class HeldoutFilter:
    """True for any text that shares an `n`-word run with the held-out questions' hidden texts."""

    def __init__(self, hidden_texts: Iterable[str], n: int = SHINGLE_WORDS) -> None:
        self.n = n
        self._sh: set[str] = set()
        for t in hidden_texts:
            self._sh |= shingles(t, n)

    def overlaps(self, text: str) -> bool:
        return not self._sh.isdisjoint(shingles(text, self.n))


def load_sections(path: Path) -> list[tuple[str, str, str, str]]:
    """(chapter id, section id, heading, plain text) for every section in reading order."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    chapters: Iterable[Any] = raw if isinstance(raw, list) else raw.get("chapters", [])
    out: list[tuple[str, str, str, str]] = []
    for ch in chapters:
        for s in ch.get("sections") or []:
            text = _FIGURE_REF.sub("", html_to_text(str(s.get("html") or "")))
            out.append((str(ch["id"]), str(s["id"]), str(s.get("heading") or ""), text))
    return out


def is_answer_key(text: str) -> bool:
    """A mock exam's answer key ("8 B Hematology: HIT 9 D Pharmacology ...") gives answers away without sharing
    any wording with the questions, so the shingle filter cannot see it."""
    return len(_ANSWER_KEY_ROW.findall(text)) >= ANSWER_KEY_MIN_ROWS


def build_corpus(
    sections: Sequence[tuple[str, str, str, str]], heldout: HeldoutFilter
) -> tuple[list[Chunk], dict[str, int]]:
    """Chunks of every section, minus those overlapping a held-out question and answer-key chunks.
    Returns (chunks, {"heldout_overlap": n, "answer_key": m})."""
    chunks: list[Chunk] = []
    dropped = {"heldout_overlap": 0, "answer_key": 0}
    for chapter_id, section_id, heading, text in sections:
        for i, body in enumerate(chunk_text(text, heading=heading), start=1):
            if heldout.overlaps(body):
                dropped["heldout_overlap"] += 1
                continue
            if is_answer_key(body):
                dropped["answer_key"] += 1
                continue
            chunks.append(Chunk(f"{section_id}#{i}", section_id, chapter_id, heading, body))
    return chunks, dropped


def _cos(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def corpus_key(chunks: Sequence[Chunk], model: str) -> str:
    h = hashlib.sha256(model.encode())
    for c in chunks:
        h.update(b"\0" + c.id.encode() + b"\0" + c.text.encode())
    return h.hexdigest()


class Index:
    """`cache` (optional): an .npz file holding the passage vectors for exactly this corpus and model (keyed by a
    hash of both), so repeated local runs skip the slow CPU embedding. Vectors only, no text."""

    def __init__(
        self,
        chunks: Sequence[Chunk],
        embedder: Embedder,
        *,
        candidates: int = 20,
        cache: Path | None = None,
        model_id: str = "",
    ) -> None:
        self.chunks = list(chunks)
        self._embedder = embedder
        self._candidates = candidates
        key = corpus_key(self.chunks, model_id)
        vecs: Any = None
        if cache is not None and cache.is_file():
            with np.load(cache, allow_pickle=False) as data:
                if str(data["key"]) == key:
                    vecs = data["vectors"]
        if vecs is None:
            vecs = embedder.embed_passages([c.text for c in self.chunks]) if self.chunks else []
            if cache is not None:
                cache.parent.mkdir(parents=True, exist_ok=True)
                np.savez(cache, key=np.asarray(key), vectors=np.asarray(vecs, dtype=np.float32))
        m = np.asarray(vecs, dtype=np.float32)
        m = m.reshape(len(self.chunks), -1) if self.chunks else np.zeros((0, 1), dtype=np.float32)
        norms = np.linalg.norm(m, axis=1, keepdims=True)
        self._matrix = m / np.where(norms == 0, 1.0, norms)
        self.ids = {c.id for c in self.chunks}

    def __len__(self) -> int:
        return len(self.chunks)

    def search(self, query: str, k: int = 4) -> list[Hit]:
        if not self.chunks or k <= 0:
            return []
        q = np.asarray(self._embedder.embed_query(query), dtype=np.float32)
        qn = float(np.linalg.norm(q))
        sims = self._matrix @ (q / qn if qn else q)
        ranked = sorted(
            ((i, float(s)) for i, s in enumerate(sims.tolist())), key=lambda t: (-t[1], self.chunks[t[0]].id)
        )[: max(k, self._candidates)]
        docs = [self.chunks[i].text for i, _ in ranked]
        scores = self._embedder.rerank(query, docs)
        reranked = sorted(zip(ranked, scores, strict=True), key=lambda t: (-t[1], self.chunks[t[0][0]].id))
        return [Hit(self.chunks[i], float(s)) for (i, _), s in reranked[:k]]


class HashingEmbedder:
    """Deterministic bag-of-words embedder for `--dry-run` and unit tests (no model files, no network)."""

    def __init__(self, dims: int = 384) -> None:
        self.dims = dims

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dims
        for w in _words(text):
            h = int.from_bytes(hashlib.sha256(w.encode()).digest()[:4], "big")
            v[h % self.dims] += 1.0
        return v

    def embed_passages(self, texts: Sequence[str], *, batch_size: int = 32) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)

    def rerank(self, query: str, docs: Sequence[str]) -> list[float]:
        q = self._vec(query)
        return [_cos(q, self._vec(d)) for d in docs]


__all__ = [
    "SHINGLE_WORDS",
    "Chunk",
    "Embedder",
    "HashingEmbedder",
    "HeldoutFilter",
    "Hit",
    "Index",
    "build_corpus",
    "load_sections",
    "shingles",
]
