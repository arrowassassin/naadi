"""Wiring of `pipeline.Deps` for the two callers: the admin HTTP trigger and the `author` graph."""

from __future__ import annotations

import asyncio
from typing import Any

from ..config_api.schemas import AuthoringRules
from ..db import Database
from ..logs import get_logger
from ..settings import Settings
from .lint import lab_refs
from .pipeline import Deps
from .store import PgAuthoringStore

log = get_logger("naadi.authoring.deps")


async def load_rules(config: Any) -> AuthoringRules:
    return AuthoringRules.model_validate(await config.get("authoring"))


async def build_deps(db: Database, gateway: Any, settings: Settings, config: Any, rules: AuthoringRules) -> Deps:
    from ..embeddings.engine import get_engine, vector_literal

    labs: list[Any] = []
    try:
        labs = lab_refs(await config.get("lab_values"))
    except Exception as exc:  # noqa: BLE001 - the lab-value rule is skipped without its table
        log.warning("authoring.labs_unavailable", error=type(exc).__name__)
    try:
        engine = get_engine(settings.config_dir)
    except Exception as exc:  # noqa: BLE001 - no embedding engine: the originality check is skipped (and recorded)
        log.warning("authoring.no_embedding_engine", error=type(exc).__name__)
        engine = None

    async def embed(text: str) -> str:
        assert engine is not None
        vec = await asyncio.to_thread(lambda: engine.embed_passages([text])[0])
        return vector_literal(vec)

    return Deps(
        store=PgAuthoringStore(db),
        gateway=gateway,
        rules=rules,
        config_dir=settings.config_dir,
        labs=labs,
        embed=embed if engine is not None else None,
    )
