"""Vault API (segment 17). See `vault.py` for the privacy rules: this module imports no model, search or
embeddings code, never logs a body, title or path, and answers with `Cache-Control: no-store`.

    POST   /api/vault/uploads        title, type, size -> a signed upload URL (the browser sends the bytes to Storage)
    POST   /api/vault/{id}/complete  checks the stored object's bytes (PDF, JPEG, PNG, HEIC), records size and hash
    GET    /api/vault                metadata only
    GET    /api/vault/{id}/url       a signed URL that works `pathway.signed_url_seconds`
    PATCH  /api/vault/{id}           title, type, expiry
    DELETE /api/vault/{id}           the object, then the row
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from ..auth import CurrentUser
from ..config_api.schemas import PathwayRules
from ..errors import ErrorResponse
from . import vault
from .db import PathwayError, as_service, as_user, database, limited, student_today
from .models import (
    VaultDocOut,
    VaultList,
    VaultPatch,
    VaultUploadOut,
    VaultUploadRequest,
    VaultUrlOut,
)

router = APIRouter(prefix="/api/vault", tags=["vault"])
NO_STORE = {"Cache-Control": "no-store"}

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Missing, expired or invalid access token"},
    429: {"model": ErrorResponse, "description": "Rate limited"},
    502: {"model": ErrorResponse, "description": "vault_storage_unavailable"},
    503: {"model": ErrorResponse, "description": "pathway_unavailable or vault_unavailable"},
}
_NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"model": ErrorResponse, "description": "vault_not_found"}}


def storage_of(request: Request) -> vault.VaultStorage:
    s: vault.VaultStorage | None = getattr(request.app.state, "vault_storage", None)
    if s is None:
        from .storage import SupabaseVaultStorage

        s = SupabaseVaultStorage(request.app.state.settings)
        request.app.state.vault_storage = s
    return s


async def rules_of(request: Request) -> PathwayRules:
    return PathwayRules.model_validate(await request.app.state.app_config.get("pathway"))


def _out(row: dict[str, Any], today: Any, cfg: PathwayRules) -> VaultDocOut:
    return VaultDocOut.model_validate(vault.shape(row, today=today, soon_days=cfg.expiry_soon_days))


def _json(model: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(model.model_dump(mode="json"), status_code=status, headers=NO_STORE)


@router.get(
    "",
    operation_id="listVault",
    summary="The student's vault documents: metadata only",
    response_model=VaultList,
    responses=_ERRORS,
)
async def list_vault(request: Request, user: Annotated[CurrentUser, limited("vault_read")]) -> JSONResponse:
    cfg = await rules_of(request)

    async def run(conn: psycopg.AsyncConnection[Any]) -> VaultList:
        today = await student_today(conn, user.user_id)
        docs = await vault.list_docs(conn, user.user_id)
        return VaultList(
            items=[_out(d, today, cfg) for d in docs],
            max_documents=cfg.vault_max_documents,
            max_bytes=cfg.vault_max_bytes,
            expiry_soon_days=cfg.expiry_soon_days,
            types=cfg.vault_types,
        )

    return _json(await as_service(database(request), run))


@router.post(
    "/uploads",
    operation_id="createVaultUpload",
    summary="Start an upload: a signed Storage URL for `vault/{user_id}/{id}` (at most 20 MB)",
    status_code=201,
    response_model=VaultUploadOut,
    responses={
        **_ERRORS,
        409: {"model": ErrorResponse, "description": "vault_limit or vault_id_conflict"},
        413: {"model": ErrorResponse, "description": "vault_file_too_large"},
        422: {"model": ErrorResponse, "description": "validation_error or vault_type_unknown"},
    },
)
async def create_upload(
    request: Request, body: VaultUploadRequest, user: Annotated[CurrentUser, limited("vault_write")]
) -> JSONResponse:
    cfg = await rules_of(request)
    storage = storage_of(request)
    doc_id = body.id or uuid.uuid4()

    async def run(conn: psycopg.AsyncConnection[Any]) -> VaultUploadOut:
        signed_url, token = await vault.begin_upload(
            conn,
            storage,
            user.user_id,
            doc_id=doc_id,
            title=body.title,
            doc_type=body.doc_type,
            mime=body.mime,
            size=body.bytes,
            expires_on=body.expires_on,
            cfg=cfg,
        )
        return VaultUploadOut(
            id=doc_id,
            bucket=vault.BUCKET,
            path=vault.object_path(user.user_id, doc_id),
            token=token,
            signed_url=signed_url,
        )

    return _json(await as_service(database(request), run), 201)


@router.post(
    "/{doc_id}/complete",
    operation_id="completeVaultUpload",
    summary="Finish an upload: the stored bytes are checked (PDF, JPEG, PNG or HEIC) and sized",
    response_model=VaultDocOut,
    responses={
        **_ERRORS,
        **_NOT_FOUND,
        409: {"model": ErrorResponse, "description": "vault_upload_missing (nothing arrived in Storage)"},
        413: {"model": ErrorResponse, "description": "vault_file_too_large"},
        415: {"model": ErrorResponse, "description": "vault_file_type (the file is deleted)"},
    },
)
async def complete_upload(
    request: Request, doc_id: uuid.UUID, user: Annotated[CurrentUser, limited("vault_write")]
) -> JSONResponse:
    cfg = await rules_of(request)
    storage = storage_of(request)

    async def run(conn: psycopg.AsyncConnection[Any]) -> VaultDocOut:
        today = await student_today(conn, user.user_id)
        row = await vault.complete_upload(conn, storage, user.user_id, doc_id, cfg)
        return _out(row, today, cfg)

    try:
        return _json(await as_service(database(request), run))
    except PathwayError as exc:
        if exc.code in ("vault_file_type", "vault_file_too_large"):
            # The row and object were removed inside the transaction that is now rolling back: do it again, committed.
            await _drop(request, user, doc_id, storage)
        raise


async def _drop(request: Request, user: CurrentUser, doc_id: uuid.UUID, storage: vault.VaultStorage) -> None:
    async def run(conn: psycopg.AsyncConnection[Any]) -> None:
        await vault.delete_doc(conn, storage, user.user_id, doc_id)

    await as_service(database(request), run)


@router.get(
    "/{doc_id}/url",
    operation_id="getVaultUrl",
    summary="A signed URL to open the document (works `signed_url_seconds`, never cached)",
    response_model=VaultUrlOut,
    responses={**_ERRORS, **_NOT_FOUND},
)
async def vault_url(
    request: Request, doc_id: uuid.UUID, user: Annotated[CurrentUser, limited("vault_url")]
) -> JSONResponse:
    cfg = await rules_of(request)
    storage = storage_of(request)

    async def run(conn: psycopg.AsyncConnection[Any]) -> bool:
        return await vault.get_doc(conn, user.user_id, doc_id) is not None

    if not await as_user(database(request), user, run):
        raise PathwayError(404, "vault_not_found")
    url = await storage.signed_url(vault.object_path(user.user_id, doc_id), cfg.signed_url_seconds)
    return _json(VaultUrlOut(url=url, expires_in=cfg.signed_url_seconds))


@router.patch(
    "/{doc_id}",
    operation_id="patchVaultDocument",
    summary="Rename a document, change its type or its expiry date",
    response_model=VaultDocOut,
    responses={**_ERRORS, **_NOT_FOUND, 422: {"model": ErrorResponse, "description": "vault_type_unknown"}},
)
async def patch_doc(
    request: Request, doc_id: uuid.UUID, body: VaultPatch, user: Annotated[CurrentUser, limited("vault_write")]
) -> JSONResponse:
    cfg = await rules_of(request)
    fields = body.model_dump(exclude_unset=True)
    if "doc_type" in fields:
        if fields["doc_type"] is None:
            raise PathwayError(422, "validation_error")
        vault.check_type(fields["doc_type"], cfg)
    if "title" in fields and fields["title"] is None:
        raise PathwayError(422, "validation_error")

    async def run(conn: psycopg.AsyncConnection[Any]) -> VaultDocOut:
        if await vault.get_doc(conn, user.user_id, doc_id) is None:
            raise PathwayError(404, "vault_not_found")
        assignments = ", ".join(f"{k} = %s" for k in fields)
        await conn.execute(
            f"update public.vault_documents set {assignments} where user_id = %s and id = %s",  # noqa: S608
            [*fields.values(), user.user_id, doc_id],
        )
        row = await vault.get_doc(conn, user.user_id, doc_id)
        assert row is not None
        return _out(row, await student_today(conn, user.user_id), cfg)

    return _json(await as_service(database(request), run))


@router.delete(
    "/{doc_id}",
    operation_id="deleteVaultDocument",
    summary="Delete the document: its Storage object first, then the row",
    status_code=204,
    responses={**_ERRORS, **_NOT_FOUND},
)
async def delete_doc(
    request: Request, doc_id: uuid.UUID, user: Annotated[CurrentUser, limited("vault_write")]
) -> Response:
    storage = storage_of(request)

    async def run(conn: psycopg.AsyncConnection[Any]) -> bool:
        return await vault.delete_doc(conn, storage, user.user_id, doc_id)

    if not await as_service(database(request), run):
        raise PathwayError(404, "vault_not_found")
    return Response(status_code=204, headers=NO_STORE)
