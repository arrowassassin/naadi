"""Local search: fastembed embeddings, the incremental index, and hybrid retrieval (segment 07).

Other segments use two entry points: `index.reconcile` (or `jobs.schedule_reindex(app, ...)`) after content
changes, and `query.search_local` to retrieve.
"""

from .engine import Engine, ModelsNotCached, get_engine, set_engine, spec_from_config
from .index import IndexReport, reconcile
from .jobs import IndexJobs, schedule_reindex
from .query import LocalResult, Scope, search_local
from .sources import KINDS, Kind

__all__ = [
    "KINDS",
    "Engine",
    "IndexJobs",
    "IndexReport",
    "Kind",
    "LocalResult",
    "ModelsNotCached",
    "Scope",
    "get_engine",
    "reconcile",
    "schedule_reindex",
    "search_local",
    "set_engine",
    "spec_from_config",
]
