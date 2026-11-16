"""Layered search: one entry point, cheapest and most authoritative source first (segment 04 brief §2).

    results = await get_search().search("anion gap in DKA", user_id=uid)

Routing (rules, no model call):
- pathway / visa / exam-registration questions and explicit "latest" / news → Tavily, restricted to the
  official domains in `search.include_domains`;
- everything else is treated as medical / evidence: PubMed, Europe PMC, MedlinePlus, openFDA and
  ClinicalTrials.gov in parallel (each rate-limited by its own `rps`);
- SearXNG (self-hosted, JSON API) only when those return nothing useful or Tavily's guard is exhausted.

Every source's results are cached in `search_cache` under `<source>:<normalized query>` with the source's
`ttl_hours`, so repeating a question costs nothing. Tavily is guarded by our own ledger (`search_usage`):
per-user daily cap and a monthly stop at `monthly_credit_guard` credits; Tavily's usage endpoint is never
called from here.

Licences: every result carries `license` and `storable`. Only CC BY / CC BY-SA / CC0 / public-domain items
may be stored (with attribution); everything else (PubMed abstracts, most articles, web pages) is context,
citation and link only. Media helpers return Wikimedia Commons images with an allowed licence (title,
author and licence on each), PMC open-access figures only for CC BY / CC0 articles, and YouTube metadata
via oEmbed for the official IFrame player.

Requests send a generic client identity only (never the student's name or email); queries are logged as a
hash, never as text.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import re
import time
import uuid
import warnings
import xml.etree.ElementTree as ET  # responses are size-capped; ElementTree resolves no external entities
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any, Literal, Protocol
from urllib.parse import quote, urlparse

import httpx
from pydantic import SecretStr

from .db import Database
from .llm_config import RuntimeConfig, SearchLayer, SearchSection
from .llm_privacy import PrivacyDirectory, PrivacyUnavailable, scrub_generic
from .logs import get_logger

log = get_logger("naadi.search")

Intent = Literal["medical", "pathway", "latest", "general"]
MEDICAL_LAYERS = ("pubmed", "europepmc", "medlineplus", "openfda", "clinicaltrials")
MAX_BODY_BYTES = 2_000_000
HTTP_TIMEOUT_S = 8.0
REPO_URL = "https://github.com/arrowassassin/naadi"
STORABLE_LICENSES = re.compile(
    r"^(cc[ -]?by(?:[ -]sa)?(?:[ -]\d(?:\.\d)?)?|cc0(?:[ -]1\.0)?|public[ -]domain|pd(?:[ -].*)?)$", re.I
)
NONCOMMERCIAL = re.compile(r"\b(nc|nd)\b", re.I)
_PATHWAY = re.compile(
    r"\b(visa|j-?1|h-?1b|ecfmg|eras|nrmp|match day|residency application|usmle registration|intealth|oet|uscis|"
    r"travel\.state|work permit|green card|sevis|ds-?2019|interview season|"
    r"step ?[123] (?:fee|registration|scheduling))\b",
    re.I,
)
_LATEST = re.compile(r"\b(latest|recent|news|this (?:year|month|week)|today|updated?|new guideline|20[2-3]\d)\b", re.I)
_TAGS = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def normalize_query(q: str) -> str:
    return _WS.sub(" ", q.strip().lower())[:400]


def query_hash(q: str) -> str:
    return hashlib.sha256(normalize_query(q).encode()).hexdigest()[:16]


def classify(query: str) -> Intent:
    if _PATHWAY.search(query):
        return "pathway"
    if _LATEST.search(query):
        return "latest"
    return "medical"


def strip_html(s: str) -> str:
    return _WS.sub(" ", html.unescape(_TAGS.sub(" ", s))).strip()


def license_is_storable(lic: str | None) -> bool:
    if not lic:
        return False
    norm = lic.strip().lower().replace("_", " ")
    return bool(STORABLE_LICENSES.match(norm)) and not NONCOMMERCIAL.search(norm)


@dataclass(frozen=True)
class SearchResult:
    source: str
    title: str
    url: str
    snippet: str
    license: str
    storable: bool
    kind: Literal["article", "topic", "drug_label", "trial", "web", "image", "video"] = "article"
    published: str | None = None
    citation: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> SearchResult:
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


@dataclass(frozen=True)
class SearchAnswer:
    query_hash: str
    intent: Intent
    results: list[SearchResult]
    layers: dict[str, str]  # layer -> "hit" | "cache" | "empty" | "error" | "skipped:<why>"

    def to_dict(self) -> dict[str, Any]:
        return {
            "query_hash": self.query_hash,
            "intent": self.intent,
            "layers": self.layers,
            "results": [r.to_dict() for r in self.results],
        }


class TavilyGuardExhausted(RuntimeError):
    def __init__(self, reason: Literal["user_daily_cap", "monthly_credits"], reset_at: datetime) -> None:
        super().__init__(reason)
        self.reason = reason
        self.reset_at = reset_at


class SearchStore(Protocol):
    async def get(self, key: str, max_age_s: float) -> list[dict[str, Any]] | None: ...

    async def put(self, key: str, results: list[dict[str, Any]], credits: int) -> None: ...

    async def tavily_usage(self, user_id: uuid.UUID | None) -> tuple[int, int]:
        """(this user's Tavily searches today, Tavily credits used this calendar month UTC)."""

    async def record_tavily(self, user_id: uuid.UUID | None, credits: int) -> None: ...


class PgSearchStore:
    """`search_cache` + `search_usage` through the service connection (both closed to clients)."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def _pool(self) -> Any:
        if self._db.pool is None:
            await self._db.open()
        if self._db.pool is None:
            raise RuntimeError("DATABASE_URL is not set: search needs the cache tables")
        return self._db.pool

    async def get(self, key: str, max_age_s: float) -> list[dict[str, Any]] | None:
        pool = await self._pool()
        async with pool.connection(timeout=5.0) as conn, asyncio.timeout(5.0):
            cur = await conn.execute(
                "select results from public.search_cache where query_norm = %s"
                " and created_at > now() - make_interval(secs => %s)",
                (key, max_age_s),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        value = row[0]
        return list(value) if isinstance(value, list) else None

    async def put(self, key: str, results: list[dict[str, Any]], credits: int) -> None:
        pool = await self._pool()
        async with pool.connection(timeout=5.0) as conn, asyncio.timeout(5.0):
            await conn.execute(
                "insert into public.search_cache (query_norm, results, credits, created_at)"
                " values (%s, %s::jsonb, %s, now())"
                " on conflict (query_norm) do update set results = excluded.results, credits = excluded.credits,"
                " created_at = excluded.created_at",
                (key, json.dumps(results), credits),
            )

    async def tavily_usage(self, user_id: uuid.UUID | None) -> tuple[int, int]:
        pool = await self._pool()
        async with pool.connection(timeout=5.0) as conn, asyncio.timeout(5.0):
            cur = await conn.execute(
                "select"
                " count(*) filter (where user_id is not distinct from %s"
                "   and created_at >= (date_trunc('day', now() at time zone 'utc') at time zone 'utc')),"
                " coalesce(sum(credits) filter (where"
                "   created_at >= (date_trunc('month', now() at time zone 'utc') at time zone 'utc')), 0)"
                " from public.search_usage where source = 'tavily'",
                (user_id,),
            )
            row = await cur.fetchone()
        return (int(row[0]), int(row[1])) if row else (0, 0)

    async def record_tavily(self, user_id: uuid.UUID | None, credits: int) -> None:
        pool = await self._pool()
        async with pool.connection(timeout=5.0) as conn, asyncio.timeout(5.0):
            await conn.execute(
                "insert into public.search_usage (user_id, source, credits) values (%s, 'tavily', %s)",
                (user_id, credits),
            )


class _Limiter:
    """At most `rps` requests per second per source (spacing, async-safe)."""

    def __init__(
        self, clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    ) -> None:
        self._next: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._clock = clock
        self._sleep = sleep

    async def wait(self, source: str, rps: float) -> None:
        lock = self._locks.setdefault(source, asyncio.Lock())
        async with lock:
            now = self._clock()
            at = self._next.get(source, now)
            if at > now:
                await self._sleep(at - now)
                now = at
            self._next[source] = now + 1.0 / rps


def _month_reset(now: datetime | None = None) -> datetime:
    now = now or datetime.now(UTC)
    first = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return (first + timedelta(days=32)).replace(day=1)


def _day_reset(now: datetime | None = None) -> datetime:
    now = now or datetime.now(UTC)
    return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


# ── source adapters: pure parsers (unit-tested against recorded responses) ──


def parse_europepmc(body: Mapping[str, Any], limit: int) -> list[SearchResult]:
    out: list[SearchResult] = []
    for r in (body.get("resultList") or {}).get("result", [])[:limit]:
        lic = (r.get("license") or "").strip()
        pmcid = r.get("pmcid")
        pmid = r.get("pmid")
        url = f"https://europepmc.org/article/{r.get('source', 'MED')}/{r.get('id')}"
        if pmcid:
            url = f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/"
        journal = ((r.get("journalInfo") or {}).get("journal") or {}).get("title")
        out.append(
            SearchResult(
                source="europepmc",
                title=strip_html(r.get("title") or ""),
                url=url,
                # Abstract text only for reusable licences; otherwise title + citation + link, like PubMed.
                snippet=strip_html(r.get("abstractText") or "")[:500] if license_is_storable(lic) else "",
                license=lic or "unknown",
                storable=license_is_storable(lic),
                published=r.get("firstPublicationDate") or r.get("pubYear"),
                citation=", ".join(x for x in (r.get("authorString"), journal, r.get("pubYear")) if x) or None,
                meta={"pmid": pmid, "pmcid": pmcid, "doi": r.get("doi"), "open_access": r.get("isOpenAccess") == "Y"},
            )
        )
    return out


def parse_medlineplus(xml_text: str, limit: int) -> list[SearchResult]:
    root = ET.fromstring(xml_text)  # noqa: S314 - NLM response, size-capped; no external entities are resolved
    out: list[SearchResult] = []
    for doc in root.iter("document"):
        fields = {c.get("name"): (c.text or "") for c in doc.iter("content")}
        out.append(
            SearchResult(
                source="medlineplus",
                title=strip_html(fields.get("title", "")),
                url=doc.get("url") or "",
                snippet=strip_html(fields.get("FullSummary") or fields.get("snippet") or "")[:500],
                # MedlinePlus health topics are written by NLM (US government): public domain.
                license="public domain",
                storable=False,  # link-out plain-language explainers; we cite, we do not copy
                kind="topic",
                citation=strip_html(fields.get("organizationName", "")) or "MedlinePlus",
            )
        )
        if len(out) >= limit:
            break
    return out


def parse_openfda(body: Mapping[str, Any], limit: int) -> list[SearchResult]:
    out: list[SearchResult] = []
    for r in (body.get("results") or [])[:limit]:
        fda = r.get("openfda") or {}
        name = ", ".join((fda.get("brand_name") or [])[:2]) or ", ".join((fda.get("generic_name") or [])[:1])
        set_id = r.get("set_id")
        out.append(
            SearchResult(
                source="openfda",
                title=f"{name} (FDA label)" if name else "FDA drug label",
                url=f"https://dailymed.nlm.nih.gov/dailymed/lookup.cfm?setid={set_id}"
                if set_id
                else "https://open.fda.gov/",
                snippet=strip_html((r.get("indications_and_usage") or [""])[0])[:500],
                license="public domain",
                storable=False,
                kind="drug_label",
                published=r.get("effective_time"),
                citation="openFDA drug label",
                meta={"set_id": set_id, "generic_name": (fda.get("generic_name") or [None])[0]},
            )
        )
    return out


def parse_clinicaltrials(body: Mapping[str, Any], limit: int) -> list[SearchResult]:
    out: list[SearchResult] = []
    for s in (body.get("studies") or [])[:limit]:
        p = s.get("protocolSection") or {}
        ident = p.get("identificationModule") or {}
        nct = ident.get("nctId") or ""
        status = (p.get("statusModule") or {}).get("overallStatus")
        out.append(
            SearchResult(
                source="clinicaltrials",
                title=ident.get("briefTitle") or nct,
                url=f"https://clinicaltrials.gov/study/{nct}",
                snippet=strip_html((p.get("descriptionModule") or {}).get("briefSummary") or "")[:500],
                license="public domain",
                storable=False,
                kind="trial",
                citation=f"ClinicalTrials.gov {nct}" + (f" ({status.lower().replace('_', ' ')})" if status else ""),
                meta={"nct_id": nct, "status": status},
            )
        )
    return out


def parse_tavily(body: Mapping[str, Any], limit: int) -> list[SearchResult]:
    out: list[SearchResult] = []
    for r in (body.get("results") or [])[:limit]:
        url = r.get("url") or ""
        out.append(
            SearchResult(
                source="tavily",
                title=r.get("title") or urlparse(url).netloc,
                url=url,
                snippet=(r.get("content") or "")[:500],
                license="unknown",
                storable=False,
                kind="web",
                published=r.get("published_date"),
                citation=urlparse(url).netloc or None,
            )
        )
    return out


def parse_searxng(body: Mapping[str, Any], limit: int) -> list[SearchResult]:
    out: list[SearchResult] = []
    for r in (body.get("results") or [])[:limit]:
        url = r.get("url") or ""
        if not url.startswith("https://"):
            continue
        out.append(
            SearchResult(
                source="searxng",
                title=r.get("title") or urlparse(url).netloc,
                url=url,
                snippet=strip_html(r.get("content") or "")[:500],
                license="unknown",
                storable=False,
                kind="web",
                citation=urlparse(url).netloc or None,
            )
        )
    return out


_COMMONS_OK = re.compile(r"^(cc[ -]by(?:[ -]sa)?(?:[ -]\d(?:\.\d)?)?|cc0|public domain|pd)\b", re.I)
_LATIN = re.compile(r"^[\x20-\x7e]*$")
_NON_EN_SUFFIX = re.compile(
    r"[-_ ](pl|ca|de|fr|es|it|pt|ru|uk|ja|zh|ar|nl|sv|cs|hu|tr|ko)\.(svg|png|jpe?g|gif|webp)$", re.I
)


def parse_commons(body: Mapping[str, Any], limit: int) -> list[SearchResult]:
    pages = list(((body.get("query") or {}).get("pages") or {}).values())
    pages.sort(key=lambda p: p.get("index", 0))
    english: list[SearchResult] = []
    other: list[SearchResult] = []
    for p in pages:
        info = (p.get("imageinfo") or [{}])[0]
        em = info.get("extmetadata") or {}
        lic = strip_html((em.get("LicenseShortName") or {}).get("value", ""))
        if not _COMMONS_OK.match(lic) or NONCOMMERCIAL.search(lic):
            continue
        image = info.get("thumburl") or info.get("url") or ""
        host = urlparse(image).hostname or ""
        if host not in {"upload.wikimedia.org", "thumb.wikimedia.org"}:
            continue
        title = (p.get("title") or "").removeprefix("File:")
        res = SearchResult(
            source="commons",
            title=title,
            url=info.get("descriptionurl") or f"https://commons.wikimedia.org/wiki/{quote(p.get('title') or '')}",
            snippet=strip_html((em.get("ImageDescription") or {}).get("value", ""))[:300],
            license=lic,
            storable=license_is_storable(lic),
            kind="image",
            citation=strip_html((em.get("Artist") or {}).get("value", ""))[:200] or "Wikimedia Commons",
            meta={
                "image_url": image,
                "width": info.get("thumbwidth") or info.get("width"),
                "height": info.get("thumbheight") or info.get("height"),
                "author": strip_html((em.get("Artist") or {}).get("value", ""))[:200],
                "license_url": (em.get("LicenseUrl") or {}).get("value"),
            },
        )
        (other if _NON_EN_SUFFIX.search(title) or not _LATIN.match(title) else english).append(res)
    return (english + other)[:limit]


_YT_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def youtube_id(url: str) -> str | None:
    u = urlparse(url)
    host = (u.hostname or "").lower()
    if host in {"youtu.be"}:
        vid = u.path.lstrip("/").split("/")[0]
    elif host.endswith("youtube.com") or host.endswith("youtube-nocookie.com"):
        if u.path == "/watch":
            vid = dict(x.split("=", 1) for x in u.query.split("&") if "=" in x).get("v", "")
        elif u.path.startswith(("/embed/", "/shorts/", "/live/")):
            vid = u.path.split("/")[2]
        else:
            return None
    else:
        return None
    return vid if _YT_ID.match(vid) else None


def parse_youtube_oembed(body: Mapping[str, Any], video_id: str) -> SearchResult:
    return SearchResult(
        source="youtube",
        title=body.get("title") or "YouTube video",
        url=f"https://www.youtube.com/watch?v={video_id}",
        snippet="",
        license="youtube standard license",
        storable=False,
        kind="video",
        citation=body.get("author_name"),
        meta={
            "video_id": video_id,
            "author_url": body.get("author_url"),
            "thumbnail_url": body.get("thumbnail_url"),
            "embed_url": f"https://www.youtube-nocookie.com/embed/{video_id}",
        },
    )


PMC_OA_BUCKET = "https://pmc-oa-opendata.s3.amazonaws.com"
_S3_KEY = re.compile(r"<Key>([^<]+)</Key>")
_FIG = re.compile(r"<fig[\s>].*?</fig>", re.S)
_FIG_LABEL = re.compile(r"<label>(.*?)</label>", re.S)
_FIG_CAPTION = re.compile(r"<caption>(.*?)</caption>", re.S)
_FIG_HREF = re.compile(r'<graphic[^>]*xlink:href="([^"]+)"')
_IMAGE_EXT = re.compile(r"\.(webp|jpe?g|png|gif)$", re.I)


def latest_pmc_version(list_xml: str, pmcid: str) -> str | None:
    """`PMC123.2` from an S3 listing of the PMC Article Datasets bucket (highest version wins)."""
    versions: set[int] = set()
    for key in _S3_KEY.findall(list_xml):
        m = re.match(rf"^{re.escape(pmcid)}\.(\d+)/", key)
        if m:
            versions.add(int(m.group(1)))
    return f"{pmcid}.{max(versions)}" if versions else None


def parse_pmc_figures(meta: Mapping[str, Any], article_xml: str, versioned: str, limit: int) -> list[SearchResult]:
    """Figures of one PMC open-access article. `meta` is the dataset's `<PMCID>.<v>.json` record; only a
    CC BY / CC0 / public-domain article that is not retracted yields images (with citation + link)."""
    lic = str(meta.get("license_code") or "")
    if not meta.get("is_pmc_openaccess") or meta.get("is_retracted") or not license_is_storable(lic):
        return []
    pmcid = str(meta.get("pmcid") or versioned.split(".", 1)[0])
    media = {u.split("?", 1)[0].rsplit("/", 1)[-1] for u in meta.get("media_urls") or [] if isinstance(u, str)}
    out: list[SearchResult] = []
    for fig in _FIG.findall(article_xml):
        href = _FIG_HREF.search(fig)
        if not href:
            continue
        name = href.group(1).rsplit("/", 1)[-1]
        if not _IMAGE_EXT.search(name) or name not in media:
            continue
        label = _FIG_LABEL.search(fig)
        caption = _FIG_CAPTION.search(fig)
        out.append(
            SearchResult(
                source="pmc_figure",
                title=strip_html(label.group(1)) if label else name,
                url=f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/",
                snippet=strip_html(caption.group(1))[:400] if caption else "",
                license=lic,
                storable=True,
                kind="image",
                citation=str(meta.get("citation") or "") or None,
                meta={
                    "image_url": f"{PMC_OA_BUCKET}/{versioned}/{quote(name)}",
                    "pmcid": pmcid,
                    "doi": meta.get("doi"),
                },
            )
        )
        if len(out) >= limit:
            break
    return out


# ── the service ──


class SearchService:
    def __init__(
        self,
        runtime: RuntimeConfig,
        store: SearchStore,
        *,
        tavily_key: SecretStr | None,
        app_version: str,
        transport: httpx.AsyncBaseTransport | None = None,
        tavily_search: Callable[..., Awaitable[Mapping[str, Any]]] | None = None,
        pubmed_search: Callable[[str, int], Awaitable[list[SearchResult]]] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        privacy: PrivacyDirectory | None = None,
    ) -> None:
        self._runtime = runtime
        self._store = store
        self._tavily_key = tavily_key
        self._ua = f"Naadi/{app_version} (+{REPO_URL})"
        self._transport = transport
        self._limiter = _Limiter(sleep=sleep)
        self._tavily_search = tavily_search or self._tavily_sdk
        self._pubmed_search = pubmed_search or self._pubmed_entrez
        self._client: httpx.AsyncClient | None = None
        self._privacy = privacy

    async def _clean(self, query: str, user_id: uuid.UUID | None) -> str:
        """The query with PII replaced, before any outbound request and before the cache key is computed:
        the student's own identifiers (PrivacyDirectory) for a user-scoped search, generic patterns always."""
        if user_id is None:
            return scrub_generic(query)
        if self._privacy is None:
            raise PrivacyUnavailable("no privacy directory configured for user-scoped search")
        return (await self._privacy.get(user_id)).scrub(query)

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=HTTP_TIMEOUT_S,
                headers={"user-agent": self._ua, "accept": "application/json"},
                transport=self._transport,
                follow_redirects=True,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _get(
        self, url: str, params: Mapping[str, Any] | None = None, accept: str = "application/json"
    ) -> httpx.Response:
        res = await self._http().get(url, params=params, headers={"accept": accept})
        res.raise_for_status()
        if len(res.content) > MAX_BODY_BYTES:
            raise ValueError("response too large")
        return res

    # Raw httpx below: Europe PMC, MedlinePlus, openFDA, ClinicalTrials.gov, MediaWiki, YouTube oEmbed and
    # SearXNG publish REST APIs but no official Python SDK.
    async def _europepmc(self, q: str, n: int) -> list[SearchResult]:
        res = await self._get(
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
            {"query": q, "format": "json", "resultType": "core", "pageSize": n},
        )
        return parse_europepmc(res.json(), n)

    async def _medlineplus(self, q: str, n: int) -> list[SearchResult]:
        res = await self._get(
            "https://wsearch.nlm.nih.gov/ws/query",
            {"db": "healthTopics", "term": q, "retmax": n},
            accept="application/xml",
        )
        return parse_medlineplus(res.text, n)

    async def _openfda(self, q: str, n: int) -> list[SearchResult]:
        terms = " ".join(re.findall(r"[a-z0-9\-]+", q.lower())[:6])
        if not terms:
            return []
        try:
            res = await self._get("https://api.fda.gov/drug/label.json", {"search": f'"{terms}"', "limit": n})
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:  # openFDA answers 404 for "no matches"
                return []
            raise
        return parse_openfda(res.json(), n)

    async def _clinicaltrials(self, q: str, n: int) -> list[SearchResult]:
        res = await self._get(
            "https://clinicaltrials.gov/api/v2/studies", {"query.term": q, "pageSize": n, "format": "json"}
        )
        return parse_clinicaltrials(res.json(), n)

    async def _searxng(self, q: str, n: int, layer: SearchLayer) -> list[SearchResult]:
        base = str(getattr(layer, "base_url", "") or "").rstrip("/")
        if not base:
            return []
        res = await self._get(f"{base}/search", {"q": q, "format": "json", "safesearch": 1})
        return parse_searxng(res.json(), n)

    async def _pubmed_entrez(self, q: str, n: int) -> list[SearchResult]:
        def run() -> list[SearchResult]:
            from Bio import Entrez as _entrez

            Entrez: Any = _entrez  # biopython ships no type hints

            Entrez.tool = "naadi"
            with warnings.catch_warnings():
                # Bio warns when no email is set; NCBI accepts `tool` alone and we never send a personal email.
                warnings.simplefilter("ignore")
                handle = Entrez.esearch(db="pubmed", term=q, retmax=n, sort="relevance")
                ids = Entrez.read(handle)["IdList"]
                handle.close()
                if not ids:
                    return []
                handle = Entrez.esummary(db="pubmed", id=",".join(ids))
                docs = Entrez.read(handle)
                handle.close()
            out: list[SearchResult] = []
            for d in docs:
                pmid = str(d.get("Id"))
                authors = list(d.get("AuthorList") or [])
                out.append(
                    SearchResult(
                        source="pubmed",
                        title=strip_html(str(d.get("Title") or "")),
                        url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                        snippet="",  # abstracts are copyrighted: cite and link, never copy
                        license="copyrighted (abstract not stored)",
                        storable=False,
                        published=str(d.get("PubDate") or "") or None,
                        citation=", ".join(
                            x
                            for x in (
                                ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else ""),
                                str(d.get("Source") or ""),
                                str(d.get("PubDate") or "")[:4],
                            )
                            if x
                        ),
                        meta={"pmid": pmid, "doi": str(d.get("DOI") or "") or None},
                    )
                )
            return out

        return await asyncio.wait_for(asyncio.to_thread(run), timeout=HTTP_TIMEOUT_S * 2)

    async def _tavily_sdk(self, **kw: Any) -> Mapping[str, Any]:
        from tavily import AsyncTavilyClient  # type: ignore[import-untyped]

        if self._tavily_key is None:
            raise RuntimeError("TAVILY_API_KEY is not set")
        client = AsyncTavilyClient(api_key=self._tavily_key.get_secret_value())
        body: Mapping[str, Any] = await client.search(**kw)
        return body

    async def _cached(
        self,
        name: str,
        q: str,
        layer: SearchLayer,
        fetch: Callable[[], Awaitable[list[SearchResult]]],
        layers: dict[str, str],
        credits: int = 0,
        key_extra: str = "",
    ) -> list[SearchResult]:
        key = f"{name}:{key_extra}{normalize_query(q)}"
        try:
            hit = await self._store.get(key, layer.ttl_hours * 3600)
        except Exception as exc:  # noqa: BLE001 - a cache outage must not stop search
            log.warning("search.cache_unavailable", error=type(exc).__name__)
            hit = None
        if hit is not None:
            layers[name] = "cache"
            return [SearchResult.from_dict(d) for d in hit]
        await self._limiter.wait(name, layer.rps)
        started = time.monotonic()
        try:
            results = await fetch()
        except TavilyGuardExhausted:
            raise
        except Exception as exc:  # noqa: BLE001 - one source failing never fails the search
            layers[name] = "error"
            log.warning("search.layer", layer=name, ok=False, error=type(exc).__name__, query_hash=query_hash(q))
            return []
        layers[name] = "hit" if results else "empty"
        log.info(
            "search.layer",
            layer=name,
            ok=True,
            results=len(results),
            latency_ms=int((time.monotonic() - started) * 1000),
            query_hash=query_hash(q),
        )
        try:
            await self._store.put(key, [r.to_dict() for r in results], credits)
        except Exception as exc:  # noqa: BLE001
            log.warning("search.cache_write_failed", error=type(exc).__name__)
        return results

    async def _tavily(
        self,
        q: str,
        user_id: uuid.UUID | None,
        cfg: SearchSection,
        layer: SearchLayer,
        layers: dict[str, str],
        domains: Sequence[str],
    ) -> list[SearchResult]:
        credits = 2 if cfg.depth == "advanced" else 1

        async def fetch() -> list[SearchResult]:
            if self._tavily_key is None and self._tavily_search == self._tavily_sdk:
                raise RuntimeError("TAVILY_API_KEY is not set")
            user_today, month = await self._store.tavily_usage(user_id)
            if month + credits > cfg.monthly_credit_guard:
                raise TavilyGuardExhausted("monthly_credits", _month_reset())
            if user_id is not None and user_today >= cfg.per_user_daily_cap:
                raise TavilyGuardExhausted("user_daily_cap", _day_reset())
            # Record before the request: a crash mid-call still counts against the monthly stop.
            await self._store.record_tavily(user_id, credits)
            body = await self._tavily_search(
                query=q,
                search_depth=cfg.depth,
                max_results=min(cfg.max_results, layer.max_results),
                include_answer=cfg.include_answer,
                include_domains=list(domains) or None,
            )
            return parse_tavily(body, layer.max_results)

        return await self._cached(
            "tavily", q, layer, fetch, layers, credits=credits, key_extra=",".join(sorted(domains)) + "|"
        )

    async def search(
        self, query: str, *, user_id: uuid.UUID | None, intent: Intent | None = None, web: bool = False
    ) -> SearchAnswer:
        """`web=True`: the student explicitly asked for the web (Tavily first, official domains not forced)."""
        query = query.strip()
        if not query:
            raise ValueError("empty query")
        query = (await self._clean(query, user_id)).strip()
        cfg = (await self._runtime.get()).search
        intent = intent or ("latest" if web else classify(query))
        layers: dict[str, str] = {}
        results: list[SearchResult] = []
        tavily_blocked: str | None = None

        if intent in ("medical", "general"):
            tasks: list[tuple[str, Awaitable[list[SearchResult]]]] = []
            for name in MEDICAL_LAYERS:
                layer = cfg.layer(name)
                if layer is None:
                    layers[name] = "skipped:disabled"
                    continue
                fetchers: dict[str, Callable[[str, int], Awaitable[list[SearchResult]]]] = {
                    "pubmed": self._pubmed_search,
                    "europepmc": self._europepmc,
                    "medlineplus": self._medlineplus,
                    "openfda": self._openfda,
                    "clinicaltrials": self._clinicaltrials,
                }
                tasks.append(
                    (name, self._cached(name, query, layer, partial(fetchers[name], query, layer.max_results), layers))
                )
            for chunk in await asyncio.gather(*(t for _, t in tasks)):
                results.extend(chunk)
        else:
            layer = cfg.layer("tavily")
            if layer is None:
                layers["tavily"] = "skipped:disabled"
            else:
                # Pathway and "latest" questions stay on the official domains in app_config.search; only an
                # explicit "search the web" request (web=True) goes unrestricted.
                domains = [] if web else cfg.include_domains
                try:
                    results.extend(await self._tavily(query, user_id, cfg, layer, layers, domains))
                except TavilyGuardExhausted as exc:
                    layers["tavily"] = f"skipped:{exc.reason}"
                    tavily_blocked = exc.reason

        if not results:
            layer = cfg.layer("searxng")
            if layer is None:
                layers["searxng"] = "skipped:disabled"
            else:
                results.extend(
                    await self._cached(
                        "searxng", query, layer, lambda: self._searxng(query, layer.max_results, layer), layers
                    )
                )
        if tavily_blocked:
            log.info("search.tavily_guard", reason=tavily_blocked, query_hash=query_hash(query))
        return SearchAnswer(query_hash(query), intent, results, layers)

    # ── media ──
    async def commons_images(self, query: str, *, user_id: uuid.UUID | None = None) -> list[SearchResult]:
        query = await self._clean(query, user_id)
        cfg = (await self._runtime.get()).search
        layer = cfg.layer("commons_images")
        if layer is None:
            return []
        n = layer.max_results

        async def fetch() -> list[SearchResult]:
            res = await self._get(
                "https://commons.wikimedia.org/w/api.php",
                {
                    "action": "query",
                    "generator": "search",
                    "gsrsearch": query,
                    "gsrnamespace": 6,
                    "gsrlimit": n * 3,
                    "prop": "imageinfo",
                    "iiprop": "url|extmetadata",
                    "iiurlwidth": 960,
                    "format": "json",
                },
            )
            return parse_commons(res.json(), n)

        return await self._cached("commons_images", query, layer, fetch, {})

    async def pmc_figures(self, pmcid: str, *, limit: int = 3) -> list[SearchResult]:
        """Figures of a PMC open-access article from NCBI's PMC Article Datasets (public S3 bucket), only when
        the dataset record says CC BY / CC0. (The PMC article pages answer automated clients with a
        CAPTCHA, so the dataset is the reliable official route.)"""
        if not re.fullmatch(r"PMC\d{4,10}", pmcid):
            return []
        cfg = (await self._runtime.get()).search
        layer = cfg.layer("europepmc")
        if layer is None:
            return []

        async def fetch() -> list[SearchResult]:
            # Raw httpx: anonymous GETs of a public bucket; an AWS SDK adds nothing here.
            listing = await self._get(
                f"{PMC_OA_BUCKET}/", {"list-type": 2, "prefix": f"{pmcid}."}, accept="application/xml"
            )
            versioned = latest_pmc_version(listing.text, pmcid)
            if versioned is None:
                return []
            meta = (await self._get(f"{PMC_OA_BUCKET}/{versioned}/{versioned}.json")).json()
            if not license_is_storable(str(meta.get("license_code") or "")):
                return []
            xml = await self._get(f"{PMC_OA_BUCKET}/{versioned}/{versioned}.xml", accept="application/xml")
            return parse_pmc_figures(meta, xml.text, versioned, limit)

        return await self._cached("pmc_figures", pmcid, layer, fetch, {})

    async def youtube(self, url: str) -> SearchResult | None:
        vid = youtube_id(url)
        if vid is None:
            return None
        cfg = (await self._runtime.get()).search
        layer = cfg.layer("youtube_oembed")
        if layer is None:
            return None

        async def fetch() -> list[SearchResult]:
            res = await self._get(
                "https://www.youtube.com/oembed", {"url": f"https://www.youtube.com/watch?v={vid}", "format": "json"}
            )
            return [parse_youtube_oembed(res.json(), vid)]

        got = await self._cached("youtube_oembed", vid, layer, fetch, {})
        return got[0] if got else None


_SEARCH: SearchService | None = None


def set_search(s: SearchService | None) -> None:
    global _SEARCH
    _SEARCH = s


def get_search() -> SearchService:
    if _SEARCH is None:
        raise RuntimeError("search is not initialised (naadi_api.agent.runtime.init_runtime)")
    return _SEARCH


__all__ = [
    "SearchAnswer",
    "SearchResult",
    "SearchService",
    "TavilyGuardExhausted",
    "classify",
    "get_search",
    "license_is_storable",
    "normalize_query",
    "set_search",
    "youtube_id",
]
