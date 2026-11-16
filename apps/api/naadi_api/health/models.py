"""Response models for /api/health and /api/ready (exported to OpenAPI → zod + TS client)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Status = Literal["ok", "degraded", "down"]


class Quota(BaseModel):
    used: int = Field(ge=0)
    limit: int | None = Field(description="None when the provider reports no limit")
    remaining: int | None
    unit: Literal["requests", "credits"]
    period: Literal["day", "month"]
    guard: int | None = Field(default=None, description="Our own stop point below the provider limit")


class ModelProbe(BaseModel):
    """One configured model's liveness, from a one-token call (NVIDIA lists retired models in its catalog)."""

    provider: Literal["nvidia", "openrouter"]
    model: str = Field(max_length=120)
    ok: bool
    latency_ms: float | None
    detail: str = Field(max_length=80, description="`answers`, or why the call failed (e.g. `HTTP 410 (retired)`)")


class ComponentHealth(BaseModel):
    status: Status
    latency_ms: float | None = Field(description="Time the check took; None if it could not run")
    detail: str = Field(max_length=240, description="Short human-readable summary, never secret material")
    checked_at: datetime
    cached: bool = Field(description="True when served from the check cache")
    quota: Quota | None = None
    free_tier: bool | None = Field(default=None, description="OpenRouter only: account has no credit purchase")
    configured: bool = Field(default=True, description="False when the settings this check needs are absent")
    models: list[ModelProbe] | None = Field(default=None, description="NVIDIA only: per-model probe results")


class HealthComponents(BaseModel):
    database: ComponentHealth
    storage: ComponentHealth
    openrouter: ComponentHealth
    nvidia: ComponentHealth
    tavily: ComponentHealth
    agent_runtime: ComponentHealth | None = Field(
        default=None,
        exclude_if=lambda v: v is None,
        description="Aegra agent runtime (graphs loaded, its DB pool, broker, cron scheduler, last hourly ping); "
        "present when the process is the Aegra host",
    )


class HealthResponse(BaseModel):
    status: Status
    version: str
    environment: Literal["local", "staging", "prod"]
    checked_at: datetime
    components: HealthComponents


class ReadyResponse(BaseModel):
    ready: bool
    checks: dict[str, bool]
