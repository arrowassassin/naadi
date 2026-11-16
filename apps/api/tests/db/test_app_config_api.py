"""app_config over HTTP against the real database: public config, admin editor with history and optimistic
concurrency, cross-process cache invalidation (LISTEN/NOTIFY), and the admin seed job."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb
from pydantic import SecretStr

from naadi_api.auth import TokenVerifier
from naadi_api.config_api.service import AppConfigService
from naadi_api.db import Database
from naadi_api.main import create_app
from naadi_api.seed.importer import run_seed
from tests.auth.accounts import signed_in
from tests.auth.jwt_helpers import SUPABASE_URL, JwksServer, SigningKey
from tests.conftest import fake_service, make_settings

from .rls import DB_MARKS, create_auth_user, require_local
from .seed_helpers import CONFIG, bundle, options, remove_seed

pytestmark = DB_MARKS

ADMIN_EMAIL = "owner@example.com"


@pytest.fixture(scope="module")
def url(migrated_url: str) -> Iterator[str]:
    require_local(migrated_url)
    b = bundle()
    remove_seed(migrated_url, b)
    run_seed(options(migrated_url, only=("sources", "app_config"), storage=None), bundle=b)
    yield migrated_url
    remove_seed(migrated_url, b)


@pytest.fixture
def users(url: str) -> Iterator[dict[str, uuid.UUID]]:
    with psycopg.connect(url, autocommit=True) as c:
        ids = {"admin": create_auth_user(c), "student": create_auth_user(c)}
    yield ids
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("delete from public.seed_runs")
        c.execute("delete from auth.users where id = any(%s)", (list(ids.values()),))


@pytest.fixture
def key() -> SigningKey:
    return SigningKey()


@pytest.fixture
def client(url: str, key: SigningKey, static_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("NAADI_STORAGE_URL", "")  # the seed job test runs groups that need no Storage
    s = make_settings(
        static_dir=static_dir,
        supabase_url=SUPABASE_URL,
        database_url=SecretStr(url),
        admin_emails=ADMIN_EMAIL,
        app_env="prod",
        # every secret explicitly absent: these tests only ever talk to the local database
        supabase_service_role_key=None,
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


def _h(key: SigningKey, user: uuid.UUID, email: str) -> dict[str, str]:
    # A confirmed account with a live session (the API checks both in Supabase's auth schema).
    return signed_in(key, user, email=email, header="Authorization")


def test_public_config(client: TestClient) -> None:
    assert client.get("/api/config/public").status_code in (404, 405)  # one public config endpoint only
    r = client.get("/api/public-config")
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "public, max-age=60"
    body = r.json()
    assert {"features", "nav", "exam_rules", "ui_copy", "supabase_url"} <= set(body)
    assert body["features"]["today"] is True
    assert body["features"]["assistant"] is False
    assert body["features"]["ink_lab"] is False  # admins only in prod
    assert [i["id"] for i in body["nav"]] == ["today", "learn", "practice"]
    assert body["exam_rules"]["modes"]["exam"]["assistant"] is False
    assert body["ui_copy"]["agent.quota_reset"] == "AI quota resets at {time}"
    # nothing from the server-only keys (model routing, budgets, search rules, buckets, generation)
    for server_only in ("nemotron", "daily_request_cap", "monthly_credit_guard", "content-private", "space_id"):
        assert server_only not in r.text


def test_flag_nav_and_copy_changes_apply_without_a_redeploy(
    client: TestClient, key: SigningKey, users: dict[str, uuid.UUID], url: str
) -> None:
    admin = _h(key, users["admin"], ADMIN_EMAIL)
    student = _h(key, users["student"], "student@example.com")
    me = client.get("/api/me", headers=student).json()
    assert me["features"]["assistant"] is False
    assert me["features"]["ink_lab"] is False  # prod: admins only
    assert client.get("/api/me", headers=admin).json()["features"]["ink_lab"] is True
    nav = client.get("/api/nav", headers=student).json()
    assert nav["source"] == "app_config"
    assert "notes" not in [i["id"] for i in nav["items"]]

    flags = client.get("/api/admin/config/feature_flags", headers=admin).json()
    on = {**flags["value"], "notes": {"enabled": True}}
    r = client.put(
        "/api/admin/config/feature_flags", headers=admin, json={"value": on, "expected_updated_at": flags["updated_at"]}
    )
    assert r.status_code == 200, r.text
    nav_cfg = client.get("/api/admin/config/ui.nav", headers=admin).json()
    items = [
        *nav_cfg["value"],
        {"id": "notes", "label": "Notes", "path": "/notes", "icon": "notebook-pen", "feature": "notes"},
    ]
    r = client.put(
        "/api/admin/config/ui.nav", headers=admin, json={"value": items, "expected_updated_at": nav_cfg["updated_at"]}
    )
    assert r.status_code == 200, r.text
    copy = client.get("/api/admin/config/ui_copy", headers=admin).json()
    r = client.put(
        "/api/admin/config/ui_copy",
        headers=admin,
        json={
            "value": {**copy["value"], "signin.tagline": "Study in one place"},
            "expected_updated_at": copy["updated_at"],
        },
    )
    assert r.status_code == 200, r.text
    try:
        assert "notes" in [i["id"] for i in client.get("/api/nav", headers=student).json()["items"]]
        assert client.get("/api/me", headers=student).json()["features"]["notes"] is True
        public = client.get("/api/public-config").json()
        assert public["features"]["notes"] is True
        assert "notes" in [i["id"] for i in public["nav"]]
        assert public["ui_copy"]["signin.tagline"] == "Study in one place"
    finally:
        with psycopg.connect(url, autocommit=True) as c:
            c.execute("delete from public.app_config where key = any(%s)", (["feature_flags", "ui.nav", "ui_copy"],))
            c.execute(
                "delete from public.app_config_history where key = any(%s)", (["feature_flags", "ui.nav", "ui_copy"],)
            )
        run_seed(options(url, only=("app_config",), storage=None), bundle=bundle())
        client.app.state.app_config.invalidate()  # type: ignore[attr-defined]


def test_required_copy_cannot_be_removed_and_a_restore_clears_the_edit_mark(
    client: TestClient, key: SigningKey, users: dict[str, uuid.UUID], url: str
) -> None:
    admin = _h(key, users["admin"], ADMIN_EMAIL)
    entry = client.get("/api/admin/config/ui_copy", headers=admin).json()
    assert entry["admin_edited_at"] is None
    without = {k: v for k, v in entry["value"].items() if k not in ("signin.submit", "account.sign_out")}
    r = client.put(
        "/api/admin/config/ui_copy", headers=admin, json={"value": without, "expected_updated_at": entry["updated_at"]}
    )
    assert r.status_code == 422
    assert "account.sign_out, signin.submit" in r.text
    edited = {**entry["value"], "signin.submit": "Send the link"}
    saved = client.put(
        "/api/admin/config/ui_copy", headers=admin, json={"value": edited, "expected_updated_at": entry["updated_at"]}
    ).json()
    assert saved["admin_edited_at"] is not None
    restored = client.put(
        "/api/admin/config/ui_copy",
        headers=admin,
        json={"value": entry["value"], "expected_updated_at": saved["updated_at"], "admin_edited": False},
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["admin_edited_at"] is None
    with psycopg.connect(url) as c:
        row = c.execute("select admin_edited_at from public.app_config where key = 'ui_copy'").fetchone()
    assert row == (None,)
    with psycopg.connect(url, autocommit=True) as c:
        # (deleting the admin later would null updated_by, which itself writes a history row)
        c.execute("update public.app_config set updated_by = null where key = 'ui_copy'")
        c.execute("delete from public.app_config_history where key = 'ui_copy'")


async def test_a_stored_copy_missing_new_keys_is_merged_not_discarded(url: str) -> None:
    """An admin-edited ui_copy saved before keys became required: stored strings win, shipped ones fill gaps."""
    shipped = AppConfigService(Database(None), CONFIG).file_value("ui_copy")
    stored = {k: v for k, v in shipped.items() if k not in ("signin.heading", "account.cancel")}
    stored["signin.tagline"] = "Edited by the admin"
    with psycopg.connect(url, autocommit=True) as c:
        c.execute(
            "update public.app_config set value = %s, admin_edited_at = now() where key = 'ui_copy'", (Jsonb(stored),)
        )
    db = Database(SecretStr(url))
    try:
        row = await AppConfigService(db, CONFIG).row("ui_copy")
        assert row.source == "db"
        assert row.value["signin.tagline"] == "Edited by the admin"  # stored wins
        assert row.value["signin.heading"] == shipped["signin.heading"]  # filled
        assert row.value["account.cancel"] == shipped["account.cancel"]
        assert row.invalid_reason == "2 missing keys filled from config/app_config.yaml"
        # a broken row (not just missing keys) still falls back to the files as a whole
        with psycopg.connect(url, autocommit=True) as c:
            c.execute("update public.app_config set value = '{\"Bad Key\": \"x\"}' where key = 'ui_copy'")
        broken = await AppConfigService(db, CONFIG).row("ui_copy")
        assert broken.source == "file"
        assert broken.value == shipped
    finally:
        await db.close()
        with psycopg.connect(url, autocommit=True) as c:
            c.execute(
                "update public.app_config set value = %s, admin_edited_at = null where key = 'ui_copy'",
                (Jsonb(shipped),),
            )
            c.execute("delete from public.app_config_history where key = 'ui_copy'")


def test_put_body_is_capped(client: TestClient, key: SigningKey, users: dict[str, uuid.UUID]) -> None:
    admin = _h(key, users["admin"], ADMIN_EMAIL)
    big = {f"x.k{i}": "v" * 400 for i in range(200)}  # ~90 KB
    r = client.put("/api/admin/config/ui_copy", headers=admin, json={"value": big, "expected_updated_at": None})
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "payload_too_large"


def test_admin_list_get_put_history(client: TestClient, key: SigningKey, users: dict[str, uuid.UUID]) -> None:
    admin = _h(key, users["admin"], ADMIN_EMAIL)
    student = _h(key, users["student"], "student@example.com")
    assert client.get("/api/admin/config").status_code == 401
    assert client.get("/api/admin/config", headers=student).status_code == 403
    assert (
        client.put(
            "/api/admin/config/ui_copy", headers=student, json={"value": {}, "expected_updated_at": None}
        ).status_code
        == 403
    )

    listed = client.get("/api/admin/config", headers=admin).json()["items"]
    assert {i["key"]: i["source"] for i in listed} == {i["key"]: "db" for i in listed}
    assert len(listed) == 12  # S10 added fsrs

    entry = client.get("/api/admin/config/ui_copy", headers=admin).json()
    assert entry["source"] == "db"
    assert entry["json_schema"]["type"] == "object"
    new = {**entry["value"], "agent.offline": "No connection: the assistant needs one."}
    r = client.put(
        "/api/admin/config/ui_copy", headers=admin, json={"value": new, "expected_updated_at": entry["updated_at"]}
    )
    assert r.status_code == 200, r.text
    saved = r.json()
    assert saved["updated_by"] == str(users["admin"])
    assert saved["value"]["agent.offline"].startswith("No connection")
    # this process sees the change at once (cache invalidated on write)
    assert client.get("/api/public-config").json()["ui_copy"]["agent.offline"].startswith("No connection")

    stale = client.put(
        "/api/admin/config/ui_copy", headers=admin, json={"value": new, "expected_updated_at": entry["updated_at"]}
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "conflict"

    history = client.get("/api/admin/config/ui_copy/history", headers=admin).json()
    assert history["key"] == "ui_copy"
    assert len(history["items"]) == 1
    assert history["items"][0]["op"] == "update"
    assert "agent.offline" in history["items"][0]["value"]
    assert history["items"][0]["value"]["agent.offline"] == "You're offline. The assistant needs a connection."

    bad = client.put(
        "/api/admin/config/ui_copy",
        headers=admin,
        json={"value": {"Bad Key": "x"}, "expected_updated_at": saved["updated_at"]},
    )
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "validation_error"
    budget = client.get("/api/admin/config/budget", headers=admin).json()
    over = client.put(
        "/api/admin/config/budget",
        headers=admin,
        json={"value": {**budget["value"], "reserve_for_chat": 2}, "expected_updated_at": budget["updated_at"]},
    )
    assert over.status_code == 422
    nav = client.get("/api/admin/config/ui.nav", headers=admin).json()
    ghost = [*nav["value"], {"id": "ghost", "label": "Ghost", "path": "/ghost", "icon": "sun", "feature": "ghost"}]
    r = client.put(
        "/api/admin/config/ui.nav", headers=admin, json={"value": ghost, "expected_updated_at": nav["updated_at"]}
    )
    assert r.status_code == 422
    assert "unknown feature flag" in r.json()["error"]["message"]
    assert client.get("/api/admin/config/nope", headers=admin).status_code == 404
    assert client.get("/api/admin/config/Bad Key!", headers=admin).status_code in (404, 422)


async def test_notify_invalidates_other_processes(url: str) -> None:
    """Two services = two API processes. A write through A reaches B's cache via pg_notify, not the TTL."""
    db_a, db_b = Database(SecretStr(url)), Database(SecretStr(url))
    a = AppConfigService(db_a, CONFIG, ttl_s=3600)
    b = AppConfigService(db_b, CONFIG, ttl_s=3600)
    await b.start()
    try:
        for _ in range(50):
            if b.listening:
                break
            await asyncio.sleep(0.1)
        assert b.listening
        before = await b.row("exam_rules")
        value = dict(before.value)
        value["diagnostic_questions"] = 39 if before.value["diagnostic_questions"] != 39 else 40
        with psycopg.connect(url, autocommit=True) as c:
            admin = create_auth_user(c)
        try:
            await a.put("exam_rules", value, expected_updated_at=before.updated_at, user_id=admin)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if (await b.get("exam_rules"))["diagnostic_questions"] == value["diagnostic_questions"]:
                    break
                await asyncio.sleep(0.05)
            assert (await b.get("exam_rules"))["diagnostic_questions"] == value["diagnostic_questions"]
        finally:
            with psycopg.connect(url, autocommit=True) as c:
                c.execute("update public.app_config set updated_by = null where key = 'exam_rules'")
                c.execute("delete from auth.users where id = %s", (admin,))
    finally:
        await b.stop()
        await db_a.close()
        await db_b.close()


def _wait_run(client: TestClient, headers: dict[str, str], run_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        run = client.get(f"/api/admin/seed/runs/{run_id}", headers=headers).json()
        if run["status"] != "running":
            return run  # type: ignore[no-any-return]
        time.sleep(0.2)
    raise AssertionError("seed job did not finish")


def test_admin_seed_job(client: TestClient, key: SigningKey, users: dict[str, uuid.UUID], url: str) -> None:
    admin = _h(key, users["admin"], ADMIN_EMAIL)
    student = _h(key, users["student"], "student@example.com")
    assert client.post("/api/admin/seed", headers=student, json={}).status_code == 403
    assert client.post("/api/admin/seed", headers=admin, json={"only": ["everything"]}).status_code == 422

    r = client.post("/api/admin/seed", headers=admin, json={"dry_run": True, "only": ["sources", "app_config"]})
    assert r.status_code == 202, r.text
    run = _wait_run(client, admin, r.json()["id"])
    assert run["status"] == "succeeded", run["error"]
    assert run["report"]["ok"] is True
    assert run["report"]["dry_run"] is True
    assert run["options"]["only"] == ["sources", "app_config"]
    assert client.get("/api/admin/seed/runs", headers=admin).json()["items"][0]["id"] == run["id"]
    assert client.get(f"/api/admin/seed/runs/{uuid.uuid4()}", headers=admin).status_code == 404

    # a run that needs Storage fails cleanly (no SUPABASE_SERVICE_ROLE_KEY in this app) and says why
    r = client.post("/api/admin/seed", headers=admin, json={"dry_run": True, "only": ["figures"]})
    failed = _wait_run(client, admin, r.json()["id"])
    assert failed["status"] == "failed"
    assert "SUPABASE_SERVICE_ROLE_KEY" in failed["error"]

    # one at a time: a live `running` row blocks a second start; a stale one is failed and replaced
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("insert into public.seed_runs (status) values ('running')")
    assert client.post("/api/admin/seed", headers=admin, json={}).status_code == 409
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("update public.seed_runs set started_at = now() - interval '1 hour' where status = 'running'")
    r = client.post("/api/admin/seed", headers=admin, json={"dry_run": True, "only": ["app_config"]})
    assert r.status_code == 202
    assert _wait_run(client, admin, r.json()["id"])["status"] == "succeeded"
    with psycopg.connect(url) as c:
        row = c.execute("select error from public.seed_runs where error like 'interrupted%%'").fetchone()
    assert row is not None
