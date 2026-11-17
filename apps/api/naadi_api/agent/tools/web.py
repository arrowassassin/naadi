"""Web tools: `web_search` and `web_extract` through `naadi_api/search.py` (layered sources, 7-day cache, Tavily's
monthly stop and per-user cap, PII scrubbed before any request).

Guard (AGENTS.md "grounded first"): allowed only when the student switched the web on for this turn, or when
`search_knowledge` already ran this turn and its best score is below `app_config.assistant.kb_low_score`.
Every result keeps its source, licence and link, labelled with its layer ("tavily" is shown as Tavily).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field, HttpUrl

from ...logs import get_logger
from ...search import SearchResult, TavilyGuardExhausted, get_search
from .executor import ToolRefused
from .registry import ToolContext, ToolResult, register_tool, web_url_key

log = get_logger("naadi.agent.tools.web")


def _guard(ctx: ToolContext) -> None:
    if ctx.web_allowed:
        return
    if ctx.kb_best is None:
        raise ToolRefused("search_knowledge_first")
    if ctx.kb_best >= ctx.kb_low_score:
        raise ToolRefused("knowledge_base_answers")


def web_citation(i: int, r: SearchResult) -> dict[str, Any]:
    return {
        "id": f"web:{r.source}:{i}",
        "title": r.title[:200],
        "source": r.source,
        "layer": "web",
        "url": r.url if r.url.startswith("https://") else None,
        "target": None,
        "license": r.license or None,
        "n": i,
    }


class WebSearchArgs(BaseModel):
    query: str = Field(min_length=2, max_length=300)


async def web_search(ctx: ToolContext, args: WebSearchArgs) -> ToolResult:
    """Search trusted medical and official web sources (PubMed, MedlinePlus, openFDA, guidelines, official exam and
    visa sites). Only when the knowledge base had nothing good or the student asked for the web. Cite as [n]."""
    _guard(ctx)
    try:
        answer = await get_search().search(args.query, user_id=ctx.user_id, web=ctx.web_allowed)
    except ValueError as exc:
        raise ToolRefused("empty_query") from exc
    results = [r for r in answer.results if r.url.startswith("https://")][:6]
    if not results:
        return ToolResult(content="No web results.")
    start = 101  # web citations are numbered apart from knowledge-base ones
    citations = [web_citation(start + i, r) for i, r in enumerate(results)]
    lines = [f"[{c['n']}] {r.title} ({r.source}): {r.snippet[:500]}" for c, r in zip(citations, results, strict=True)]
    return ToolResult(
        content="\n".join(lines),
        citations=citations,
        blocks=[{"type": "sources", "data": {"items": citations}}],
    )


class WebExtractArgs(BaseModel):
    url: HttpUrl = Field(description="One https page to read")


async def web_extract(ctx: ToolContext, args: WebExtractArgs) -> ToolResult:
    """Read the text of one web page (https) found by web_search or given by the student. Same guard as web_search."""
    _guard(ctx)
    url = str(args.url)
    if not url.startswith("https://"):
        raise ToolRefused("https_only")
    if web_url_key(url) not in ctx.web_urls:
        # Only a page web_search returned this turn, or one the student typed: text injected into a section, a
        # note or a page must not be able to send the model to a URL it made up (data could ride in the URL).
        raise ToolRefused("url_not_from_search")
    try:
        r = await get_search().extract(url, user_id=ctx.user_id)
    except TavilyGuardExhausted as exc:
        raise ToolRefused(f"web_quota:{exc.reason}") from exc
    if r is None:
        return ToolResult(content="The page gave no text.")
    page = SearchResult(
        source="tavily",
        title=r.title or (urlparse(r.url).hostname or r.url),
        url=r.url,
        snippet=r.text[:8000],
        license="unknown",
        storable=False,
        kind="web",
    )
    cite = web_citation(150, page)
    return ToolResult(content=f"[150] {page.url}:\n{page.snippet}", citations=[cite])


register_tool("web_search", web_search, writes=False)
register_tool("web_extract", web_extract, writes=False)
