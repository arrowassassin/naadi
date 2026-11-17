"""Build the generation service for the API process and for the Aegra host (tools)."""

from __future__ import annotations

from typing import Any

from ..chapters_media import configured_buckets
from ..config_api.service import AppConfigService
from ..db import Database
from ..llm_config import GenerationSection
from ..settings import Settings
from .client import FakeGenerationClient, GenerationClient, GenerationRequest, GenerationResult, GradioSpaceClient, Kind
from .client import ProgressFn as ProgressFn
from .service import GenerationService
from .store import MediaStore, MemoryMediaStore, SupabaseMediaStore


class LazySpaceClient:
    """Reads `app_config.generation` (space id, models) on first use, so an admin edit is picked up on restart."""

    def __init__(self, token: str, config: Any) -> None:
        self._token = token
        self._config = config
        self._inner: GradioSpaceClient | None = None

    async def generate(self, req: GenerationRequest, progress: ProgressFn) -> GenerationResult:
        if self._inner is None:
            cfg: GenerationSection = await self._config()
            models: dict[Kind, str] = {"image": cfg.image.model, "video": cfg.video.model}
            self._inner = GradioSpaceClient(space_id=cfg.space_id, token=self._token, models=models)
        return await self._inner.generate(req, progress)


def build_generation_service(settings: Settings, db: Database, app_config: AppConfigService) -> GenerationService:
    async def config() -> GenerationSection:
        return GenerationSection.model_validate(await app_config.get("generation"))

    buckets = configured_buckets(settings)
    client: GenerationClient | None
    store: MediaStore
    if settings.generation_fake and settings.app_env != "prod":
        client, store = FakeGenerationClient(), MemoryMediaStore()
    else:
        store = SupabaseMediaStore(settings)
        token = settings.hf_gen_token.get_secret_value() if settings.hf_gen_token is not None else None
        client = LazySpaceClient(token, config) if token else None
    return GenerationService(
        db=db, config=config, client=client, store=store, buckets=(buckets.generated_media, buckets.content_public)
    )


__all__ = ["LazySpaceClient", "build_generation_service"]
