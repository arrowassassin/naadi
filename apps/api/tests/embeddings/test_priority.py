"""The index job runs at a lower priority than request threads; the status reports whether models are cached."""

from __future__ import annotations

import os
import threading
from types import SimpleNamespace
from typing import Any

import pytest

from naadi_api.embeddings.engine import ModelsNotCached
from naadi_api.embeddings.index import INDEX_NICE, _index_pool
from naadi_api.search_routes import models_cached


def test_the_indexing_thread_is_niced_and_the_calling_thread_is_not() -> None:
    if not hasattr(os, "setpriority"):
        pytest.skip("per-thread niceness needs Linux")
    main = os.getpriority(os.PRIO_PROCESS, threading.get_native_id())
    worker = _index_pool().submit(lambda: os.getpriority(os.PRIO_PROCESS, threading.get_native_id())).result()
    assert worker == min(19, main + INDEX_NICE) and worker > main
    assert os.getpriority(os.PRIO_PROCESS, threading.get_native_id()) == main


def test_models_cached_reports_missing_models() -> None:
    class Missing:
        @property
        def embedder(self) -> Any:
            raise ModelsNotCached("no")

    ok = SimpleNamespace(embedder=object(), reranker=object())
    assert models_cached(ok) is True  # type: ignore[arg-type]
    assert models_cached(Missing()) is False  # type: ignore[arg-type]
