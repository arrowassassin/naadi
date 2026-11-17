"""Pickers and the audit trail for the admin console (segment 19): sources, taxonomy, the `admin_audit` log, and
`app_config` revert (the editor itself is S05's `config_api`; a revert is a validated PUT of a history value)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Any, Literal

import psycopg
from fastapi import APIRouter, HTTPException, Path, Query, Request
from psycopg import AsyncConnection
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..config_api.routes import KEY_PATTERN, ConfigEntry, _auditor, _entry
from ..config_api.schemas import KEY_SPECS
from ..config_api.service import AppConfigService, CrossKeyInvalid, StaleWrite
from ..errors import FieldError, error_response
from .common import ADMIN_ERRORS, CONFLICT, INVALID, NOT_FOUND, ConsoleAdmin, one, rows, run_tx
from .licence import SourceTerms, load_policy, usable_for

router = APIRouter(prefix="/api/admin", tags=["admin"])


class AdminSource(BaseModel):
    id: str
    name: str
    license: str
    url: str | None
    allow_copy: bool
    allow_modify: bool
    allow_commercial: bool
    allow_ai_ingest: bool
    verified: bool
    usable_for: list[Literal["host", "embed", "page", "text"]] = Field(
        description="Uses the licence gates allow: host = store a file, embed = YouTube player, page = in a chapter"
        " page, text = a question or chapter written from it"
    )


class AdminSourceList(BaseModel):
    items: list[AdminSource]


class TaxonomyNode(BaseModel):
    id: str
    kind: str
    parent_id: str | None
    label: str
    exam: list[str]


class TaxonomyList(BaseModel):
    items: list[TaxonomyNode]


class AuditEntry(BaseModel):
    id: int
    actor_user_id: uuid.UUID | None
    action: str
    ref_type: str
    ref_id: str
    note: str | None
    at: dt.datetime


class AuditList(BaseModel):
    items: list[AuditEntry]


class ConfigRevert(BaseModel):
    model_config = ConfigDict(extra="forbid")
    history_id: int = Field(ge=1)
    expected_updated_at: dt.datetime | None


@router.get(
    "/sources",
    operation_id="listAdminSources",
    summary="Admin only: content sources and their licence terms",
    response_model=AdminSourceList,
    responses=ADMIN_ERRORS,
)
async def list_sources(request: Request, admin: ConsoleAdmin) -> AdminSourceList:
    async def run(conn: AsyncConnection[Any]) -> AdminSourceList:
        found = await rows(
            conn,
            "select id, name, license, url, allow_copy, allow_modify, allow_commercial, allow_ai_ingest,"
            " verified from public.sources order by id",
        )
        policy = await load_policy(conn)
        return AdminSourceList(
            items=[AdminSource(**r, usable_for=usable_for(SourceTerms.of(r), policy)) for r in found]
        )

    return await run_tx(request, run)


@router.get(
    "/taxonomy",
    operation_id="listAdminTaxonomy",
    summary="Admin only: taxonomy nodes for the tag pickers",
    response_model=TaxonomyList,
    responses=ADMIN_ERRORS,
)
async def list_taxonomy(
    request: Request, admin: ConsoleAdmin, exam: Annotated[Literal["step1", "step2ck", "step3"] | None, Query()] = None
) -> TaxonomyList:
    async def run(conn: AsyncConnection[Any]) -> TaxonomyList:
        sql = "select id, kind, parent_id, label, exam from public.taxonomy_nodes"
        found = await rows(
            conn, sql + (" where %(exam)s = any(exam)" if exam else "") + " order by sort, id", {"exam": exam}
        )
        return TaxonomyList(items=[TaxonomyNode(**r) for r in found])

    return await run_tx(request, run)


@router.get(
    "/audit",
    operation_id="listAdminAudit",
    summary="Admin only: the admin audit trail, newest first",
    response_model=AuditList,
    responses=ADMIN_ERRORS,
)
async def list_audit(
    request: Request,
    admin: ConsoleAdmin,
    ref_type: Annotated[str | None, Query(pattern=r"^[a-z][a-z0-9_]{0,47}$")] = None,
    ref_id: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AuditList:
    async def run(conn: AsyncConnection[Any]) -> AuditList:
        sql = "select id, actor_user_id, action, ref_type, ref_id, note, at from public.admin_audit where true"
        if ref_type:
            sql += " and ref_type = %(t)s"
        if ref_id:
            sql += " and ref_id = %(i)s"
        found = await rows(
            conn, sql + " order by at desc, id desc limit %(limit)s", {"t": ref_type, "i": ref_id, "limit": limit}
        )
        return AuditList(items=[AuditEntry(**r) for r in found])

    return await run_tx(request, run)


@router.post(
    "/config/{key}/revert",
    operation_id="revertAppConfig",
    summary="Admin only: restore an app_config value from its history (validated like any save)",
    response_model=ConfigEntry,
    responses={**ADMIN_ERRORS, **NOT_FOUND, **CONFLICT, **INVALID},
)
async def revert_config(
    request: Request, admin: ConsoleAdmin, key: Annotated[str, Path(pattern=KEY_PATTERN)], body: ConfigRevert
) -> Any:
    if key not in KEY_SPECS:
        raise HTTPException(404, f"Unknown config key {key!r}.")

    async def load(conn: AsyncConnection[Any]) -> dict[str, Any] | None:
        return await one(
            conn, "select value from public.app_config_history where id = %s and key = %s", (body.history_id, key)
        )

    hist = await run_tx(request, load)
    if hist is None:
        raise HTTPException(404, "No such history entry.")
    svc: AppConfigService = request.app.state.app_config
    try:
        row = await svc.put(
            key,
            hist["value"],
            expected_updated_at=body.expected_updated_at,
            user_id=admin.user_id,
            on_saved=_auditor(admin, key, "config.revert", str(body.history_id)),
        )
    except ValidationError as exc:
        # An old value can be invalid under today's schema (a key was added since): refuse with the paths.
        details = [
            FieldError(
                loc=["history", *(p for p in e["loc"] if isinstance(p, str | int))], msg=e["msg"], type=e["type"]
            )
            for e in exc.errors()
        ]
        return error_response(
            422, f"This earlier value is not valid for {key} any more.", "validation_error", details=details[:50]
        )
    except CrossKeyInvalid as exc:
        return error_response(
            422, str(exc), "validation_error", details=[FieldError(loc=["history"], msg=str(exc), type="value_error")]
        )
    except StaleWrite as exc:
        raise HTTPException(409, "This setting was changed by someone else. Reload it and try again.") from exc
    except (psycopg.OperationalError, TimeoutError) as exc:
        raise HTTPException(503, "The database cannot be reached right now. Try again in a moment.") from exc

    return _entry(row)
