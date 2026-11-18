"""Server-side rasterization of a layer's ink for handwriting OCR (Pillow).

Only the ink is drawn: black strokes on a white page, nothing of the section, question or card underneath, and no
typed text (text boxes are already text; `split_items` hands them to the caller, who adds them to the recognised
text without sending them anywhere). Marker strokes are highlights over content, not writing, and are skipped.

Geometry (packages/shared/src/ink.ts): coordinates are surface units (su), where the surface's content width is 1.0
on both axes. The page is drawn `width_px` (1600) wide: su x in [min(0, ink), max(1, ink)] maps across it, so a
normal page keeps its proportions and ink that strays outside the column is still on the image. Vertically only the
bands that hold ink are drawn, packed into tiles of at most `max_tile_px` (2048) px; empty stretches between bands
are skipped, and a band taller than a tile is cut. At most `max_tiles` tiles are made, in reading order; the rest is
reported as `truncated`. `plan_tiles` is pure (no Pillow), so the planner is unit-tested on its own.
"""

from __future__ import annotations

import io
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from PIL import Image, ImageDraw

WIDTH_PX = 1600
MAX_TILE_PX = 2048
MIN_TILE_PX = 64
PAD_SU = 0.015  # around each band, so descenders and stroke width stay on the tile


@dataclass(frozen=True)
class Box:
    """An item's bounds in su (stroke width included)."""

    x0: float
    y0: float
    x1: float
    y1: float


@dataclass(frozen=True)
class Tile:
    y0: float  # su
    y1: float  # su
    height_px: int


@dataclass(frozen=True)
class Plan:
    x0: float  # su at pixel x = 0
    scale: float  # px per su
    width_px: int
    tiles: tuple[Tile, ...]
    truncated: bool  # ink below the last tile was left out (more than max_tiles needed)


def _bands(boxes: Sequence[Box], pad: float) -> list[tuple[float, float]]:
    """Vertical intervals that hold ink, overlapping ones merged, top to bottom."""
    spans = sorted((b.y0 - pad, b.y1 + pad) for b in boxes)
    out: list[tuple[float, float]] = []
    for y0, y1 in spans:
        if out and y0 <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], y1))
        else:
            out.append((y0, y1))
    return out


def plan_tiles(
    boxes: Sequence[Box],
    *,
    width_px: int = WIDTH_PX,
    max_tile_px: int = MAX_TILE_PX,
    max_tiles: int = 4,
    pad_su: float = PAD_SU,
) -> Plan | None:
    """Tiles covering every ink band (see the module docstring). None when there is nothing to draw."""
    boxes = [b for b in boxes if all(math.isfinite(v) for v in (b.x0, b.y0, b.x1, b.y1))]
    if not boxes or max_tiles < 1:
        return None
    x0 = min(0.0, min(b.x0 for b in boxes) - pad_su)
    x1 = max(1.0, max(b.x1 for b in boxes) + pad_su)
    scale = width_px / (x1 - x0)
    tile_su = max_tile_px / scale
    tiles: list[tuple[float, float]] = []
    for b0, b1 in _bands(boxes, pad_su):
        start = b0
        if tiles and b1 - tiles[-1][0] <= tile_su:
            # The band fits on the current tile (with the gap before it): extend the tile.
            tiles[-1] = (tiles[-1][0], b1)
            continue
        while b1 - start > tile_su:  # a band taller than a tile: cut it
            tiles.append((start, start + tile_su))
            start += tile_su
        tiles.append((start, b1))
    truncated = len(tiles) > max_tiles
    out = tuple(
        Tile(y0, y1, max(MIN_TILE_PX, min(max_tile_px, math.ceil((y1 - y0) * scale)))) for y0, y1 in tiles[:max_tiles]
    )
    return Plan(x0=x0, scale=scale, width_px=width_px, tiles=out, truncated=truncated)


# ── Items ──


@dataclass(frozen=True)
class Stroke:
    points: tuple[tuple[float, float, float], ...]  # x, y, pressure
    size: float
    box: Box


@dataclass(frozen=True)
class TextLine:
    x: float
    y: float
    text: str


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, int | float):
        return None
    f = float(v)
    return f if math.isfinite(f) else None


def split_items(items: Iterable[Mapping[str, Any]]) -> tuple[list[Stroke], list[TextLine]]:
    """Live pen strokes (shapes included) to draw, and text boxes to read as text. Markers, tombstones and anything
    malformed are left out (the items were validated on save; this is defence in depth)."""
    strokes: list[Stroke] = []
    texts: list[TextLine] = []
    for it in items:
        if not isinstance(it, Mapping) or it.get("deleted"):
            continue
        tool = it.get("tool")
        if tool == "text":
            x, y, text = _num(it.get("x")), _num(it.get("y")), it.get("text")
            if x is not None and y is not None and isinstance(text, str) and text.strip():
                texts.append(TextLine(x, y, text.strip()))
            continue
        if tool != "pen":
            continue
        size = _num(it.get("size")) or 0.002
        pts: list[tuple[float, float, float]] = []
        for p in it.get("points") or []:
            if isinstance(p, list | tuple) and len(p) >= 3:
                x, y, pr = _num(p[0]), _num(p[1]), _num(p[2])
                if x is not None and y is not None:
                    pts.append((x, y, pr if pr is not None else 0.5))
        if not pts:
            continue
        half = size / 2
        box = Box(
            min(p[0] for p in pts) - half,
            min(p[1] for p in pts) - half,
            max(p[0] for p in pts) + half,
            max(p[1] for p in pts) + half,
        )
        strokes.append(Stroke(tuple(pts), size, box))
    return strokes, texts


def text_box_lines(texts: Sequence[TextLine]) -> list[str]:
    """Typed text boxes in reading order (rows of 0.02 su, then x)."""
    return [t.text for t in sorted(texts, key=lambda t: (math.floor(t.y / 0.02), t.x))]


def render_tile(strokes: Sequence[Stroke], plan: Plan, tile: Tile) -> bytes:
    """One tile as a PNG: black ink on white, `plan.width_px` x `tile.height_px`, no metadata."""
    img = Image.new("L", (plan.width_px, tile.height_px), 255)
    draw = ImageDraw.Draw(img)
    s = plan.scale

    def px(x: float, y: float) -> tuple[float, float]:
        return ((x - plan.x0) * s, (y - tile.y0) * s)

    for st in strokes:
        if st.box.y1 < tile.y0 or st.box.y0 > tile.y1:
            continue
        # perfect-freehand thins with pressure; a constant width at the mean pressure reads as well for OCR.
        mean_p = sum(p[2] for p in st.points) / len(st.points)
        w = max(2, round(st.size * s * (0.55 + 0.45 * mean_p)))
        pts = [px(p[0], p[1]) for p in st.points]
        r = w / 2
        if len(pts) == 1:
            x, y = pts[0]
            draw.ellipse((x - r, y - r, x + r, y + r), fill=0)
            continue
        draw.line(pts, fill=0, width=w, joint="curve")
        for x, y in (pts[0], pts[-1]):
            draw.ellipse((x - r, y - r, x + r, y + r), fill=0)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def strokes_boxes(strokes: Sequence[Stroke]) -> list[Box]:
    return [s.box for s in strokes]


__all__ = [
    "MAX_TILE_PX",
    "WIDTH_PX",
    "Box",
    "Plan",
    "Stroke",
    "TextLine",
    "Tile",
    "plan_tiles",
    "render_tile",
    "split_items",
    "strokes_boxes",
    "text_box_lines",
]
