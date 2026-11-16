"""Agent auth (Aegra handler) without a server: 503 (not 401) when the account cannot be checked, the shared
account directory, and the one-pool guarantee of Database.open under concurrent first use."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import pytest
from langgraph_sdk import Auth
from pydantic import SecretStr

from naadi_api.agent import auth as agent_auth
from naadi_api.agent.auth import AuthUnavailableAs503
from naadi_api.auth import AuthUnavailable, CurrentUser
from naadi_api.db import Database


class FixedVerifier:
    def __init__(self, sub: uuid.UUID) -> None:
        self.sub = sub

    async def verify(self, token: str) -> dict[str, Any]:
        return {"sub": str(self.sub), "role": "authenticated", "session_id": "s1"}


class FailingAccounts:
    async def resolve(self, user: CurrentUser) -> CurrentUser:
        raise AuthUnavailable("Your account cannot be checked right now. Try again in a moment.")


class OkAccounts:
    async def resolve(self, user: CurrentUser) -> CurrentUser:
        return CurrentUser(user_id=user.user_id, email=None, email_verified=True, is_admin=True, session_id="s1")


@pytest.fixture(autouse=True)
def _reset() -> Any:
    yield
    agent_auth.configure(None, None)


async def test_unavailable_account_check_is_503_and_marked() -> None:
    agent_auth.configure(FixedVerifier(uuid.uuid4()), FailingAccounts())  # type: ignore[arg-type]
    flag = {"unavailable": False}
    token = agent_auth.unavailable_flag.set(flag)
    try:
        with pytest.raises(Auth.exceptions.HTTPException) as e:
            await agent_auth.authenticate({"authorization": "Bearer a.b.c"})
    finally:
        agent_auth.unavailable_flag.reset(token)
    assert e.value.status_code == 503
    assert flag["unavailable"] is True


async def test_admin_identity_from_the_shared_directory() -> None:
    uid = uuid.uuid4()
    agent_auth.configure(FixedVerifier(uid), OkAccounts())  # type: ignore[arg-type]
    user = await agent_auth.authenticate({"authorization": "Bearer a.b.c"})
    assert user["identity"] == str(uid)
    assert user["permissions"] == ["admin"]
    assert "email" not in user


async def _run_asgi(app: Any) -> tuple[int, dict[bytes, bytes], bytes]:
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(m: dict[str, Any]) -> None:
        sent.append(m)

    await app({"type": "http", "method": "POST", "path": "/threads", "headers": []}, receive, send)
    start = sent[0]
    body = b"".join(m.get("body", b"") for m in sent[1:])
    return start["status"], dict(start["headers"]), body


async def test_middleware_rewrites_only_marked_401s() -> None:
    async def aegra_like(scope: Any, receive: Any, send: Any, mark: bool) -> None:
        if mark:
            agent_auth.mark_unavailable()
        body = b'{"error":"unauthorized","message":"x"}'
        await send(
            {"type": "http.response.start", "status": 401, "headers": [(b"content-length", str(len(body)).encode())]}
        )
        await send({"type": "http.response.body", "body": body, "more_body": False})

    status, headers, body = await _run_asgi(AuthUnavailableAs503(lambda s, r, se: aegra_like(s, r, se, True)))
    assert status == 503
    assert headers[b"retry-after"] == b"5"
    assert json.loads(body)["error"] == "service_unavailable"
    assert int(headers[b"content-length"]) == len(body)

    status, _, body = await _run_asgi(AuthUnavailableAs503(lambda s, r, se: aegra_like(s, r, se, False)))
    assert status == 401
    assert json.loads(body)["error"] == "unauthorized"


async def test_concurrent_first_open_creates_one_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[Any] = []

    class FakePool:
        closed = False

        def __init__(self, **kw: Any) -> None:
            created.append(self)

        async def open(self, wait: bool = False) -> None:
            await asyncio.sleep(0.01)

        @staticmethod
        async def check_connection(conn: Any) -> None:
            return None

    monkeypatch.setattr("naadi_api.db.AsyncConnectionPool", FakePool)
    db = Database(SecretStr("postgresql://u:p@127.0.0.1:1/x"))
    await asyncio.gather(*(db.open() for _ in range(50)))
    assert len(created) == 1
    assert db.pool is created[0]


# ── S04 re-review R2: a stalled database never hangs an agent request ──


class _StalledConn:
    async def execute(self, *_: Any, **__: Any) -> Any:
        await asyncio.Event().wait()  # the server never answers (e.g. a paused Postgres)


class _StalledCtx:
    def __init__(self, stall: bool) -> None:
        self.stall = stall

    async def __aenter__(self) -> _StalledConn:
        if self.stall:
            await asyncio.Event().wait()
        return _StalledConn()

    async def __aexit__(self, *_: object) -> None:
        return None


class _StalledPool:
    def __init__(self, stall_checkout: bool) -> None:
        self.stall_checkout = stall_checkout

    def connection(self, timeout: float | None = None) -> _StalledCtx:
        return _StalledCtx(self.stall_checkout)


class _StalledDb:
    configured = True

    def __init__(self, *, stall_open: bool = False, stall_checkout: bool = False) -> None:
        self.pool: _StalledPool | None = None if stall_open else _StalledPool(stall_checkout)

    async def open(self) -> None:
        await asyncio.Event().wait()


@pytest.mark.parametrize(
    "db",
    [_StalledDb(), _StalledDb(stall_checkout=True), _StalledDb(stall_open=True)],
    ids=["query", "checkout", "open"],
)
async def test_stalled_database_is_unavailable_within_the_deadline(db: _StalledDb) -> None:
    from naadi_api.auth import AccountDirectory

    accounts = AccountDirectory(db, frozenset(), deadline_s=0.2)
    user = CurrentUser(user_id=uuid.uuid4(), email=None, email_verified=False, is_admin=False, session_id="s1")
    loop = asyncio.get_running_loop()
    started = loop.time()
    with pytest.raises(AuthUnavailable):
        await asyncio.wait_for(accounts.resolve(user), timeout=5)
    assert loop.time() - started < 2


async def test_stalled_database_makes_agent_auth_answer_503() -> None:
    from naadi_api.auth import AccountDirectory

    agent_auth.configure(
        FixedVerifier(uuid.uuid4()),  # type: ignore[arg-type]
        AccountDirectory(_StalledDb(), frozenset(), deadline_s=0.2),
    )
    flag = {"unavailable": False}
    token = agent_auth.unavailable_flag.set(flag)
    try:
        with pytest.raises(Auth.exceptions.HTTPException) as e:
            await asyncio.wait_for(agent_auth.authenticate({"authorization": "Bearer a.b.c"}), timeout=5)
    finally:
        agent_auth.unavailable_flag.reset(token)
    assert e.value.status_code == 503
    assert flag["unavailable"] is True
