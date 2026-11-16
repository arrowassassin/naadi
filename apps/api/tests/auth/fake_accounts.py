"""Unit-test stand-in for the `auth.users` / `auth.sessions` lookup behind `AccountDirectory`.

It answers the directory's one query from in-memory dicts, so the directory's real logic (session
liveness, confirmed email, ADMIN_EMAILS, caching) is exercised without Postgres. The DB tests cover the
SQL itself against Supabase's real auth schema.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from .jwt_helpers import SigningKey, claims, mint


@dataclass
class _User:
    email: str | None
    confirmed: bool


@dataclass
class FakeAuthDb:
    users: dict[uuid.UUID, _User] = field(default_factory=dict)
    sessions: dict[str, uuid.UUID] = field(default_factory=dict)
    queries: int = 0
    configured: bool = True

    @property
    def pool(self) -> FakeAuthDb:
        return self

    async def open(self) -> None:
        return None

    @asynccontextmanager
    async def connection(self, **_: Any) -> AsyncIterator[FakeAuthDb]:
        yield self

    async def execute(self, sql: str, params: dict[str, Any]) -> _Cursor:
        assert "from auth.users" in sql and "auth.sessions" in sql
        self.queries += 1
        u = self.users.get(params["uid"])
        if u is None:
            return _Cursor(None)
        live = self.sessions.get(str(params["sid"])) == params["uid"]
        return _Cursor((u.email, u.confirmed, live))

    def add_user(self, email: str | None, *, confirmed: bool = True) -> uuid.UUID:
        uid = uuid.uuid4()
        self.users[uid] = _User(email, confirmed)
        return uid

    def open_session(self, uid: uuid.UUID) -> str:
        sid = str(uuid.uuid4())
        self.sessions[sid] = uid
        return sid

    def login(self, key: SigningKey, email: str | None, *, confirmed: bool = True, **extra: Any) -> str:
        """A user with a live session, and an access token for it (token email = account email)."""
        uid = self.add_user(email, confirmed=confirmed)
        sid = self.open_session(uid)
        return mint(key, claims(uid, email=email, session_id=sid, **extra))


@dataclass
class _Cursor:
    row: tuple[Any, ...] | None

    async def fetchone(self) -> tuple[Any, ...] | None:
        return self.row
