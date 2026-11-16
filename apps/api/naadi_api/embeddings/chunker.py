"""Split text into retrieval chunks of roughly 200-400 tokens, keeping paragraph boundaries.

Tokens are estimated from words (English text averages ~1.35 tokens per word with bge's WordPiece), which
is close enough to size chunks; the embedding model truncates at its own limit regardless.
"""

from __future__ import annotations

import re

TOKENS_PER_WORD = 1.35
TARGET_TOKENS = 300
MAX_TOKENS = 400
MIN_TOKENS = 120
_SENTENCE = re.compile(r"(?<=[.!?;])\s+(?=[A-Z0-9(\"'])")


def estimate_tokens(text: str) -> int:
    return round(len(text.split()) * TOKENS_PER_WORD)


def _split_long(block: str, max_tokens: int) -> list[str]:
    """A block above the cap (a long paragraph) is cut at sentence ends, then at word counts."""
    parts: list[str] = []
    current: list[str] = []
    for sentence in _SENTENCE.split(block):
        words = sentence.split()
        if not words:
            continue
        if estimate_tokens(" ".join(words)) > max_tokens:
            limit = max(1, int(max_tokens / TOKENS_PER_WORD))
            if current:
                parts.append(" ".join(current))
                current = []
            parts.extend(" ".join(words[i : i + limit]) for i in range(0, len(words), limit))
            continue
        if current and estimate_tokens(" ".join([*current, sentence])) > max_tokens:
            parts.append(" ".join(current))
            current = []
        current.append(sentence)
    if current:
        parts.append(" ".join(current))
    return parts


def chunk_text(
    text: str,
    *,
    heading: str | None = None,
    target_tokens: int = TARGET_TOKENS,
    max_tokens: int = MAX_TOKENS,
    min_tokens: int = MIN_TOKENS,
) -> list[str]:
    """Chunks in reading order. Every chunk starts with `heading` (when given) so it stands alone."""
    blocks: list[str] = []
    for raw in text.split("\n"):
        block = raw.strip()
        if not block:
            continue
        blocks.extend(_split_long(block, max_tokens) if estimate_tokens(block) > max_tokens else [block])

    chunks: list[list[str]] = []
    current: list[str] = []
    size = 0
    for block in blocks:
        t = estimate_tokens(block)
        if current and (size + t > max_tokens or (size >= target_tokens and size + t > target_tokens)):
            chunks.append(current)
            current, size = [], 0
        current.append(block)
        size += t
    if current:
        chunks.append(current)
    # A short tail joins the previous chunk when that stays under the cap.
    if len(chunks) > 1:
        tail = sum(estimate_tokens(b) for b in chunks[-1])
        prev = sum(estimate_tokens(b) for b in chunks[-2])
        if tail < min_tokens and prev + tail <= max_tokens:
            chunks[-2].extend(chunks.pop())
    prefix = f"{heading.strip()}\n" if heading and heading.strip() else ""
    return [prefix + "\n".join(c) for c in chunks]
