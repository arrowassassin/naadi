"""Sync contract pieces shared by the LWW endpoints: the `sync_deleted` conflict and the stamp field."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..errors import ErrorBody, error_payload
from .lww import MAX_STAMP_MS

ClientStamp = Annotated[
    int,
    Field(
        ge=0,
        le=MAX_STAMP_MS,
        description="Offline sync: the device's time of this change (ms since the epoch). Last writer wins per row "
        "(clamped to server time + 5 min; a tie keeps the stored row). Omitted: stamped with the server's clock",
    ),
]

SYNC_DELETED_DESCRIPTION = (
    "sync_deleted: the row was deleted by a change at least as new as this one (`tombstone` carries the delete's "
    "stamp). The write is not applied. A client whose own change is newer re-creates the row with a create that "
    "carries its stamp"
)


class TombstoneOut(BaseModel):
    entity: str = Field(description="note, highlight, page, notebook, …")
    key: str = Field(description="The deleted row's id")
    client_updated_at_ms: int = Field(description="Stamp of the delete that won")


class SyncDeletedResponse(BaseModel):
    """The 409 body of an LWW endpoint: the usual error, plus `tombstone` when the code is `sync_deleted`."""

    error: ErrorBody
    tombstone: TombstoneOut | None = Field(default=None, description="Present when error.code is sync_deleted")


class SyncDeleted(Exception):
    def __init__(self, entity: str, key: str, stamp_ms: int) -> None:
        super().__init__("sync_deleted")
        self.tombstone = TombstoneOut(entity=entity, key=key, client_updated_at_ms=stamp_ms)


async def sync_deleted_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, SyncDeleted)
    payload: dict[str, Any] = error_payload(409, "sync_deleted", "sync_deleted")
    payload["tombstone"] = exc.tombstone.model_dump(mode="json")
    return JSONResponse(payload, status_code=409)


SYNC_DELETED_RESPONSE: dict[int | str, dict[str, Any]] = {
    409: {"model": SyncDeletedResponse, "description": SYNC_DELETED_DESCRIPTION}
}

__all__ = [
    "SYNC_DELETED_DESCRIPTION",
    "SYNC_DELETED_RESPONSE",
    "ClientStamp",
    "SyncDeleted",
    "SyncDeletedResponse",
    "TombstoneOut",
    "sync_deleted_handler",
]
