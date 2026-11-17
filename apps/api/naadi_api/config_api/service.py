"""AppConfigService: the one runtime reader/writer of `app_config` (HANDOFF §7.5).

- `get(key)`: validated value, cached 60 s per process. Missing row, invalid row or unreachable database ->
  the value from `config/*.yaml` (logged once per cache period as `app_config_fallback`, key + reason only).
- Writes (`put`) validate with the key's pydantic model, use optimistic concurrency on `updated_at`, and the
  0004 history trigger records the replaced value. Every write fires `pg_notify('naadi_app_config', key)`;
  `start()` LISTENs on a dedicated connection so every process drops the key at once. If that connection
  drops, the TTL still bounds staleness and the listener reconnects with backoff.

The API's database role bypasses RLS (app_config is admin-only and has no client privileges); callers
gate admin access before calling the write methods.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import json
import time
import uuid
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import psycopg
from psycopg.rows import dict_row
from pydantic import ValidationError

from .. import llm_config
from ..db import Database
from ..logs import get_logger
from .defaults import file_values
from .schemas import KEY_SPECS, FeatureFlags, UnknownConfigKey, cross_key_problems, validate_value

log = get_logger("naadi.app_config")

CACHE_TTL_S = 60.0


def _default_unavailable() -> Collection[str]:
    """Flags whose backing secret is absent in this process (read through settings.py)."""
    from ..settings import get_settings

    return () if get_settings().generation_configured else ("generation",)


CHANNEL = "naadi_app_config"
DB_DEADLINE_S = 3.0

Source = Literal["db", "file"]


@dataclass(frozen=True)
class ConfigRow:
    key: str
    value: Any
    updated_at: dt.datetime | None
    updated_by: uuid.UUID | None
    source: Source
    invalid_reason: str | None = None
    admin_edited_at: dt.datetime | None = None


@dataclass(frozen=True)
class HistoryRow:
    id: int
    value: Any
    updated_by: uuid.UUID | None
    updated_at: dt.datetime | None
    op: str
    replaced_at: dt.datetime


class StaleWrite(Exception):
    """`expected_updated_at` does not match the stored row (someone else saved first)."""

    def __init__(self, current: dt.datetime | None) -> None:
        super().__init__("app_config row changed since it was read")
        self.current = current


class CrossKeyInvalid(ValueError):
    pass


class AppConfigService:
    def __init__(
        self,
        db: Database,
        config_dir: Path,
        *,
        ttl_s: float = CACHE_TTL_S,
        clock: Callable[[], float] = time.monotonic,
        unavailable: Callable[[], Collection[str]] | None = None,
    ) -> None:
        self._db = db
        self._config_dir = config_dir
        self._ttl = ttl_s
        self._clock = clock
        # Flags that stay off whatever app_config says, because what they need is not configured (e.g. `generation`
        # without HF_GEN_TOKEN). The default reads the process settings, so the Aegra host agrees with the API.
        self._unavailable = unavailable or _default_unavailable
        self._cache: dict[str, tuple[float, ConfigRow]] = {}
        self._files: dict[str, Any] | None = None
        self._lock = asyncio.Lock()
        self._listener: asyncio.Task[None] | None = None
        self.listening = False

    # ── reads ──

    def file_value(self, key: str) -> Any:
        if key not in KEY_SPECS:
            raise UnknownConfigKey(key)
        if self._files is None:
            self._files = file_values(self._config_dir)
        return self._files[key]

    def invalidate(self, key: str | None = None) -> None:
        if key is None:
            self._cache.clear()
        else:
            self._cache.pop(key, None)

    async def get(self, key: str) -> Any:
        return (await self.row(key)).value

    async def row(self, key: str) -> ConfigRow:
        if key not in KEY_SPECS:
            raise UnknownConfigKey(key)
        now = self._clock()
        hit = self._cache.get(key)
        if hit is not None and now - hit[0] < self._ttl:
            return hit[1]
        async with self._lock:
            hit = self._cache.get(key)
            if hit is not None and self._clock() - hit[0] < self._ttl:
                return hit[1]
            row = await self._load(key)
            self._cache[key] = (self._clock(), row)
            return row

    async def get_many(self, keys: tuple[str, ...]) -> dict[str, Any]:
        return {k: await self.get(k) for k in keys}

    async def _load(self, key: str) -> ConfigRow:
        reason: str
        try:
            raw = await self._fetch(key)
        except (psycopg.Error, TimeoutError, OSError) as exc:
            reason = f"database unavailable ({type(exc).__name__})"
        else:
            if raw is None:
                reason = "no row"
            else:
                try:
                    value = validate_value(key, raw["value"])
                except ValidationError as exc:
                    merged = self._fill_missing_copy(key, raw["value"])
                    if merged is not None:
                        value, added = merged
                        note = f"{len(added)} missing keys filled from config/app_config.yaml"
                        log.info("app_config_filled", key=key, added=len(added))
                        return ConfigRow(
                            key,
                            value,
                            raw["updated_at"],
                            raw["updated_by"],
                            "db",
                            invalid_reason=note,
                            admin_edited_at=raw["admin_edited_at"],
                        )
                    reason = f"invalid row ({exc.error_count()} errors)"
                    log.warning("app_config_fallback", key=key, reason=reason)
                    return ConfigRow(
                        key, self.file_value(key), raw["updated_at"], raw["updated_by"], "file", invalid_reason=reason
                    )
                return ConfigRow(
                    key, value, raw["updated_at"], raw["updated_by"], "db", admin_edited_at=raw["admin_edited_at"]
                )
        log.warning("app_config_fallback", key=key, reason=reason)
        return ConfigRow(key, self.file_value(key), None, None, "file")

    def _fill_missing_copy(self, key: str, stored: object) -> tuple[Any, list[str]] | None:
        """A stored ui_copy that predates newly required keys (an admin-edited row the seed keeps): stored strings
        win, the shipped value fills only the keys it lacks. None when that still does not validate."""
        if key != "ui_copy" or not isinstance(stored, dict):
            return None
        shipped: dict[str, str] = self.file_value(key)
        added = sorted(k for k in shipped if k not in stored)
        if not added:
            return None
        try:
            return validate_value(key, {**shipped, **stored}), added
        except ValidationError:
            return None

    async def _fetch(self, key: str) -> dict[str, Any] | None:
        if not self._db.configured:
            raise psycopg.OperationalError("DATABASE_URL is not set")
        if self._db.pool is None:
            await self._db.open()
        assert self._db.pool is not None
        async with self._db.pool.connection(timeout=DB_DEADLINE_S) as conn, asyncio.timeout(DB_DEADLINE_S):
            cur = conn.cursor(row_factory=dict_row)
            await cur.execute(
                "select value, updated_at, updated_by, admin_edited_at from public.app_config where key = %s", (key,)
            )
            return await cur.fetchone()

    # ── derived views ──

    async def flags(self) -> FeatureFlags:
        return FeatureFlags.model_validate(await self.get("feature_flags"))

    async def feature_enabled(self, name: str, *, is_admin: bool, app_env: str) -> bool:
        flag = (await self.flags()).root.get(name)
        return flag is not None and name not in self._unavailable() and flag.on_for(is_admin=is_admin, app_env=app_env)

    async def resolved_features(self, *, is_admin: bool, app_env: str) -> dict[str, bool]:
        off = set(self._unavailable())
        return {
            k: k not in off and f.on_for(is_admin=is_admin, app_env=app_env)
            for k, f in (await self.flags()).root.items()
        }

    async def nav_for(self, *, is_admin: bool, app_env: str) -> list[dict[str, Any]]:
        on = await self.resolved_features(is_admin=is_admin, app_env=app_env)
        nav: list[dict[str, Any]] = await self.get("ui.nav")
        return [item for item in nav if on.get(item["feature"], False)]

    # ── admin ──

    async def list_rows(self) -> list[ConfigRow]:
        return [await self.row(k) for k in KEY_SPECS]

    async def stored_row(self, key: str) -> ConfigRow:
        """The row as stored now (no cache)."""
        self.invalidate(key)
        return await self.row(key)

    async def put(
        self,
        key: str,
        value: object,
        *,
        expected_updated_at: dt.datetime | None,
        user_id: uuid.UUID,
        admin_edited: bool = True,
    ) -> ConfigRow:
        """Save `value`. `admin_edited=False` hands the key back to the seed (no admin_edited_at mark), e.g. when
        an admin restores the shipped value: the next seed run may then update it again."""
        normalized = validate_value(key, value)
        others = {k: (normalized if k == key else (await self.get(k))) for k in ("ui.nav", "feature_flags")}
        problems = cross_key_problems(others)
        if problems:
            raise CrossKeyInvalid("; ".join(problems))
        if not self._db.configured:
            raise psycopg.OperationalError("DATABASE_URL is not set")
        if self._db.pool is None:
            await self._db.open()
        assert self._db.pool is not None
        async with self._db.pool.connection(timeout=DB_DEADLINE_S) as conn, conn.transaction():
            cur = conn.cursor(row_factory=dict_row)
            await cur.execute("select updated_at from public.app_config where key = %s for update", (key,))
            current = await cur.fetchone()
            current_at = current["updated_at"] if current else None
            if current_at != expected_updated_at:
                raise StaleWrite(current_at)
            await cur.execute(
                "insert into public.app_config (key, value, updated_by, admin_edited_at)"
                " values (%s, %s::jsonb, %s, case when %s then now() end)"
                " on conflict (key) do update set value = excluded.value, updated_by = excluded.updated_by,"
                " admin_edited_at = excluded.admin_edited_at"
                " returning value, updated_at, updated_by, admin_edited_at",
                (key, json.dumps(normalized, separators=(",", ":")), user_id, admin_edited),
            )
            saved = await cur.fetchone()
        assert saved is not None
        self.invalidate(key)
        if key in llm_config.AGENT_CONFIG_KEYS:
            llm_config.invalidate_runtime_config()
        log.info("app_config_updated", key=key)
        return ConfigRow(
            key, normalized, saved["updated_at"], saved["updated_by"], "db", admin_edited_at=saved["admin_edited_at"]
        )

    async def history(self, key: str, *, limit: int = 50) -> list[HistoryRow]:
        if key not in KEY_SPECS:
            raise UnknownConfigKey(key)
        if not self._db.configured:
            raise psycopg.OperationalError("DATABASE_URL is not set")
        if self._db.pool is None:
            await self._db.open()
        assert self._db.pool is not None
        async with self._db.pool.connection(timeout=DB_DEADLINE_S) as conn:
            cur = conn.cursor(row_factory=dict_row)
            await cur.execute(
                "select id, value, updated_by, updated_at, op, replaced_at from public.app_config_history"
                " where key = %s order by replaced_at desc, id desc limit %s",
                (key, limit),
            )
            rows = await cur.fetchall()
        return [HistoryRow(**r) for r in rows]

    # ── cross-process invalidation ──

    async def start(self) -> None:
        """Start the LISTEN task (idempotent). Safe without a database: it simply does not listen."""
        if self._listener is None and self._db.configured:
            self._listener = asyncio.create_task(self._listen(), name="app_config_listener")

    async def stop(self) -> None:
        task, self._listener = self._listener, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _listen(self) -> None:
        url = self._db.conninfo()
        backoff = 1.0
        while True:
            try:
                async with await psycopg.AsyncConnection.connect(
                    url, autocommit=True, connect_timeout=5, application_name="naadi-app-config-listener"
                ) as conn:
                    await conn.execute(f"listen {CHANNEL}")
                    self.listening = True
                    # Notifications may have been missed while disconnected.
                    self.invalidate()
                    backoff = 1.0
                    async for note in conn.notifies():
                        self.invalidate(note.payload or None)
                        if note.payload in llm_config.AGENT_CONFIG_KEYS:
                            llm_config.invalidate_runtime_config()
            except asyncio.CancelledError:
                self.listening = False
                raise
            except (psycopg.Error, OSError) as exc:
                self.listening = False
                log.warning("app_config_listener_down", error=type(exc).__name__, retry_s=backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)


def public_values(values: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in values.items() if KEY_SPECS[k].public}
