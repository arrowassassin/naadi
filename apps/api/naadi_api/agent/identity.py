"""Who a graph run belongs to.

Aegra puts the authenticated user into `config["configurable"]["langgraph_auth_user"]` (server-set) and
also `setdefault`s `configurable.user_id`, which a client could pre-fill. Graphs therefore read identity
ONLY from `langgraph_auth_user`, through `run_identity`.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from langchain_core.runnables import RunnableConfig

# Owner of system crons (Aegra stores identity as text). Never a UUID, so it can never collide with a
# Supabase user, and it has no access to user-private data.
SYSTEM_IDENTITY = "naadi-system"
ADMIN_PERMISSION = "admin"


class RunIdentityError(PermissionError):
    pass


@dataclass(frozen=True)
class RunIdentity:
    identity: str
    is_admin: bool
    run_id: str | None
    thread_id: str | None

    @property
    def is_system(self) -> bool:
        return self.identity == SYSTEM_IDENTITY

    @property
    def user_id(self) -> uuid.UUID | None:
        if self.is_system:
            return None
        return uuid.UUID(self.identity)


def _field(user: Any, name: str) -> Any:
    if isinstance(user, Mapping):
        return user.get(name)
    if hasattr(user, "to_dict"):
        return user.to_dict().get(name)
    return getattr(user, name, None)


def run_identity(config: RunnableConfig) -> RunIdentity:
    conf = config.get("configurable") or {}
    user = conf.get("langgraph_auth_user")
    if user is None:
        raise RunIdentityError("run has no authenticated user")
    identity = str(_field(user, "identity") or "")
    if identity != SYSTEM_IDENTITY:
        try:
            uuid.UUID(identity)
        except ValueError as exc:
            raise RunIdentityError("run identity is not a user id") from exc
    perms = _field(user, "permissions") or []
    return RunIdentity(
        identity=identity,
        is_admin=ADMIN_PERMISSION in perms,
        run_id=str(conf.get("run_id")) if conf.get("run_id") else None,
        thread_id=str(conf.get("thread_id")) if conf.get("thread_id") else None,
    )
