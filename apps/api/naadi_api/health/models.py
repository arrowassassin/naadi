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


AgentRuntimeReason = Literal["ok", "not_installed", "not_hosted", "checks_failed", "no_cron_ping", "cron_stale"]
AgentRuntimeCheck = Literal["graphs_loaded", "database", "broker", "cron_scheduler"]


class AgentRuntimeFacts(BaseModel):
    """agent_runtime only: what the status is based on, as codes and values the client words with ui_copy."""

    reason: AgentRuntimeReason
    graphs: list[str] = Field(default_factory=list, max_length=50, description="Graph ids Aegra has loaded")
    failing: list[AgentRuntimeCheck] = Field(default_factory=list, description="Runtime checks that are not ok")
    last_cron_ping: datetime | None = Field(default=None, description="When the hourly system ping last recorded")


GenerationHealthState = Literal["ready", "paused", "disabled"]


class GenerationFacts(BaseModel):
    """generation only: the Space state as codes and numbers (a not-configured Space has `configured: false`)."""

    state: GenerationHealthState
    resets_at: datetime | None = Field(default=None, description="End of an active ZeroGPU quota pause")


BackupReason = Literal["ok", "not_configured", "never_ran", "failed", "stale"]


class BackupFacts(BaseModel):
    """backup only (segment 22): what the status is based on, as codes and times the client words with ui_copy."""

    reason: BackupReason
    missing: list[str] = Field(
        default_factory=list, max_length=5, description="Names of the unset settings (never values)"
    )
    last_db_ok_at: datetime | None = Field(default=None, description="End of the last successful database backup")
    last_storage_ok_at: datetime | None = Field(default=None, description="End of the last successful Storage backup")
    last_outcome: str | None = Field(default=None, max_length=40, description="Outcome code of the latest run")
    retention_days: int = Field(default=30, description="Days a database backup is kept")


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
    agent: AgentRuntimeFacts | None = Field(
        default=None, description="agent_runtime only: structured facts behind the status (None if the check failed)"
    )
    generation: GenerationFacts | None = Field(
        default=None, description="generation only: state and pause reset time (None if not configured)"
    )
    backup: BackupFacts | None = Field(default=None, description="backup only: structured facts behind the status")


class HealthComponents(BaseModel):
    database: ComponentHealth
    storage: ComponentHealth
    openrouter: ComponentHealth
    nvidia: ComponentHealth
    tavily: ComponentHealth
    generation: ComponentHealth | None = Field(
        default=None,
        exclude_if=lambda v: v is None,
        description="Image/video generation on the naadi-gen Space: `configured: false` until HF_GEN_TOKEN is set. "
        "Never changes the overall status (a quota pause is expected, not an outage)",
    )
    backup: ComponentHealth | None = Field(
        default=None,
        exclude_if=lambda v: v is None,
        description="Encrypted backups to the private HF dataset (nightly database, weekly Storage): "
        "`configured: false` until HF_BACKUP_TOKEN and BACKUP_AGE_RECIPIENT are set, which leaves the overall "
        "status alone; a failed or stale backup degrades it",
    )
    searxng: ComponentHealth | None = Field(
        default=None,
        exclude_if=lambda v: v is None,
        description="Self-hosted SearXNG (search layer 8) on loopback; present where it is installed (the Space)",
    )
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
