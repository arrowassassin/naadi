"""Request/response models of the planner screen, rebalance proposals, the calendar feed and Web Push.

Structured fields only: the client words every sentence from `ui_copy` (plan item titles are the plan template's own
text, verbatim, like everywhere else in the plan).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import PlanItemOut

RebalanceTrigger = Literal["missed", "student", "coach"]
RebalanceStatus = Literal["proposed", "accepted", "declined", "expired"]
StudentKind = Literal["read", "questions", "cards", "video", "admin"]
StudentRef = Literal["chapter", "qbank", "cards", "section", "block", "external"]
MilestoneKind = Literal["assessment", "phase", "rest", "exam"]


class WeekItemOut(PlanItemOut):
    behind: bool = Field(description="Planned or in progress on a day that has passed (word + amber on screen)")
    moved_from: dt.date | None = Field(description="Where a rebalance moved the item from")
    client_updated_at_ms: int | None


class WeekDayOut(BaseModel):
    date: dt.date
    weekday: str
    day_no: int | None = Field(description="null for a date outside the plan")
    phase: str | None
    chapter_ref: str | None
    topic: str | None
    target_hours: float | None
    planned_hours: float = Field(description="Sum of the items' minutes (skipped items excluded), in hours")
    assessment: bool
    rest: bool
    extra: bool
    in_plan: bool
    day_off: bool = Field(description="A date inside the plan's span that is not a study day")
    is_today: bool
    past: bool
    items: list[WeekItemOut]


class MilestoneOut(BaseModel):
    kind: MilestoneKind
    date: dt.date
    day_no: int | None
    title: str | None = Field(description="The plan template's own text for the assessment or phase")
    days_away: int = Field(description="From today (negative: already passed)")


class RebalanceMove(BaseModel):
    item_id: uuid.UUID
    from_day: dt.date
    to_day: dt.date
    sort: int
    kind: str
    title: str
    minutes: int | None


class RebalanceDrop(BaseModel):
    item_id: uuid.UUID
    day: dt.date
    reason: Literal["review"]
    kind: str
    title: str


class RebalanceWarning(BaseModel):
    code: Literal["unplaced", "assessment_missed", "no_study_days"]
    count: int
    item_ids: list[uuid.UUID]


class RebalanceProposalOut(BaseModel):
    moves: list[RebalanceMove]
    drops: list[RebalanceDrop]
    warnings: list[RebalanceWarning]
    missed: int
    spilled: int
    fingerprint: str


class RebalanceOut(BaseModel):
    id: uuid.UUID
    created_at: dt.datetime
    trigger: RebalanceTrigger
    status: RebalanceStatus
    applied_at: dt.datetime | None
    thread_id: str | None = Field(description="The planner run waiting for the confirm, when there is one")
    proposal: RebalanceProposalOut


class RebalancesOut(BaseModel):
    rebalances: list[RebalanceOut]


class RebalanceStartOut(BaseModel):
    thread_id: str = Field(description="The planner run proposes, then waits on this thread for the student's confirm")
    run_id: str


class WeekOut(BaseModel):
    plan_id: uuid.UUID
    start: dt.date
    end: dt.date
    week: int = Field(description="Plan week of `start` (1-based)")
    weeks: int
    today: dt.date
    exam: str
    exam_date: dt.date | None
    days_to_exam: int | None
    prev_start: dt.date | None
    next_start: dt.date | None
    hours_planned: float
    hours_target: float
    behind: int = Field(description="Items planned or in progress on days that have passed")
    days: list[WeekDayOut]
    milestones: list[MilestoneOut]
    next_item: WeekItemOut | None = Field(description="Today's first open item: the screen's one lime action")
    rebalance: RebalanceOut | None = Field(description="The proposal waiting for the student, if any")


class PlanWeekOut(BaseModel):
    """`week` is null for a student who has no plan yet (a normal state, so not an error response)."""

    week: WeekOut | None


class ItemPatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["planned", "done", "skipped"]
    client_updated_at_ms: int = Field(
        ge=0,
        le=9_999_999_999_999,
        description="When the student made the change (device clock, ms since the epoch); the server never lets a"
        " stamp run more than a few minutes ahead of its own clock",
    )


class ItemPatchOut(BaseModel):
    applied: bool = Field(description="false: a newer (or equal) change was already stored; `item` is that row")
    item: PlanItemOut


class StudentItemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    day: dt.date
    kind: StudentKind
    title: str = Field(min_length=1, max_length=200)
    target_minutes: int | None = Field(default=None, ge=5, le=720)
    target_count: int | None = Field(default=None, ge=1, le=400)
    ref_type: StudentRef | None = None
    ref_id: str | None = Field(default=None, max_length=100)

    @field_validator("title")
    @classmethod
    def _title(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("title is empty")
        return v


class StudentItemsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[StudentItemIn] = Field(min_length=1, max_length=50)


class StudentItemsOut(BaseModel):
    items: list[PlanItemOut]


class CalendarFeedOut(BaseModel):
    active: bool
    created_at: dt.datetime | None


class CalendarFeedCreatedOut(BaseModel):
    url: str = Field(description="Shown once: the token is stored only as a hash and cannot be shown again")
    created_at: dt.datetime


class PushKeyOut(BaseModel):
    configured: bool = Field(description="false until the owner provides VAPID keys (needs a public address)")
    public_key: str | None


class PushSubscriptionKeys(BaseModel):
    model_config = ConfigDict(extra="forbid")

    p256dh: str = Field(min_length=1, max_length=200)
    auth: str = Field(min_length=1, max_length=100)


class PushSubscriptionIn(BaseModel):
    """What `PushSubscription.toJSON()` gives (the browser's own object)."""

    model_config = ConfigDict(extra="ignore")

    endpoint: str = Field(min_length=12, max_length=1024, pattern=r"^https://")
    keys: PushSubscriptionKeys

    @field_validator("endpoint")
    @classmethod
    def _push_service(cls, v: str) -> str:
        from ..push.hosts import allowed_endpoint

        if not allowed_endpoint(v):
            raise ValueError("endpoint is not a known push service")
        return v


class PushSubscriptionOut(BaseModel):
    id: uuid.UUID
    ua_label: str | None
    created_at: dt.datetime
    last_ok_at: dt.datetime | None


class PushSubscriptionsOut(BaseModel):
    configured: bool
    subscriptions: list[PushSubscriptionOut]


class PushTestOut(BaseModel):
    sent: int
    removed: int
    failed: int
