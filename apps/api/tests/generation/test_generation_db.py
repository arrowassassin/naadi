"""Integration (real Postgres): the guards, quota and paused state, storage paths, labelling data, user isolation
and the admin approval, through the service and the HTTP routes."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import errors

from naadi_api.generation.client import FakeGenerationClient, next_utc_midnight
from naadi_api.generation.service import GenerationError
from tests.auth.accounts import signed_in
from tests.auth.jwt_helpers import SigningKey
from tests.db.rls import as_user

from .conftest import ADMIN_EMAIL, PRIVATE, PUBLIC, Rig, World

pytestmark = [pytest.mark.integration, pytest.mark.db]


def rows(pg: psycopg.Connection[Any], uid: uuid.UUID) -> list[dict[str, Any]]:
    cur = pg.execute(
        "select id, kind, status, error, prompt, storage_path, model, ai_generated, paused_until"
        " from public.generated_media where user_id = %s order by created_at",
        (uid,),
    )
    cols = [d.name for d in cur.description or []]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


async def refused(rig: Rig, uid: uuid.UUID, kind: str = "image", prompt: str = "heart", **kw: Any) -> GenerationError:
    with pytest.raises(GenerationError) as e:
        await rig.service.run(uid, kind, prompt, **kw)  # type: ignore[arg-type]
    return e.value


# ── a run ──


async def test_image_run_stores_under_the_users_folder_and_streams_progress(
    rig: Rig, world: World, pg: psycopg.Connection[Any]
) -> None:
    events: list[dict[str, Any]] = []

    async def on_event(e: dict[str, Any]) -> None:
        events.append(e)

    item = await rig.service.run(world.user_a, "image", "Nephron cross-section for jane@example.org", on_event=on_event)
    assert item.status == "ready" and item.kind == "image" and item.ai_generated is True and not item.published
    assert "jane@example.org" not in item.prompt and "[email]" in item.prompt  # the stored prompt is the scrubbed one
    assert item.url and item.url.startswith("http://storage.test/generated-media/")
    assert [e["stage"] for e in events] == ["queued", "queued", "generating", "saving"]
    assert all(e["media_id"] == str(item.id) for e in events)
    path = f"{world.user_a}/{item.id}.webp"
    assert (PRIVATE, path) in rig.store.objects and rig.store.objects[(PRIVATE, path)][1] == "image/webp"
    (row,) = rows(pg, world.user_a)
    assert row["storage_path"] == path and row["status"] == "ready" and row["ai_generated"] is True
    assert row["model"] == "fake-image"  # the model the Space reported, not a guess
    assert len(rig.client.calls) == 1 and "[email]" in rig.client.calls[0].prompt  # scrubbed before the Space call


async def test_video_run_is_capped_at_five_seconds_and_stored_as_mp4(rig: Rig, world: World) -> None:
    preview = await rig.service.prepare(world.user_a, "video", "Heart valve opening", 60)
    assert preview.seconds == 5 and preview.kind == "video"
    item = await rig.service.run(world.user_a, "video", "Heart valve opening", 60)
    assert item.kind == "video" and item.duration_s == 1.0 and item.url
    assert rig.client.calls[0].seconds == 5
    assert (PRIVATE, f"{world.user_a}/{item.id}.mp4") in rig.store.objects


async def test_prepare_writes_nothing_and_shows_remaining_quota(
    rig: Rig, world: World, pg: psycopg.Connection[Any]
) -> None:
    p = await rig.service.prepare(world.user_a, "image", "A heart")
    assert (p.remaining_today, p.daily_cap, p.prompt) == (30, 30, "A heart")
    assert p.as_dict() == {
        "kind": "image",
        "prompt": "A heart",
        "seconds": None,
        "remaining_today": 30,
        "daily_cap": 30,
    }
    assert rows(pg, world.user_a) == [] and rig.client.calls == []


# ── guards ──


@pytest.mark.parametrize(
    ("prompt", "code"),
    [
        ("Portrait of Albert Einstein", "prompt_real_person"),
        ("clinical photograph of my patient's rash", "prompt_patient_photo"),
        ("gory surgery scene", "prompt_gore"),
        ("", "prompt_empty"),
    ],
)
async def test_refused_prompts_create_no_row_and_make_no_call(
    rig: Rig, world: World, pg: psycopg.Connection[Any], prompt: str, code: str
) -> None:
    err = await refused(rig, world.user_a, prompt=prompt)
    assert (err.status, err.code) == (422, code)
    assert rows(pg, world.user_a) == [] and rig.client.calls == []


async def test_daily_caps_count_from_the_table_and_failed_runs_do_not_count(
    rig: Rig, world: World, pg: psycopg.Connection[Any]
) -> None:
    rig.caps["image"] = 2  # type: ignore[attr-defined]
    rig.client.fail_with = "generation_unavailable"  # never reached the Space: costs nothing, does not count
    assert (await refused(rig, world.user_a)).code == "generation_unavailable"
    rig.client.fail_with = None
    await rig.service.run(world.user_a, "image", "one")
    await rig.service.run(world.user_a, "image", "two")
    err = await refused(rig, world.user_a, prompt="three")
    assert (err.status, err.code) == (429, "generation_cap_reached")
    assert err.resets_at == next_utc_midnight(rig.now[0])
    assert [r["status"] for r in rows(pg, world.user_a)] == ["failed", "ready", "ready"]
    st = await rig.service.status(world.user_a)
    assert (st.image.used, st.image.cap, st.image.remaining) == (2, 2, 0)
    # another user and the other kind are unaffected
    await rig.service.run(world.user_b, "image", "b one")
    await rig.service.run(world.user_a, "video", "a clip")


async def test_yesterdays_items_do_not_count_toward_today(rig: Rig, world: World, pg: psycopg.Connection[Any]) -> None:
    rig.caps["image"] = 1  # type: ignore[attr-defined]
    await rig.service.run(world.user_a, "image", "yesterday")
    pg.execute(
        "update public.generated_media set created_at = now() - interval '2 days' where user_id = %s", (world.user_a,)
    )
    await rig.service.run(world.user_a, "image", "today")


async def test_quota_exceeded_pauses_with_the_reset_time_and_is_not_retried(
    rig: Rig, world: World, pg: psycopg.Connection[Any]
) -> None:
    reset = datetime.now(UTC) + timedelta(minutes=40)
    rig.client.quota_reset = reset
    err = await refused(rig, world.user_a)
    assert (err.status, err.code, err.resets_at) == (429, "generation_paused", reset)
    (row,) = rows(pg, world.user_a)
    assert row["status"] == "paused" and row["error"] == "generation_paused" and row["paused_until"] == reset
    assert len(rig.client.calls) == 1  # never retried in a loop
    # the pause is shared: another user is stopped before any call, and the status says paused with the reset
    err_b = await refused(rig, world.user_b)
    assert err_b.code == "generation_paused" and err_b.resets_at == reset
    assert len(rig.client.calls) == 1
    st = await rig.service.status(world.user_b)
    assert st.state == "paused" and st.resets_at == reset
    # a paused row does not use up anyone's cap
    assert (await rig.service.status(world.user_a)).image.used == 0
    # once the reset time passes, generation works again
    pg.execute("update public.generation_state set paused_until = now() - interval '1 minute'")
    rig.client.quota_reset = None
    assert (await rig.service.run(world.user_b, "image", "after the pause")).status == "ready"


async def test_not_configured_refuses_everything_and_status_says_so(world: World, rig: Rig) -> None:
    from naadi_api.generation.service import GenerationService

    svc = GenerationService(
        db=rig.service._db,
        config=rig.service._config,
        client=None,
        store=rig.store,
        buckets=(PRIVATE, PUBLIC),
    )
    with pytest.raises(GenerationError) as e:
        await svc.prepare(world.user_a, "image", "heart")
    assert (e.value.status, e.value.code) == (503, "generation_not_configured")
    assert (await svc.status(world.user_a)).state == "not_configured"


async def test_flag_off_reads_disabled_and_zero_cap_disables_a_kind(rig: Rig, world: World) -> None:
    assert (await rig.service.status(world.user_a, enabled=False)).state == "disabled"
    rig.caps["video"] = 0  # type: ignore[attr-defined]
    err = await refused(rig, world.user_a, "video", "valve")
    assert err.code == "generation_kind_disabled"
    assert (await rig.service.status(world.user_a)).video_enabled is False


async def test_one_run_at_a_time_and_a_stale_run_stops_blocking(
    rig: Rig, world: World, pg: psycopg.Connection[Any]
) -> None:
    pg.execute(
        "insert into public.generated_media (user_id, kind, prompt, model, status)"
        " values (%s, 'image', 'x', 'm', 'generating')",
        (world.user_a,),
    )
    assert (await refused(rig, world.user_a)).code == "generation_busy"
    pg.execute(
        "update public.generated_media set created_at = now() - interval '30 minutes' where user_id = %s",
        (world.user_a,),
    )
    assert (await rig.service.run(world.user_a, "image", "after the stale run")).status == "ready"
    assert [r["status"] for r in rows(pg, world.user_a)] == ["failed", "ready"]


async def test_storage_failure_marks_the_row_failed(rig: Rig, world: World, pg: psycopg.Connection[Any]) -> None:
    rig.store.fail_put = True
    err = await refused(rig, world.user_a)
    assert err.code == "storage_write_failed"
    assert rows(pg, world.user_a)[0]["status"] == "failed"


async def test_client_errors_surface_only_their_stable_code(
    rig: Rig, world: World, pg: psycopg.Connection[Any]
) -> None:
    class Boom(FakeGenerationClient):
        async def generate(self, req: Any, progress: Any) -> Any:
            raise RuntimeError("Authorization: hf_SECRET_TOKEN")

    rig.service._client = Boom()
    err = await refused(rig, world.user_a)
    assert err.code == "generation_failed" and "SECRET" not in str(err)
    assert rows(pg, world.user_a)[0]["error"] == "generation_failed"


# ── row level security ──


async def test_user_b_cannot_see_or_touch_user_a_media_and_clients_cannot_write(
    rig: Rig, world: World, pg: psycopg.Connection[Any]
) -> None:
    item = await rig.service.run(world.user_a, "image", "private heart")
    with as_user(pg, world.user_b) as conn:
        assert conn.execute("select count(*) from public.generated_media where id = %s", (item.id,)).fetchone() == (0,)
        with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
            conn.execute("delete from public.generated_media where id = %s", (item.id,))
    with as_user(pg, world.user_a) as conn:
        assert conn.execute("select count(*) from public.generated_media where id = %s", (item.id,)).fetchone() == (1,)
        for stmt in (
            "update public.generated_media set approved_for_publish = true",
            "update public.generated_media set status = 'ready'",
            "update public.generated_media set title = 'x'",
            "delete from public.generated_media",  # the client cannot erase its own rows (they count toward the cap)
        ):
            with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
                conn.execute(stmt)
        with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
            conn.execute(
                "insert into public.generated_media (user_id, kind, prompt, model) values (%s, 'image', 'p', 'm')",
                (world.user_a,),
            )
    # the check constraints hold even for the API's own writes
    with pytest.raises(errors.CheckViolation):
        pg.execute("update public.generated_media set approved_for_publish = true where id = %s", (item.id,))
    with pytest.raises(errors.CheckViolation):
        pg.execute("update public.generated_media set ai_generated = false where id = %s", (item.id,))
    with pytest.raises(errors.CheckViolation):
        pg.execute("update public.generated_media set status = 'paused' where id = %s", (item.id,))


# ── HTTP ──


def bearer(key: SigningKey, uid: uuid.UUID, email: str | None = None) -> dict[str, str]:
    return signed_in(key, uid, email=email, header="Authorization")


async def test_list_get_delete_are_own_only_and_delete_removes_the_file(
    client: TestClient, key: SigningKey, rig: Rig, world: World, pg: psycopg.Connection[Any]
) -> None:
    a = await rig.service.run(world.user_a, "image", "a heart")
    b = await rig.service.run(world.user_b, "image", "b lung")
    ha, hb = bearer(key, world.user_a), bearer(key, world.user_b)
    got = client.get("/api/generated", headers=ha).json()
    assert [i["id"] for i in got["items"]] == [str(a.id)]
    item = got["items"][0]
    assert item["ai_generated"] is True and item["status"] == "ready" and item["url"] and item["published"] is False
    assert client.get("/api/generated?kind=video", headers=ha).json()["items"] == []
    assert client.get(f"/api/generated/{b.id}", headers=ha).status_code == 404
    assert client.get(f"/api/generated/{a.id}", headers=ha).json()["prompt"] == "a heart"
    assert client.delete(f"/api/generated/{b.id}", headers=ha).status_code == 404
    path = f"{world.user_b}/{b.id}.webp"
    assert (PRIVATE, path) in rig.store.objects  # A's attempt did not touch B's file
    assert client.delete(f"/api/generated/{b.id}", headers=hb).status_code == 204
    assert (PRIVATE, path) not in rig.store.objects
    # the item is hidden, not erased: the row stays (so it still counts toward the cap) without its file or prompt
    (hidden,) = pg.execute(
        "select hidden_at is not null, storage_path, prompt from public.generated_media where user_id = %s",
        (world.user_b,),
    ).fetchall()
    assert hidden == (True, None, "[removed]")
    assert client.get(f"/api/generated/{b.id}", headers=hb).status_code == 404
    assert client.get("/api/generated", headers=hb).json()["items"] == []


def test_routes_need_a_signed_in_user(client: TestClient) -> None:
    for method, path in (
        ("get", "/api/generated"),
        ("get", "/api/generation/status"),
        ("delete", f"/api/generated/{uuid.uuid4()}"),
    ):
        assert getattr(client, method)(path).status_code == 401, path


async def test_status_route_reports_usage_and_the_stub_route_is_absent_in_prod(
    client: TestClient, key: SigningKey, rig: Rig, world: World
) -> None:
    await rig.service.run(world.user_a, "image", "x")
    body = client.get("/api/generation/status", headers=bearer(key, world.user_a)).json()
    assert body["state"] == "ready" and body["image"] == {"used": 1, "cap": 30, "remaining": 29}
    assert body["max_video_seconds"] == 5 and body["video_enabled"] is True
    # the non-production stub is not mounted: the path only matches the item routes, which have no POST
    stub = client.post("/api/generated/stub", headers=bearer(key, world.user_a), json={"prompt": "x"})
    assert stub.status_code in (404, 405)


# ── admin approval lives in S19's queue ──


async def test_there_is_no_second_approval_path_and_the_queue_sees_only_publishable_items(
    client: TestClient, key: SigningKey, rig: Rig, world: World
) -> None:

    hd = bearer(key, world.admin, ADMIN_EMAIL)
    img = await rig.service.run(world.user_a, "image", "queue heart")
    hidden = await rig.service.run(world.user_a, "image", "queue lung")
    vid = await rig.service.run(world.user_a, "video", "queue valve")
    assert await rig.service.delete(world.user_a, hidden.id)
    # the 15b routes (and their bypass of S19's gates, schema check and audit) are gone
    for method, path in (
        ("get", "/api/admin/generated"),
        ("post", f"/api/admin/generated/{img.id}/approve"),
    ):
        assert getattr(client, method)(path, headers=hd).status_code in (404, 405), path
    queue = client.get("/api/admin/queues/generated_media", headers=hd)
    assert queue.status_code == 200, queue.text
    ids = {i["id"] for i in queue.json()["items"]}
    assert str(img.id) in ids  # a ready image waits for S19's decision
    assert str(hidden.id) not in ids  # a hidden (student-deleted) item has no file and never waits
    assert str(vid.id) not in ids  # a clip is never publishable
    mine = next(i for i in queue.json()["items"] if i["id"] == str(img.id))
    assert mine["detail"]["ai_generated"] is True


def test_the_local_stub_generates_serves_and_labels_through_the_real_routes(
    url: str, key: SigningKey, static_dir: Any, world: World
) -> None:
    from .conftest import build_client

    with build_client(url, key, static_dir, None, app_env="local", generation_fake=True) as c:
        h = bearer(key, world.user_a)
        r = c.post("/api/generated/stub", headers=h, json={"prompt": "Nephron cross-section"})
        assert r.status_code == 200, r.text
        item = r.json()
        assert item["status"] == "ready" and item["ai_generated"] is True
        assert item["url"].startswith("/api/generated/stub-file/generated-media/")
        file = c.get(item["url"])
        assert file.status_code == 200 and file.headers["content-type"] == "image/webp" and file.content[:4] == b"RIFF"
        assert c.get("/api/generation/status", headers=h).json()["state"] == "ready"
        failed = c.post("/api/generated/stub", headers=h, json={"prompt": "FAILME heart"})
        assert (failed.status_code, failed.json()["error"]["code"]) == (502, "generation_failed")
        listing = c.get("/api/generated", headers=h).json()["items"]
        assert {i["status"] for i in listing} == {"ready", "failed"}
        assert c.delete(f"/api/generated/{item['id']}", headers=h).status_code == 204
        assert c.get(item["url"]).status_code == 404  # the file went with the row


async def test_tool_core_returns_blocks_canvas_and_code_only_results(rig: Rig, world: World) -> None:
    from naadi_api.generation.tools import prepare_preview, run_tool

    prev = await prepare_preview(rig.service, world.user_a, "image", "A heart")
    assert prev.remaining_today == 30
    events: list[dict[str, Any]] = []

    async def on_event(e: dict[str, Any]) -> None:
        events.append(e)

    out = await run_tool(rig.service, world.user_a, "image", "A heart", on_event=on_event)
    assert out.status == "ok" and out.blocks[0]["type"] == "image"
    mid = out.blocks[0]["data"]["media_id"]
    assert out.canvas[0]["kind"] == "generated_media" and out.canvas[0]["ref"] == {"type": "generated_media", "id": mid}
    assert [e["stage"] for e in events][-2:] == ["generating", "saving"]
    refused_out = await run_tool(rig.service, world.user_a, "image", "Portrait of Albert Einstein")
    assert (refused_out.status, refused_out.content) == ("refused", "prompt_real_person") and not refused_out.blocks
    rig.client.quota_reset = datetime.now(UTC) + timedelta(minutes=5)
    paused = await run_tool(rig.service, world.user_a, "image", "another heart")
    assert paused.status == "error" and paused.content.startswith("generation_paused until=")


async def test_deleting_items_never_frees_the_daily_cap(rig: Rig, world: World) -> None:
    rig.caps["image"] = 2  # type: ignore[attr-defined]
    a = await rig.service.run(world.user_a, "image", "one")
    b = await rig.service.run(world.user_a, "image", "two")
    assert await rig.service.delete(world.user_a, a.id) and await rig.service.delete(world.user_a, b.id)
    assert (await rig.service.list_items(world.user_a, kind=None, before=None, limit=10))[0] == []
    err = await refused(rig, world.user_a, prompt="three")
    assert (err.status, err.code) == (429, "generation_cap_reached")
    st = await rig.service.status(world.user_a)
    assert (st.image.used, st.image.remaining) == (2, 0)


async def test_failed_runs_that_reached_the_space_count_but_unreachable_ones_do_not(
    rig: Rig, world: World, pg: psycopg.Connection[Any]
) -> None:
    rig.caps["image"] = 2  # type: ignore[attr-defined]
    rig.client.fail_with = "generation_timeout"  # it ran on the Space and spent GPU
    assert (await refused(rig, world.user_a)).code == "generation_timeout"
    assert (await rig.service.status(world.user_a)).image.used == 1
    rig.client.fail_with = "generation_unavailable"  # the Space never took the job
    assert (await refused(rig, world.user_a)).code == "generation_unavailable"
    assert (await rig.service.status(world.user_a)).image.used == 1
    assert [r["status"] for r in rows(pg, world.user_a)] == ["failed", "failed"]


async def test_the_pause_survives_the_owner_deleting_everything(
    rig: Rig, world: World, pg: psycopg.Connection[Any]
) -> None:
    rig.client.quota_reset = datetime.now(UTC) + timedelta(hours=5)
    await refused(rig, world.user_a)
    rig.client.quota_reset = None
    with as_user(pg, world.user_a) as conn:  # what supabase-js .delete() sends, in one transaction
        with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
            conn.execute("delete from public.generated_media")
        with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
            conn.execute("select * from public.generation_state")
        with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
            conn.execute("update public.generation_state set paused_until = null")
    pg.execute("delete from public.generated_media where user_id = %s", (world.user_a,))  # even an erased row
    err = await refused(rig, world.user_b)
    assert err.code == "generation_paused"
    assert (await rig.service.status(world.user_b)).state == "paused"
