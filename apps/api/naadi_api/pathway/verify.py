"""The weekly official-page check (`pathway_verify` graph, Aegra cron Mondays 04:13 UTC).

For up to `pathway.verify_max_tasks_per_run` tasks (oldest `last_verified` first, tasks an admin has not yet
resolved left out) whose `official_url` is on an official domain (`search.include_domains`): fetch the page, reduce
it to text and hash it.

- Same hash: a `unchanged` check. A task with no stored hash yet gets its baseline (summary `baseline`): the first run
  records what the official pages say today, it flags nothing.
- Different hash, a redirect to another path, an error status or no answer: the task gets `needs_review = true`
  and a check row says why. For a changed page, one `structured` call (purpose `job`, at most
  `pathway.verify_max_llm_calls` per run) compares the task's stored facts with the new text; every quote it returns
  must appear verbatim in the fetched text or it is dropped. Free chains only, through `naadi_api/llm.py`.
- The check NEVER changes `last_verified` (only an admin confirming a task does) and never touches student data.
  The inputs of the model call are shipped seed facts and a public page: no PII exists in them.
- Tavily is not used here (credit budget belongs to the students' own questions); the page fetch uses `httpx`
  directly because no SDK exists for fetching a plain web page. The user agent names the check and robots.txt is
  obeyed.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Protocol
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field, ValidationError, field_validator

from ..config_api.schemas import PathwayRules
from ..db import Database
from ..llm import CallContext, LLMUnavailable
from ..llm_budget import BudgetExhausted
from ..llm_privacy import PrivacyUnavailable
from ..logs import get_logger
from . import rules
from .models import CheckStatus
from .store import fetch, jsonb

log = get_logger("naadi.pathway.verify")

USER_AGENT = "NaadiPathwayCheck/1.0 (weekly official-page check; obeys robots.txt)"
ROLE = "structured"
MAX_REDIRECTS = 5
MODEL_TEXT_CHARS = 12_000
MAX_QUOTES = 3
QUOTE_MAX = 300
MAX_DIFFERENCES = 5
RETENTION_DAYS = 180
HOST_DELAY_S = 0.5

SYSTEM_PROMPT = (
    "You compare a pathway task that a medical-licensing guide stores with the current text of the official web page "
    "it cites. Decide whether the stored facts (title, detail, cost in USD) are still accurate according to the page. "
    "Answer ONLY with a JSON object: "
    '{"still_accurate": true|false, "differences": [up to 5 short plain sentences describing what differs], '
    '"quotes": [up to 3 exact sentences copied word for word from the page text that show the difference]}. '
    "A quote must be copied exactly from the page text and be at most 300 characters. If the page does not say "
    "anything about the stored facts, answer still_accurate true with no differences. Never invent facts."
)


# ── fetching ──


@dataclass
class Fetched:
    url: str  # the URL the page was finally read from
    status: int | None  # None: no answer
    text: str | None  # normalised text (HTML pages); None for other content
    content_hash: str | None
    outcome: str  # ok | http_error | network_error | robots_disallowed | off_domain_redirect | too_many_redirects
    moved: bool = False


class Fetcher(Protocol):
    async def fetch(self, url: str) -> Fetched: ...
    async def aclose(self) -> None: ...


class _TextExtractor(HTMLParser):
    SKIP = frozenset({"script", "style", "noscript", "template", "svg", "head"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.SKIP:
            self._skip += 1
        elif tag in {"p", "br", "li", "tr", "h1", "h2", "h3", "h4", "div", "section"}:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def normalise_text(html: str) -> str:
    """The page as one line of text: markup, scripts and styles gone, whitespace collapsed."""
    p = _TextExtractor()
    p.feed(html)
    p.close()
    return re.sub(r"\s+", " ", "".join(p.parts)).strip()


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _path_key(url: str) -> tuple[str, str]:
    parts = urlsplit(url)
    return (parts.hostname or "").lower(), parts.path.rstrip("/") or "/"


class HttpFetcher:
    """`httpx` page fetch: 15 s timeout, our user agent, robots.txt obeyed, redirects followed one hop at a time and
    only inside the official domains, size capped."""

    def __init__(
        self,
        domains: list[str],
        *,
        timeout_s: float = 15.0,
        max_bytes: int = 2_000_000,
        transport: httpx.AsyncBaseTransport | None = None,
        delay_s: float = HOST_DELAY_S,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._domains = domains
        self._max_bytes = max_bytes
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_s),
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,text/plain;q=0.8,*/*;q=0.1"},
            follow_redirects=False,
            transport=transport,
        )
        self._robots: dict[str, RobotFileParser | None] = {}
        self._cache: dict[str, Fetched] = {}
        self._last_at: dict[str, float] = {}
        self._delay = delay_s
        self._sleep = sleep

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            rp: RobotFileParser | None = RobotFileParser()
            try:
                r = await self._client.get(origin + "/robots.txt")
                if r.status_code == 200:
                    assert rp is not None
                    rp.parse(r.text.splitlines())
                elif r.status_code in (401, 403):
                    assert rp is not None
                    rp.parse(["User-agent: *", "Disallow: /"])
                else:
                    rp = None  # no robots file: everything is allowed
            except httpx.HTTPError:
                rp = None
            self._robots[origin] = rp
        rp = self._robots[origin]
        return rp is None or rp.can_fetch(USER_AGENT, url)

    async def fetch(self, url: str) -> Fetched:
        if url in self._cache:
            return self._cache[url]
        result = await self._fetch(url)
        self._cache[url] = result
        return result

    async def _get(self, url: str) -> httpx.Response:
        host = urlsplit(url).hostname or ""
        loop = asyncio.get_running_loop()
        wait = self._last_at.get(host, 0.0) + self._delay - loop.time()
        if wait > 0:
            await self._sleep(wait)
        self._last_at[host] = loop.time()
        async with self._client.stream("GET", url) as resp:
            body = bytearray()
            async for chunk in resp.aiter_bytes():
                body += chunk
                if len(body) > self._max_bytes:
                    break
            return httpx.Response(
                resp.status_code, headers=resp.headers, content=bytes(body[: self._max_bytes]), request=resp.request
            )

    async def _fetch(self, url: str) -> Fetched:
        current = url
        moved = False
        for _ in range(MAX_REDIRECTS + 1):
            host = rules.https_host(current)
            if host is None or not rules.host_matches(host, self._domains):
                return Fetched(current, None, None, None, "off_domain_redirect", moved=True)
            if not await self._allowed(current):
                return Fetched(current, None, None, None, "robots_disallowed")
            try:
                resp = await self._get(current)
            except httpx.HTTPError:
                return Fetched(current, None, None, None, "network_error", moved=moved)
            if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("location"):
                current = urljoin(current, resp.headers["location"])
                if _path_key(current) != _path_key(url):
                    moved = True
                continue
            if resp.status_code != 200:
                return Fetched(current, resp.status_code, None, None, "http_error", moved=moved)
            ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
            if ctype in ("text/html", "application/xhtml+xml", "text/plain", ""):
                text = normalise_text(resp.text)
                return Fetched(current, 200, text, hash_text(text), "ok", moved=moved)
            return Fetched(current, 200, None, hashlib.sha256(resp.content).hexdigest(), "ok", moved=moved)
        return Fetched(current, None, None, None, "too_many_redirects", moved=True)


# ── comparing with the model ──


class Comparison(BaseModel):
    still_accurate: bool
    differences: list[str] = Field(default_factory=list)
    quotes: list[str] = Field(default_factory=list)

    @field_validator("differences", "quotes", mode="before")
    @classmethod
    def _list(cls, v: Any) -> Any:
        return v if isinstance(v, list) else []


def parse_comparison(content: Any) -> Comparison | None:
    """The first JSON object in a model reply, validated; None when there is none or it does not fit."""
    if isinstance(content, list):
        content = "".join(p if isinstance(p, str) else str(p.get("text", "")) for p in content)
    if not isinstance(content, str):
        return None
    text = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.M).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return Comparison.model_validate(json.loads(text[start : end + 1]))
    except (ValueError, ValidationError):
        return None


def verbatim_quotes(quotes: list[str], page_text: str, url: str) -> list[dict[str, str]]:
    """Only quotes that appear word for word in the fetched text survive (whitespace is not significant). Each
    keeps the URL it came from; none is longer than 300 characters; duplicates go."""
    norm = re.sub(r"\s+", " ", page_text)
    kept: list[dict[str, str]] = []
    seen: set[str] = set()
    for q in quotes:
        t = re.sub(r"\s+", " ", str(q)).strip()
        if not t or len(t) > QUOTE_MAX or t in seen or t not in norm:
            continue
        seen.add(t)
        kept.append({"text": t, "url": url})
        if len(kept) == MAX_QUOTES:
            break
    return kept


def build_prompt(task: dict[str, Any], page_text: str) -> list[BaseMessage]:
    cost = task["cost_usd"]
    facts = f"Title: {task['title']}\nDetail: {task['detail_md']}\nCost (USD): {'not stated' if cost is None else cost}"
    return [
        SystemMessage(SYSTEM_PROMPT),
        HumanMessage(
            f"STORED FACTS\n{facts}\n\nOFFICIAL PAGE TEXT ({task['official_url']})\n{page_text[:MODEL_TEXT_CHARS]}"
        ),
    ]


class Gateway(Protocol):
    async def ainvoke(self, role: str, messages: list[BaseMessage], ctx: CallContext, **kwargs: Any) -> Any: ...


@dataclass
class VerifyStats:
    tasks: int = 0
    fetched: int = 0
    unchanged: int = 0
    baselined: int = 0
    changed: int = 0
    moved: int = 0
    unreachable: int = 0
    flagged: int = 0
    llm_calls: int = 0
    llm_skipped: int = 0
    budget_spent: bool = False
    pruned: int = 0
    notes: list[str] = field(default_factory=list)

    def count(self, status: str) -> None:
        if status == "unchanged":
            self.unchanged += 1
        elif status == "changed":
            self.changed += 1
        elif status == "moved":
            self.moved += 1
        else:
            self.unreachable += 1

    def as_dict(self) -> dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items() if k != "notes"}
        d["notes"] = sorted(set(self.notes))[:10]
        return d


async def compare_with_model(
    gateway: Gateway | None,
    task: dict[str, Any],
    fetched: Fetched,
    stats: VerifyStats,
    cfg: PathwayRules,
    run_id: str | None,
) -> dict[str, Any]:
    """The finding for a changed page. Without a model (cap reached, budget spent, outage, no text) the finding says
    the page changed and an admin has to look; it never invents a comparison."""
    base: dict[str, Any] = {
        "status": "changed",
        "summary_key": "page_changed_unreviewed",
        "differences": [],
        "quotes": [],
    }
    if gateway is None or fetched.text is None or stats.budget_spent:
        stats.llm_skipped += 1
        return base
    if stats.llm_calls >= cfg.verify_max_llm_calls:
        stats.llm_skipped += 1
        stats.notes.append("llm_cap")
        return base
    ctx = CallContext(purpose="job", run_id=run_id)
    stats.llm_calls += 1
    try:
        reply = await gateway.ainvoke(ROLE, build_prompt(task, fetched.text), ctx)
    except BudgetExhausted as exc:
        if exc.reason == "daily_cap":
            stats.budget_spent = True
        stats.notes.append(f"budget_{exc.reason}")
        return base
    except (LLMUnavailable, PrivacyUnavailable) as exc:
        stats.notes.append(type(exc).__name__)
        return base
    cmp = parse_comparison(getattr(reply, "content", reply))
    if cmp is None:
        stats.notes.append("invalid_output")
        return base
    return {
        "status": "changed",
        "summary_key": "page_changed_no_difference" if cmp.still_accurate else "page_changed_differences",
        "still_accurate": cmp.still_accurate,
        "differences": [d.strip()[:300] for d in cmp.differences if d.strip()][:MAX_DIFFERENCES],
        "quotes": verbatim_quotes(cmp.quotes, fetched.text, fetched.url),
    }


# ── the pass ──

_SELECT = (
    "select id, title, detail_md, cost_usd::float8 as cost_usd, official_url, content_hash from public.pathway_tasks"
    " where official_url like 'https://%%' and not needs_review order by last_verified nulls first, id"
)


def classify(task: dict[str, Any], f: Fetched) -> tuple[CheckStatus, str | None]:
    """`(status, summary_key)` before any model call. 'changed' needs the model step; the others are final."""
    if f.moved:
        return "moved", "moved"
    if f.outcome != "ok":
        return "unreachable", f.outcome
    if task["content_hash"] is None:
        return "unchanged", "baseline"
    if f.content_hash == task["content_hash"]:
        return "unchanged", None
    return "changed", None


async def verify_pass(
    db: Database,
    gateway: Gateway | None,
    cfg: PathwayRules,
    domains: list[str],
    fetcher: Fetcher,
    *,
    run_id: str | None = None,
) -> VerifyStats:
    stats = VerifyStats()
    if db.pool is None:
        await db.open()
    assert db.pool is not None
    async with db.pool.connection(timeout=10.0) as conn:
        tasks = await fetch(conn, _SELECT)
    picked = [t for t in tasks if rules.is_official_url(t["official_url"], domains)][: cfg.verify_max_tasks_per_run]
    stats.tasks = len(picked)
    for task in picked:
        fetched = await fetcher.fetch(task["official_url"])
        stats.fetched += 1
        status, key = classify(task, fetched)
        finding: dict[str, Any]
        if status == "changed":
            finding = await compare_with_model(gateway, task, fetched, stats, cfg, run_id)
        else:
            finding = {"status": status, "summary_key": key, "differences": [], "quotes": []}
        flagged = status != "unchanged"
        stats.count(status)
        if key == "baseline":
            stats.baselined += 1
        async with db.pool.connection(timeout=10.0) as conn, conn.transaction():
            await conn.execute(
                "insert into public.pathway_task_checks"
                " (task_id, url, http_status, content_hash, changed, finding, run_id)"
                " values (%s, %s, %s, %s, %s, %s, %s)",
                (
                    task["id"],
                    fetched.url if fetched.url.startswith("https://") else task["official_url"],
                    fetched.status,
                    fetched.content_hash,
                    flagged,
                    jsonb(finding),
                    run_id,
                ),
            )
            if flagged:
                await conn.execute("update public.pathway_tasks set needs_review = true where id = %s", (task["id"],))
                stats.flagged += 1
            elif key == "baseline":
                await conn.execute(
                    "update public.pathway_tasks set content_hash = %s where id = %s and content_hash is null",
                    (fetched.content_hash, task["id"]),
                )
    async with db.pool.connection(timeout=10.0) as conn:
        cur = await conn.execute(
            "delete from public.pathway_task_checks where checked_at < %s and (resolved_at is not null or not changed)",
            (dt.datetime.now(dt.UTC) - dt.timedelta(days=RETENTION_DAYS),),
        )
        stats.pruned = cur.rowcount
    log.info("pathway_verify.done", **{k: v for k, v in stats.as_dict().items() if k != "notes"})
    return stats
