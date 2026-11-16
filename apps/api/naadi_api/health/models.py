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


class ComponentHealth(BaseModel):
    status: Status
    latency_ms: float | None = Field(description="Time the check took; None if it could not run")
    detail: str = Field(max_length=240, description="Short human-readable summary, never secret material")
    checked_at: datetime
    cached: bool = Field(description="True when served from the check cache")
    quota: Quota | None = None
    free_tier: bool | None = Field(default=None, description="OpenRouter only: account has no credit purchase")


class HealthComponents(BaseModel):
    database: ComponentHealth
    storage: ComponentHealth
    openrouter: ComponentHealth
    tavily: ComponentHealth


class HealthResponse(BaseModel):
    status: Status
    version: str
    environment: Literal["local", "staging", "prod"]
    checked_at: datetime
    components: HealthComponents


class ReadyResponse(BaseModel):
    ready: bool
    checks: dict[str, bool]
