"""Integration: the vault over HTTP with an in-memory Storage. Two-step upload with the bytes checked, signed URLs only
for the owner, no body or title in logs, the ingest hand-off."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from naadi_api.config_api.schemas import PathwayRules
from naadi_api.pathway import vault

from .conftest import HEIC, JPEG, PDF, PNG, World

pytestmark = [pytest.mark.integration, pytest.mark.db]


def begin(client: TestClient, world: World, who: uuid.UUID | None = None, **over: Any) -> Any:
    body = {"title": "Passport", "doc_type": "passport", "mime": "application/pdf", "bytes": len(PDF), **over}
    return client.post("/api/vault/uploads", json=body, headers=world.h(who or world.a))


def upload(client: TestClient, world: World, data: bytes = PDF, *, who: uuid.UUID | None = None, **over: Any) -> Any:
    """The whole browser flow: sign, put the bytes straight into Storage, complete."""
    who = who or world.a
    r = begin(client, world, who, bytes=len(data), **over)
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["bucket"] == "vault" and out["path"] == f"{who}/{out['id']}"
    world.storage.objects[("vault", out["path"])] = data
    return client.post(f"/api/vault/{out['id']}/complete", headers=world.h(who)), out["id"]


def test_upload_open_rename_delete(client: TestClient, world: World) -> None:
    h = world.h(world.a)
    r, doc_id = upload(client, world, expires_on=(dt.date.today() + dt.timedelta(days=30)).isoformat())
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["mime"] == "application/pdf" and doc["bytes"] == len(PDF) and doc["doc_type"] == "passport"
    assert doc["expiry"] == "soon" and 28 <= doc["expires_in_days"] <= 31
    assert r.headers["cache-control"] == "no-store"
    listed = client.get("/api/vault", headers=h)
    assert listed.headers["cache-control"] == "no-store"
    assert [d["id"] for d in listed.json()["items"]] == [doc_id]
    assert listed.json()["max_bytes"] == 20 * 1024 * 1024 and "passport" in listed.json()["types"]
    # the signed URL is short-lived and never cached
    u = client.get(f"/api/vault/{doc_id}/url", headers=h)
    assert u.status_code == 200 and u.headers["cache-control"] == "no-store"
    assert u.json()["expires_in"] == 60 and u.json()["url"].startswith("https://storage.example.test/sign/")
    assert world.storage.signed[-1] == (f"{world.a}/{doc_id}", 60)
    p = client.patch(
        f"/api/vault/{doc_id}", json={"title": "Passport (renewed)", "expires_on": None, "doc_type": "visa"}, headers=h
    )
    assert p.status_code == 200 and p.json()["title"] == "Passport (renewed)" and p.json()["expiry"] == "none"
    assert p.json()["doc_type"] == "visa"
    d = client.delete(f"/api/vault/{doc_id}", headers=h)
    assert d.status_code == 204
    assert ("vault", f"{world.a}/{doc_id}") in world.storage.removed
    assert client.get("/api/vault", headers=h).json()["items"] == []
    assert client.delete(f"/api/vault/{doc_id}", headers=h).status_code == 404


@pytest.mark.parametrize(
    ("data", "mime"), [(PDF, "application/pdf"), (JPEG, "image/jpeg"), (PNG, "image/png"), (HEIC, "image/heic")]
)
def test_types_are_sniffed_from_the_bytes(client: TestClient, world: World, data: bytes, mime: str) -> None:
    r, _ = upload(client, world, data)
    assert r.status_code == 200, r.text
    assert r.json()["mime"] == mime


def test_the_declared_type_is_not_trusted(client: TestClient, world: World, pg: psycopg.Connection[Any]) -> None:
    r, doc_id = upload(client, world, b"<html><script>alert(1)</script></html>", mime="application/pdf")
    assert r.status_code == 415 and r.json()["error"]["code"] == "vault_file_type"
    assert client.get("/api/vault", headers=world.h(world.a)).json()["items"] == []
    assert pg.execute("select count(*) from public.vault_documents where id = %s", (doc_id,)).fetchone() == (0,)
    assert ("vault", f"{world.a}/{doc_id}") in world.storage.removed  # the object is gone too
    # an executable renamed to .pdf is no better
    r, _ = upload(client, world, b"MZ\x90\x00\x03" + b"\x00" * 64)
    assert r.status_code == 415


def test_upload_limits_and_validation(client: TestClient, world: World) -> None:
    assert begin(client, world, bytes=20 * 1024 * 1024 + 1).status_code == 422
    assert begin(client, world, mime="text/html").status_code == 422
    assert begin(client, world, doc_type="Bad Type").status_code == 422
    assert begin(client, world, doc_type="nonsense").json()["error"]["code"] == "vault_type_unknown"
    assert begin(client, world, title="   ").status_code == 422
    assert begin(client, world, bytes=0).status_code == 422
    assert client.post("/api/vault/uploads", json={}, headers=world.h(world.a)).status_code == 422


def test_a_pending_upload_is_not_listed_and_complete_needs_the_object(client: TestClient, world: World) -> None:
    r = begin(client, world)
    doc_id = r.json()["id"]
    assert client.get("/api/vault", headers=world.h(world.a)).json()["items"] == []
    assert client.get(f"/api/vault/{doc_id}/url", headers=world.h(world.a)).status_code == 404  # not complete: no URL
    c = client.post(f"/api/vault/{doc_id}/complete", headers=world.h(world.a))
    assert c.status_code == 409 and c.json()["error"]["code"] == "vault_upload_missing"


def test_abandoned_uploads_are_swept(client: TestClient, world: World, pg: psycopg.Connection[Any]) -> None:
    doc_id = begin(client, world).json()["id"]
    pg.execute("update public.vault_documents set created_at = now() - interval '3 hours' where id = %s", (doc_id,))
    begin(client, world)  # the next upload sweeps the stale one
    assert pg.execute("select count(*) from public.vault_documents where id = %s", (doc_id,)).fetchone() == (0,)


def test_complete_is_idempotent(client: TestClient, world: World) -> None:
    r, doc_id = upload(client, world)
    again = client.post(f"/api/vault/{doc_id}/complete", headers=world.h(world.a))
    assert again.status_code == 200 and again.json() == r.json()


def test_another_student_gets_nothing(client: TestClient, world: World) -> None:
    _, doc_id = upload(client, world)
    hb = world.h(world.b)
    assert client.get("/api/vault", headers=hb).json()["items"] == []
    for method, path in (
        ("get", f"/api/vault/{doc_id}/url"),
        ("delete", f"/api/vault/{doc_id}"),
        ("post", f"/api/vault/{doc_id}/complete"),
    ):
        resp = getattr(client, method)(path, headers=hb)
        assert resp.status_code == 404, (method, resp.text)
    assert client.patch(f"/api/vault/{doc_id}", json={"title": "mine now"}, headers=hb).status_code == 404
    # nor can they claim the same id for their own upload
    clash = client.post(
        "/api/vault/uploads",
        json={"id": doc_id, "title": "x", "doc_type": "other", "mime": "application/pdf", "bytes": 5},
        headers=hb,
    )
    assert clash.status_code == 409
    assert ("vault", f"{world.a}/{doc_id}") in world.storage.objects
    assert client.get("/api/vault").status_code == 401
    assert client.get(f"/api/vault/{doc_id}/url").status_code == 401


def test_vault_limit(client: TestClient, world: World, pg: psycopg.Connection[Any]) -> None:
    import json

    base = client.app.state.app_config.file_value("pathway")  # type: ignore[attr-defined]
    pg.execute("delete from public.app_config where key = 'pathway'")
    pg.execute(
        "insert into public.app_config (key, value) values ('pathway', %s::jsonb)",
        (json.dumps({**base, "vault_max_documents": 1}),),
    )
    try:
        client.app.state.app_config.invalidate("pathway")  # type: ignore[attr-defined]
        assert upload(client, world)[0].status_code == 200
        assert begin(client, world).json()["error"]["code"] == "vault_limit"
    finally:
        pg.execute("delete from public.app_config where key = 'pathway'")
        client.app.state.app_config.invalidate("pathway")  # type: ignore[attr-defined]


def test_titles_bytes_and_paths_never_reach_the_logs(
    client: TestClient, world: World, capfd: pytest.CaptureFixture[str]
) -> None:
    _, doc_id = upload(client, world, title="zz-secret-title-zz")
    client.get(f"/api/vault/{doc_id}/url", headers=world.h(world.a))
    client.delete(f"/api/vault/{doc_id}", headers=world.h(world.a))
    out = capfd.readouterr()
    blob = out.out + out.err
    assert "zz-secret-title-zz" not in blob and str(world.a) not in blob and "token=t" not in blob


async def test_adopt_object_moves_a_flagged_upload_into_the_vault(world: World, url: str) -> None:
    """The ingest hand-off: bytes move bucket to bucket inside this process; the source is deleted."""
    from pydantic import SecretStr

    from naadi_api.db import Database
    from naadi_api.pathway.db import service_tx

    cfg = PathwayRules.model_validate(
        {
            "vault_types": ["passport", "other"],
            "currencies": ["USD"],
        }
    )
    src = f"{world.a}/scan.pdf"
    world.storage.objects[("user-uploads", src)] = PDF
    db = Database(SecretStr(url))
    await db.open()
    try:
        async with service_tx(db) as conn:
            row = await vault.adopt_object(
                conn, world.storage, cfg, world.a, bucket="user-uploads", path=src, title="Scan", doc_type="passport"
            )
            assert row["mime"] == "application/pdf" and row["bytes"] == len(PDF)
            assert ("vault", f"{world.a}/{row['id']}") in world.storage.objects
            assert ("user-uploads", src) not in world.storage.objects
            # someone else's object, or an unknown type, is refused
            other = f"{world.b}/x.pdf"
            world.storage.objects[("user-uploads", other)] = PDF
            from naadi_api.pathway.db import PathwayError

            with pytest.raises(PathwayError) as exc:
                await vault.adopt_object(
                    conn, world.storage, cfg, world.a, bucket="user-uploads", path=other, title="x"
                )
            assert exc.value.code == "vault_source_not_yours"
            bad = f"{world.a}/notes.txt"
            world.storage.objects[("user-uploads", bad)] = b"hello"
            with pytest.raises(PathwayError) as exc2:
                await vault.adopt_object(conn, world.storage, cfg, world.a, bucket="user-uploads", path=bad, title="x")
            assert exc2.value.code == "vault_file_type"
            assert ("user-uploads", bad) in world.storage.objects  # a refused file is left where it was
    finally:
        await db.close()
