"""Pure ranking helpers: query tokens, reciprocal-rank fusion, snippet windows and highlight segments."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Hashable, Sequence
from dataclasses import dataclass

# Tunables, chosen against the seeded corpus with the latency report in tests/embeddings/test_perf.py.
RRF_K = 60
CANDIDATES = 40  # per ranker (vector, full text) before fusion
RERANK_TOP_N = 5  # fused candidates the cross-encoder re-orders; the rest keep their fused order
RERANK_WORDS = 32  # the reranker reads the best window of the chunk, not the whole chunk
SNIPPET_WORDS = 42
PER_GROUP = 6  # results kept per result group when searching everything
# Cross-encoder scores are logits. Measured on the seeded corpus: a real match scores above -4 (typically -1 to +7);
# unrelated queries ("how do I cook pasta", gibberish) score -8.5 to -11 against their nearest neighbours. Vector
# search always returns neighbours, so this floor is what makes "nothing found" possible.
RERANK_FLOOR = -7.0  # below this a candidate is not a result
WEAK_RERANK = -3.0  # best score below this: the local answer is weak, so the screen also offers the web
TAIL_COSINE = 0.65  # candidates beyond the reranked head are kept on a full-text match or this similarity

STOP = frozenset(
    (  # noqa: SIM905 - a long word list reads better as one string
        "a an and are as at be but by can do does for from has have how i if in into is it its me my of "
        "on or our so than that the their them then there these they this to was we were what when where "
        "which who whom why will with would you your about vs versus"
    ).split()
)
_WORD = re.compile(r"[A-Za-z0-9]+")
_SENTENCE = re.compile(r"(?<=[.!?;:])\s+|\n+")


def query_terms(q: str) -> list[str]:
    """Lower-case alphanumeric terms without stop words (kept if that would leave none)."""
    words = [w.lower() for w in _WORD.findall(q)]
    terms = [w for w in words if w not in STOP and (len(w) > 1 or w.isdigit())]
    return list(dict.fromkeys(terms or words))[:12]


def tsquery(terms: Sequence[str], *, prefix_last: bool = True) -> str | None:
    """`a | b | c:*` for `to_tsquery('english', ...)`. Terms are alphanumeric, so nothing can inject syntax."""
    safe = [t for t in terms if t.isalnum()]
    if not safe:
        return None
    parts = list(safe)
    if prefix_last and len(parts[-1]) >= 3:
        parts[-1] += ":*"
    return " | ".join(parts)


def rrf[T: Hashable](rankings: Sequence[Sequence[T]], k: int = RRF_K) -> dict[T, float]:
    """Reciprocal-rank fusion: each ranking contributes 1 / (k + rank), rank starting at 1."""
    score: dict[T, float] = defaultdict(float)
    for ranking in rankings:
        seen: set[T] = set()
        for rank, item in enumerate(ranking, start=1):
            if item in seen:
                continue
            seen.add(item)
            score[item] += 1.0 / (k + rank)
    return dict(score)


def _stem(term: str) -> str:
    """Words match by prefix: a short term in full, a long one without its last letter (hyperkalemic ~ hyperkalemia)."""
    return term[:-1] if len(term) >= 8 else term


def term_pattern(terms: Sequence[str]) -> re.Pattern[str] | None:
    if not terms:
        return None
    return re.compile(r"\b(?:" + "|".join(re.escape(_stem(t)) for t in terms) + r")\w*", re.I)


@dataclass(frozen=True)
class Segment:
    text: str
    hit: bool


def drop_heading(content: str, heading: str | None) -> str:
    """Chunks start with their section heading; the snippet shows body text only."""
    lines = content.split("\n")
    if heading and lines and lines[0].strip() == heading.strip() and len(lines) > 1:
        return "\n".join(lines[1:])
    return content


def best_window(text: str, terms: Sequence[str], max_words: int = SNIPPET_WORDS) -> str:
    """The run of sentences with the most distinct query terms, trimmed to about `max_words` words."""
    flat = [s.strip() for s in _SENTENCE.split(text) if s and s.strip()]
    if not flat:
        return ""
    pat = term_pattern(terms)

    def hits(s: str) -> int:
        return len({m.group(0).lower()[:3] for m in pat.finditer(s)}) if pat else 0

    scores = [hits(s) for s in flat]
    best = max(range(len(flat)), key=lambda i: (scores[i], -i))
    words: list[str] = []
    i = best
    while i < len(flat) and len(words) < max_words:
        words.extend(flat[i].split())
        i += 1
    clipped = words[:max_words]
    out = " ".join(clipped)
    prefix = "… " if best > 0 else ""
    suffix = " …" if len(words) > max_words or i < len(flat) else ""
    return f"{prefix}{out}{suffix}"


def highlight(text: str, terms: Sequence[str]) -> list[Segment]:
    """Split `text` into plain and matching segments (the client renders `hit` ones as <mark>; no HTML here)."""
    pat = term_pattern(terms)
    if pat is None or not text:
        return [Segment(text, False)] if text else []
    out: list[Segment] = []
    pos = 0
    for m in pat.finditer(text):
        if m.start() > pos:
            out.append(Segment(text[pos : m.start()], False))
        out.append(Segment(m.group(0), True))
        pos = m.end()
    if pos < len(text):
        out.append(Segment(text[pos:], False))
    return out
