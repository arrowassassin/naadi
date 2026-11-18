"""`backup`: the nightly database backup and the weekly Storage backup (segment 22, naadi_api.ops.backup).

Run by system crons (naadi_api.agent.host): `{"mode": "db"}` at 03:00 UTC, `{"mode": "storage"}` on Sundays at
03:30 UTC. Admins may run either by hand; students may not. Every run records one `agent_health` row (kind
`backup`): `not_configured` until HF_BACKUP_TOKEN and BACKUP_AGE_RECIPIENT are set, else the outcome, the dataset
paths, sizes and sha256 of the encrypted files, and what the retention deleted. The blocking work (pg_dump,
encryption, the upload) runs in a worker thread.
"""

from __future__ import annotations

import asyncio
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, NotRequired, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from naadi_api.agent.health import record_health
from naadi_api.agent.identity import RunIdentityError, run_identity
from naadi_api.agent.runtime import agent_db, get_runtime
from naadi_api.logs import get_logger
from naadi_api.ops.backup import (
    STORAGE_BUCKETS,
    BackupConfig,
    BackupError,
    HfDatasetStore,
    run_db_backup,
    run_storage_backup,
)

log = get_logger("naadi.agent.backup")


class BackupState(TypedDict):
    mode: NotRequired[Literal["db", "storage"]]
    ok: NotRequired[bool]
    detail: NotRequired[dict[str, Any]]
    recorded_id: NotRequired[int]


def _buckets(settings: Any) -> list[tuple[str, Any, tuple[str, ...]]]:
    from naadi_api.config_api.defaults import file_values
    from naadi_api.seed.storage import StorageTarget

    if not (settings.supabase_url and settings.supabase_service_role_key):
        raise BackupError("storage_not_configured", "SUPABASE_URL,SUPABASE_SERVICE_ROLE_KEY")
    names = file_values(settings.config_dir)["storage"]
    client = StorageTarget(
        key=settings.supabase_service_role_key.get_secret_value(), supabase_url=settings.supabase_url
    ).client()
    return [(names[key], client.from_(names[key]), skip) for key, skip in STORAGE_BUCKETS]


def _run_blocking(mode: str, settings: Any) -> dict[str, Any]:
    cfg = BackupConfig.from_settings(settings)
    if cfg.missing and (mode == "db" or set(cfg.missing) - {"DATABASE_URL"}):
        raise BackupError("not_configured", ",".join(cfg.missing))
    store = HfDatasetStore(cfg.repo_id, str(cfg.token))
    today = datetime.now(UTC).date()
    with tempfile.TemporaryDirectory(prefix="naadi-backup-") as tmp:
        work = Path(tmp)
        if mode == "storage":
            return run_storage_backup(cfg, store, _buckets(settings), today=today, workdir=work)
        return run_db_backup(cfg, store, today=today, workdir=work)


async def run(state: BackupState, config: RunnableConfig) -> dict[str, Any]:
    who = run_identity(config)
    if not (who.is_admin or who.is_system):
        raise RunIdentityError("the backup graph is admin-only")
    mode = state.get("mode") or "db"
    if mode not in ("db", "storage"):
        return {"ok": False, "detail": {"outcome": "bad_mode", "kind": str(mode)[:20]}}
    try:
        detail = await asyncio.to_thread(_run_blocking, mode, get_runtime().settings)
    except BackupError as exc:
        # `detail` names missing variables or a scrubbed tool message, never a value.
        return {"ok": False, "detail": {"outcome": exc.code, "kind": mode, "info": exc.detail[:300]}}
    except Exception as exc:  # noqa: BLE001 - any other failure is recorded, never raised into the scheduler
        log.warning("backup_failed", kind=mode, error=type(exc).__name__)
        return {"ok": False, "detail": {"outcome": "error", "kind": mode, "error": type(exc).__name__}}
    return {"ok": True, "detail": detail}


async def record(state: BackupState, config: RunnableConfig) -> dict[str, Any]:
    who = run_identity(config)
    rid = await record_health(
        agent_db(),
        kind="backup",
        trigger="cron" if who.is_system else "background",
        ok=bool(state.get("ok")),
        run_id=who.run_id,
        detail=state.get("detail") or {},
    )
    return {"recorded_id": rid}


def build() -> StateGraph[BackupState]:
    g: StateGraph[BackupState] = StateGraph(BackupState)
    g.add_node("run", run)
    g.add_node("record", record)
    g.add_edge(START, "run")
    g.add_edge("run", "record")
    g.add_edge("record", END)
    return g


graph = build()
