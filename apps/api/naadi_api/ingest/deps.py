"""Build the pipeline's dependencies from the agent runtime (graph runs) or the API app (requests)."""

from __future__ import annotations

import uuid
from typing import Any

from ..chapters_media import configured_buckets
from ..config_api.schemas import FeatureFlags, IngestRules
from ..config_api.service import AppConfigService
from .pipeline import Deps
from .storage import get_storage

_CONFIG: dict[int, AppConfigService] = {}


def _config(db: Any, config_dir: Any) -> AppConfigService:
    key = id(db)
    svc = _CONFIG.get(key)
    if svc is None:
        svc = _CONFIG[key] = AppConfigService(db, config_dir)
    return svc


def deps_for(db: Any, settings: Any, *, is_admin: bool, gateway: Any, search: Any, start_child: Any = None) -> Deps:
    cfg = _config(db, settings.config_dir)

    async def rules() -> IngestRules:
        return IngestRules.model_validate(await cfg.get("ingest"))

    async def flags_for(_user_id: uuid.UUID) -> dict[str, bool]:
        flags = FeatureFlags.model_validate(await cfg.get("feature_flags")).root
        return {k: f.on_for(is_admin=is_admin, app_env=settings.app_env) for k, f in flags.items()}

    async def pathway_rules() -> Any:
        return await cfg.get("pathway")

    return Deps(
        pathway_rules=pathway_rules,
        db=db,
        storage=get_storage(settings),
        gateway=gateway,
        buckets=configured_buckets(settings),
        rules=rules,
        flags_for=flags_for,
        search=search,
        start_child=start_child,
    )


async def build_deps(config: Any) -> Deps:
    """For a graph run: the agent runtime's DB pool, LLM gateway and search, the run user's flags."""
    from ..agent.identity import run_identity
    from ..agent.runtime import get_runtime
    from .runner import get_runner

    rt = get_runtime()
    who = run_identity(config)
    runner = get_runner()

    async def start_child(item_id: uuid.UUID, user_id: uuid.UUID) -> None:
        await runner.start(user_id, who.is_admin, item_id, {"item_id": str(item_id), "mode": "start"}, rt.db)

    return deps_for(
        rt.db, rt.settings, is_admin=who.is_admin, gateway=rt.gateway, search=rt.search, start_child=start_child
    )


__all__ = ["build_deps", "deps_for"]
