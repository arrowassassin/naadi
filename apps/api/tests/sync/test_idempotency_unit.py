"""Idempotency-Key handling with an in-memory store (the Postgres store is exercised by the db tests)."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Annotated, Any

import pytest
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.testclient import TestClient
from pydantic import BaseModel

from naadi_api.auth import CurrentUser, get_account_user
from naadi_api.db import Database
from naadi_api.errors import CodedHTTPException, install_error_handlers
from naadi_api.main import create_app
from naadi_api.sync.idempotency import (
    IDEMPOTENT,
    REPLAYED_HEADER,
    Busy,
    IdempotentRoute,
    Outcome,
    Reserved,
    Stored,
    check_routes,
    idempotent_paths,
    outcome_of,
    parse_key,
    storable,
)

from ..conftest import fake_service, make_settings

A = uuid.UUID("00000000-0000-4000-8000-00000000000a")
B = uuid.UUID("00000000-0000-4000-8000-00000000000b")


class FakeStore:
    def __init__(self) -> None:
        self.rows: dict[tuple[uuid.UUID, uuid.UUID], dict[str, Any]] = {}
        self.calls: list[str] = []

    async def reserve(self, user_id: uuid.UUID, op_id: uuid.UUID, method: str, path: str) -> Outcome:
        self.calls.append("reserve")
        row = self.rows.get((user_id, op_id))
        if row is None:
            self.rows[(user_id, op_id)] = {"method": method, "path": path, "status": 0, "body": None}
            return Reserved()
        return outcome_of(row, method, path)

    async def finish(self, user_id: uuid.UUID, op_id: uuid.UUID, status: int, body: Any) -> None:
        self.calls.append("finish")
        row = self.rows[(user_id, op_id)]
        if row["status"] == 0:
            row.update(status=status, body=body)

    async def release(self, user_id: uuid.UUID, op_id: uuid.UUID) -> None:
        self.calls.append("release")
        if self.rows.get((user_id, op_id), {}).get("status") == 0:
            del self.rows[(user_id, op_id)]


class Body(BaseModel):
    n: int


def make_app(store: FakeStore) -> tuple[FastAPI, dict[str, int]]:
    app = FastAPI()
    install_error_handlers(app)
    app.state.sync_op_store = store
    runs = {"count": 0}
    router = APIRouter(prefix="/api", route_class=IdempotentRoute)

    def fake_user(request: Request) -> CurrentUser:
        return CurrentUser(
            user_id=uuid.UUID(request.headers.get("x-user", str(A))), email=None, email_verified=True, is_admin=False
        )

    app.dependency_overrides[get_account_user] = fake_user

    @router.post("/things", dependencies=[IDEMPOTENT], status_code=201)
    async def create(body: Body, user: Annotated[CurrentUser, Depends(get_account_user)]) -> dict[str, Any]:
        runs["count"] += 1
        return {"n": body.n, "run": runs["count"]}

    @router.delete("/things/{n}", dependencies=[IDEMPOTENT], status_code=204)
    async def delete(n: int) -> Response:
        runs["count"] += 1
        return Response(status_code=204)

    @router.post("/fail/{status}", dependencies=[IDEMPOTENT])
    async def fail(status: int) -> JSONResponse:
        runs["count"] += 1
        if status == 500:
            raise RuntimeError("boom")
        raise CodedHTTPException(status, f"code_{status}", f"code_{status}")

    @router.get("/plain")
    async def plain() -> dict[str, int]:
        runs["count"] += 1
        return {"run": runs["count"]}

    app.include_router(router)
    check_routes(app)
    return app, runs


@pytest.fixture
def store() -> FakeStore:
    return FakeStore()


@pytest.fixture
def world(store: FakeStore) -> tuple[TestClient, dict[str, int]]:
    app, runs = make_app(store)
    return TestClient(app, raise_server_exceptions=False), runs


def key() -> str:
    return str(uuid.uuid4())


def test_no_header_is_todays_behaviour(world: tuple[TestClient, dict[str, int]], store: FakeStore) -> None:
    client, runs = world
    assert client.post("/api/things", json={"n": 1}).json() == {"n": 1, "run": 1}
    assert client.post("/api/things", json={"n": 1}).json() == {"n": 1, "run": 2}
    assert store.calls == [] and runs["count"] == 2


def test_a_replay_returns_the_stored_response_without_running(world: tuple[TestClient, dict[str, int]]) -> None:
    client, runs = world
    k = key()
    first = client.post("/api/things", json={"n": 1}, headers={"Idempotency-Key": k})
    assert first.status_code == 201 and first.json() == {"n": 1, "run": 1}
    assert REPLAYED_HEADER not in first.headers
    again = client.post("/api/things", json={"n": 99}, headers={"Idempotency-Key": k})
    assert again.status_code == 201 and again.json() == {"n": 1, "run": 1}
    assert again.headers[REPLAYED_HEADER] == "true"
    assert runs["count"] == 1


def test_an_empty_204_is_replayed(world: tuple[TestClient, dict[str, int]]) -> None:
    client, runs = world
    k = key()
    assert client.delete("/api/things/3", headers={"Idempotency-Key": k}).status_code == 204
    again = client.delete("/api/things/3", headers={"Idempotency-Key": k})
    assert again.status_code == 204 and again.content == b"" and again.headers[REPLAYED_HEADER] == "true"
    assert runs["count"] == 1


def test_keys_are_per_user(world: tuple[TestClient, dict[str, int]]) -> None:
    client, runs = world
    k = key()
    client.post("/api/things", json={"n": 1}, headers={"Idempotency-Key": k})
    other = client.post("/api/things", json={"n": 2}, headers={"Idempotency-Key": k, "x-user": str(B)})
    assert other.status_code == 201 and other.json() == {"n": 2, "run": 2}
    assert runs["count"] == 2


def test_the_same_key_on_another_route_is_refused(world: tuple[TestClient, dict[str, int]]) -> None:
    client, runs = world
    k = key()
    client.post("/api/things", json={"n": 1}, headers={"Idempotency-Key": k})
    reused = client.delete("/api/things/1", headers={"Idempotency-Key": k})
    assert reused.status_code == 422 and reused.json()["error"]["code"] == "idempotency_key_reused"
    path = client.delete("/api/things/2", headers={"Idempotency-Key": k})
    assert path.status_code == 422
    assert runs["count"] == 1


@pytest.mark.parametrize(
    "bad", ["nope", "", "{00000000-0000-4000-8000-00000000000a}", "000000000000400080000000000000aa", "x" * 64]
)
def test_a_malformed_key_is_refused(world: tuple[TestClient, dict[str, int]], bad: str) -> None:
    client, runs = world
    res = client.post("/api/things", json={"n": 1}, headers={"Idempotency-Key": bad})
    assert res.status_code == 422 and res.json()["error"]["code"] in ("idempotency_key_invalid", "validation_error")
    assert runs["count"] == 0


def test_parse_key() -> None:
    assert parse_key(str(A)) == A
    assert parse_key(str(A).upper()) == A
    with pytest.raises(CodedHTTPException):
        parse_key("urn:uuid:" + str(A))


def test_a_key_in_flight_answers_409_with_retry_after(
    world: tuple[TestClient, dict[str, int]], store: FakeStore
) -> None:
    client, runs = world
    k = uuid.uuid4()
    store.rows[(A, k)] = {"method": "POST", "path": "/api/things", "status": 0, "body": None}
    res = client.post("/api/things", json={"n": 1}, headers={"Idempotency-Key": str(k)})
    assert res.status_code == 409 and res.json()["error"]["code"] == "idempotency_in_progress"
    assert res.headers["retry-after"] == "2"
    assert runs["count"] == 0
    assert store.rows[(A, k)]["status"] == 0  # the other request still owns it


@pytest.mark.parametrize(("status", "kept"), [(404, True), (409, True), (422, True), (401, False), (429, False)])
def test_final_4xx_are_stored_and_retryable_ones_are_not(
    world: tuple[TestClient, dict[str, int]], store: FakeStore, status: int, kept: bool
) -> None:
    client, runs = world
    k = uuid.UUID(key())
    first = client.post(f"/api/fail/{status}", headers={"Idempotency-Key": str(k)})
    assert first.status_code == status and first.json()["error"]["code"] == f"code_{status}"
    assert ((A, k) in store.rows) is kept
    again = client.post(f"/api/fail/{status}", headers={"Idempotency-Key": str(k)})
    assert again.status_code == status
    assert runs["count"] == (1 if kept else 2)
    assert (again.headers.get(REPLAYED_HEADER) == "true") is kept


def test_a_crash_releases_the_key(world: tuple[TestClient, dict[str, int]], store: FakeStore) -> None:
    client, runs = world
    k = uuid.UUID(key())
    assert client.post("/api/fail/500", headers={"Idempotency-Key": str(k)}).status_code == 500
    assert (A, k) not in store.rows and store.calls[-1] == "release"
    assert client.post("/api/fail/500", headers={"Idempotency-Key": str(k)}).status_code == 500
    assert runs["count"] == 2


def test_a_validation_error_is_a_final_answer(world: tuple[TestClient, dict[str, int]], store: FakeStore) -> None:
    client, runs = world
    k = uuid.UUID(key())
    res = client.post("/api/things", json={"n": "x"}, headers={"Idempotency-Key": str(k)})
    assert res.status_code == 422 and res.json()["error"]["code"] == "validation_error"
    assert store.rows[(A, k)]["status"] == 422
    assert runs["count"] == 0


def test_routes_without_the_dependency_pass_through(world: tuple[TestClient, dict[str, int]], store: FakeStore) -> None:
    client, _ = world
    assert client.get("/api/plain", headers={"Idempotency-Key": key()}).json() == {"run": 1}
    assert store.calls == []


def test_storable() -> None:
    assert storable(200) and storable(201) and storable(204) and storable(404) and storable(409)
    assert not storable(401) and not storable(408) and not storable(429) and not storable(500)
    assert not storable(503) and not storable(302)


def test_outcome_of() -> None:
    row = {"method": "POST", "path": "/api/x", "status": 201, "body": {"a": 1}}
    assert outcome_of(row, "POST", "/api/x") == Stored(201, {"a": 1})
    assert isinstance(outcome_of({**row, "status": 0}, "POST", "/api/x"), Busy)
    assert type(outcome_of(row, "PATCH", "/api/x")).__name__ == "Reused"


def test_check_routes_refuses_a_plain_route_with_the_dependency() -> None:
    app = FastAPI()
    router = APIRouter()

    @router.post("/x", dependencies=[IDEMPOTENT])
    async def x() -> None:
        return None

    app.include_router(router)
    with pytest.raises(RuntimeError, match="IdempotentRoute"):
        check_routes(app)


# Every write the S18 outbox replays takes the key (the real app).
REPLAYED_WRITES = {
    ("POST", "/api/blocks/{block_id}/attempts"),
    ("POST", "/api/cards/{card_id}/review"),
    ("POST", "/api/ink/{surface_type}/{surface_id}/changes"),
    ("POST", "/api/notes"),
    ("PATCH", "/api/notes/{note_id}"),
    ("DELETE", "/api/notes/{note_id}"),
    ("POST", "/api/notebooks"),
    ("PATCH", "/api/notebooks/{notebook_id}"),
    ("DELETE", "/api/notebooks/{notebook_id}"),
    ("POST", "/api/notebooks/{notebook_id}/pages"),
    ("PUT", "/api/notebooks/{notebook_id}/pages/order"),
    ("PUT", "/api/reading-position"),
    ("POST", "/api/plan/items/{item_id}/status"),
}


def test_the_app_takes_the_key_on_every_replayed_write(static_dir: Path) -> None:
    app = create_app(make_settings(static_dir=static_dir), health=fake_service(), db=Database(None))
    assert set(idempotent_paths(app)) == REPLAYED_WRITES
    spec = app.openapi()
    for method, path in REPLAYED_WRITES:
        params = spec["paths"][path][method.lower()].get("parameters", [])
        assert any(p["name"] == "Idempotency-Key" and p["in"] == "header" for p in params), (method, path)
