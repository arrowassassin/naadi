"""Integration (real Postgres, migrated schema, full HTTP path): the pathway, the student's progress, costs and the
admin actions. Re-verify day 90 vs 91, last-writer-wins, totals per stage and currency, isolation between students."""

from __future__ import annotations

import datetime as dt
import time
import uuid
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from .conftest import World

pytestmark = [pytest.mark.integration, pytest.mark.db]


def stage_of(body: dict[str, Any], key: str) -> dict[str, Any]:
    return next(s for s in body["stages"] if s["key"] == key)


def task_of(body: dict[str, Any], stage: str, task: str) -> dict[str, Any]:
    return next(t for t in stage_of(body, stage)["tasks"] if t["id"] == task)


def now_ms() -> int:
    return int(time.time() * 1000)


# ── reading the pathway ──


def test_pathway_lists_stages_with_reverify_flags(client: TestClient, world: World) -> None:
    r = client.get("/api/pathway", headers=world.h(world.a))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reverify_days"] == 90
    s = stage_of(body, world.stage)
    assert [t["id"] for t in s["tasks"]] == [world.t_fresh, world.t_day90, world.t_day91, world.t_flagged, world.t_paid]
    flags = {t["id"]: t["reverify"] for t in s["tasks"]}
    assert flags == {
        world.t_fresh: None,
        world.t_day90: None,  # day 90 is still fine
        world.t_day91: "age",  # day 91 is stale
        world.t_flagged: "flagged",
        world.t_paid: None,
    }
    assert task_of(body, world.stage, world.t_day91)["days_since_verified"] == 91
    assert s["reverify_count"] == 2
    assert s["reverify"] == "flagged"
    assert s["spend_planned_usd"] == 100.0
    fresh = task_of(body, world.stage, world.t_fresh)
    assert fresh["due_rule"] == {
        "kind": "anchor",
        "anchor": "start",
        "days": 7,
        "date": None,
        "month": None,
        "day": None,
    }
    assert fresh["status"] == "todo" and fresh["official"] is True
    assert task_of(body, world.stage, world.t_day91)["official"] is False  # example.com is not an official domain
    assert body["today"] == dt.datetime.now(dt.UTC).date().isoformat() or body["today"]


def test_pathway_needs_a_session(client: TestClient) -> None:
    assert client.get("/api/pathway").status_code == 401
    assert client.patch("/api/pathway/tasks/x", json={"status": "done", "client_updated_at_ms": 1}).status_code == 401


# ── the student's progress ──


def patch(client: TestClient, world: World, task: str, who: uuid.UUID | None = None, **body: Any) -> Any:
    return client.patch(f"/api/pathway/tasks/{task}", json=body, headers=world.h(who or world.a))


def test_task_status_persists_and_is_last_writer_wins(client: TestClient, world: World) -> None:
    t = now_ms()
    r = patch(client, world, world.t_fresh, status="done", client_updated_at_ms=t)
    assert r.status_code == 200, r.text
    assert r.json()["applied"] is True and r.json()["task"]["status"] == "done"
    body = client.get("/api/pathway", headers=world.h(world.a)).json()
    assert task_of(body, world.stage, world.t_fresh)["status"] == "done"
    assert stage_of(body, world.stage)["done"] == 1
    # an older edit from a device that was offline loses and gets the stored row back
    r = patch(client, world, world.t_fresh, status="todo", client_updated_at_ms=t - 5000)
    assert r.json()["applied"] is False and r.json()["task"]["status"] == "done"
    # a tie goes to the server
    r = patch(client, world, world.t_fresh, status="doing", client_updated_at_ms=t)
    assert r.json()["applied"] is False
    # a newer one wins; fields not sent keep their value
    r = patch(client, world, world.t_fresh, notes="  booked  ", client_updated_at_ms=t + 10)
    assert r.json()["applied"] is True
    assert r.json()["task"]["status"] == "done" and r.json()["task"]["notes"] == "booked"
    r = patch(client, world, world.t_fresh, due="2027-01-31", client_updated_at_ms=t + 20)
    assert r.json()["task"]["due"] == "2027-01-31"


def test_a_clock_far_in_the_future_cannot_freeze_a_row(
    client: TestClient, world: World, pg: psycopg.Connection[Any]
) -> None:
    far = now_ms() + 10 * 24 * 3600 * 1000
    r = patch(client, world, world.t_fresh, status="done", client_updated_at_ms=far)
    assert r.json()["applied"] is True
    stored = r.json()["task"]["client_updated_at_ms"]
    assert stored <= now_ms() + 5 * 60 * 1000 + 1000  # clamped to now + 5 minutes
    # a device with a right clock, a few minutes later, can still change it
    r = patch(client, world, world.t_fresh, status="todo", client_updated_at_ms=now_ms() + 6 * 60 * 1000)
    assert r.json()["applied"] is True


def test_task_patch_validation(client: TestClient, world: World) -> None:
    assert patch(client, world, "nope-123", status="done", client_updated_at_ms=1).status_code == 404
    assert patch(client, world, world.t_fresh, status="finished", client_updated_at_ms=1).status_code == 422
    assert patch(client, world, world.t_fresh, client_updated_at_ms=1).status_code == 422  # nothing to change
    assert patch(client, world, world.t_fresh, notes="x" * 4001, client_updated_at_ms=1).status_code == 422
    assert patch(client, world, world.t_fresh, status="done").status_code == 422  # a stamp is required


def test_one_student_never_sees_or_changes_anothers_progress(client: TestClient, world: World) -> None:
    patch(client, world, world.t_fresh, status="done", notes="private note", client_updated_at_ms=now_ms())
    body = client.get("/api/pathway", headers=world.h(world.b)).json()
    t = task_of(body, world.stage, world.t_fresh)
    assert t["status"] == "todo" and t["notes"] is None
    r = patch(client, world, world.t_fresh, who=world.b, status="doing", client_updated_at_ms=now_ms())
    assert r.json()["task"]["status"] == "doing"
    a = task_of(client.get("/api/pathway", headers=world.h(world.a)).json(), world.stage, world.t_fresh)
    assert a["status"] == "done" and a["notes"] == "private note"


def test_current_stage_is_chosen_or_derived(client: TestClient, world: World) -> None:
    h = world.h(world.a)
    r = client.put("/api/me/pathway-stage", json={"stage_key": world.stage2}, headers=h)
    assert r.status_code == 200 and r.json() == {"stage_key": world.stage2}
    body = client.get("/api/pathway", headers=h).json()
    assert body["current_stage"] == world.stage2 and body["current_stage_source"] == "chosen"
    assert client.put("/api/me/pathway-stage", json={"stage_key": "nope"}, headers=h).status_code == 404
    assert client.put("/api/me/pathway-stage", json={"stage_key": "Bad Key"}, headers=h).status_code == 422
    client.put("/api/me/pathway-stage", json={"stage_key": None}, headers=h)
    assert client.get("/api/pathway", headers=h).json()["current_stage_source"] in ("derived", None)


# ── costs ──


def add_cost(client: TestClient, world: World, who: uuid.UUID | None = None, **over: Any) -> Any:
    body = {"label": "Exam fee", "amount": 100, "currency": "USD", "client_updated_at_ms": now_ms(), **over}
    return client.post("/api/costs", json=body, headers=world.h(who or world.a))


def test_costs_total_by_stage_and_currency_without_conversion(client: TestClient, world: World) -> None:
    add_cost(client, world, task_id=world.t_paid, amount=110.5, paid_on="2026-11-01")
    add_cost(client, world, task_id=world.t_paid, amount=20, paid_on="2026-11-02", label="Courier")
    add_cost(
        client, world, stage_key=world.stage, amount=8000, currency="INR", paid_on="2026-11-03", label="Bank draft"
    )
    add_cost(client, world, task_id=world.t_other, amount=50, label="Seat deposit")  # no paid date: upcoming
    add_cost(client, world, amount=5, label="Misc")  # no stage
    s = client.get("/api/costs/summary", headers=world.h(world.a)).json()
    assert s["currencies"] == ["USD", "INR"]
    by = {x["stage_key"]: x for x in s["by_stage"]}
    one = by[world.stage]
    assert one["paid"] == [{"currency": "INR", "amount": "8000.00"}, {"currency": "USD", "amount": "130.50"}]
    assert one["planned_usd"] == "100.00" and one["entries"] == 3
    two = by[world.stage2]
    assert two["paid"] == [] and two["upcoming"] == [{"currency": "USD", "amount": "50.00"}]
    assert two["planned_usd"] == "50.00"
    assert by[None]["upcoming"] == [{"currency": "USD", "amount": "5.00"}]
    totals = {m["currency"]: m["amount"] for m in s["paid"]}
    assert totals == {"INR": "8000.00", "USD": "130.50"}  # 8000 INR is never added to dollars
    assert s["next_fee"]["label"] in {"Seat deposit", "Misc"}


def test_planned_leaves_out_tasks_marked_not_needed(client: TestClient, world: World) -> None:
    before = client.get("/api/costs/summary", headers=world.h(world.a)).json()
    patch(client, world, world.t_paid, status="na", client_updated_at_ms=now_ms())
    after = client.get("/api/costs/summary", headers=world.h(world.a)).json()
    by = {x["stage_key"]: x for x in after["by_stage"]}
    assert by[world.stage]["planned_usd"] == "0.00"
    assert float(before["planned_usd"]) - float(after["planned_usd"]) == pytest.approx(100.0)


def test_cost_create_is_idempotent_and_last_writer_wins(client: TestClient, world: World) -> None:
    cid, t = str(uuid.uuid4()), now_ms()
    r = add_cost(client, world, id=cid, amount=40, client_updated_at_ms=t)
    assert r.status_code == 201 and r.json()["applied"] is True
    assert r.json()["cost"]["amount"] == "40.00" and r.json()["cost"]["stage_key"] is None
    # the same request again (a replay) is a 200 and changes nothing
    r = add_cost(client, world, id=cid, amount=40, client_updated_at_ms=t)
    assert r.status_code == 200 and r.json()["applied"] is False
    # an older edit loses, a newer one wins
    r = client.patch(f"/api/costs/{cid}", json={"amount": 1, "client_updated_at_ms": t - 1}, headers=world.h(world.a))
    assert r.json()["applied"] is False and r.json()["cost"]["amount"] == "40.00"
    r = client.patch(
        f"/api/costs/{cid}",
        json={"amount": 45.25, "paid_on": "2026-11-10", "task_id": world.t_other, "client_updated_at_ms": t + 5},
        headers=world.h(world.a),
    )
    c = r.json()["cost"]
    assert r.json()["applied"] is True and c["amount"] == "45.25" and c["stage_key"] == world.stage2
    assert c["task_id"] == world.t_other and c["paid_on"] == "2026-11-10"


def test_cost_delete_is_idempotent_and_loses_to_a_newer_edit(client: TestClient, world: World) -> None:
    cid, t = str(uuid.uuid4()), now_ms()
    add_cost(client, world, id=cid, client_updated_at_ms=t)
    h = world.h(world.a)
    kept = client.delete(f"/api/costs/{cid}?client_updated_at_ms={t - 100}", headers=h)
    assert kept.status_code == 200 and kept.json()["applied"] is False  # stored edit is newer
    assert client.delete(f"/api/costs/{cid}?client_updated_at_ms={t + 100}", headers=h).status_code == 204
    assert client.delete(f"/api/costs/{cid}", headers=h).status_code == 204  # already gone
    assert [c for c in client.get("/api/costs", headers=h).json()["items"] if c["id"] == cid] == []


def test_cost_validation_and_isolation(client: TestClient, world: World) -> None:
    assert add_cost(client, world, currency="EUR").status_code == 422  # not an offered currency
    assert add_cost(client, world, currency="usd").status_code == 422
    assert add_cost(client, world, amount=-1).status_code == 422
    assert add_cost(client, world, label="  ").status_code == 422
    assert add_cost(client, world, task_id="nope-123").status_code == 404
    assert add_cost(client, world, stage_key="nope").status_code == 404
    cid = str(uuid.uuid4())
    assert add_cost(client, world, id=cid).status_code == 201
    # B can neither read, change, delete nor claim A's id
    assert cid not in [c["id"] for c in client.get("/api/costs", headers=world.h(world.b)).json()["items"]]
    r = client.patch(
        f"/api/costs/{cid}", json={"amount": 1, "client_updated_at_ms": now_ms()}, headers=world.h(world.b)
    )
    assert r.status_code == 404
    assert client.delete(f"/api/costs/{cid}", headers=world.h(world.b)).status_code == 204
    assert add_cost(client, world, who=world.b, id=cid).status_code == 409
    mine = [c for c in client.get("/api/costs", headers=world.h(world.a)).json()["items"] if c["id"] == cid]
    assert mine and mine[0]["amount"] == "100.00"


def test_cost_limit(client: TestClient, world: World, pg: psycopg.Connection[Any]) -> None:
    import json

    from tests.db.rls import database_url  # noqa: F401

    base = client.app.state.app_config.file_value("pathway")  # type: ignore[attr-defined]
    pg.execute("delete from public.app_config where key = 'pathway'")
    pg.execute(
        "insert into public.app_config (key, value) values ('pathway', %s::jsonb)",
        (json.dumps({**base, "max_costs_per_user": 1}),),
    )
    try:
        client.app.state.app_config.invalidate("pathway")  # type: ignore[attr-defined]
        assert add_cost(client, world).status_code == 201
        assert add_cost(client, world).status_code == 409
        assert add_cost(client, world).json()["error"]["code"] == "cost_limit"
    finally:
        pg.execute("delete from public.app_config where key = 'pathway'")
        client.app.state.app_config.invalidate("pathway")  # type: ignore[attr-defined]


# ── admin ──


def test_admin_routes_are_admin_only(client: TestClient, world: World) -> None:
    h = world.h(world.a)
    assert client.get("/api/admin/pathway/checks", headers=h).status_code == 403
    assert client.post(f"/api/admin/pathway/tasks/{world.t_day91}/verify", headers=h).status_code == 403
    assert client.patch(f"/api/admin/pathway/tasks/{world.t_day91}", json={"cost_usd": 1}, headers=h).status_code == 403
    assert client.get("/api/admin/pathway/checks").status_code == 401


def add_check(
    pg: psycopg.Connection[Any], task: str, status: str, *, changed: bool = True, content_hash: str | None = None
) -> int:
    import json

    row = pg.execute(
        "insert into public.pathway_task_checks (task_id, url, http_status, content_hash, changed, finding)"
        " values (%s, 'https://www.usmle.org/d', 200, %s, %s, %s::jsonb) returning id",
        (
            task,
            content_hash,
            changed,
            json.dumps(
                {
                    "status": status,
                    "summary_key": "page_changed_differences",
                    "differences": ["The fee changed."],
                    "quotes": [{"text": "The fee is $950.", "url": "https://www.usmle.org/d"}],
                }
            ),
        ),
    ).fetchone()
    assert row is not None
    return int(row[0])


def test_admin_sees_open_checks_and_verify_resolves_them(
    client: TestClient, world: World, pg: psycopg.Connection[Any]
) -> None:
    h = world.h(world.admin)
    new_hash = "a" * 64
    cid = add_check(pg, world.t_flagged, "changed", content_hash=new_hash)
    add_check(pg, world.t_fresh, "unchanged", changed=False)
    r = client.get("/api/admin/pathway/checks?open=1", headers=h)
    assert r.status_code == 200, r.text
    items = [i for i in r.json()["items"] if i["task_id"] in {world.t_flagged, world.t_fresh}]
    assert [i["id"] for i in items] == [cid]
    assert items[0]["finding"]["quotes"][0]["url"] == "https://www.usmle.org/d"
    assert items[0]["needs_review"] is True and r.json()["open_count"] >= 1
    # only an admin's confirmation moves last_verified
    before = pg.execute("select last_verified from public.pathway_tasks where id = %s", (world.t_flagged,)).fetchone()
    v = client.post(f"/api/admin/pathway/tasks/{world.t_flagged}/verify", headers=h)
    assert v.status_code == 200, v.text
    today = dt.datetime.now(dt.UTC).date().isoformat()
    assert v.json()["last_verified"] == today and v.json()["needs_review"] is False
    assert before is not None
    row = pg.execute(
        "select needs_review, content_hash from public.pathway_tasks where id = %s", (world.t_flagged,)
    ).fetchone()
    assert row == (False, new_hash)  # the new page is the baseline now
    resolved = pg.execute(
        "select resolved_at is not null, resolved_by = %s from public.pathway_task_checks where id = %s",
        (world.admin, cid),
    ).fetchone()
    assert resolved == (True, True)
    assert client.post("/api/admin/pathway/tasks/nope-1/verify", headers=h).status_code == 404
    body = client.get("/api/pathway", headers=world.h(world.a)).json()
    assert task_of(body, world.stage, world.t_flagged)["reverify"] is None


def test_verifying_the_last_stale_task_refreshes_the_stage(
    client: TestClient, world: World, pg: psycopg.Connection[Any]
) -> None:
    h = world.h(world.admin)
    pg.execute(
        "update public.pathway_stages set last_verified = %s where key = %s", (dt.date(2020, 1, 1), world.stage2)
    )
    client.post(f"/api/admin/pathway/tasks/{world.t_other}/verify", headers=h)
    got = pg.execute("select last_verified from public.pathway_stages where key = %s", (world.stage2,)).fetchone()
    assert got == (dt.datetime.now(dt.UTC).date(),)


def test_admin_edit_does_not_change_last_verified_and_keeps_links_official(
    client: TestClient, world: World, pg: psycopg.Connection[Any]
) -> None:
    h = world.h(world.admin)
    before = pg.execute("select last_verified from public.pathway_tasks where id = %s", (world.t_day91,)).fetchone()
    r = client.patch(
        f"/api/admin/pathway/tasks/{world.t_day91}",
        json={"cost_usd": 12.5, "official_url": "https://www.ecfmg.org/new", "detail_md": "New detail"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["cost_usd"] == 12.5 and r.json()["official_url"] == "https://www.ecfmg.org/new"
    assert (
        pg.execute("select last_verified from public.pathway_tasks where id = %s", (world.t_day91,)).fetchone()
        == before
    )
    bad = client.patch(
        f"/api/admin/pathway/tasks/{world.t_day91}", json={"official_url": "https://blog.example.com/x"}, headers=h
    )
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "pathway_url_not_official"
    assert (
        client.patch(
            f"/api/admin/pathway/tasks/{world.t_day91}", json={"official_url": "http://www.ecfmg.org"}, headers=h
        ).status_code
        == 422
    )
    assert client.patch(f"/api/admin/pathway/tasks/{world.t_day91}", json={}, headers=h).status_code == 422


# ── privacy in logs ──


def test_notes_labels_and_amounts_are_never_logged(
    client: TestClient, world: World, capfd: pytest.CaptureFixture[str]
) -> None:
    secret_note, secret_label = "zz-private-note-zz", "zz-private-label-zz"
    patch(client, world, world.t_fresh, notes=secret_note, client_updated_at_ms=now_ms())
    add_cost(client, world, label=secret_label, amount=123456.78)
    client.get("/api/pathway", headers=world.h(world.a))
    out = capfd.readouterr()
    assert secret_note not in out.out + out.err and secret_label not in out.out + out.err
    assert "123456.78" not in out.out + out.err
