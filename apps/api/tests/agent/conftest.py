"""Shared fixtures for the agent integration tests: one real host process per test module."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import psycopg
import pytest
from langgraph_sdk import get_client
from langgraph_sdk.errors import NotFoundError

from ..db.rls import database_url, require_local
from .host_server import Host, running_host


@pytest.fixture(scope="module")
def host(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Host]:
    url = database_url()
    require_local(url)
    with running_host(url, tmp_path_factory.mktemp("agent-host")) as h:
        yield h


def client(h: Host, tok: str | None) -> Any:
    headers = {"authorization": f"Bearer {tok}"} if tok else {}
    return get_client(url=h.url, headers=headers)


async def expect_404(call: Any) -> None:
    """The SDK raises NotFoundError on most routes and a bare httpx.HTTPStatusError on some (join)."""
    try:
        await call()
    except NotFoundError:
        return
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
    else:
        pytest.fail("expected 404")
    if status != 404:
        pytest.fail(f"expected 404, got {status}")


def pg() -> psycopg.Connection:
    return psycopg.connect(database_url(), autocommit=True)


def agent_health_rows(run_id: str) -> list[tuple[Any, ...]]:
    with pg() as c:
        return c.execute(
            "select trigger, ok, provider from public.agent_health where run_id = %s", (run_id,)
        ).fetchall()
