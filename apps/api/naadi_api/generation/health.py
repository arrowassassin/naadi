"""The `generation` health component: local facts only (it never calls the Space, so a health poll spends no GPU
quota and cannot wake a sleeping Space)."""

from __future__ import annotations

from ..db import Database
from ..health.checks import CheckFn, CheckOutcome, NotConfigured
from ..health.models import GenerationFacts
from ..settings import Settings
from .service import service_tx


def generation_check(settings: Settings, db: Database, *, flag_on: bool = True) -> CheckFn:
    async def run() -> CheckOutcome:
        if not settings.generation_configured:
            # status ok: an optional feature that is not set up yet is not an outage
            raise NotConfigured("HF_GEN_TOKEN", status="ok")
        async with service_tx(db) as conn:
            cur = await conn.execute(
                "select max(paused_until) as until from public.generated_media"
                " where status = 'paused' and paused_until > now()"
            )
            paused = (await cur.fetchone() or {}).get("until")
        state = "paused" if paused is not None else ("ready" if flag_on else "disabled")
        facts = GenerationFacts(state=state, resets_at=paused)
        return CheckOutcome(
            "degraded" if state == "paused" else "ok",
            "ZeroGPU quota paused" if state == "paused" else "Space configured",
            generation=facts,
        )

    return run
