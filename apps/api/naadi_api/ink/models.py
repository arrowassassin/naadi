"""Ink models: the `InkItem` mirror of packages/shared/src/ink.ts, the ink sync API and `profiles.settings.ink`.

The zod schema in @naadi/shared is the source for the item shape (segment 12a came first); these models mirror it
field for field and a shared fixture set (packages/shared/fixtures/ink/items.json) must pass/fail identically in
both (tests/ink/test_models.py and packages/shared/src/ink.fixtures.test.ts). The parity rules that differ from
pydantic's defaults:

- strict types: no "1" -> 1, no true -> 1, no 1 -> true (zod coerces nothing);
- an integer is any JSON number without a fraction (zod's `.int()`: 3.0 is an integer, 3.5 is not);
- no NaN / Infinity (zod's number rejects both);
- string lengths count code points, as zod 4 does (an emoji is 1, not JavaScript's `.length` of 2).
"""

from __future__ import annotations

import math
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    TypeAdapter,
)

# Kept equal to packages/shared/src/ink.ts (tests/ink/test_models.py compares them with ink.schema.json).
INK_SCHEMA_VERSION = 1
INK_PRECISION = 5
INK_MAX_POINTS = 10_000
INK_MAX_ITEMS = 20_000
INK_MAX_TEXT = 2_000
UUID_V7_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
JS_MAX_SAFE_INTEGER = 9_007_199_254_740_991

SurfaceType = Literal["section", "question", "flashcard", "pdf_page", "notebook_page", "exam_scratch"]
InkColor = Literal["ink", "violet", "lime", "amber"]
InkShape = Literal["line", "arrow", "circle", "ellipse", "rect"]

_ITEM_CONFIG = ConfigDict(strict=True, extra="forbid", allow_inf_nan=False)


def _js_number(v: Any) -> Any:
    """A JSON number as zod sees it: int or float, never bool, never NaN/Infinity."""
    if isinstance(v, bool) or not isinstance(v, int | float):
        raise ValueError("expected a number")
    if isinstance(v, float) and not math.isfinite(v):
        raise ValueError("expected a finite number")
    return v


def _js_int(v: Any) -> int:
    v = _js_number(v)
    if isinstance(v, float):
        if not v.is_integer():
            raise ValueError("expected an integer")
        v = int(v)
    if abs(v) > JS_MAX_SAFE_INTEGER:
        raise ValueError("expected a safe integer")
    return int(v)


JsNumber = Annotated[float, BeforeValidator(_js_number), Field(strict=False)]
JsInt = Annotated[int, BeforeValidator(_js_int)]
Su = Annotated[JsNumber, Field(ge=-10_000, le=10_000)]
EpochMs = Annotated[JsInt, Field(ge=0)]
InkId = Annotated[str, Field(pattern=UUID_V7_PATTERN)]
Pressure = Annotated[JsNumber, Field(ge=0, le=1)]
PointT = Annotated[JsInt, Field(ge=0, le=86_400_000)]
# JSON arrays arrive as lists; strict mode would only take a Python tuple (the elements stay checked above).
InkPoint = Annotated[tuple[Su, Su, Pressure, PointT], Field(strict=False)]


class InkAnchor(BaseModel):
    """Text-anchored placement: a ProseMirror document position plus an optional su offset."""

    model_config = _ITEM_CONFIG
    pm: Annotated[JsInt, Field(ge=0)]
    # Optional but never null, like zod's `.optional()`: an explicit null fails (defaults are not validated).
    dx: Su = None  # type: ignore[assignment]
    dy: Su = None  # type: ignore[assignment]


class _ItemBase(BaseModel):
    model_config = _ITEM_CONFIG
    id: InkId
    color: InkColor
    anchor: InkAnchor = None  # type: ignore[assignment]
    createdAt: EpochMs
    updatedAt: EpochMs
    deleted: Literal[True] = None  # type: ignore[assignment]


class InkStroke(_ItemBase):
    tool: Literal["pen", "marker"]
    size: Annotated[JsNumber, Field(gt=0, le=0.1)]
    points: Annotated[list[InkPoint], Field(min_length=1, max_length=INK_MAX_POINTS)]
    shape: InkShape = None  # type: ignore[assignment]


class InkTextBox(_ItemBase):
    tool: Literal["text"]
    size: Annotated[JsNumber, Field(gt=0, le=0.2)]
    x: Su
    y: Su
    w: Annotated[JsNumber, Field(gt=0, le=4)]
    text: Annotated[str, Field(min_length=1, max_length=INK_MAX_TEXT)]  # code points, like zod 4


InkItem = Annotated[InkStroke | InkTextBox, Field(discriminator="tool")]
INK_ITEM: TypeAdapter[InkStroke | InkTextBox] = TypeAdapter(InkItem)


def parse_item(raw: Any) -> InkStroke | InkTextBox:
    """Validate one item exactly as `InkItemSchema.parse` does (raises pydantic.ValidationError).

    The API stores and returns the item as sent (the validated JSON object, not a re-serialisation), so the bytes a
    client wrote are the bytes every replica merges: a model dump would turn 1 into 1.0."""
    return INK_ITEM.validate_python(raw)


# ── API ──

MAX_CHANGE_ITEMS = 500
MAX_CHANGE_BYTES = 2 * 1024 * 1024

RejectReason = Literal["invalid", "stale", "duplicate"]


class InkLayerOut(BaseModel):
    layer_id: str | None = Field(description="null until the first item is saved on this surface")
    rev: int = Field(description="Sync cursor: send it back as `since` (GET) or `base_rev` (POST)")
    items: list[dict[str, Any]] = Field(
        description="InkItem objects (@naadi/shared InkItemSchema). A full load omits tombstones; `since` includes them"
    )
    reset: bool = Field(
        description="true when `since` is older than the tombstone horizon: `items` is then a full load and the "
        "client must replace its replica (keeping its own unsent changes) instead of merging"
    )
    hash: str | None = Field(description="sha256 of every id:updatedAt:deleted sorted by id (convergence check)")
    item_count: int = Field(description="Live (not deleted) items on the surface")
    outdated: int = Field(
        description="Live items drawn on an earlier revision of the surface content (section/question/card text)"
    )
    server_now: int = Field(description="Server clock, epoch ms")


class InkChangesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_rev: int = Field(ge=0, description="The `rev` this client last merged")
    items: list[Any] = Field(
        max_length=MAX_CHANGE_ITEMS,
        description=f"Upserts and tombstones (InkItem objects), at most {MAX_CHANGE_ITEMS} and 2 MB per request",
    )


class InkRejected(BaseModel):
    id: str | None = Field(description="The item id as sent, when it had one")
    reason: RejectReason = Field(
        description="invalid: not a valid InkItem. stale: the server holds a newer or equal version (it is in "
        "`remote`). duplicate: the same id was sent twice in this request (the newer copy was used)"
    )


class InkChangesOut(BaseModel):
    layer_id: str
    rev: int
    applied: list[str] = Field(description="Ids written by this request")
    rejected: list[InkRejected]
    remote: list[dict[str, Any]] = Field(
        description="Items with seq > base_rev (other devices' changes, tombstones included) plus the server's "
        "version of every stale item; apply them with InkEngine.applyRemote"
    )
    reset: bool = Field(description="base_rev is older than the tombstone horizon: `remote` is a full load")
    hash: str | None
    item_count: int
    server_now: int


class InkSettings(BaseModel):
    """`profiles.settings.ink` (Settings -> Pencil & ink; the toolbar remembers the last tool and colour)."""

    model_config = ConfigDict(extra="forbid", strict=True)
    pen_only: bool = Field(default=True, description="Only Apple Pencil draws; fingers scroll and tap")
    predict: bool = Field(default=True, description="Predicted ink ahead of the pen (lower perceived latency)")
    tool: Literal["pen", "marker", "eraser", "lasso", "shapes", "text"] | None = None
    color: InkColor | None = None


__all__ = [
    "INK_ITEM",
    "INK_MAX_ITEMS",
    "INK_MAX_POINTS",
    "INK_MAX_TEXT",
    "INK_PRECISION",
    "INK_SCHEMA_VERSION",
    "MAX_CHANGE_BYTES",
    "MAX_CHANGE_ITEMS",
    "InkAnchor",
    "InkChangesIn",
    "InkChangesOut",
    "InkColor",
    "InkLayerOut",
    "InkRejected",
    "InkSettings",
    "InkStroke",
    "InkTextBox",
    "SurfaceType",
    "parse_item",
]
