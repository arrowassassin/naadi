"""The weekly official-page check: page fetching (robots, redirects, size), normalising and hashing, the model
comparison with verbatim quotes, the LLM cap, and a whole pass against a real database with fixture pages."""

from __future__ import annotations

import datetime as dt
import json
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import psycopg
import pytest
from pydantic import SecretStr

from naadi_api.config_api.schemas import PathwayRules
from naadi_api.db import Database
from naadi_api.llm import LLMUnavailable
from naadi_api.llm_budget import BudgetExhausted
from naadi_api.pathway import verify

CFG = PathwayRules.model_validate({"vault_types": ["other"], "currencies": ["USD"]})
DOMAINS = ["ecfmg.org", "usmle.org"]
PAGE = (
    "<html><head><title>x</title><style>.a{}</style></head><body><nav>Menu</nav><h1>Fees</h1>"
    "<p>The Step 1 fee is $695.</p><script>var a=1;</script></body></html>"
)


def site(pages: dict[str, tuple[int, str, dict[str, str]]], robots: str | None = None) -> httpx.MockTransport:
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        if req.url.path == "/robots.txt":
            return httpx.Response(200 if robots is not None else 404, text=robots or "")
        status, body, headers = pages.get(str(req.url), (404, "", {}))
        return httpx.Response(status, text=body, headers={"content-type": "text/html", **headers})

    t = httpx.MockTransport(handler)
    t.seen = seen  # type: ignore[attr-defined]
    return t


def fetcher(transport: httpx.MockTransport, **kw: Any) -> verify.HttpFetcher:
    async def no_sleep(_: float) -> None:
        return None

    return verify.HttpFetcher(DOMAINS, transport=transport, delay_s=0, sleep=no_sleep, **kw)


# ── normalising and hashing ──


def test_normalise_drops_markup_scripts_and_whitespace_noise() -> None:
    assert verify.normalise_text(PAGE) == "Menu Fees The Step 1 fee is $695."
    assert verify.normalise_text("<p>A</p>\n\n<p>  B   C </p>") == "A B C"
    a = verify.hash_text(verify.normalise_text("<div>Same</div>"))
    b = verify.hash_text(verify.normalise_text("<div>\n  Same\n</div><script>x()</script>"))
    assert a == b and len(a) == 64
    assert a != verify.hash_text("Different")


# ── fetching ──


async def test_fetch_ok_sends_our_user_agent_and_normalises() -> None:
    seen_agents: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen_agents.append(req.headers["user-agent"])
        return httpx.Response(
            404 if req.url.path == "/robots.txt" else 200,
            text=PAGE,
            headers={"content-type": "text/html; charset=utf-8"},
        )

    f = verify.HttpFetcher(DOMAINS, transport=httpx.MockTransport(handler), delay_s=0)
    out = await f.fetch("https://www.usmle.org/fees")
    await f.aclose()
    assert out.outcome == "ok" and out.status == 200 and out.moved is False
    assert out.text == "Menu Fees The Step 1 fee is $695." and out.content_hash == verify.hash_text(out.text)
    assert set(seen_agents) == {verify.USER_AGENT} and "Naadi" in verify.USER_AGENT


async def test_fetch_is_cached_per_url_within_a_run() -> None:
    t = site({"https://www.usmle.org/fees": (200, PAGE, {})})
    f = fetcher(t)
    await f.fetch("https://www.usmle.org/fees")
    await f.fetch("https://www.usmle.org/fees")
    await f.aclose()
    assert t.seen.count("https://www.usmle.org/fees") == 1  # type: ignore[attr-defined]


@pytest.mark.parametrize(("status", "outcome"), [(404, "http_error"), (500, "http_error"), (403, "http_error")])
async def test_error_statuses_are_unreachable(status: int, outcome: str) -> None:
    f = fetcher(site({"https://www.usmle.org/x": (status, "nope", {})}))
    out = await f.fetch("https://www.usmle.org/x")
    await f.aclose()
    assert (out.outcome, out.status, out.text) == (outcome, status, None)


async def test_a_network_failure_is_unreachable() -> None:
    def boom(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        raise httpx.ConnectTimeout("slow", request=req)

    f = verify.HttpFetcher(DOMAINS, transport=httpx.MockTransport(boom), delay_s=0)
    out = await f.fetch("https://www.usmle.org/x")
    await f.aclose()
    assert out.outcome == "network_error" and out.status is None


async def test_a_redirect_to_another_path_is_moved_and_to_the_same_path_is_not() -> None:
    pages = {
        "https://www.usmle.org/old": (301, "", {"location": "/new"}),
        "https://www.usmle.org/new": (200, PAGE, {}),
        "https://www.usmle.org/same": (301, "", {"location": "https://www.usmle.org/same/"}),
        "https://www.usmle.org/same/": (200, PAGE, {}),
    }
    f = fetcher(site(pages))
    moved = await f.fetch("https://www.usmle.org/old")
    same = await f.fetch("https://www.usmle.org/same")
    await f.aclose()
    assert moved.moved is True and moved.url == "https://www.usmle.org/new" and moved.outcome == "ok"
    assert same.moved is False


async def test_redirects_never_leave_the_official_domains() -> None:
    pages = {"https://www.usmle.org/go": (302, "", {"location": "https://evil.example.com/steal"})}
    t = site(pages)
    f = fetcher(t)
    out = await f.fetch("https://www.usmle.org/go")
    await f.aclose()
    assert out.moved is True and out.outcome == "off_domain_redirect" and out.text is None
    assert not any("evil.example.com" in u for u in t.seen)  # type: ignore[attr-defined]


async def test_a_redirect_loop_stops() -> None:
    f = fetcher(
        site(
            {
                "https://www.usmle.org/a": (302, "", {"location": "/b"}),
                "https://www.usmle.org/b": (302, "", {"location": "/a"}),
            }
        )
    )
    out = await f.fetch("https://www.usmle.org/a")
    await f.aclose()
    assert out.outcome == "too_many_redirects"


async def test_robots_txt_is_obeyed() -> None:
    t = site(
        {"https://www.usmle.org/private/x": (200, PAGE, {}), "https://www.usmle.org/fees": (200, PAGE, {})},
        robots="User-agent: *\nDisallow: /private/",
    )
    f = fetcher(t)
    blocked = await f.fetch("https://www.usmle.org/private/x")
    ok = await f.fetch("https://www.usmle.org/fees")
    await f.aclose()
    assert blocked.outcome == "robots_disallowed" and ok.outcome == "ok"
    assert "https://www.usmle.org/private/x" not in t.seen  # type: ignore[attr-defined]


async def test_non_https_and_foreign_hosts_are_never_fetched() -> None:
    t = site({})
    f = fetcher(t)
    a = await f.fetch("http://www.usmle.org/x")
    b = await f.fetch("https://example.com/x")
    await f.aclose()
    assert a.outcome == b.outcome == "off_domain_redirect" and t.seen == []  # type: ignore[attr-defined]


async def test_an_oversize_page_is_cut_not_loaded_whole() -> None:
    big = "<p>" + "x" * 500_000 + "</p>"
    f = fetcher(site({"https://www.usmle.org/big": (200, big, {})}), max_bytes=50_000)
    out = await f.fetch("https://www.usmle.org/big")
    await f.aclose()
    assert out.text is not None and len(out.text) <= 50_000


async def test_a_pdf_is_hashed_but_not_read() -> None:
    f = fetcher(site({"https://www.usmle.org/b.pdf": (200, "%PDF-1.7 data", {"content-type": "application/pdf"})}))
    out = await f.fetch("https://www.usmle.org/b.pdf")
    await f.aclose()
    assert out.outcome == "ok" and out.text is None and out.content_hash and len(out.content_hash) == 64


# ── quotes and the model's answer ──


def test_only_verbatim_quotes_survive() -> None:
    page = "The Step 1 fee is $695 plus a $210 surcharge. Apply early."
    url = "https://www.usmle.org/fees"
    got = verify.verbatim_quotes(
        [
            "The Step 1 fee is $695 plus a $210 surcharge.",  # verbatim
            "The Step 1 fee is $900.",  # invented
            "  The Step 1   fee is $695\nplus a $210 surcharge. ",  # whitespace is not significant
            "Apply early.",
            "Apply early.",  # duplicate
            "the step 1 fee is $695",  # a different case is not verbatim
            "x" * 301,  # too long
            "",
        ],
        page,
        url,
    )
    assert [q["text"] for q in got] == ["The Step 1 fee is $695 plus a $210 surcharge.", "Apply early."]
    assert all(q["url"] == url for q in got)


def test_at_most_three_quotes_are_kept() -> None:
    page = "A one. B two. C three. D four."
    got = verify.verbatim_quotes(["A one.", "B two.", "C three.", "D four."], page, "https://www.usmle.org/x")
    assert len(got) == 3


@pytest.mark.parametrize(
    ("content", "ok"),
    [
        ('{"still_accurate": false, "differences": ["fee changed"], "quotes": ["q"]}', True),
        ('```json\n{"still_accurate": true}\n```', True),
        ('Sure! {"still_accurate": true, "differences": "none", "quotes": null} done', True),
        ("no json here", False),
        ('{"still_accurate": "maybe"}', False),
        ("{broken", False),
        ([{"type": "text", "text": '{"still_accurate": true}'}], True),
        (None, False),
    ],
)
def test_parse_comparison(content: Any, ok: bool) -> None:
    assert (verify.parse_comparison(content) is not None) is ok


class FakeReply:
    def __init__(self, content: str) -> None:
        self.content = content


class FakeGateway:
    def __init__(self, *replies: str | BaseException) -> None:
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []

    async def ainvoke(self, role: str, messages: list[Any], ctx: Any, **kw: Any) -> FakeReply:
        self.calls.append(
            {
                "role": role,
                "purpose": ctx.purpose,
                "user_id": ctx.user_id,
                "text": "\n".join(str(m.content) for m in messages),
            }
        )
        r = self.replies.pop(0) if self.replies else '{"still_accurate": true}'
        if isinstance(r, BaseException):
            raise r
        return FakeReply(r)


TASK = {
    "id": "t1",
    "title": "Apply for Step 1",
    "detail_md": "The fee is $695.",
    "cost_usd": 695.0,
    "official_url": "https://www.usmle.org/fees",
}


def changed_fetch(text: str = "The Step 1 fee is $750 from January.") -> verify.Fetched:
    return verify.Fetched("https://www.usmle.org/fees", 200, text, verify.hash_text(text), "ok")


async def test_compare_uses_the_structured_role_for_a_job_with_no_user() -> None:
    gw = FakeGateway(
        json.dumps(
            {
                "still_accurate": False,
                "differences": ["The fee is now $750."],
                "quotes": ["The Step 1 fee is $750 from January.", "The fee is $9."],
            }
        )
    )
    stats = verify.VerifyStats()
    out = await verify.compare_with_model(gw, TASK, changed_fetch(), stats, CFG, "run-1")
    assert out["summary_key"] == "page_changed_differences" and out["still_accurate"] is False
    assert out["differences"] == ["The fee is now $750."]
    assert out["quotes"] == [{"text": "The Step 1 fee is $750 from January.", "url": "https://www.usmle.org/fees"}]
    call = gw.calls[0]
    assert (call["role"], call["purpose"], call["user_id"]) == ("structured", "job", None)  # no student in the call
    assert "Apply for Step 1" in call["text"] and "$750" in call["text"]
    assert stats.llm_calls == 1


async def test_the_model_budget_per_run_is_a_hard_cap() -> None:
    cfg = PathwayRules.model_validate({"vault_types": ["other"], "currencies": ["USD"], "verify_max_llm_calls": 2})
    gw = FakeGateway()
    stats = verify.VerifyStats()
    outs = [await verify.compare_with_model(gw, TASK, changed_fetch(), stats, cfg, None) for _ in range(5)]
    assert len(gw.calls) == 2 and stats.llm_calls == 2 and stats.llm_skipped == 3
    assert [o["summary_key"] for o in outs[:2]] == ["page_changed_no_difference"] * 2
    assert [o["summary_key"] for o in outs[2:]] == ["page_changed_unreviewed"] * 3
    assert all(o["status"] == "changed" for o in outs)  # still flagged: an admin looks
    zero = PathwayRules.model_validate({"vault_types": ["other"], "currencies": ["USD"], "verify_max_llm_calls": 0})
    gw0 = FakeGateway()
    await verify.compare_with_model(gw0, TASK, changed_fetch(), verify.VerifyStats(), zero, None)
    assert gw0.calls == []


@pytest.mark.parametrize(
    "failure",
    [
        BudgetExhausted("daily_cap", dt.datetime.now(dt.UTC), purpose="job"),
        LLMUnavailable("structured", []),
        "not json at all",
    ],
)
async def test_a_model_that_is_spent_down_or_wrong_leaves_an_unreviewed_finding(failure: Any) -> None:
    gw = FakeGateway(failure, '{"still_accurate": true}')
    stats = verify.VerifyStats()
    out = await verify.compare_with_model(gw, TASK, changed_fetch(), stats, CFG, None)
    assert out["summary_key"] == "page_changed_unreviewed" and out["quotes"] == [] and out["differences"] == []
    if isinstance(failure, BudgetExhausted):
        assert stats.budget_spent is True
        again = await verify.compare_with_model(gw, TASK, changed_fetch(), stats, CFG, None)
        assert again["summary_key"] == "page_changed_unreviewed" and len(gw.calls) == 1  # no more calls this run


async def test_without_a_gateway_or_text_no_call_is_made() -> None:
    stats = verify.VerifyStats()
    assert (await verify.compare_with_model(None, TASK, changed_fetch(), stats, CFG, None))[
        "summary_key"
    ] == "page_changed_unreviewed"
    pdf = verify.Fetched("https://www.usmle.org/b.pdf", 200, None, "a" * 64, "ok")
    gw = FakeGateway()
    assert (await verify.compare_with_model(gw, TASK, pdf, stats, CFG, None))[
        "summary_key"
    ] == "page_changed_unreviewed"
    assert gw.calls == []


def test_classification() -> None:
    task = {"content_hash": "h"}

    def mk(**kw: Any) -> verify.Fetched:
        status, chash, outcome = kw.pop("status", 200), kw.pop("content_hash", "h"), kw.pop("outcome", "ok")
        return verify.Fetched("https://www.usmle.org/x", status, "t", chash, outcome, **kw)

    assert verify.classify(task, mk()) == ("unchanged", None)
    assert verify.classify({"content_hash": None}, mk()) == ("unchanged", "baseline")
    assert verify.classify(task, mk(content_hash="other")) == ("changed", None)
    assert verify.classify(task, mk(moved=True)) == ("moved", "moved")
    assert verify.classify(task, mk(outcome="http_error", status=404)) == ("unreachable", "http_error")
    assert verify.classify(task, mk(outcome="robots_disallowed")) == ("unreachable", "robots_disallowed")
    assert verify.classify(task, mk(outcome="network_error", status=None)) == ("unreachable", "network_error")


# ── a whole pass against a real database ──


@pytest.fixture
def tasks(pg: psycopg.Connection[Any]) -> Iterator[dict[str, Any]]:
    from tests.db.fixtures import insert_row

    tag = uuid.uuid4().hex[:8]
    host = f"t{tag}.ecfmg.org"
    stage = f"t{tag}_v"
    insert_row(
        pg,
        "pathway_stages",
        {"key": stage, "sort": 9100, "title": "V", "summary_md": "s", "official_links": psycopg.types.json.Jsonb([])},
    )
    old = dt.date(2026, 1, 1)
    spec = {
        "same": (f"https://{host}/same", PAGE, 1),
        "changed": (f"https://{host}/changed", PAGE, 2),
        "moved": (f"https://{host}/moved", PAGE, 3),
        "gone": (f"https://{host}/gone", PAGE, 4),
        "fresh": (f"https://{host}/fresh", PAGE, 5),
        "offsite": ("https://example.com/offsite", PAGE, 6),
    }
    out: dict[str, Any] = {"host": host, "stage": stage, "ids": {}, "urls": {}}
    for name, (url, page, n) in spec.items():
        tid = f"t{tag}-{name}"
        h = verify.hash_text(verify.normalise_text(page)) if name in ("same", "changed", "moved", "gone") else None
        insert_row(
            pg,
            "pathway_tasks",
            {
                "id": tid,
                "stage_key": stage,
                "sort": n,
                "title": f"Task {name}",
                "detail_md": "The fee is $695.",
                "cost_usd": 695,
                "official_url": url,
                "last_verified": old if name != "fresh" else None,
                "content_hash": h,
            },
        )
        out["ids"][name] = tid
        out["urls"][name] = url
    yield out
    pg.execute("delete from public.pathway_stages where key = %s", (stage,))


async def run_pass(
    url: str,
    tasks: dict[str, Any],
    pages: dict[str, tuple[int, str, dict[str, str]]],
    gateway: Any,
    cfg: PathwayRules = CFG,
) -> verify.VerifyStats:
    db = Database(SecretStr(url))
    await db.open()
    domains = [tasks["host"]]
    f = verify.HttpFetcher(domains, transport=site(pages), delay_s=0)
    try:
        return await verify.verify_pass(db, gateway, cfg, domains, f, run_id="run-test")
    finally:
        await f.aclose()
        await db.close()


@pytest.mark.db
@pytest.mark.integration
async def test_a_pass_records_unchanged_changed_moved_and_unreachable(
    url: str, pg: psycopg.Connection[Any], tasks: dict[str, Any]
) -> None:
    u = tasks["urls"]
    new_page = PAGE.replace("$695", "$750")
    pages = {
        u["same"]: (200, PAGE, {}),
        u["changed"]: (200, new_page, {}),
        u["moved"]: (301, "", {"location": f"https://{tasks['host']}/elsewhere"}),
        f"https://{tasks['host']}/elsewhere": (200, PAGE, {}),
        u["gone"]: (404, "", {}),
        u["fresh"]: (200, PAGE, {}),
    }
    gw = FakeGateway(
        json.dumps(
            {
                "still_accurate": False,
                "differences": ["Fee is now $750."],
                "quotes": ["The Step 1 fee is $750.", "invented line"],
            }
        )
    )
    before = {
        r[0]: r[1:]
        for r in pg.execute(
            "select id, last_verified from public.pathway_tasks where stage_key = %s", (tasks["stage"],)
        ).fetchall()
    }
    stats = await run_pass(url, tasks, pages, gw)
    assert (stats.tasks, stats.unchanged, stats.changed, stats.moved, stats.unreachable, stats.baselined) == (
        5,
        2,
        1,
        1,
        1,
        1,
    )
    assert stats.flagged == 3 and stats.llm_calls == 1

    rows = {
        r[0]: r[1:]
        for r in pg.execute(
            "select t.id, t.needs_review, t.last_verified, c.finding, c.changed, c.http_status, t.content_hash"
            " from public.pathway_tasks t join public.pathway_task_checks c on c.task_id = t.id where t.stage_key = %s",
            (tasks["stage"],),
        ).fetchall()
    }
    ids = tasks["ids"]
    needs, _, finding, changed_flag, http, content_hash = range(6)  # column positions after the task id
    assert rows[ids["same"]][needs] is False and rows[ids["same"]][changed_flag] is False
    fresh = rows[ids["fresh"]]
    assert fresh[needs] is False and fresh[changed_flag] is False and fresh[content_hash] is not None  # baseline stored
    assert fresh[finding]["summary_key"] == "baseline"
    ch = rows[ids["changed"]]
    assert (
        ch[needs] is True and ch[finding]["status"] == "changed" and ch[finding]["differences"] == ["Fee is now $750."]
    )
    assert ch[finding]["quotes"] == [
        {"text": "The Step 1 fee is $750.", "url": tasks["urls"]["changed"]}
    ]  # invented one dropped
    assert rows[ids["moved"]][needs] is True and rows[ids["moved"]][finding]["status"] == "moved"
    gone = rows[ids["gone"]]
    assert gone[needs] is True and gone[finding]["status"] == "unreachable" and gone[http] == 404
    # the weekly check never moves last_verified
    after = {
        r[0]: r[1:]
        for r in pg.execute(
            "select id, last_verified from public.pathway_tasks where stage_key = %s", (tasks["stage"],)
        ).fetchall()
    }
    assert after == before
    # the changed task keeps its old hash until an admin confirms (so a later run does not forget the change)
    assert ch[content_hash] == verify.hash_text(verify.normalise_text(PAGE))
    # the off-domain task was never fetched
    assert pg.execute(
        "select count(*) from public.pathway_task_checks where task_id = %s", (ids["offsite"],)
    ).fetchone() == (0,)


@pytest.mark.db
@pytest.mark.integration
async def test_flagged_tasks_are_not_rechecked_until_an_admin_resolves_them(
    url: str, pg: psycopg.Connection[Any], tasks: dict[str, Any]
) -> None:
    u = tasks["urls"]
    pages = {
        u["same"]: (200, PAGE.replace("$695", "$1"), {}),
        u["changed"]: (200, PAGE, {}),
        u["moved"]: (200, PAGE, {}),
        u["gone"]: (200, PAGE, {}),
        u["fresh"]: (200, PAGE, {}),
    }
    await run_pass(url, tasks, pages, FakeGateway())
    again = await run_pass(url, tasks, pages, FakeGateway())
    assert again.tasks == 4  # `same` is flagged now and waits for an admin; the rest go on
    n = pg.execute(
        "select count(*) from public.pathway_task_checks where task_id = %s", (tasks["ids"]["same"],)
    ).fetchone()
    assert n == (1,)


@pytest.mark.db
@pytest.mark.integration
async def test_a_pass_is_bounded_by_tasks_per_run_and_by_llm_calls(
    url: str, pg: psycopg.Connection[Any], tasks: dict[str, Any]
) -> None:
    changed = PAGE.replace("$695", "$1")
    pg.execute(
        "update public.pathway_tasks set content_hash = %s where stage_key = %s",
        (verify.hash_text("old"), tasks["stage"]),
    )
    pages = {u: (200, changed, {}) for u in tasks["urls"].values()}
    cfg = PathwayRules.model_validate(
        {"vault_types": ["other"], "currencies": ["USD"], "verify_max_tasks_per_run": 3, "verify_max_llm_calls": 2}
    )
    gw = FakeGateway()
    stats = await run_pass(url, tasks, pages, gw, cfg)
    assert stats.tasks == 3 and stats.llm_calls == 2 and stats.llm_skipped == 1 and len(gw.calls) == 2
    # oldest `last_verified` first, never-verified before all: the fresh (null) task goes first
    got = [
        r[0]
        for r in pg.execute(
            "select task_id from public.pathway_task_checks where task_id = any(%s) order by id",
            (list(tasks["ids"].values()),),
        ).fetchall()
    ]
    assert got[0] == tasks["ids"]["fresh"] and len(got) == 3


@pytest.mark.db
@pytest.mark.integration
async def test_old_resolved_checks_are_pruned(url: str, pg: psycopg.Connection[Any], tasks: dict[str, Any]) -> None:
    tid = tasks["ids"]["same"]
    pg.execute(
        "insert into public.pathway_task_checks (task_id, url, changed, finding, checked_at, resolved_at) values"
        " (%s, 'https://x.example/', false, '{\"status\": \"unchanged\"}', now() - interval '200 days', null),"
        " (%s, 'https://x.example/', true, '{\"status\": \"changed\"}', now() - interval '200 days', null)",
        (tid, tid),
    )
    stats = await run_pass(url, tasks, {}, None)
    assert stats.pruned >= 1
    left = pg.execute(
        "select changed from public.pathway_task_checks"
        " where task_id = %s and checked_at < now() - interval '100 days'",
        (tid,),
    ).fetchall()
    assert left == [(True,)]  # an unresolved finding is kept however old
