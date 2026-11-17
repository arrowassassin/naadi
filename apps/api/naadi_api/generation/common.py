"""Shared pieces of the generation service and the admin approval (no imports of either)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .models import AdminGeneratedItem, GeneratedItem

SOURCE_ID = "naadi-ai-generated"

COLUMNS = (
    "id, user_id, kind, status, title, prompt, model, storage_path, width, height, duration_s, error, paused_until, "
    "approved_for_publish, media_asset_id, created_at"
)


class GenerationError(Exception):
    """A refusal or failure with a stable `code` the client words with ui_copy (`generation.error.<code>`)."""

    def __init__(self, status: int, code: str, *, resets_at: datetime | None = None) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.resets_at = resets_at


def row_to_item(row: dict[str, Any], url: str | None = None) -> GeneratedItem:
    return GeneratedItem(
        id=row["id"],
        kind=row["kind"],
        status=row["status"],
        title=row["title"],
        prompt=row["prompt"],
        model=row["model"],
        width=row["width"],
        height=row["height"],
        duration_s=float(row["duration_s"]) if row["duration_s"] is not None else None,
        url=url,
        error=row["error"],
        paused_until=row["paused_until"],
        published=bool(row["approved_for_publish"]),
        created_at=row["created_at"],
    )


def row_to_admin_item(row: dict[str, Any], url: str | None = None) -> AdminGeneratedItem:
    return AdminGeneratedItem(**row_to_item(row, url).model_dump(), user_ref=str(row["user_id"])[:8])
