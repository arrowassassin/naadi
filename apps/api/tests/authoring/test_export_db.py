"""Export-all against the real database and the real storage-api (the local stack): the ZIP is built on the
server, holds only the caller's own rows and files, the vault only when ticked, signed URLs work for the job's 24
hours, expired jobs lose their objects, and nobody sees another student's export."""

from __future__ import annotations

import asyncio
import io
import json
import os
import time
import uuid
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from storage3 import SyncStorageClient

from naadi_api.auth import TokenVerifier
from naadi_api.db import Database
from naadi_api.main import create_app
from tests.auth.accounts import signed_in
from tests.auth.jwt_helpers import SUPABASE_URL, JwksServer, SigningKey
from tests.conftest import fake_service, make_settings
from tests.db.fixtures import insert_row
from tests.db.rls import DB_MARKS, create_auth_user, require_local
from tests.db.storage import local_service_key, require_local_storage

pytestmark = DB_MARKS


@pytest.fixture(scope="module")
def url() -> str:
    from tests.db.rls import database_url, ensure_migrated

    u = database_url()
    require_local(u)
    ensure_migrated(u)
    return u


@pytest.fixture
def pg(url: str) -> Iterator[psycopg.Connection[Any]]:
    with psycopg.connect(url, autocommit=True) as conn:
        yield conn


@pytest.fixture
def key() -> SigningKey:
    return SigningKey()


@pytest.fixture
def storage_url() -> str:
    return require_local_storage()


@pytest.fixture
def bucket(storage_url: str) -> Any:
    client = SyncStorageClient(
        storage_url.rstrip("/") + "/",
        {"apikey": local_service_key(), "Authorization": f"Bearer {local_service_key()}"},
    )
    return client.from_


@pytest.fixture
def client(url: str, key: SigningKey, static_dir: Path, storage_url: str) -> Iterator[TestClient]:
    s = make_settings(
        static_dir=static_dir,
        supabase_url=SUPABASE_URL,
        database_url=SecretStr(url),
        supabase_service_role_key=SecretStr(local_service_key()),
        storage_url=storage_url,
        admin_emails=None,
        app_env="prod",
        supabase_jwt_secret=None,
        openrouter_api_key=None,
        nvidia_api_key=None,
        tavily_api_key=None,
    )
    app = create_app(
        s,
        health=fake_service(),
        db=Database(s.database_url),
        verifier=TokenVerifier(SUPABASE_URL, transport=JwksServer(key).transport()),
    )
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


class Student:
    def __init__(self, pg: psycopg.Connection[Any], key: SigningKey, tag: str) -> None:
        self.pg = pg
        self.id = create_auth_user(pg)
        self.key = key
        self.tag = tag

    @property
    def h(self) -> dict[str, str]:
        return signed_in(self.key, self.id, email=f"{self.tag}@example.com", header="Authorization")


def wait_ready(client: TestClient, student: Student, job_id: str, timeout: float = 30) -> dict[str, Any]:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        job = client.get(f"/api/export/{job_id}", headers=student.h).json()
        if job["status"] not in ("queued", "running"):
            return job  # type: ignore[no-any-return]
        time.sleep(0.2)
    raise AssertionError("the export did not finish")


def fetch_zip(storage_url: str, signed: str) -> zipfile.ZipFile:
    full = signed if signed.startswith("http") else storage_url.rstrip("/") + signed
    r = httpx.get(full, timeout=30)
    assert r.status_code == 200, (full, r.status_code, r.text[:200])
    return zipfile.ZipFile(io.BytesIO(r.content))


@pytest.fixture
def me(pg: psycopg.Connection[Any], key: SigningKey, bucket: Any) -> Iterator[Student]:
    tag = uuid.uuid4().hex[:8]
    s = Student(pg, key, f"me-{tag}")
    other = Student(pg, key, f"other-{tag}")
    s.other = other  # type: ignore[attr-defined]
    for who, name in ((s, "mine"), (other, "theirs")):
        insert_row(pg, "costs", {"user_id": who.id, "label": f"{name} fee", "amount": 12.5})
        insert_row(pg, "report_issues", {"user_id": who.id, "ref_type": "other", "body": f"{name} report"})
        insert_row(pg, "vault_documents", {"user_id": who.id, "doc_type": "passport", "title": f"{name} passport"})
    bucket("user-uploads").upload(f"{s.id}/notes/a.txt", b"hello my notes", {"content-type": "text/plain"})
    bucket("user-uploads").upload(f"{other.id}/theirs.txt", b"not mine", {"content-type": "text/plain"})
    bucket("generated-media").upload(f"{s.id}/pic.png", b"\x89PNG-mine", {"content-type": "image/png"})
    bucket("vault").upload(f"{s.id}/passport.pdf", b"%PDF-SECRET", {"content-type": "application/pdf"})
    yield s
    for b, paths in (
        ("user-uploads", [f"{s.id}/notes/a.txt", f"{other.id}/theirs.txt"]),
        ("generated-media", [f"{s.id}/pic.png"]),
        ("vault", [f"{s.id}/passport.pdf"]),
    ):
        bucket(b).remove(paths)
    pg.execute("delete from public.export_jobs where user_id = any(%s)", ([s.id, other.id],))
    pg.execute("delete from auth.users where id = any(%s)", ([s.id, other.id],))


def test_export_everything_builds_a_zip_of_my_rows_and_files_without_the_vault(
    client: TestClient, me: Student, storage_url: str
) -> None:
    r = client.post("/api/export", json={"include_vault": False}, headers=me.h)
    assert r.status_code == 202, r.text
    assert r.json()["status"] in ("queued", "running") and r.json()["downloads"] == []
    job = wait_ready(client, me, r.json()["id"])
    assert job["status"] == "ready" and job["error"] is None and job["parts"] == 1 and job["bytes"] > 0
    assert job["expires_at"] is not None and job["include_vault"] is False
    assert [d["part"] for d in job["downloads"]] == [1]
    z = fetch_zip(storage_url, job["downloads"][0]["url"])
    names = set(z.namelist())
    assert "manifest.json" in names and "data/costs.json" in names and "data/profiles.json" in names
    assert {"data/questions.json", "data/chapters.json", "data/sources.json", "data/embeddings.json"}.isdisjoint(names)
    assert "data/vault_documents.json" not in names and "data/export_jobs.json" not in names
    assert [c["label"] for c in json.loads(z.read("data/costs.json"))] == ["mine fee"]
    assert [c["body"] for c in json.loads(z.read("data/report_issues.json"))] == ["mine report"]
    assert z.read("files/uploads/notes/a.txt") == b"hello my notes"
    assert z.read("files/generated-media/pic.png") == b"\x89PNG-mine"
    assert not any("vault" in n or "theirs" in n for n in names)
    assert b"theirs" not in b"".join(z.read(n) for n in names)  # nobody else's data in my archive
    m = json.loads(z.read("manifest.json"))
    assert m["include_vault"] is False and m["user_id"] == str(me.id) and m["tables"]["costs"] == 1
    assert {f["name"] for f in m["files"]} == {"files/uploads/notes/a.txt", "files/generated-media/pic.png"}


def test_the_vault_travels_only_when_ticked(client: TestClient, me: Student, storage_url: str) -> None:
    r = client.post("/api/export", json={"include_vault": True}, headers=me.h)
    job = wait_ready(client, me, r.json()["id"])
    z = fetch_zip(storage_url, job["downloads"][0]["url"])
    assert z.read("files/vault/passport.pdf") == b"%PDF-SECRET"
    assert [v["title"] for v in json.loads(z.read("data/vault_documents.json"))] == ["mine passport"]
    assert job["include_vault"] is True


def test_exam_mode_correctness_is_withheld_until_the_block_is_finished(
    client: TestClient, me: Student, storage_url: str, pg: psycopg.Connection[Any]
) -> None:
    src = f"t{uuid.uuid4().hex[:6]}-src"
    insert_row(pg, "sources", {"id": src, "name": "x", "license": "proprietary-naadi"})
    qs = []
    for i in range(3):
        qid = f"{src}-q{i}"
        insert_row(
            pg,
            "questions",
            {
                "id": qid,
                "exam": "step1",
                "stem_html": "<p>s</p>",
                "options": json.dumps([{"label": "A", "html": "a"}]),
                "answer": "A",
                "source_id": src,
            },
        )
        qs.append(qid)
    live, done = uuid.uuid4(), uuid.uuid4()
    for block, finished in ((live, False), (done, True)):
        pg.execute(
            "insert into public.blocks (id, user_id, config, question_ids, finished_at)"
            " values (%s, %s, '{}', %s, case when %s then now() end)",
            (block, me.id, qs, finished),
        )
    rows = [(qs[0], live, "exam"), (qs[1], done, "exam"), (qs[2], None, "tutor")]
    for qid, attempt_block, mode in rows:
        pg.execute(
            "insert into public.attempts (user_id, question_id, block_id, chosen, correct, mode)"
            " values (%s, %s, %s, 'A', true, %s)",
            (me.id, qid, attempt_block, mode),
        )
    try:
        job = wait_ready(client, me, client.post("/api/export", json={}, headers=me.h).json()["id"])
        z = fetch_zip(storage_url, job["downloads"][0]["url"])
        got = {a["question_id"]: a["correct"] for a in json.loads(z.read("data/attempts.json"))}
        assert got == {qs[0]: None, qs[1]: True, qs[2]: True}  # a running exam's grading is not exported
    finally:
        pg.execute("delete from public.attempts where user_id = %s", (me.id,))
        pg.execute("delete from public.blocks where user_id = %s", (me.id,))
        pg.execute("delete from public.questions where source_id = %s", (src,))
        pg.execute("delete from public.sources where id = %s", (src,))


def test_one_export_at_a_time_and_jobs_are_private(
    client: TestClient, me: Student, pg: psycopg.Connection[Any]
) -> None:
    pg.execute("insert into public.export_jobs (user_id, status) values (%s, 'running')", (me.id,))
    again = client.post("/api/export", json={}, headers=me.h)
    assert again.status_code == 409 and again.json()["error"]["code"] == "export_active"
    pg.execute(
        "update public.export_jobs set status = 'ready', expires_at = now() + interval '1 hour' where user_id = %s",
        (me.id,),
    )
    job_id = pg.execute("select id from public.export_jobs where user_id = %s", (me.id,)).fetchone()
    assert job_id is not None
    other: Student = me.other  # type: ignore[attr-defined]
    assert client.get(f"/api/export/{job_id[0]}", headers=other.h).status_code == 404
    assert client.get("/api/export", headers=other.h).json()["items"] == []
    assert [j["id"] for j in client.get("/api/export", headers=me.h).json()["items"]] == [str(job_id[0])]
    assert client.delete(f"/api/export/{job_id[0]}", headers=other.h).status_code == 404
    assert client.post("/api/export", json={}).status_code == 401 and client.get("/api/export").status_code == 401


def test_a_failed_job_says_so_with_a_stable_code(
    client: TestClient, me: Student, pg: psycopg.Connection[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("storage down")

    monkeypatch.setattr("naadi_api.export.service.build_export", boom)
    job = wait_ready(client, me, client.post("/api/export", json={}, headers=me.h).json()["id"])
    assert job["status"] == "failed" and job["error"] == "export_failed" and job["downloads"] == []
    assert client.post("/api/export", json={}, headers=me.h).status_code == 202  # a failed job does not block the next


def test_expired_jobs_lose_their_files_and_downloads(
    client: TestClient, me: Student, pg: psycopg.Connection[Any], storage_url: str, bucket: Any
) -> None:
    job = wait_ready(client, me, client.post("/api/export", json={}, headers=me.h).json()["id"])
    assert job["downloads"]
    assert any(o["name"] == f"{job['id']}.zip" for o in bucket("user-uploads").list(f"{me.id}/exports"))
    pg.execute("update public.export_jobs set expires_at = now() - interval '1 minute' where id = %s", (job["id"],))
    after_expiry = client.get(f"/api/export/{job['id']}", headers=me.h).json()
    assert after_expiry["downloads"] == []  # no new link after the 24 hours, even before the sweep runs
    result = asyncio.run(sweep(cast(Any, client.app).state.export_service))
    assert result["expired"] >= 1
    assert not any(o["name"] == f"{job['id']}.zip" for o in bucket("user-uploads").list(f"{me.id}/exports"))
    assert client.get(f"/api/export/{job['id']}", headers=me.h).json()["status"] == "expired"


async def sweep(svc: Any) -> dict[str, int]:
    """The housekeeping on a service of its own (the app's lives on the TestClient's event loop)."""
    fresh = type(svc)(Database(SecretStr(os.environ["DATABASE_URL"])), svc.storage, svc.buckets)
    try:
        result: dict[str, int] = await fresh.gc()
        return result
    finally:
        await fresh.db.close()


def test_delete_removes_the_job_and_its_files(client: TestClient, me: Student, bucket: Any) -> None:
    job = wait_ready(client, me, client.post("/api/export", json={}, headers=me.h).json()["id"])
    assert client.delete(f"/api/export/{job['id']}", headers=me.h).status_code == 204
    assert client.get(f"/api/export/{job['id']}", headers=me.h).status_code == 404
    assert not any(o["name"].startswith(job["id"]) for o in bucket("user-uploads").list(f"{me.id}/exports"))


def test_export_needs_configured_storage(url: str, key: SigningKey, static_dir: Path, me: Student) -> None:
    s = make_settings(
        static_dir=static_dir,
        supabase_url=SUPABASE_URL,
        database_url=SecretStr(url),
        supabase_service_role_key=None,
        supabase_jwt_secret=None,
        openrouter_api_key=None,
        nvidia_api_key=None,
        tavily_api_key=None,
        admin_emails=None,
    )
    app = create_app(
        s,
        health=fake_service(),
        db=Database(s.database_url),
        verifier=TokenVerifier(SUPABASE_URL, transport=JwksServer(key).transport()),
    )
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.post("/api/export", json={}, headers=me.h)
    assert r.status_code == 503 and r.json()["error"]["code"] == "export_unavailable"
