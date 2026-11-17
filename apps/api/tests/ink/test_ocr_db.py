"""Integration (real Postgres, full HTTP save path, mocked vision gateway): handwriting OCR.

Queueing on save (content hash, min items, idle time, surfaces), the sweep and the nightly pass, `done` / `deferred`
/ `failed` / rate limited / superseded outcomes, the per-student daily cap, the opt-out (cleared at once, nothing
queued, nightly safety net), stale text cleared after erasing, OCR columns closed to clients, PII never in the
prompt, and `POST /api/ink/convert`. No live model is called.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, BaseMessage
from PIL import Image
from pydantic import SecretStr

from naadi_api.config_api.schemas import InkOcrRules
from naadi_api.db import Database
from naadi_api.ink.ocr.run import OcrPass, PassStats
from naadi_api.llm import CallContext, LLMUnavailable, set_gateway
from naadi_api.llm_budget import BudgetExhausted
from tests.auth.jwt_helpers import SigningKey

from .conftest import Ink
from .test_ink_db import post, stroke, uid7

pytestmark = [pytest.mark.integration, pytest.mark.db]

RULES = InkOcrRules(idle_seconds=10, min_items=3, max_tiles=4, per_user_daily=40, max_layers_per_run=200)
EMAIL = "student.private@example.com"


@dataclass
class FakeGateway:
    reply: Any = '{"text": "Loop of Henle", "lines": ["Loop of Henle"]}'
    calls: list[tuple[str, list[BaseMessage], CallContext]] = field(default_factory=list)
    during: Callable[[], None] | None = None

    async def ainvoke(self, role: str, messages: list[BaseMessage], ctx: CallContext, **kw: Any) -> AIMessage:
        self.calls.append((role, messages, ctx))
        if self.during is not None:
            await asyncio.to_thread(self.during)
        if isinstance(self.reply, BaseException):
            raise self.reply
        return AIMessage(content=self.reply)


def text_box(text: str, y: float = 0.5) -> dict[str, Any]:
    now = int(time.time() * 1000)
    return {
        "id": uid7(),
        "tool": "text",
        "color": "ink",
        "size": 0.02,
        "x": 0.1,
        "y": y,
        "w": 0.4,
        "text": text,
        "createdAt": now,
        "updatedAt": now,
    }


def strokes(n: int, y: float = 0.1) -> list[dict[str, Any]]:
    return [stroke(points=[[0.1, y + i * 0.05, 0.5, 0], [0.4, y + i * 0.05, 0.5, 9]]) for i in range(n)]


def layer(pg: psycopg.Connection[Any], ink: Ink, surface: str = "section", sid: str | None = None) -> dict[str, Any]:
    row = pg.execute(
        "select id::text, ocr_status, ocr_hash, ocr_at, recognized_text, content_hash, item_count"
        " from public.ink_layers where user_id = %s and surface_type = %s and surface_id = %s",
        (ink.owner, surface, sid or ink.section),
    ).fetchone()
    assert row is not None
    keys = ("id", "ocr_status", "ocr_hash", "ocr_at", "recognized_text", "content_hash", "item_count")
    return dict(zip(keys, row, strict=True))


def age(pg: psycopg.Connection[Any], uid: uuid.UUID) -> None:
    """Make the student's layers idle (the updated_at trigger is skipped for this one statement)."""
    pg.execute("set session_replication_role = replica")
    try:
        pg.execute("update public.ink_layers set updated_at = now() - interval '1 hour' where user_id = %s", (uid,))
    finally:
        pg.execute("set session_replication_role = origin")


def run_pass(url: str, gw: FakeGateway, mode: str = "sweep", rules: InkOcrRules = RULES) -> tuple[PassStats, list[Any]]:
    indexed: list[Any] = []

    async def indexer(db: Database, owner: uuid.UUID, ref: str) -> None:
        indexed.append((owner, ref))

    async def go() -> PassStats:
        db = Database(SecretStr(url))
        await db.open()
        try:
            return await OcrPass(db, gw, rules, indexer=indexer).run(mode)  # type: ignore[arg-type]
        finally:
            await db.close()

    return asyncio.run(go()), indexed


@pytest.fixture(autouse=True)
def _only_mine(pg: psycopg.Connection[Any]) -> None:
    """Layers left pending by other modules must not be read by these passes."""
    pg.execute("update public.ink_layers set ocr_status = null where ocr_status in ('pending', 'deferred')")


def save(
    client: TestClient, key: SigningKey, ink: Ink, items: list[Any], surface: str = "section", sid: str | None = None
) -> None:
    r = post(client, ink.h(key), surface, sid or ink.section, items)
    assert r.status_code == 200, r.text


# ── queueing and the sweep ──


def test_a_save_queues_and_the_idle_sweep_recognises(
    client: TestClient, key: SigningKey, ink: Ink, pg: psycopg.Connection[Any], url: str
) -> None:
    save(client, key, ink, [*strokes(3), text_box(f"call {EMAIL}")])
    lay = layer(pg, ink)
    assert lay["ocr_status"] == "pending" and lay["ocr_hash"] is None
    gw = FakeGateway()
    stats, _ = run_pass(url, gw)
    assert stats.claimed == 0 and gw.calls == []  # not idle yet
    age(pg, ink.owner)
    stats, indexed = run_pass(url, gw)
    assert stats.done == 1 and stats.vision_calls == 1
    lay = layer(pg, ink)
    assert lay["ocr_status"] == "done" and lay["ocr_hash"] == lay["content_hash"] and lay["ocr_at"] is not None
    assert lay["recognized_text"] == f"Loop of Henle\ncall {EMAIL}"  # typed text joins the recognised text
    assert indexed == [(ink.owner, f"section:{ink.section}")]
    role, messages, ctx = gw.calls[0]
    assert role == "vision" and ctx.purpose == "job" and ctx.user_id == ink.owner
    assert ctx.run_id == f"ink_ocr:{lay['id']}"
    # Only the fixed instruction and the image reach the model: never the typed text box (or anything else).
    dumped = json.dumps([m.content for m in messages])
    assert EMAIL not in dumped and "call " not in dumped
    image = messages[1].content[1]["image_url"]["url"]  # type: ignore[index]
    png = Image.open(io.BytesIO(base64.b64decode(image.split(",", 1)[1])))
    assert png.size[0] == 1600 and png.size[1] <= 2048 and png.mode == "L"
    # A new stroke queues it again; the same content read twice is not.
    stats, _ = run_pass(url, gw)
    assert stats.claimed == 0
    save(client, key, ink, strokes(1, y=0.6))
    assert layer(pg, ink)["ocr_status"] == "pending"


def test_too_little_ink_exam_scratch_and_unchanged_content_are_not_queued(
    client: TestClient, key: SigningKey, ink: Ink, pg: psycopg.Connection[Any]
) -> None:
    save(client, key, ink, strokes(2))
    assert layer(pg, ink)["ocr_status"] is None
    scratch = f"{ink.block}:1"
    save(client, key, ink, strokes(5), surface="exam_scratch", sid=scratch)
    assert layer(pg, ink, "exam_scratch", scratch)["ocr_status"] is None
    # resending what is stored applies nothing, so nothing is queued
    lay = layer(pg, ink)
    pg.execute("update public.ink_layers set ocr_hash = content_hash, ocr_status = 'done' where id = %s", (lay["id"],))
    first = strokes(1)
    save(client, key, ink, first)
    pg.execute("update public.ink_layers set ocr_hash = content_hash, ocr_status = 'done' where id = %s", (lay["id"],))
    save(client, key, ink, first)
    assert layer(pg, ink)["ocr_status"] == "done"


def test_budget_and_daily_cap_defer_and_the_nightly_pass_retries(
    client: TestClient, key: SigningKey, ink: Ink, pg: psycopg.Connection[Any], url: str
) -> None:
    save(client, key, ink, strokes(4))
    age(pg, ink.owner)
    reset = datetime.now(UTC) + timedelta(hours=3)
    stats, _ = run_pass(url, FakeGateway(reply=BudgetExhausted("daily_cap", reset, purpose="job")))
    assert stats.deferred == 1 and stats.stopped == "daily_cap"
    assert layer(pg, ink)["ocr_status"] == "deferred" and layer(pg, ink)["recognized_text"] is None
    gw = FakeGateway()
    stats, _ = run_pass(url, gw)  # the sweep leaves deferred layers to the nightly pass
    assert stats.claimed == 0 and gw.calls == []
    stats, _ = run_pass(url, gw, "nightly")
    assert stats.done == 1 and layer(pg, ink)["ocr_status"] == "done"


def test_the_students_daily_cap_defers_without_a_model_call(
    client: TestClient, key: SigningKey, ink: Ink, pg: psycopg.Connection[Any], url: str
) -> None:
    rules = RULES.model_copy(update={"per_user_daily": 2})
    pg.execute(
        "insert into public.llm_usage (user_id, provider, purpose, role, model, run_id)"
        " select %s, 'nvidia', 'job', 'vision', 'm', 'ink_ocr:' || g from generate_series(1, 2) g",
        (ink.owner,),
    )
    try:
        save(client, key, ink, strokes(3))
        age(pg, ink.owner)
        gw = FakeGateway()
        stats, _ = run_pass(url, gw, rules=rules)
        assert stats.deferred == 1 and gw.calls == []
        assert layer(pg, ink)["ocr_status"] == "deferred"
    finally:
        pg.execute("delete from public.llm_usage where user_id = %s", (ink.owner,))


@pytest.mark.parametrize(
    ("reply", "status", "outcome"),
    [
        ("I am unable to read handwriting.", "failed", "failed"),
        (LLMUnavailable("vision", []), "deferred", "deferred"),
        (BudgetExhausted("rate_limited", datetime.now(UTC)), "pending", "rate_limited"),
    ],
)
def test_unreadable_replies_fail_and_outages_wait(
    client: TestClient,
    key: SigningKey,
    ink: Ink,
    pg: psycopg.Connection[Any],
    url: str,
    reply: Any,
    status: str,
    outcome: str,
) -> None:
    save(client, key, ink, strokes(3))
    age(pg, ink.owner)
    stats, _ = run_pass(url, FakeGateway(reply=reply))
    assert getattr(stats, outcome) == 1
    lay = layer(pg, ink)
    assert lay["ocr_status"] == status and lay["recognized_text"] is None
    if status == "failed":
        assert lay["ocr_hash"] == lay["content_hash"]  # not retried until the ink changes
        save(client, key, ink, strokes(1, y=0.8))
        assert layer(pg, ink)["ocr_status"] == "pending"


def test_a_save_during_the_call_keeps_the_layer_pending(
    client: TestClient, key: SigningKey, ink: Ink, pg: psycopg.Connection[Any], url: str
) -> None:
    save(client, key, ink, strokes(3))
    age(pg, ink.owner)
    gw = FakeGateway(during=lambda: save(client, key, ink, strokes(1, y=0.9)))
    stats, _ = run_pass(url, gw)
    assert stats.superseded == 1
    lay = layer(pg, ink)
    assert lay["recognized_text"] == "Loop of Henle"  # stored, but for older content
    assert lay["ocr_status"] == "pending" and lay["ocr_hash"] != lay["content_hash"]


def test_erasing_below_the_threshold_clears_stale_text(
    client: TestClient, key: SigningKey, ink: Ink, pg: psycopg.Connection[Any], url: str
) -> None:
    items = strokes(3)
    save(client, key, ink, items)
    age(pg, ink.owner)
    run_pass(url, FakeGateway())
    assert layer(pg, ink)["recognized_text"] == "Loop of Henle"
    pg.execute(
        "insert into public.embeddings (owner_user_id, ref_type, ref_id, chunk_index, content)"
        " values (%s, 'ink', %s, 0, 'Loop of Henle')",
        (ink.owner, f"section:{ink.section}"),
    )
    later = int(time.time() * 1000) + 10
    save(client, key, ink, [{**i, "deleted": True, "updatedAt": later} for i in items[:2]])
    assert layer(pg, ink)["ocr_status"] == "pending"  # 1 item left, but stale text to clear
    age(pg, ink.owner)
    gw = FakeGateway()
    stats, _ = run_pass(url, gw)
    assert stats.cleared == 1 and gw.calls == []
    assert layer(pg, ink)["recognized_text"] is None
    assert pg.execute(
        "select count(*) from public.embeddings where owner_user_id = %s and ref_type = 'ink'", (ink.owner,)
    ).fetchone() == (0,)


# ── opt-out ──


def test_turning_handwriting_search_off_clears_now_and_stops_runs(
    client: TestClient, key: SigningKey, ink: Ink, pg: psycopg.Connection[Any], url: str
) -> None:
    h = ink.h(key)
    save(client, key, ink, strokes(3))
    age(pg, ink.owner)
    run_pass(url, FakeGateway())
    pg.execute(
        "insert into public.embeddings (owner_user_id, ref_type, ref_id, chunk_index, content)"
        " values (%s, 'ink', %s, 0, 'Loop of Henle')",
        (ink.owner, f"section:{ink.section}"),
    )
    assert client.patch("/api/me", json={"settings": {"handwriting_search": "no"}}, headers=h).status_code == 422
    r = client.patch("/api/me", json={"settings": {"handwriting_search": False}}, headers=h)
    assert r.status_code == 200 and r.json()["profile"]["settings"]["handwriting_search"] is False
    lay = layer(pg, ink)
    assert (lay["recognized_text"], lay["ocr_status"], lay["ocr_hash"]) == (None, None, None)
    assert pg.execute(
        "select count(*) from public.embeddings where owner_user_id = %s and ref_type = 'ink'", (ink.owner,)
    ).fetchone() == (0,)
    save(client, key, ink, strokes(3, y=0.5))
    assert layer(pg, ink)["ocr_status"] is None  # nothing is queued while off
    # Back on (null = the default, on): the ink already there is queued.
    r = client.patch("/api/me", json={"settings": {"handwriting_search": None}}, headers=h)
    assert r.status_code == 200 and "handwriting_search" not in r.json()["profile"]["settings"]
    assert layer(pg, ink)["ocr_status"] == "pending"


def test_the_nightly_pass_clears_an_opt_out_made_by_any_path(
    client: TestClient, key: SigningKey, ink: Ink, pg: psycopg.Connection[Any], url: str
) -> None:
    save(client, key, ink, strokes(3))
    age(pg, ink.owner)
    run_pass(url, FakeGateway())
    pg.execute("insert into public.profiles (user_id) values (%s) on conflict do nothing", (ink.owner,))
    pg.execute(
        "update public.profiles set settings = settings || '{\"handwriting_search\": false}' where user_id = %s",
        (ink.owner,),
    )
    stats, _ = run_pass(url, FakeGateway(), "nightly")
    assert stats.opted_out_cleared >= 1
    assert layer(pg, ink)["recognized_text"] is None


def test_ocr_columns_are_backend_only(
    ink: Ink, pg: psycopg.Connection[Any], client: TestClient, key: SigningKey
) -> None:
    from tests.db.rls import as_user

    save(client, key, ink, strokes(3))
    with as_user(pg, ink.owner) as conn:  # type: ignore[arg-type]
        row = conn.execute("select ocr_status from public.ink_layers where surface_id = %s", (ink.section,)).fetchone()
        assert row == ("pending",)
        for stmt in (
            "update public.ink_layers set ocr_status = 'done'",
            "update public.ink_layers set recognized_text = 'x', ocr_hash = 'h', ocr_at = now()",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
                conn.execute(stmt)


# ── convert to text ──


def png(size: tuple[int, int] = (120, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", size, (0, 0, 0, 255)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def gateway() -> Iterator[FakeGateway]:
    gw = FakeGateway(reply='{"text": "ATP", "lines": ["ATP"]}')
    set_gateway(gw)  # type: ignore[arg-type]
    yield gw
    set_gateway(None)


def convert(client: TestClient, h: dict[str, str], body: bytes, ctype: str = "image/png") -> Any:
    return client.post("/api/ink/convert", content=body, headers={**h, "content-type": ctype})


def test_convert_returns_text_and_stores_nothing(
    client: TestClient, key: SigningKey, ink: Ink, pg: psycopg.Connection[Any], gateway: FakeGateway
) -> None:
    before = pg.execute("select count(*) from public.ink_layers where user_id = %s", (ink.owner,)).fetchone()
    r = convert(client, ink.h(key), png())
    assert r.status_code == 200, r.text
    assert r.json() == {"text": "ATP", "lines": ["ATP"]}
    role, _, ctx = gateway.calls[0]
    assert role == "vision" and ctx.purpose == "chat" and str(ctx.run_id).startswith("ink_convert:")
    after = pg.execute("select count(*) from public.ink_layers where user_id = %s", (ink.owner,)).fetchone()
    assert before == after
    assert client.post("/api/ink/convert", content=png()).status_code == 401


def test_convert_validates_the_upload_and_the_budget(
    client: TestClient, key: SigningKey, ink: Ink, pg: psycopg.Connection[Any], gateway: FakeGateway
) -> None:
    h = ink.h(key)
    assert convert(client, h, png(), "image/jpeg").json()["error"]["code"] == "ink_convert_not_png"
    assert convert(client, h, b"GIF89a").status_code == 415
    assert convert(client, h, png((5000, 20))).json()["error"]["code"] == "ink_convert_invalid"
    big = b"\x89PNG\r\n\x1a\n" + b"\0" * (4 * 1024 * 1024 + 10)
    assert convert(client, h, big).status_code == 413
    gateway.reply = "no idea"
    assert convert(client, h, png()).json()["error"]["code"] == "ink_convert_failed"
    gateway.reply = BudgetExhausted("daily_cap", datetime.now(UTC) + timedelta(minutes=5), purpose="chat")
    r = convert(client, h, png())
    assert r.status_code == 429 and r.json()["error"]["code"] == "ink_convert_budget"
    assert 1 <= int(r.headers["retry-after"]) <= 300
    pg.execute(
        "insert into public.llm_usage (user_id, provider, purpose, role, model, run_id)"
        " select %s, 'nvidia', 'chat', 'vision', 'm', 'ink_convert:' || g from generate_series(1, 30) g",
        (ink.owner,),
    )
    try:
        r = convert(client, h, png())
        assert r.status_code == 429 and r.json()["error"]["code"] == "ink_convert_daily_cap"
    finally:
        pg.execute("delete from public.llm_usage where user_id = %s", (ink.owner,))


def test_convert_without_an_agent_runtime_is_unavailable(client: TestClient, key: SigningKey, ink: Ink) -> None:
    set_gateway(None)
    r = convert(client, ink.h(key), png())
    assert r.status_code == 503 and r.json()["error"]["code"] == "ink_convert_unavailable"
