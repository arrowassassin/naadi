"""The Aegra store's semantic index for long-term memory: local fastembed (BAAI/bge-small-en-v1.5, 384 dims), the
same engine as course search, so no memory text leaves the Space.

Aegra builds its `AsyncPostgresStore` from aegra.json's `store.index`, whose `embed` must be a provider string;
`naadi_api.agent.host` replaces Aegra's store-config loader with `store_config()` so the index uses this object.
Where the models are not cached (a quick local run), vectors fall back to a deterministic hashed bag of words, so
memory still works (lexically) instead of failing every write.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
from typing import Any

from langchain_core.embeddings import Embeddings

from ..embeddings.engine import ModelsNotCached, get_engine
from ..settings import get_settings

DIMS = 384
_WORD = re.compile(r"[a-z0-9]+")


def hashed_vector(text: str, dims: int = DIMS) -> list[float]:
    v = [0.0] * dims
    for w in _WORD.findall(text.lower()):
        h = int.from_bytes(hashlib.blake2b(w.encode(), digest_size=8).digest(), "big")
        v[h % dims] += 1.0 if (h >> 32) & 1 else -1.0
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / norm for x in v]


class LocalEmbeddings(Embeddings):
    def _engine(self) -> Any:
        return get_engine(get_settings().config_dir)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        try:
            return [list(map(float, v)) for v in self._engine().embed_passages(texts)]
        except ModelsNotCached:
            return [hashed_vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        try:
            return list(map(float, self._engine().embed_query(text)))
        except ModelsNotCached:
            return hashed_vector(text)

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return await asyncio.to_thread(self.embed_documents, texts)

    async def aembed_query(self, text: str) -> list[float]:
        return await asyncio.to_thread(self.embed_query, text)


def store_config() -> dict[str, Any]:
    return {"index": {"dims": DIMS, "embed": LocalEmbeddings(), "fields": ["content"]}}
