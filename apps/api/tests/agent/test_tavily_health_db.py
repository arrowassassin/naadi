"""/api/health's Tavily component reads the stored daily reading and our ledger from a real database, and
sends nothing to Tavily (S04 re-review R3)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from naadi_api.db import Database
from naadi_api.health.checks import tavily_check, tavily_db_reader

from ..conftest import make_settings
from ..db.rls import database_url, require_local
from .conftest import pg

pytestmark = [pytest.mark.integration, pytest.mark.db]


async def test_health_reads_the_latest_reading_and_the_ledger(monkeypatch: pytest.MonkeyPatch) -> None:
    url = database_url()
    require_local(url)

    async def refuse(*_: Any, **__: Any) -> Any:
        raise AssertionError("the tavily health check made a network request")

    monkeypatch.setattr(httpx.AsyncClient, "send", refuse)
    detail = {"outcome": "ok", "tavily_used": 40, "tavily_limit": 1000, "ledger_month_credits": 0}
    with pg() as c:
        # Ahead of any other reading, so it is the latest; removed afterwards.
        rid = c.execute(
            "insert into public.agent_health (kind, trigger, ok, detail, created_at)"
            " values ('tavily_usage', 'cron', true, %s::jsonb, now() + interval '1 minute') returning id",
            (json.dumps(detail),),
        ).fetchone()[0]  # type: ignore[index]
        ledger_before = c.execute(
            "select coalesce(sum(credits), 0) from public.search_usage where source = 'tavily'"
            " and created_at >= (date_trunc('month', now() at time zone 'utc') at time zone 'utc')"
        ).fetchone()[0]  # type: ignore[index]
        ids = [
            r[0]
            for r in c.execute(
                "insert into public.search_usage (source, credits) values ('tavily', 2), ('tavily', 1) returning id"
            ).fetchall()
        ]
    db = Database(SecretStr(url))
    try:
        reading = await tavily_db_reader(db)()
        assert reading.reading_ok is True
        assert reading.detail["tavily_used"] == 40
        assert reading.ledger_month_credits == int(ledger_before) + 3
        out = await tavily_check(make_settings(tavily_api_key="tvly-test-key"), 950, tavily_db_reader(db))()
        assert out.quota is not None
        assert out.quota.used == max(40, int(ledger_before) + 3)
        assert out.quota.limit == 1000
    finally:
        await db.close()
        with pg() as c:
            c.execute("delete from public.agent_health where id = %s", (rid,))
            c.execute("delete from public.search_usage where id = any(%s)", (ids,))
