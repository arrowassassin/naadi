"""Search endpoints (segment 07).

    GET  /api/search?q=&scope=&limit=   local hybrid search (vector + full text, RRF, rerank), as the caller
    GET  /api/search/web?q=&web=        web results through the layered search of segment 04, labelled by source
    GET  /api/admin/embeddings          index row counts and the last index job
    POST /api/admin/embeddings/reindex  run the index job (admin)

Local and web are separate calls so the local answer (target p95 < 300 ms) never waits on a network source.
`suggest_web` on the local response says when the screen should fetch the web panel by itself (nothing found
locally, or the best match is weak); a student can always ask for it.

Query text is never logged: only a short hash.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from typing import Annotated, Any, Literal, cast

import psycopg
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException

from .auth import AdminUser, get_account_user
from .auth_limits import RateLimiter
from .db import Database, DatabaseNotConfigured
from .embeddings.engine import Engine, ModelsNotCached, spec_from_config
from .embeddings.index import index_counts
from .embeddings.jobs import IndexJobs
from .embeddings.query import Hit, Scope, search_local
from .embeddings.sources import KINDS, Kind
from .errors import ErrorResponse
from .logs import get_logger
from .search import SearchResult, TavilyGuardExhausted, get_search, query_hash

log = get_logger("naadi.search_routes")

router = APIRouter(prefix="/api", tags=["search"])

SEARCH_UNAVAILABLE = "Search is not ready yet. Try again in a moment."
WEB_LABELS = {
    "tavily": "Tavily",
    "pubmed": "PubMed",
    "europepmc": "Europe PMC",
    "medlineplus": "MedlinePlus",
    "openfda": "openFDA",
    "clinicaltrials": "ClinicalTrials.gov",
    "searxng": "SearXNG",
}
MAX_LIMIT = 50

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Missing, expired or invalid access token"},
    429: {"model": ErrorResponse, "description": "Rate limited"},
    503: {"model": ErrorResponse, "description": "Search or the database is unavailable"},
}
_ADMIN_ERRORS: dict[int | str, dict[str, Any]] = {
    **_ERRORS,
    403: {"model": ErrorResponse, "description": "Not an admin"},
}


# ── Models ──


class Segment(BaseModel):
    """A run of snippet text; `hit` runs matched the query (render as <mark>). Plain text, never HTML."""

    text: str
    hit: bool


class Target(BaseModel):
    """Where a hit opens. The screen turns it into a route only when that route exists in the build."""

    type: Literal["section", "question", "note", "ink", "pathway"]
    chapter_id: str | None = None
    section_id: str | None = None
    question_id: str | None = None
    note_id: str | None = None
    anchor_type: str | None = None
    anchor_id: str | None = None
    surface_type: str | None = None
    surface_id: str | None = None
    stage_key: str | None = None
    task_id: str | None = None
    guide_key: str | None = None


class SearchHit(BaseModel):
    id: str = Field(description="Stable per result: `<type>:<ref id>`")
    type: Literal["section", "upload", "kb_doc", "question", "note", "ink", "pathway"]
    group: Literal["chapters", "notes", "handwriting", "questions", "uploads", "pathway"]
    title: str
    meta: str
    snippet: list[Segment]
    target: Target
    score: float


class SearchResponse(BaseModel):
    scope: Scope
    hits: list[SearchHit]
    suggest_web: bool = Field(description="True when nothing matched locally or the best match is weak")
    took_ms: int


class WebResult(BaseModel):
    title: str
    url: str
    snippet: str
    source: str = Field(description="Source id, e.g. tavily, pubmed")
    source_label: str = Field(description="Display name of the source, e.g. Tavily, PubMed")
    kind: str
    license: str
    published: str | None = None


class WebResponse(BaseModel):
    status: Literal["ok", "empty", "unavailable", "limit"] = Field(
        description="`limit`: a daily or monthly search allowance is used up; `unavailable`: no web source answered"
    )
    labels: list[str] = Field(description="Sources that answered, for the panel's label")
    results: list[WebResult]
    message: str | None = None


class IndexJob(BaseModel):
    status: Literal["idle", "running", "succeeded", "failed"]
    started_at: str | None
    finished_at: str | None
    error: str | None
    report: dict[str, Any] | None


class IndexStatus(BaseModel):
    rows: dict[str, int] = Field(description="Index rows per ref type")
    models_ready: bool
    job: IndexJob


class ReindexRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kinds: list[Literal["sections", "questions", "pathway", "notes", "ink"]] = Field(
        default_factory=lambda: list(KINDS), min_length=1
    )


# ── Plumbing ──


def limited(bucket: str) -> Any:
    async def dep(request: Request, user: Annotated[Any, Depends(get_account_user)]) -> Any:
        limiters: dict[str, RateLimiter] = request.app.state.rate_limiters
        limiters[bucket].hit(str(user.user_id))
        return user

    return Depends(dep)


def default_search_limiters() -> dict[str, RateLimiter]:
    return {
        # The screen searches as the student types (debounced); this is a ceiling, not a target.
        "search_local": RateLimiter(120, 60.0),
        "search_web": RateLimiter(20, 60.0),
        "admin_index": RateLimiter(6, 60.0),
    }


def _jobs(request: Request) -> IndexJobs:
    jobs: IndexJobs | None = getattr(request.app.state, "embedding_jobs", None)
    if jobs is None:
        raise HTTPException(503, SEARCH_UNAVAILABLE)
    return jobs


def _hit(h: Hit) -> SearchHit:
    return SearchHit(
        id=f"{h.ref_type}:{h.ref_id}",
        type=cast("Any", h.ref_type),
        group=h.group,
        title=h.title,
        meta=h.meta,
        snippet=[Segment(text=s.text, hit=s.hit) for s in h.snippet],
        target=Target.model_validate(h.target),
        score=round(h.score, 4),
    )


# ── Routes ──


@router.get(
    "/search",
    operation_id="searchLocal",
    summary="Search chapters, questions, notes, handwriting, uploads and the pathway (local, ranked, as you)",
    response_model=SearchResponse,
    responses=_ERRORS,
)
async def search(
    request: Request,
    user: Annotated[Any, limited("search_local")],
    q: Annotated[str, Query(min_length=1, max_length=300, description="What to look for")],
    scope: Annotated[Scope, Query(description="Result type filter; `all` searches everything")] = "all",
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 24,
) -> SearchResponse:
    if not q.strip():
        raise HTTPException(422, "Type something to search for.")
    jobs = _jobs(request)
    t0 = time.perf_counter()
    try:
        result = await search_local(jobs.db, jobs.engine, user, q, scope=scope, limit=limit)
    except ModelsNotCached as exc:
        log.warning("search_models_not_cached")
        raise HTTPException(503, SEARCH_UNAVAILABLE) from exc
    except DatabaseNotConfigured as exc:
        raise HTTPException(503, SEARCH_UNAVAILABLE) from exc
    except (psycopg.OperationalError, TimeoutError) as exc:
        log.warning("search_db_unavailable", error=type(exc).__name__)
        raise HTTPException(503, SEARCH_UNAVAILABLE) from exc
    took = int((time.perf_counter() - t0) * 1000)
    log.info("search_local", q=query_hash(q), scope=scope, hits=len(result.hits), ms=took, stages=result.timings_ms)
    return SearchResponse(
        scope=scope, hits=[_hit(h) for h in result.hits], suggest_web=result.suggest_web, took_ms=took
    )


def _web_result(r: SearchResult) -> WebResult:
    return WebResult(
        title=r.title,
        url=r.url,
        snippet=r.snippet,
        source=r.source,
        source_label=WEB_LABELS.get(r.source, r.source),
        kind=r.kind,
        license=r.license,
        published=r.published,
    )


@router.get(
    "/search/web",
    operation_id="searchWeb",
    summary="Web results through the layered search (PubMed and other medical sources; Tavily for latest and pathway)",
    response_model=WebResponse,
    responses=_ERRORS,
)
async def search_web(
    request: Request,
    user: Annotated[Any, limited("search_web")],
    q: Annotated[str, Query(min_length=1, max_length=300)],
    web: Annotated[bool, Query(description="The student asked for the web or for the latest: Tavily first")] = False,
) -> WebResponse:
    if not q.strip():
        raise HTTPException(422, "Type something to search for.")
    try:
        service = get_search()
    except RuntimeError:
        return WebResponse(
            status="unavailable", labels=[], results=[], message="Web search is not available on this server."
        )
    try:
        answer = await asyncio.wait_for(service.search(q, user_id=user.user_id, web=web), timeout=20.0)
    except TimeoutError:
        return WebResponse(status="unavailable", labels=[], results=[], message="Web search took too long.")
    except TavilyGuardExhausted as exc:
        return WebResponse(
            status="limit", labels=[], results=[], message=f"Web search allowance used up ({exc.reason})."
        )
    except Exception as exc:  # noqa: BLE001 - a failing source must not break the Search screen
        log.warning("search_web_failed", q=query_hash(q), error=type(exc).__name__)
        return WebResponse(status="unavailable", labels=[], results=[], message="Web search did not answer.")
    results = [_web_result(r) for r in answer.results]
    guard = any(v.startswith("skipped:") and v != "skipped:disabled" for v in answer.layers.values())
    labels = list(dict.fromkeys(r.source_label for r in results))
    log.info("search_web", q=query_hash(q), results=len(results), layers=answer.layers)
    if results:
        return WebResponse(status="ok", labels=labels, results=results)
    if guard:
        return WebResponse(status="limit", labels=[], results=[], message="Web search allowance used up for now.")
    return WebResponse(status="empty", labels=[], results=[])


def _job(jobs: IndexJobs) -> IndexJob:
    s = jobs.state
    return IndexJob(
        status=cast("Any", s.status),
        started_at=s.started_at.isoformat() if s.started_at else None,
        finished_at=s.finished_at.isoformat() if s.finished_at else None,
        error=s.error,
        report=s.report,
    )


@router.get(
    "/admin/embeddings",
    operation_id="getEmbeddingsStatus",
    summary="Search index: rows per type, whether the models are cached, the last index job",
    response_model=IndexStatus,
    responses=_ADMIN_ERRORS,
    tags=["admin"],
)
async def embeddings_status(request: Request, _admin: AdminUser) -> IndexStatus:
    jobs = _jobs(request)
    try:
        rows = await index_counts(jobs.db)
    except (DatabaseNotConfigured, psycopg.OperationalError, TimeoutError) as exc:
        raise HTTPException(503, SEARCH_UNAVAILABLE) from exc
    ready = await asyncio.to_thread(models_cached, jobs.engine)
    return IndexStatus(rows=rows, models_ready=ready, job=_job(jobs))


def models_cached(engine: Engine) -> bool:
    try:
        _ = engine.embedder, engine.reranker
    except ModelsNotCached:
        return False
    return True


@router.post(
    "/admin/embeddings/reindex",
    operation_id="reindexEmbeddings",
    summary="Bring the search index in line with the content (idempotent; unchanged chunks are skipped)",
    response_model=IndexStatus,
    status_code=202,
    responses={**_ADMIN_ERRORS, 409: {"model": ErrorResponse, "description": "A run is already in progress"}},
    tags=["admin"],
)
async def reindex(request: Request, _admin: AdminUser, body: ReindexRequest | None = None) -> IndexStatus:
    request.app.state.rate_limiters["admin_index"].hit(str(_admin.user_id))
    jobs = _jobs(request)
    if jobs.running:
        raise HTTPException(409, "An index run is already in progress.")
    kinds = cast("Sequence[Kind]", (body or ReindexRequest()).kinds)
    jobs.schedule(kinds)
    await asyncio.sleep(0)  # let the task mark itself running before the status is read
    rows = await index_counts(jobs.db)
    return IndexStatus(rows=rows, models_ready=True, job=_job(jobs))


# ── Wiring ──


async def start_search(app: Any) -> None:
    """Called from the app lifespan: warm the models and reconcile the index in the background."""
    jobs: IndexJobs = app.state.embedding_jobs
    settings = app.state.settings

    async def boot() -> None:
        if not settings.embeddings_autostart:
            return
        try:
            await asyncio.to_thread(jobs.engine.warm)
        except ModelsNotCached:
            log.warning("embeddings.models_missing", hint="python -m naadi_api.embeddings prefetch")
            return
        if jobs.db.configured:
            await jobs.run()

    task = asyncio.create_task(boot(), name="embeddings-boot")
    jobs._tasks.add(task)
    task.add_done_callback(jobs._tasks.discard)


async def stop_search(app: Any) -> None:
    jobs: IndexJobs | None = getattr(app.state, "embedding_jobs", None)
    if jobs is not None:
        await jobs.drain()


def install_search_routes(app: Any, engine: Engine | None = None) -> None:
    limiters: dict[str, RateLimiter] = getattr(app.state, "rate_limiters", {})
    for k, v in default_search_limiters().items():
        limiters.setdefault(k, v)
    app.state.rate_limiters = limiters
    db: Database = app.state.db
    eng = engine or Engine(spec_from_config(app.state.settings.config_dir))
    app.state.embedding_jobs = IndexJobs(db, eng)
    app.include_router(router)


__all__ = ["install_search_routes", "router", "start_search", "stop_search"]
