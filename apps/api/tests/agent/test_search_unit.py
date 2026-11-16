"""Unit tests for layered search: parsers against recorded responses, licence gates, routing and the
Tavily guard (cache hit/miss, per-user daily cap, monthly credit stop). No network."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from naadi_api.llm_config import RuntimeConfig
from naadi_api.llm_privacy import PrivacyContext, PrivacyUnavailable
from naadi_api.search import (
    SearchResult,
    SearchService,
    classify,
    latest_pmc_version,
    license_is_storable,
    normalize_query,
    parse_clinicaltrials,
    parse_commons,
    parse_europepmc,
    parse_medlineplus,
    parse_openfda,
    parse_pmc_figures,
    parse_youtube_oembed,
    youtube_id,
)

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "search"
CONFIG_DIR = Path(__file__).resolve().parents[4] / "config"


def load(name: str) -> Any:
    text = (FIX / name).read_text(encoding="utf-8")
    return json.loads(text) if name.endswith(".json") else text


class MemStore:
    def __init__(self) -> None:
        self.cache: dict[str, list[dict[str, Any]]] = {}
        self.tavily: list[tuple[uuid.UUID | None, int]] = []
        self.month_offset = 0

    async def get(self, key: str, max_age_s: float) -> list[dict[str, Any]] | None:
        return self.cache.get(key)

    async def put(self, key: str, results: list[dict[str, Any]], credits: int) -> None:
        self.cache[key] = results

    async def tavily_usage(self, user_id: uuid.UUID | None) -> tuple[int, int]:
        return sum(1 for u, _ in self.tavily if u == user_id), self.month_offset + sum(c for _, c in self.tavily)

    async def record_tavily(self, user_id: uuid.UUID | None, credits: int) -> None:
        self.tavily.append((user_id, credits))


def tavily_body(n: int = 2) -> dict[str, Any]:
    return {"results": [{"url": f"https://www.uscis.gov/p{i}", "title": f"T{i}", "content": "c"} for i in range(n)]}


async def no_sleep(_: float) -> None:
    return None


class FakePrivacy:
    """Stand-in for PrivacyDirectory: every user is the same test student."""

    def __init__(self) -> None:
        self.ctx = PrivacyContext.from_profile(
            display_name="Meera Krishnan", email="meera.k@example.org", phone="+91 90000 11111"
        )

    async def get(self, user_id: Any) -> PrivacyContext:
        return self.ctx


def service(
    store: MemStore,
    *,
    tavily_calls: list[dict[str, Any]] | None = None,
    handler: Any = None,
    pubmed_queries: list[str] | None = None,
) -> SearchService:
    calls = tavily_calls if tavily_calls is not None else []
    pq = pubmed_queries if pubmed_queries is not None else []

    async def fake_tavily(**kw: Any) -> dict[str, Any]:
        calls.append(kw)
        return tavily_body()

    async def fake_pubmed(q: str, n: int) -> list[SearchResult]:
        pq.append(q)
        return []

    transport = httpx.MockTransport(handler or (lambda req: httpx.Response(200, json={})))
    return SearchService(
        RuntimeConfig(None, CONFIG_DIR),
        store,
        tavily_key=SecretStr("tvly-test"),
        app_version="test",
        transport=transport,
        tavily_search=fake_tavily,
        pubmed_search=fake_pubmed,
        sleep=no_sleep,
        privacy=FakePrivacy(),  # type: ignore[arg-type]
    )


# ── parsers on recorded responses ──


def test_europepmc_keeps_licence_per_article() -> None:
    rs = parse_europepmc(load("europepmc.json"), 5)
    assert rs and all(r.source == "europepmc" and r.url.startswith("https://") for r in rs)
    cc = [r for r in rs if r.license.lower() == "cc by"]
    assert cc and all(r.storable for r in cc)
    assert all(not r.storable for r in rs if r.license in ("unknown", ""))


def test_medlineplus_topics_are_cited_not_stored() -> None:
    rs = parse_medlineplus(load("medlineplus.xml"), 3)
    assert rs and rs[0].url.startswith("https://medlineplus.gov/")
    assert all(r.kind == "topic" and not r.storable for r in rs)
    assert "<" not in rs[0].snippet


def test_openfda_and_clinicaltrials() -> None:
    fda = parse_openfda(load("openfda.json"), 3)
    assert fda[0].kind == "drug_label" and "dailymed" in fda[0].url
    ct = parse_clinicaltrials(load("clinicaltrials.json"), 3)
    assert ct[0].url.startswith("https://clinicaltrials.gov/study/NCT")
    assert ct[0].meta["nct_id"].startswith("NCT")


def test_commons_keeps_only_reusable_licences_and_prefers_english() -> None:
    body = load("commons.json")
    rs = parse_commons(body, 4)
    assert rs
    for r in rs:
        assert r.kind == "image" and r.license.upper().startswith(("CC BY", "CC0", "PUBLIC DOMAIN", "PD"))
        assert r.meta["image_url"].startswith(("https://upload.wikimedia.org/", "https://thumb.wikimedia.org/"))
        assert r.citation  # author shown on every image
    titles = [r.title for r in rs]
    non_en = [t for t in titles if t.lower().endswith(("-ca.svg", " pl.svg"))]
    if non_en:
        assert titles.index(non_en[0]) >= len(titles) - len(non_en)
    # A non-commercial or all-rights-reserved file is dropped.
    page = next(iter(body["query"]["pages"].values()))
    bad = {
        "query": {
            "pages": {
                "1": {
                    **page,
                    "imageinfo": [
                        {**page["imageinfo"][0], "extmetadata": {"LicenseShortName": {"value": "CC BY-NC 4.0"}}}
                    ],
                }
            }
        }
    }
    assert parse_commons(bad, 4) == []


def test_pmc_figures_only_for_open_cc_by_articles() -> None:
    meta = load("pmc_oa.json")
    xml = load("pmc_oa_figs.xml")
    v = latest_pmc_version(load("pmc_oa_list.xml"), meta["pmcid"])
    assert v == f"{meta['pmcid']}.1"
    figs = parse_pmc_figures(meta, xml, v, 3)
    assert len(figs) == 2
    assert figs[0].title == "Figure 1" and figs[0].citation and figs[0].license == "CC BY"
    assert figs[0].meta["image_url"].startswith(f"https://pmc-oa-opendata.s3.amazonaws.com/{v}/")
    assert parse_pmc_figures({**meta, "license_code": "CC BY-NC"}, xml, v, 3) == []
    assert parse_pmc_figures({**meta, "is_retracted": True}, xml, v, 3) == []


def test_youtube_ids_and_oembed() -> None:
    assert youtube_id("https://www.youtube.com/watch?v=czY5nyvZ7cU&t=10") == "czY5nyvZ7cU"
    assert youtube_id("https://youtu.be/czY5nyvZ7cU") == "czY5nyvZ7cU"
    assert youtube_id("https://www.youtube-nocookie.com/embed/czY5nyvZ7cU") == "czY5nyvZ7cU"
    assert youtube_id("https://evil.example/watch?v=czY5nyvZ7cU") is None
    assert youtube_id("https://www.youtube.com/watch?v=bad") is None
    r = parse_youtube_oembed(load("youtube_oembed.json"), "czY5nyvZ7cU")
    assert r.kind == "video" and r.meta["embed_url"] == "https://www.youtube-nocookie.com/embed/czY5nyvZ7cU"


@pytest.mark.parametrize(
    ("lic", "ok"),
    [
        ("CC BY", True),
        ("cc by", True),
        ("CC BY-SA 3.0", True),
        ("CC0", True),
        ("public domain", True),
        ("CC BY-NC 4.0", False),
        ("CC BY-NC-ND", False),
        ("unknown", False),
        (None, False),
        ("", False),
    ],
)
def test_licence_gate(lic: str | None, ok: bool) -> None:
    assert license_is_storable(lic) is ok


def test_routing_rules() -> None:
    assert classify("How do I apply for a J-1 visa waiver?") == "pathway"
    assert classify("ERAS opening date") == "pathway"
    assert classify("latest ADA guideline on SGLT2") == "latest"
    assert classify("anion gap in DKA") == "medical"
    assert normalize_query("  Anion   GAP  ") == "anion gap"


# ── Tavily guard ──


async def test_tavily_second_identical_query_is_a_cache_hit() -> None:
    store, calls = MemStore(), []
    svc = service(store, tavily_calls=calls)
    uid = uuid.uuid4()
    a = await svc.search("J-1 visa waiver", user_id=uid)
    b = await svc.search("  j-1 VISA   waiver ", user_id=uid)
    assert a.layers["tavily"] == "hit" and b.layers["tavily"] == "cache"
    assert len(calls) == 1 and len(store.tavily) == 1
    assert calls[0]["search_depth"] == "basic" and calls[0]["include_answer"] is False
    assert "uscis.gov" in calls[0]["include_domains"]
    assert [r.url for r in a.results] == [r.url for r in b.results]


async def test_tavily_per_user_daily_cap() -> None:
    store, calls = MemStore(), []
    svc = service(store, tavily_calls=calls)
    uid, other = uuid.uuid4(), uuid.uuid4()
    for i in range(25):
        await svc.search(f"ERAS question {i}", user_id=uid)
    blocked = await svc.search("ERAS question 26", user_id=uid)
    assert blocked.layers["tavily"] == "skipped:user_daily_cap" and len(calls) == 25
    ok = await svc.search("ERAS question 26", user_id=other)
    assert ok.layers["tavily"] == "hit"


async def test_tavily_stops_at_the_monthly_credit_guard() -> None:
    store, calls = MemStore(), []
    store.month_offset = 949
    svc = service(store, tavily_calls=calls)
    first = await svc.search("NRMP match day", user_id=uuid.uuid4())
    assert first.layers["tavily"] == "hit"
    second = await svc.search("NRMP match statistics", user_id=uuid.uuid4())
    assert second.layers["tavily"] == "skipped:monthly_credits"
    assert len(calls) == 1
    # Still served from cache after the stop.
    again = await svc.search("NRMP match day", user_id=uuid.uuid4())
    assert again.layers["tavily"] == "cache"


async def test_medical_layers_run_in_parallel_and_failures_are_isolated() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        host = req.url.host
        if host == "www.ebi.ac.uk":
            return httpx.Response(200, json=load("europepmc.json"))
        if host == "wsearch.nlm.nih.gov":
            return httpx.Response(200, text=load("medlineplus.xml"))
        if host == "api.fda.gov":
            return httpx.Response(500)
        if host == "clinicaltrials.gov":
            return httpx.Response(200, json=load("clinicaltrials.json"))
        return httpx.Response(404)

    store = MemStore()
    svc = service(store, handler=handler)
    ans = await svc.search("diabetic ketoacidosis", user_id=None)
    assert ans.intent == "medical"
    assert ans.layers["openfda"] == "error"
    assert ans.layers["europepmc"] == "hit" and ans.layers["clinicaltrials"] == "hit"
    assert {r.source for r in ans.results} >= {"europepmc", "medlineplus", "clinicaltrials"}
    again = await svc.search("diabetic ketoacidosis", user_id=None)
    assert again.layers["europepmc"] == "cache"


async def test_requests_carry_only_the_generic_client_identity() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"resultList": {"result": []}, "studies": []})

    svc = service(MemStore(), handler=handler)
    await svc.search("renal tubular acidosis", user_id=uuid.uuid4())
    assert seen
    for r in seen:
        assert r.headers["user-agent"].startswith("Naadi/test (+https://")
        assert "authorization" not in r.headers and "cookie" not in r.headers


async def test_no_pii_leaves_in_requests_or_cache_keys() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"resultList": {"result": []}, "studies": []})

    store, tavily, pubmed = MemStore(), [], []
    svc = service(store, handler=handler, tavily_calls=tavily, pubmed_queries=pubmed)
    uid = uuid.uuid4()
    leaky = (
        "Meera Krishnan meera.k@example.org 90000 11111 asks about renal tubular acidosis,"
        " uid 3f2b8c1e-1d2a-4b9e-9c1a-0e5f6a7b8c9d"
    )
    await svc.search(leaky, user_id=uid)
    await svc.search("visa question from meera.k@example.org, call +1 415 555 0134", user_id=uid)
    outbound = [str(r.url) for r in seen] + [str(c["query"]) for c in tavily] + pubmed + list(store.cache)
    assert outbound
    for text in outbound:
        low = text.lower()
        for secret in ("meera", "krishnan", "example.org", "90000", "11111", "3f2b8c1e", "555"):
            assert secret not in low, (secret, text)
    assert any("renal tubular acidosis" in t for t in pubmed)


async def test_user_search_without_privacy_directory_is_refused() -> None:
    svc = service(MemStore())
    svc._privacy = None
    with pytest.raises(PrivacyUnavailable):
        await svc.search("anion gap", user_id=uuid.uuid4())
    # Anonymous (system) searches still run with the generic patterns.
    ans = await svc.search("anion gap", user_id=None)
    assert ans.intent == "medical"


def test_europepmc_copies_abstracts_only_for_reusable_licences() -> None:
    rs = parse_europepmc(load("europepmc.json"), 5)
    for r in rs:
        if r.storable:
            assert r.snippet
        else:
            assert r.snippet == ""
            assert r.title and r.url and r.citation
    body = load("europepmc.json")
    first = dict(body["resultList"]["result"][0], license="cc by-nc")
    only = parse_europepmc({"resultList": {"result": [first]}}, 1)
    assert only[0].snippet == ""
    assert only[0].storable is False


# ── SearXNG: parser and the fallback rule (review item 4). The layer is off in config/models.yaml until
# segment 22 installs SearXNG; these tests switch it on in a config copy. The fixture follows SearXNG's
# documented `format=json` response shape (snippets written for the test). ──


class SearxRuntime(RuntimeConfig):
    """The repo config with the SearXNG layer enabled."""

    async def get(self) -> Any:
        cfg = await super().get()
        layers = dict(cfg.search.layers)
        layers["searxng"] = layers["searxng"].model_copy(update={"enabled": True})
        return cfg.model_copy(update={"search": cfg.search.model_copy(update={"layers": layers})})


def test_searxng_parser_keeps_https_results_only() -> None:
    from naadi_api.search import parse_searxng

    rs = parse_searxng(load("searxng.json"), 5)
    assert [r.url for r in rs] == [
        "https://www.ncbi.nlm.nih.gov/books/NBK519044/",
        "https://medlineplus.gov/ency/article/000493.htm",
    ]
    assert all(r.source == "searxng" and not r.storable and r.license == "unknown" for r in rs)
    assert "<b>" not in rs[1].snippet


def _searx_service(store: MemStore, seen: list[httpx.Request]) -> SearchService:
    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.url.host == "127.0.0.1":
            return httpx.Response(200, json=load("searxng.json"))
        return httpx.Response(200, json={"resultList": {"result": []}, "studies": [], "results": []})

    svc = service(store, handler=handler)
    svc._runtime = SearxRuntime(None, CONFIG_DIR)
    return svc


async def test_searxng_answers_when_every_medical_layer_is_empty() -> None:
    seen: list[httpx.Request] = []
    svc = _searx_service(MemStore(), seen)
    ans = await svc.search("renal tubular acidosis type 4", user_id=None)
    assert ans.layers["searxng"] == "hit"
    assert {r.source for r in ans.results} == {"searxng"}
    searx = [r for r in seen if r.url.host == "127.0.0.1"]
    assert searx and searx[0].url.params["format"] == "json"


async def test_searxng_answers_when_the_tavily_guard_is_exhausted() -> None:
    seen: list[httpx.Request] = []
    store = MemStore()
    store.month_offset = 10_000
    svc = _searx_service(store, seen)
    ans = await svc.search("NRMP match day timeline", user_id=uuid.uuid4())
    assert ans.layers["tavily"] == "skipped:monthly_credits"
    assert ans.layers["searxng"] == "hit"


async def test_searxng_is_not_called_when_a_better_layer_answered() -> None:
    seen: list[httpx.Request] = []
    svc = _searx_service(MemStore(), seen)
    ans = await svc.search("J-1 visa waiver", user_id=uuid.uuid4())
    assert ans.layers["tavily"] == "hit"
    assert "searxng" not in ans.layers
    assert not [r for r in seen if r.url.host == "127.0.0.1"]


async def test_latest_questions_stay_on_official_domains_unless_the_web_is_asked_for() -> None:
    calls: list[dict[str, Any]] = []
    svc = service(MemStore(), tavily_calls=calls)
    await svc.search("latest ECFMG fee update", user_id=uuid.uuid4())
    assert "ecfmg.org" in calls[-1]["include_domains"]
    await svc.search("pharmacology podcast recommendations", user_id=uuid.uuid4(), web=True)
    assert calls[-1]["include_domains"] is None
