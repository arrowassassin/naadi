"""The ingest and Library API through the real HTTP path (token -> JWKS -> routes) against the local database, with
in-memory Storage, a spy model gateway and an inline in-process runner (each run reaches its interrupt before the
request returns)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from naadi_api.config_api.schemas import IngestRules
from naadi_api.db import Database
from naadi_api.ingest import pipeline
from naadi_api.ingest.runner import InProcessRunner, set_runner
from naadi_api.ingest.storage import MemoryStorage, set_storage
from tests.auth.jwt_helpers import SigningKey
from tests.qbank.conftest import auth, build_client

from ..db.rls import DB_MARKS
from . import files
from .test_pipeline_db import BUCKETS, SpyGateway

pytestmark = DB_MARKS


@pytest.fixture(scope="module")
def url() -> str:
    from tests.db.rls import database_url, ensure_migrated, require_local

    u = database_url()
    require_local(u)
    ensure_migrated(u)
    return u


@pytest.fixture
def storage() -> Iterator[MemoryStorage]:
    s = MemoryStorage()
    set_storage(s)
    yield s
    set_storage(None)


@pytest.fixture
def spy() -> SpyGateway:
    return SpyGateway("reading")


@pytest.fixture
def runner(
    url: str, storage: MemoryStorage, spy: SpyGateway, monkeypatch: pytest.MonkeyPatch
) -> Iterator[InProcessRunner]:
    pools: dict[int, Database] = {}

    async def fake_build(_config: Any) -> pipeline.Deps:
        import asyncio

        loop = id(asyncio.get_running_loop())
        if loop not in pools:
            pools[loop] = Database(SecretStr(url), max_size=2)
        db = pools[loop]

        async def rules() -> IngestRules:
            return IngestRules()

        async def flags(_u: uuid.UUID) -> dict[str, bool]:
            return {"library": True}

        return pipeline.Deps(
            db=db, storage=storage, gateway=spy, buckets=BUCKETS, rules=rules, flags_for=flags, engine=lambda: None
        )

    monkeypatch.setattr("naadi_api.agent.graphs.ingest.build_deps", fake_build)
    r = InProcessRunner(inline=True)
    set_runner(r)
    yield r
    set_runner(None)


@pytest.fixture
def key() -> SigningKey:
    return SigningKey()


@pytest.fixture
def client(url: str, key: SigningKey, static_dir: Path, runner: InProcessRunner) -> Iterator[TestClient]:
    with build_client(url, key, static_dir) as c:
        yield c


@pytest.fixture
def people(url: str) -> Iterator[tuple[uuid.UUID, uuid.UUID]]:
    a, b = uuid.uuid4(), uuid.uuid4()
    with psycopg.connect(url, autocommit=True) as c:
        for u in (a, b):
            c.execute(
                "insert into auth.users (id, email, aud, role) values (%s, %s, 'authenticated', 'authenticated')",
                (u, f"{u.hex[:10]}@test.invalid"),
            )
    yield a, b
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("delete from public.chapters where owner_user_id = any(%s)", ([a, b],))
        c.execute("delete from auth.users where id = any(%s)", ([a, b],))


def upload(client: TestClient, h: dict[str, str], storage: MemoryStorage, data: bytes, name: str, ctype: str) -> str:
    r = client.post("/api/ingest/uploads", json={"name": name, "size": len(data), "content_type": ctype}, headers=h)
    assert r.status_code == 201, r.text
    t = r.json()
    assert t["bucket"] == "user-uploads" and t["path"].endswith(f"{t['item_id']}/original") and t["token"]
    storage.objects[("user-uploads", t["path"])] = (data, ctype)
    r = client.post(f"/api/ingest/items/{t['item_id']}/start", headers=h)
    assert r.status_code == 202, r.text
    return str(t["item_id"])


def test_upload_confirm_library_undo_delete(
    client: TestClient, key: SigningKey, storage: MemoryStorage, people: tuple[uuid.UUID, uuid.UUID], spy: SpyGateway
) -> None:
    a, b = people
    h, hb = auth(key, a), auth(key, b)
    item = upload(client, h, storage, files.pdf(3), "Renal lecture.pdf", "application/pdf")
    got = client.get(f"/api/ingest/items/{item}", headers=h).json()
    assert got["status"] == "needs_confirmation" and got["stage"] == "confirm" and got["input_kind"] == "pdf"
    assert got["options"][0]["kind"] == "private_chapter" and got["options"][0]["recommended"]
    assert "params" not in got["options"][0] and got["counts"]["pages"] == 3
    assert client.get(f"/api/ingest/items/{item}", headers=hb).status_code == 404
    assert (
        client.post(
            f"/api/ingest/items/{item}/confirm", json={"placement": {"option_id": "private_chapter:0"}}, headers=hb
        ).status_code
        == 404
    )

    r = client.post(
        f"/api/ingest/items/{item}/confirm",
        json={"placement": {"option_id": got["options"][0]["option_id"]}},
        headers=h,
    )
    assert r.status_code == 202, r.text
    done = client.get(f"/api/ingest/items/{item}", headers=h).json()
    assert done["status"] == "ready" and done["placed"]["kind"] == "private_chapter" and done["placed"]["can_undo"]
    chapter = done["chapter_id"]
    lib = client.get("/api/library", headers=h).json()
    assert [c["id"] for c in lib["chapters"]] == [chapter] and lib["chapters"][0]["item_id"] == item
    assert lib["storage"]["limit_bytes"] == 600 * 1024 * 1024 and lib["limits"]["max_file_mb"] == 50
    assert client.get("/api/library", headers=hb).json()["chapters"] == []
    # The chapter reads like any other in the Reader, for its owner only.
    assert client.get(f"/api/chapters/{chapter}", headers=h).json()["kind"] == "user_upload"
    assert client.get(f"/api/chapters/{chapter}", headers=hb).status_code == 404
    # The original for the PDF canvas, and one page's text for citations.
    f = client.get(f"/api/ingest/uploads/{done['upload_id']}/file", headers=h).json()
    assert f["kind"] == "pdf" and f["url"].startswith("memory://user-uploads/") and f["pages"] == 3
    page = client.get(f"/api/ingest/uploads/{done['upload_id']}/pages/2", headers=h).json()
    assert page["page"] == 2 and "Chapter 2" in page["text"]
    assert client.get(f"/api/ingest/uploads/{done['upload_id']}/file", headers=hb).status_code == 404

    undone = client.post(f"/api/ingest/items/{item}/undo", headers=h).json()
    assert undone["status"] == "needs_confirmation" and undone["placed"] is None and undone["options"]
    assert client.get(f"/api/chapters/{chapter}", headers=h).status_code == 404
    # A second choice after Undo starts a fresh run from the stored proposal.
    kb = next(o for o in undone["options"] if o["kind"] == "kb_entry")
    client.post(f"/api/ingest/items/{item}/confirm", json={"placement": {"option_id": kb["option_id"]}}, headers=h)
    again = client.get(f"/api/ingest/items/{item}", headers=h).json()
    assert again["status"] == "ready" and again["placed"]["kind"] == "kb_entry"
    assert client.get("/api/library", headers=h).json()["kb"][0]["kind"] == "file"

    assert client.delete(f"/api/ingest/items/{item}", headers=h).status_code == 204
    assert client.get(f"/api/ingest/items/{item}", headers=h).status_code == 404
    assert not [k for k in storage.objects if str(a) in k[1]]
    assert client.get("/api/library", headers=h).json()["chapters"] == []


def test_vault_prompt_through_the_api(
    client: TestClient, key: SigningKey, storage: MemoryStorage, people: tuple[uuid.UUID, uuid.UUID], spy: SpyGateway
) -> None:
    a, _ = people
    h = auth(key, a)
    item = upload(
        client,
        h,
        storage,
        files.pdf(1, text="", extra=files.passport_text(), image=False),
        "scan.pdf",
        "application/pdf",
    )
    got = client.get(f"/api/ingest/items/{item}", headers=h).json()
    assert got["stage"] == "vault" and "passport_mrz" in got["gate"]["signals"] and got["options"] == []
    assert (
        client.post(
            f"/api/ingest/items/{item}/confirm", json={"placement": {"option_id": "kb_entry:0"}}, headers=h
        ).status_code
        == 409
    )
    r = client.post(f"/api/ingest/items/{item}/confirm", json={"vault": "vault"}, headers=h)
    assert r.status_code == 202 and r.json()["status"] == "vaulted"
    assert spy.seen == []
    assert ("vault", f"{a}/{item}.pdf") in storage.objects


def test_validation_quota_batches_links_and_pastes(
    client: TestClient, key: SigningKey, storage: MemoryStorage, people: tuple[uuid.UUID, uuid.UUID]
) -> None:
    a, _ = people
    h = auth(key, a)
    bad = client.post(
        "/api/ingest/uploads", json={"name": "x.exe", "size": 10, "content_type": "application/x-msdownload"}, headers=h
    )
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "unsupported_type"
    big = client.post(
        "/api/ingest/uploads",
        json={"name": "x.pdf", "size": 51 * 1024 * 1024, "content_type": "application/pdf"},
        headers=h,
    )
    assert big.json()["error"]["code"] == "file_too_large"
    missing = client.post(
        "/api/ingest/uploads", json={"name": "x.pdf", "size": 10, "content_type": "application/pdf"}, headers=h
    ).json()
    r = client.post(f"/api/ingest/items/{missing['item_id']}/start", headers=h)
    assert r.status_code == 422 and r.json()["error"]["code"] == "upload_missing"

    batch = client.post(
        "/api/ingest/uploads/batch",
        headers=h,
        json={
            "files": [
                {"name": "cards.csv", "size": len(files.csv_qa()), "content_type": "text/csv"},
                {"name": "notes.md", "size": 20, "content_type": "text/markdown"},
            ]
        },
    ).json()
    assert len(batch["tickets"]) == 2
    storage.objects[("user-uploads", batch["tickets"][0]["path"])] = (files.csv_qa(), "text/csv")
    started = client.post(f"/api/ingest/batches/{batch['batch_id']}/start", headers=h).json()["items"]
    by_id = {i["id"]: i for i in started}
    first, second = (by_id[t["item_id"]] for t in batch["tickets"])
    assert first["status"] == "needs_confirmation" and first["batch_id"] == batch["batch_id"]
    assert first["options"][0]["kind"] == "flashcards" and first["options"][0]["preview"]["count"] == 10
    assert second["stage"] == "upload"  # never uploaded: skipped, still waiting for its file

    paste = client.post("/api/ingest/paste", json={"text": files.LOREM * 10, "title": "My note"}, headers=h).json()
    assert paste["status"] == "needs_confirmation" and paste["input_kind"] == "paste" and paste["name"] == "My note"
    link = client.post("/api/ingest/urls", json={"url": "http://insecure.example/x"}, headers=h)
    assert link.status_code == 422
    listed = client.get("/api/ingest/items?status=needs_confirmation", headers=h).json()["items"]
    assert {i["id"] for i in listed} >= {first["id"], paste["id"]}


def test_failed_items_retry_and_final_rejections_do_not(
    client: TestClient, key: SigningKey, storage: MemoryStorage, people: tuple[uuid.UUID, uuid.UUID]
) -> None:
    a, _ = people
    h = auth(key, a)
    exe = upload(client, h, storage, b"MZ" + b"\0" * 400, "lecture.pdf", "application/pdf")
    got = client.get(f"/api/ingest/items/{exe}", headers=h).json()
    assert (got["status"], got["error"], got["retryable"]) == ("failed", "executable_rejected", False)
    assert client.post(f"/api/ingest/items/{exe}/retry", headers=h).status_code == 409
