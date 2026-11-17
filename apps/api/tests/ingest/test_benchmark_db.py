"""Acceptance benchmark (segment 15): a generated 50 MB PDF becomes a readable, searchable private chapter in under
two minutes through the pipeline, with the real local embedding engine (fastembed) and a spy model gateway that
proves no PDF byte, no vault content and no student PII reaches `llm.py`.

Runs in CI's db job (a `cpu-basic`-sized runner); locally it needs the db stack and the fastembed model cache.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator, Iterator

import psycopg
import pytest
from pydantic import SecretStr

from naadi_api.auth import CurrentUser
from naadi_api.db import Database
from naadi_api.embeddings.engine import get_engine
from naadi_api.embeddings.query import search_local
from naadi_api.ingest import pipeline
from naadi_api.ingest import store as st
from naadi_api.ingest.storage import MemoryStorage
from naadi_api.llm_privacy import PrivacyDirectory
from naadi_api.settings import default_config_dir

from ..db.rls import DB_MARKS, database_url, ensure_migrated
from . import files
from .test_pipeline_db import SpyGateway, add_upload, make_deps

pytestmark = DB_MARKS
NAME, EMAIL = "Synthetica Placeholderova", "synthetica.placeholder@test.invalid"

BUDGET_S = 120.0


@pytest.fixture(scope="module")
def url() -> str:
    u = database_url()
    ensure_migrated(u)
    return u


@pytest.fixture
def people(url: str) -> Iterator[tuple[uuid.UUID, uuid.UUID]]:
    a, b = uuid.uuid4(), uuid.uuid4()
    with psycopg.connect(url, autocommit=True) as c:
        for u in (a, b):
            c.execute(
                "insert into auth.users (id, email, aud, role) values (%s, %s, 'authenticated', 'authenticated')",
                (u, EMAIL if u == a else f"{u.hex[:10]}@test.invalid"),
            )
            c.execute(
                "insert into public.profiles (user_id, display_name) values (%s, %s) on conflict (user_id) do nothing",
                (u, NAME),
            )
    yield a, b
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("delete from public.embeddings where owner_user_id = any(%s)", ([a, b],))
        c.execute("delete from public.chapters where owner_user_id = any(%s)", ([a, b],))
        c.execute("delete from auth.users where id = any(%s)", ([a, b],))


@pytest.fixture
async def db(url: str) -> AsyncIterator[Database]:
    d = Database(SecretStr(url), max_size=4)
    await d.open()
    yield d
    await d.close()


def _user(uid: uuid.UUID) -> CurrentUser:
    return CurrentUser(user_id=uid, email=None, email_verified=True, is_admin=False, account_checked=True)


async def test_a_50_mb_pdf_becomes_a_searchable_private_chapter_in_two_minutes(
    db: Database, people: tuple[uuid.UUID, uuid.UUID]
) -> None:
    from tests.agent.fakes import FakeClients
    from tests.agent.test_llm_unit import gateway, sections

    a, b = people
    data = files.big_pdf(
        int(__import__("os").environ.get("NAADI_BENCH_MB", "50")),
        preface=f"Lecture notes of {NAME} ({EMAIL}), phone +91 98765 43210.",
    )
    assert len(data) <= 50 * 1024 * 1024, len(data)
    engine = get_engine(default_config_dir())
    engine.warm()
    # The real gateway (privacy directory, scrubbing, vault guard) with fake provider clients that record prompts.
    clients = FakeClients({})
    gw, _ledger, _clock = gateway(sections(), clients)
    gw._privacy = PrivacyDirectory(db)
    storage = MemoryStorage()
    deps = make_deps(db, storage, SpyGateway())
    deps.gateway = gw
    deps.engine = lambda: engine
    item = await add_upload(db, storage, a, data, "Big renal reader.pdf")

    started = time.monotonic()
    out = await pipeline.run_all(deps, item, a)
    elapsed = time.monotonic() - started
    assert out.next == "end", out

    async with st.service_tx(db) as conn:
        row = await st.get_item(conn, item, a)
        assert row is not None and row["status"] == "ready", row and (row["status"], row["error"])
        chapter = row["processed_chapter_id"]
        sections = await st.rows(conn, "select id from public.chapter_sections where chapter_id = %s", (chapter,))
        indexed = await st.rows(
            conn, "select distinct ref_id from public.embeddings where owner_user_id = %s and ref_type = 'upload'", (a,)
        )
    assert sections and {r["ref_id"] for r in indexed} == {r["id"] for r in sections}
    assert elapsed < BUDGET_S, f"{elapsed:.1f}s for a {len(data) // (1024 * 1024)} MB PDF"

    found = await search_local(db, engine, _user(a), "proximal tubule reabsorbs sodium bicarbonate", scope="all")
    assert any(h.ref_id.startswith(chapter) for h in found.hits)
    other = await search_local(db, engine, _user(b), "proximal tubule reabsorbs sodium bicarbonate", scope="all")
    assert not any(h.ref_id.startswith(chapter) for h in other.hits)

    # What the providers received: bounded text, never PDF bytes, never the student's name, email or phone.
    prompts = [m for model in clients.models.values() for call in model.seen for m in call]
    assert prompts, "the structured call was made"
    seen = "\n".join(str(m.content) for m in prompts)
    assert "%PDF" not in seen and len(seen) < 12_000
    assert NAME not in seen and "Placeholderova" not in seen and EMAIL not in seen and "98765" not in seen
    print(f"\n50 MB PDF -> private chapter: {elapsed:.1f}s, {len(sections)} sections")
