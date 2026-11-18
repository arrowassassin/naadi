"""Pathway API models (segment 17). Pydantic is the contract source: `pnpm gen:contracts` exports these to
packages/shared (OpenAPI -> zod). Responses carry structured fields only; the web words them with ui_copy
(`pathway.*`, `vault.*`, `costs.*`)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

TaskStatus = Literal["todo", "doing", "done", "na"]
ReverifyReason = Literal["age", "flagged"]
CheckStatus = Literal["unchanged", "changed", "unreachable", "moved"]
ExpiryState = Literal["none", "ok", "soon", "expired"]

_IN = ConfigDict(extra="forbid")
_OUT = ConfigDict(from_attributes=True)

StageKey = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]{1,64}$")]
TaskId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")]
Currency = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
Stamp = Annotated[int, Field(ge=0, le=10_000_000_000_000, description="Device clock, ms since the epoch")]
NOTES_MAX = 4000
LABEL_MAX = 200
URL_MAX = 500


class OfficialLink(BaseModel):
    label: str
    url: str


class DueRuleOut(BaseModel):
    """A seeded due rule as structure: `anchor` + `days` (negative = before), a fixed `date`, or `annual`."""

    kind: Literal["anchor", "date", "annual"]
    anchor: str | None = None
    days: int | None = None
    date: dt.date | None = None
    month: int | None = None
    day: int | None = None


class UserTaskOut(BaseModel):
    """The student's own state for one task."""

    task_id: str
    status: TaskStatus
    due: dt.date | None
    notes: str | None
    client_updated_at_ms: int | None
    updated_at: dt.datetime | None


class TaskOut(BaseModel):
    id: str
    sort: int
    title: str
    detail_md: str
    due_rule: DueRuleOut | None
    cost_usd: float | None
    official_url: str | None
    official: bool = Field(description="official_url is on an official domain (config search.include_domains)")
    last_verified: dt.date | None
    days_since_verified: int | None
    needs_review: bool
    reverify: ReverifyReason | None = Field(description="Why it shows Re-verify: 'age' or 'flagged'; null = fine")
    status: TaskStatus
    due: dt.date | None
    notes: str | None
    client_updated_at_ms: int | None
    updated_at: dt.datetime | None


class GuideOut(BaseModel):
    key: str
    title: str
    body_md: str


class StageOut(BaseModel):
    key: str
    sort: int
    title: str
    summary_md: str
    official_links: list[OfficialLink]
    last_verified: dt.date | None
    reverify: ReverifyReason | None
    done: int = Field(description="Tasks done or not needed")
    total: int
    reverify_count: int
    spend_planned_usd: float = Field(
        description="Sum of this stage's task cost estimates (USD), tasks not needed left out"
    )
    guide: GuideOut | None
    tasks: list[TaskOut]


class PathwayOut(BaseModel):
    today: dt.date
    reverify_days: int
    current_stage: str | None
    current_stage_source: Literal["chosen", "derived"] | None
    overview: GuideOut | None
    stages: list[StageOut]


class TaskPatch(BaseModel):
    """Last-writer-wins per row: send the fields you changed with your clock's stamp."""

    model_config = _IN
    status: TaskStatus | None = None
    due: dt.date | None = None
    notes: Annotated[str, Field(max_length=NOTES_MAX)] | None = None
    client_updated_at_ms: Stamp

    @model_validator(mode="after")
    def _something(self) -> TaskPatch:
        if not self.model_fields_set - {"client_updated_at_ms"}:
            raise ValueError("send at least one of status, due, notes")
        return self


class TaskPatchResult(BaseModel):
    task: UserTaskOut
    applied: bool = Field(description="false: a newer write was already stored; this is the stored row")


class StagePut(BaseModel):
    model_config = _IN
    stage_key: StageKey | None


class StageChosenOut(BaseModel):
    stage_key: str | None


# ── costs ──


class CostOut(BaseModel):
    id: uuid.UUID
    task_id: str | None
    stage_key: str | None
    label: str
    amount: str = Field(description="Decimal string, two places")
    currency: str
    paid_on: dt.date | None
    client_updated_at_ms: int | None
    created_at: dt.datetime
    updated_at: dt.datetime


class CostCreate(BaseModel):
    """`id` is made on the device, so replaying the same request is safe."""

    model_config = _IN
    id: uuid.UUID | None = None
    task_id: TaskId | None = None
    stage_key: StageKey | None = None
    label: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=LABEL_MAX)]
    amount: Annotated[float, Field(ge=0, le=1_000_000_000, allow_inf_nan=False)]
    currency: Currency
    paid_on: dt.date | None = None
    client_updated_at_ms: Stamp


class CostPatch(BaseModel):
    model_config = _IN
    task_id: TaskId | None = None
    stage_key: StageKey | None = None
    label: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=LABEL_MAX)] | None = None
    amount: Annotated[float, Field(ge=0, le=1_000_000_000, allow_inf_nan=False)] | None = None
    currency: Currency | None = None
    paid_on: dt.date | None = None
    client_updated_at_ms: Stamp

    @model_validator(mode="after")
    def _something(self) -> CostPatch:
        if not self.model_fields_set - {"client_updated_at_ms"}:
            raise ValueError("send at least one field to change")
        return self


class CostResult(BaseModel):
    cost: CostOut
    applied: bool


class CostList(BaseModel):
    items: list[CostOut]
    currencies: list[str] = Field(description="Currencies the student can add entries in")
    max_entries: int


class MoneyOut(BaseModel):
    currency: str
    amount: str


class StageCostOut(BaseModel):
    stage_key: str | None = Field(description="null: entries not filed under a stage")
    planned_usd: str = Field(description="Task cost estimates (USD) for this stage, tasks not needed left out")
    paid: list[MoneyOut]
    upcoming: list[MoneyOut] = Field(description="Entries with no paid date")
    entries: int


class NextFeeOut(BaseModel):
    id: uuid.UUID
    label: str
    amount: str
    currency: str
    stage_key: str | None


class CostSummary(BaseModel):
    currencies: list[str] = Field(description="Currencies the student can add entries in")
    planned_usd: str
    paid: list[MoneyOut]
    upcoming: list[MoneyOut]
    by_stage: list[StageCostOut]
    next_fee: NextFeeOut | None


# ── vault ──

DocType = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,31}$")]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=LABEL_MAX)]


class VaultDocOut(BaseModel):
    id: uuid.UUID
    title: str
    doc_type: str
    mime: str
    bytes: int
    expires_on: dt.date | None
    expires_in_days: int | None
    expiry: ExpiryState
    created_at: dt.datetime


class VaultList(BaseModel):
    items: list[VaultDocOut]
    max_documents: int
    max_bytes: int
    expiry_soon_days: int
    types: list[str]


class VaultUploadRequest(BaseModel):
    model_config = _IN
    id: uuid.UUID | None = None
    title: Title
    doc_type: DocType = "other"
    mime: Literal["application/pdf", "image/jpeg", "image/png", "image/heic", "image/heif"]
    bytes: Annotated[int, Field(ge=1, le=20 * 1024 * 1024)]
    expires_on: dt.date | None = None


class VaultUploadOut(BaseModel):
    id: uuid.UUID
    bucket: str
    path: str
    token: str = Field(description="Signed upload token for supabase-js uploadToSignedUrl")
    signed_url: str


class VaultPatch(BaseModel):
    model_config = _IN
    title: Title | None = None
    doc_type: DocType | None = None
    expires_on: dt.date | None = None

    @model_validator(mode="after")
    def _something(self) -> VaultPatch:
        if not self.model_fields_set:
            raise ValueError("send at least one field to change")
        return self


class VaultUrlOut(BaseModel):
    url: str
    expires_in: int


# ── admin ──


class CheckQuote(BaseModel):
    text: str = Field(max_length=300)
    url: str


class CheckFinding(BaseModel):
    status: CheckStatus
    summary_key: str | None = None
    differences: list[str] = []
    quotes: list[CheckQuote] = []
    still_accurate: bool | None = None


class CheckOut(BaseModel):
    id: int
    task_id: str
    task_title: str
    stage_key: str
    checked_at: dt.datetime
    url: str
    http_status: int | None
    changed: bool
    finding: CheckFinding
    resolved_at: dt.datetime | None
    last_verified: dt.date | None
    needs_review: bool


class CheckList(BaseModel):
    items: list[CheckOut]
    open_count: int


class TaskAdminPatch(BaseModel):
    model_config = _IN
    detail_md: Annotated[str, Field(min_length=1, max_length=20_000)] | None = None
    cost_usd: Annotated[float, Field(ge=0, le=1_000_000, allow_inf_nan=False)] | None = None
    official_url: Annotated[str, Field(pattern=r"^https://[^\s]+$", max_length=URL_MAX)] | None = None

    @model_validator(mode="after")
    def _something(self) -> TaskAdminPatch:
        if not self.model_fields_set:
            raise ValueError("send at least one field to change")
        return self


class AdminTaskOut(BaseModel):
    id: str
    detail_md: str
    cost_usd: float | None
    official_url: str | None
    last_verified: dt.date | None
    needs_review: bool
