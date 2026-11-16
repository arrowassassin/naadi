"""Local embeddings and reranking (fastembed, ONNX on the Space CPU). No network at query time.

Model ids come from `roles.embeddings` in `config/models.yaml` (`model`, `reranker`, `dimensions`); this module
names no model. Files live in the fastembed cache (`FASTEMBED_CACHE_PATH`; the Docker image fills it at build
with `python -m naadi_api.embeddings prefetch`). Serving loads with `local_files_only`, so a missing model is
an explicit `ModelsNotCached` and never a silent download while a student waits.
"""

from __future__ import annotations

import os
import threading
import warnings
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import ConfigError, load_models_config
from ..logs import get_logger

log = get_logger("naadi.embeddings")


def _quiet_third_party_logging() -> None:
    """fastembed logs through loguru (coloured plain text on stderr) and huggingface_hub draws tqdm bars: neither
    goes through naadi_api.logs, and the Space's stdout/stderr must be JSON lines only. A failure to load is
    raised as ModelsNotCached and logged by the caller in the structured format."""
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    # fastembed asks the hub to enable its bars; the hub then warns that the variable forbids it. Expected.
    warnings.filterwarnings("ignore", message="Cannot enable progress bars", category=UserWarning)
    from loguru import logger as loguru_logger  # a fastembed dependency

    loguru_logger.disable("fastembed")


# Column type of public.embeddings.embedding (halfvec(384), db/schema.sql); a model with another size
# needs a migration and a re-index, so a mismatch fails loudly here.
EMBEDDING_DIM = 384
QUERY_CACHE_SIZE = 512
# One ONNX intra-op thread per model: measured fastest on the shared 2-4 vCPU Space (more threads spin-wait
# and lose to concurrent requests, which already run on separate worker threads).
ONNX_THREADS = 1


class ModelsNotCached(RuntimeError):
    """The embedding or reranker files are not in the local cache."""


@dataclass(frozen=True)
class EngineSpec:
    model: str
    reranker: str
    dimensions: int


def spec_from_config(config_dir: Path) -> EngineSpec:
    cfg = load_models_config(config_dir)
    role = cfg.roles.get("embeddings")
    extra: dict[str, Any] = dict(role.model_extra or {}) if role else {}
    model, reranker, dims = extra.get("model"), extra.get("reranker"), extra.get("dimensions")
    if not isinstance(model, str) or not isinstance(reranker, str) or not isinstance(dims, int):
        raise ConfigError("config/models.yaml roles.embeddings needs `model`, `reranker` and `dimensions`")
    if dims != EMBEDDING_DIM:
        raise ConfigError(f"roles.embeddings.dimensions is {dims}; public.embeddings is halfvec({EMBEDDING_DIM})")
    return EngineSpec(model=model, reranker=reranker, dimensions=dims)


def cache_dir() -> Path | None:
    raw = os.environ.get("FASTEMBED_CACHE_PATH", "").strip()
    return Path(raw) if raw else None


def vector_literal(vec: Sequence[float]) -> str:
    """pgvector text input form, rounded: halfvec keeps ~3 significant digits anyway."""
    return "[" + ",".join(f"{v:.6g}" for v in vec) + "]"


class Engine:
    """Lazy, thread-safe holder of the two ONNX models."""

    def __init__(
        self, spec: EngineSpec, *, allow_download: bool = False, cache: Path | None = None, threads: int = ONNX_THREADS
    ) -> None:
        self.spec = spec
        self._threads = threads
        self._allow_download = allow_download
        self._cache = cache if cache is not None else cache_dir()
        self._lock = threading.Lock()
        self._embedder: Any | None = None
        self._reranker: Any | None = None
        self._queries: OrderedDict[str, list[float]] = OrderedDict()

    def _kwargs(self) -> dict[str, Any]:
        kw: dict[str, Any] = {"local_files_only": not self._allow_download, "threads": self._threads}
        if self._cache is not None:
            kw["cache_dir"] = str(self._cache)
        return kw

    def _load(self, kind: str) -> Any:
        _quiet_third_party_logging()
        from fastembed import TextEmbedding  # imported lazily: onnxruntime is heavy
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        try:
            if kind == "embedder":
                return TextEmbedding(self.spec.model, **self._kwargs())
            return TextCrossEncoder(self.spec.reranker, **self._kwargs())
        except Exception as exc:
            if self._allow_download:
                raise
            raise ModelsNotCached(
                f"the {kind} files are not cached; run `python -m naadi_api.embeddings prefetch`"
                f" (FASTEMBED_CACHE_PATH={self._cache})"
            ) from exc

    @property
    def embedder(self) -> Any:
        if self._embedder is None:
            with self._lock:
                if self._embedder is None:
                    self._embedder = self._load("embedder")
        return self._embedder

    @property
    def reranker(self) -> Any:
        if self._reranker is None:
            with self._lock:
                if self._reranker is None:
                    self._reranker = self._load("reranker")
        return self._reranker

    def warm(self) -> None:
        """Load both models and run one tiny inference of each, so the first query is not the slow one."""
        self.embed_query("warm up")
        self.rerank("warm up", ["warm up"])

    def embed_passages(self, texts: Sequence[str], *, batch_size: int = 32) -> list[list[float]]:
        if not texts:
            return []
        return [[float(x) for x in v] for v in self.embedder.passage_embed(list(texts), batch_size=batch_size)]

    def embed_query(self, text: str) -> list[float]:
        key = text
        hit = self._queries.get(key)
        if hit is not None:
            self._queries.move_to_end(key)
            return hit
        vec = [float(x) for x in next(iter(self.embedder.query_embed([text])))]
        self._queries[key] = vec
        while len(self._queries) > QUERY_CACHE_SIZE:
            self._queries.popitem(last=False)
        return vec

    def rerank(self, query: str, docs: Sequence[str]) -> list[float]:
        if not docs:
            return []
        return [float(s) for s in self.reranker.rerank(query, list(docs), batch_size=len(docs))]


_ENGINE: Engine | None = None


def set_engine(engine: Engine | None) -> None:
    global _ENGINE
    _ENGINE = engine


def get_engine(config_dir: Path) -> Engine:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = Engine(spec_from_config(config_dir))
    return _ENGINE
