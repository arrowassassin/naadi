"""Pathway API (segment 17).

    GET    /api/pathway                     stages, tasks, the student state, re-verify flags, links, guides
    PATCH  /api/pathway/tasks/{id}          status / due / notes, last-writer-wins on `client_updated_at_ms`
    PUT    /api/me/pathway-stage            the stage the student is in (null: let Naadi derive it)
    GET    /api/costs                       the student's cost entries
    POST   /api/costs                       add an entry (the client's id makes a replay safe)
    PATCH  /api/costs/{id}                  last-writer-wins
    DELETE /api/costs/{id}                  idempotent
    GET    /api/costs/summary               totals by stage and by currency (no conversion), planned vs paid

The student's progress and costs are written by the API (migration 0029: clients can only read their rows). Shared
facts (`last_verified`, `needs_review`) change only through the admin routes. Notes, labels and amounts are never
logged. Errors carry a stable `code` only.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Header, Query, Request, Response
from fastapi.responses import JSONResponse

from ..auth import CurrentUser
from ..config_api.schemas import PathwayRules
from ..errors import ErrorResponse
from ..llm_config import RuntimeConfig, load_yaml_sections
from . import store
from .db import (
    PathwayError,
    as_service,
    as_user,
    database,
    limited,
    now_ms,
    student_today,
)
from .models import (
    CostCreate,
    CostList,
    CostOut,
    CostPatch,
    CostResult,
    CostSummary,
    PathwayOut,
    StageChosenOut,
    StagePut,
    TaskId,
    TaskPatch,
    TaskPatchResult,
    UserTaskOut,
)

router = APIRouter(prefix="/api", tags=["pathway"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Missing, expired or invalid access token"},
    429: {"model": ErrorResponse, "description": "Rate limited"},
    503: {"model": ErrorResponse, "description": "Database unavailable (code pathway_unavailable)"},
}
_WRITE: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "pathway_task_not_found, pathway_stage_not_found or cost_not_found"},
    422: {"model": ErrorResponse, "description": "validation_error or cost_currency_not_offered"},
}


# ── config ──


async def pathway_rules(request: Request) -> PathwayRules:
    return PathwayRules.model_validate(await request.app.state.app_config.get("pathway"))


async def official_domains(request: Request) -> list[str]:
    """`search.include_domains` of config/models.yaml (or its app_config override): the hosts Naadi calls official."""
    rc: RuntimeConfig | None = getattr(request.app.state, "pathway_runtime", None)
    if rc is None:
        rc = RuntimeConfig(database(request), request.app.state.settings.config_dir)
        request.app.state.pathway_runtime = rc
    try:
        return list((await rc.get()).search.include_domains)
    except Exception:  # noqa: BLE001 - the list is a label, not a gate: fall back to the shipped file
        sections = load_yaml_sections(request.app.state.settings.config_dir)
        return list(sections["search"].get("include_domains", []))


# ── pathway ──


@router.get(
    "/pathway",
    operation_id="getPathway",
    summary="The whole pathway: stages and tasks with the student's state, re-verify flags and official links",
    response_model=PathwayOut,
    responses=_ERRORS,
)
async def get_pathway(request: Request, user: Annotated[CurrentUser, limited("pathway_read")]) -> PathwayOut:
    rules_ = await pathway_rules(request)
    domains = await official_domains(request)

    async def run(conn: psycopg.AsyncConnection[Any]) -> PathwayOut:
        stages, tasks, guides, profile = await store.load_pathway(conn)
        today = await student_today(conn, user.user_id)
        return PathwayOut.model_validate(
            store.shape_pathway(
                stages,
                tasks,
                guides,
                profile,
                today=today,
                reverify_days=rules_.reverify_days,
                official_domains=domains,
            )
        )

    return await as_user(database(request), user, run)


@router.patch(
    "/pathway/tasks/{task_id}",
    operation_id="patchPathwayTask",
    summary="Set a task's status, due date or notes. Last writer wins by `client_updated_at_ms`",
    response_model=TaskPatchResult,
    responses={**_ERRORS, **_WRITE},
)
async def patch_task(
    request: Request,
    task_id: TaskId,
    body: TaskPatch,
    user: Annotated[CurrentUser, limited("pathway_write")],
    idempotency_key: Annotated[str | None, Header(max_length=100)] = None,
) -> TaskPatchResult:
    fields = body.model_dump(exclude_unset=True, exclude={"client_updated_at_ms"})
    if "notes" in fields and fields["notes"] is not None:
        fields["notes"] = fields["notes"].strip() or None

    async def run(conn: psycopg.AsyncConnection[Any]) -> TaskPatchResult:
        row, applied = await store.patch_user_task(
            conn, user.user_id, task_id, fields, body.client_updated_at_ms, now_ms()
        )
        return TaskPatchResult(task=UserTaskOut.model_validate(row), applied=applied)

    return await as_service(database(request), run)


@router.put(
    "/me/pathway-stage",
    operation_id="putPathwayStage",
    summary="The stage the student is in now (null: derive it from the first stage with open tasks)",
    response_model=StageChosenOut,
    responses={**_ERRORS, **_WRITE},
)
async def put_stage(
    request: Request, body: StagePut, user: Annotated[CurrentUser, limited("pathway_write")]
) -> StageChosenOut:
    async def run(conn: psycopg.AsyncConnection[Any]) -> StageChosenOut:
        await store.set_current_stage(conn, user.user_id, body.stage_key)
        return StageChosenOut(stage_key=body.stage_key)

    return await as_service(database(request), run)


# ── costs ──


def _cost(row: dict[str, Any]) -> CostOut:
    return CostOut.model_validate(row)


async def _currency_ok(request: Request, currency: str) -> PathwayRules:
    rules_ = await pathway_rules(request)
    if currency not in rules_.currencies:
        raise PathwayError(422, "cost_currency_not_offered")
    return rules_


def _dec(amount: float) -> Decimal:
    return Decimal(str(amount)).quantize(Decimal("0.01"))


@router.get(
    "/costs",
    operation_id="listCosts",
    summary="The student's cost entries, newest first",
    response_model=CostList,
    responses=_ERRORS,
)
async def list_costs(request: Request, user: Annotated[CurrentUser, limited("costs_read")]) -> CostList:
    rules_ = await pathway_rules(request)

    async def run(conn: psycopg.AsyncConnection[Any]) -> CostList:
        return CostList(
            items=[_cost(r) for r in await store.list_costs(conn, user.user_id)],
            currencies=rules_.currencies,
            max_entries=rules_.max_costs_per_user,
        )

    return await as_service(database(request), run)


@router.get(
    "/costs/summary",
    operation_id="getCostSummary",
    summary="Totals by stage and by currency (no conversion), planned (task estimates, USD) vs paid",
    response_model=CostSummary,
    responses=_ERRORS,
)
async def cost_summary(request: Request, user: Annotated[CurrentUser, limited("costs_read")]) -> CostSummary:
    rules_ = await pathway_rules(request)

    async def run(conn: psycopg.AsyncConnection[Any]) -> CostSummary:
        stages, planned, entries = await store.cost_summary_rows(conn, user.user_id)
        return CostSummary.model_validate(store.summarise_costs(stages, planned, entries, rules_.currencies))

    return await as_service(database(request), run)


@router.post(
    "/costs",
    operation_id="createCost",
    summary="Add a cost entry. A replay with the same id applies last-writer-wins and returns 200",
    status_code=201,
    response_model=CostResult,
    responses={
        **_ERRORS,
        **_WRITE,
        200: {"model": CostResult, "description": "Replayed: the stored entry"},
        409: {"model": ErrorResponse, "description": "cost_id_conflict or cost_limit"},
    },
)
async def create_cost(
    request: Request,
    body: CostCreate,
    user: Annotated[CurrentUser, limited("costs_write")],
    idempotency_key: Annotated[str | None, Header(max_length=100)] = None,
) -> JSONResponse:
    rules_ = await _currency_ok(request, body.currency)

    async def run(conn: psycopg.AsyncConnection[Any]) -> JSONResponse:
        row, created, applied = await store.create_cost(
            conn,
            user.user_id,
            cost_id=body.id or uuid.uuid4(),
            task_id=body.task_id,
            stage_key=body.stage_key,
            label=body.label,
            amount=_dec(body.amount),
            currency=body.currency,
            paid_on=body.paid_on,
            stamp=body.client_updated_at_ms,
            now_ms=now_ms(),
            max_costs=rules_.max_costs_per_user,
        )
        out = CostResult(cost=_cost(row), applied=applied)
        return JSONResponse(out.model_dump(mode="json"), status_code=201 if created else 200)

    return await as_service(database(request), run)


@router.patch(
    "/costs/{cost_id}",
    operation_id="patchCost",
    summary="Change a cost entry. Last writer wins by `client_updated_at_ms`",
    response_model=CostResult,
    responses={**_ERRORS, **_WRITE},
)
async def patch_cost(
    request: Request,
    cost_id: uuid.UUID,
    body: CostPatch,
    user: Annotated[CurrentUser, limited("costs_write")],
    idempotency_key: Annotated[str | None, Header(max_length=100)] = None,
) -> CostResult:
    if body.currency is not None:
        await _currency_ok(request, body.currency)
    fields = body.model_dump(exclude_unset=True, exclude={"client_updated_at_ms"})
    if fields.get("amount") is not None:
        fields["amount"] = _dec(fields["amount"])
    for required in ("label", "amount", "currency"):
        if required in fields and fields[required] is None:
            raise PathwayError(422, "validation_error")

    async def run(conn: psycopg.AsyncConnection[Any]) -> CostResult:
        row, applied = await store.patch_cost(conn, user.user_id, cost_id, fields, body.client_updated_at_ms, now_ms())
        return CostResult(cost=_cost(row), applied=applied)

    return await as_service(database(request), run)


@router.delete(
    "/costs/{cost_id}",
    operation_id="deleteCost",
    summary="Delete an entry. 204 (also when it is already gone); 200 with the stored entry when a newer edit than "
    "`client_updated_at_ms` keeps it",
    status_code=204,
    responses={**_ERRORS, 200: {"model": CostResult, "description": "A newer edit exists: not deleted"}},
)
async def delete_cost(
    request: Request,
    cost_id: uuid.UUID,
    user: Annotated[CurrentUser, limited("costs_write")],
    client_updated_at_ms: Annotated[int | None, Query(ge=0, le=10_000_000_000_000)] = None,
    idempotency_key: Annotated[str | None, Header(max_length=100)] = None,
) -> Response:
    async def run(conn: psycopg.AsyncConnection[Any]) -> dict[str, Any] | None:
        return await store.delete_cost(conn, user.user_id, cost_id, client_updated_at_ms, now_ms())

    kept = await as_service(database(request), run)
    if kept is not None:
        return JSONResponse(CostResult(cost=_cost(kept), applied=False).model_dump(mode="json"))
    return Response(status_code=204)
