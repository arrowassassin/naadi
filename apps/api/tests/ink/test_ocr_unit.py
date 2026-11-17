"""Unit: the OCR tile planner, Pillow rendering, the vision prompt and reply validation, lasso PNG cleaning."""

from __future__ import annotations

import base64
import io
from typing import Any

import pytest
from langchain_core.messages import HumanMessage, SystemMessage
from PIL import Image, PngImagePlugin

from naadi_api.ink.convert import ConvertError, clean_png
from naadi_api.ink.ocr.raster import (
    MAX_TILE_PX,
    WIDTH_PX,
    Box,
    plan_tiles,
    render_tile,
    split_items,
    strokes_boxes,
    text_box_lines,
)
from naadi_api.ink.ocr.recognize import SYSTEM_PROMPT, UnreadableReply, messages_for, parse_reply
from naadi_api.ink.ocr.run import compose_text


def box(y0: float, y1: float, x0: float = 0.1, x1: float = 0.9) -> Box:
    return Box(x0, y0, x1, y1)


# ── planner ──


def test_nothing_to_draw_has_no_plan() -> None:
    assert plan_tiles([]) is None
    assert plan_tiles([Box(float("nan"), 0, 1, 1)]) is None
    assert plan_tiles([box(0, 1)], max_tiles=0) is None


def test_one_page_of_ink_is_one_tile_1600_wide() -> None:
    plan = plan_tiles([box(0.1, 0.12), box(0.3, 0.32), box(1.1, 1.15)])
    assert plan is not None and plan.width_px == WIDTH_PX and plan.x0 == 0.0
    assert plan.scale == pytest.approx(WIDTH_PX)  # the surface width (1 su) is the image width
    assert len(plan.tiles) == 1 and not plan.truncated
    t = plan.tiles[0]
    assert t.y0 < 0.1 and t.y1 > 1.15 and t.height_px <= MAX_TILE_PX


def test_empty_stretches_are_skipped_and_tiles_stay_within_2048px() -> None:
    plan = plan_tiles([box(0.1, 0.2), box(5.0, 5.1), box(9.0, 9.05)])
    assert plan is not None and len(plan.tiles) == 3
    for t in plan.tiles:
        assert t.height_px <= MAX_TILE_PX
    assert plan.tiles[1].y0 > 4.9 and plan.tiles[1].y1 < 5.2  # nothing between the bands is drawn


def test_a_band_taller_than_a_tile_is_cut() -> None:
    plan = plan_tiles([box(0.0, 3.0)])  # 3 su = 4800 px at 1600 px/su
    assert plan is not None and len(plan.tiles) == 3
    assert all(t.height_px <= MAX_TILE_PX for t in plan.tiles)
    assert plan.tiles[0].y1 == pytest.approx(plan.tiles[1].y0)


def test_more_than_max_tiles_is_truncated_in_reading_order() -> None:
    boxes = [box(i * 3.0, i * 3.0 + 0.05) for i in range(6)]
    plan = plan_tiles(boxes, max_tiles=4)
    assert plan is not None and len(plan.tiles) == 4 and plan.truncated
    assert [round(t.y0) for t in plan.tiles] == [0, 3, 6, 9]


def test_ink_outside_the_column_widens_the_page() -> None:
    plan = plan_tiles([Box(-0.5, 0.1, 1.5, 0.2)])
    assert plan is not None and plan.x0 < -0.5
    assert plan.scale < WIDTH_PX / 2  # 2+ su now span the 1600 px


# ── items and rendering ──


def stroke(points: list[list[float]], **over: Any) -> dict[str, Any]:
    return {"id": "x", "tool": "pen", "color": "ink", "size": 0.004, "points": points, **over}


def test_split_keeps_pen_strokes_and_text_boxes_and_drops_markers_and_tombstones() -> None:
    items = [
        stroke([[0.1, 0.1, 0.5, 0], [0.3, 0.1, 0.5, 5]]),
        stroke([[0.1, 0.5, 0.5, 0]], shape="rect"),
        stroke([[0.1, 0.1, 0.5, 0]], tool="marker"),
        stroke([[0.1, 0.1, 0.5, 0]], deleted=True),
        {"tool": "text", "x": 0.5, "y": 0.4, "w": 0.3, "size": 0.02, "text": "second"},
        {"tool": "text", "x": 0.1, "y": 0.1, "w": 0.3, "size": 0.02, "text": " first "},
        {"tool": "text", "x": 0.1, "y": 0.1, "text": "   "},
        {"tool": "pen", "points": "nope"},
        "garbage",
    ]
    strokes, texts = split_items(items)  # type: ignore[arg-type]
    assert len(strokes) == 2
    assert text_box_lines(texts) == ["first", "second"]
    b = strokes_boxes(strokes)[0]
    assert b.x0 == pytest.approx(0.098) and b.x1 == pytest.approx(0.302)


def test_render_draws_black_ink_on_white_and_nothing_else() -> None:
    strokes, _ = split_items([stroke([[0.1, 0.1, 0.5, 0], [0.9, 0.1, 0.5, 5]]), stroke([[0.5, 0.2, 1.0, 0]])])
    plan = plan_tiles(strokes_boxes(strokes))
    assert plan is not None
    png = render_tile(strokes, plan, plan.tiles[0])
    img = Image.open(io.BytesIO(png))
    assert img.format == "PNG" and img.mode == "L" and img.size == (WIDTH_PX, plan.tiles[0].height_px)
    assert not img.info.get("text") and "exif" not in img.info
    colours = {c for _, c in img.getcolors(maxcolors=256 * 256) or []}
    assert 0 in colours and 255 in colours
    y = round((0.1 - plan.tiles[0].y0) * plan.scale)
    assert img.getpixel((round(0.5 * plan.scale), y)) < 64  # on the line
    assert img.getpixel((5, 5)) == 255  # paper


# ── prompt and reply ──


def test_messages_hold_the_fixed_instruction_and_the_image_only() -> None:
    msgs = messages_for(b"\x89PNG-bytes")
    assert isinstance(msgs[0], SystemMessage) and msgs[0].content == SYSTEM_PROMPT
    assert isinstance(msgs[1], HumanMessage)
    parts = msgs[1].content
    assert isinstance(parts, list) and [p["type"] for p in parts] == ["text", "image_url"]  # type: ignore[index]
    url = parts[1]["image_url"]["url"]  # type: ignore[index]
    assert url.startswith("data:image/png;base64,") and base64.b64decode(url.split(",", 1)[1]) == b"\x89PNG-bytes"
    assert len(msgs) == 2


@pytest.mark.parametrize(
    ("reply", "text", "lines"),
    [
        ('{"text": "Na+ K+\\nATPase", "lines": ["Na+ K+", "ATPase"]}', "Na+ K+\nATPase", ["Na+ K+", "ATPase"]),
        ('```json\n{"text": "loop", "lines": []}\n```', "loop", ["loop"]),
        ('Sure: {"lines": ["a", " ", "b"]}', "a\nb", ["a", "b"]),
        ('{"text": "", "lines": []}', "", []),
        ([{"type": "text", "text": '{"text": "x\\u0007y"}'}], "xy", ["xy"]),
    ],
)
def test_replies_are_validated_and_normalised(reply: Any, text: str, lines: list[str]) -> None:
    r = parse_reply(reply)
    assert (r.text, r.lines) == (text, lines)


@pytest.mark.parametrize(
    "reply", ["I cannot read this", '{"answer": "x"}', "{not json}", '["text"]', '{"text": 3}', 42]
)
def test_other_replies_are_unreadable(reply: Any) -> None:
    with pytest.raises(UnreadableReply):
        parse_reply(reply)


def test_compose_joins_tiles_then_typed_text_and_caps_the_length() -> None:
    assert compose_text(["one", "", "two"], ["typed"]) == "one\ntwo\ntyped"
    assert len(compose_text(["x" * 30_000], [])) == 20_000


# ── lasso PNG ──


def png_bytes(size: tuple[int, int] = (64, 32), mode: str = "RGBA", **info: str) -> bytes:
    img = Image.new(mode, size, (0, 0, 0, 0) if mode == "RGBA" else 0)
    meta = PngImagePlugin.PngInfo()
    for k, v in info.items():
        meta.add_text(k, v)
    buf = io.BytesIO()
    img.save(buf, format="PNG", pnginfo=meta)
    return buf.getvalue()


def test_clean_png_flattens_on_white_and_drops_metadata() -> None:
    out = clean_png(png_bytes(Author="student@example.com"))
    img = Image.open(io.BytesIO(out))
    assert img.mode == "RGB" and img.getpixel((1, 1)) == (255, 255, 255)  # transparent -> white, not black
    assert b"student@example.com" not in out and not img.info.get("Author")


@pytest.mark.parametrize(
    ("raw", "status", "code"),
    [
        (b"GIF89a....", 415, "ink_convert_not_png"),
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * 40, 422, "ink_convert_invalid"),
        (png_bytes((5000, 10), mode="L"), 422, "ink_convert_invalid"),
        (png_bytes((4, 4), mode="L"), 422, "ink_convert_invalid"),
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * (4 * 1024 * 1024 + 1), 413, "ink_convert_too_large"),
    ],
)
def test_clean_png_refuses_what_is_not_a_reasonable_png(raw: bytes, status: int, code: str) -> None:
    with pytest.raises(ConvertError) as err:
        clean_png(raw)
    assert (err.value.status, err.value.code) == (status, code)


# ── wiring: graph registered, crons declared ──


def test_ink_ocr_graph_is_registered_with_a_sweep_and_a_nightly_cron() -> None:
    import ast
    import json

    from naadi_api.agent.graphs import ink_ocr
    from naadi_api.settings import APP_DIR

    graphs = json.loads((APP_DIR / "aegra.json").read_text(encoding="utf-8"))["graphs"]
    assert graphs["ink_ocr"]["path"] == "./naadi_api/agent/graphs/ink_ocr.py:graph"
    assert set(ink_ocr.graph.nodes) >= {"run", "record"}
    tree = ast.parse((APP_DIR / "naadi_api" / "agent" / "host.py").read_text(encoding="utf-8"))
    node = next(
        n.value
        for n in tree.body
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.target.id == "SYSTEM_CRONS"
    )
    assert node is not None
    # Only the ink_ocr entries (all literals) are read; the module is not imported (it builds the Aegra app).
    entries = [e for e in node.elts if isinstance(e, ast.Tuple)]  # type: ignore[attr-defined]
    crons = [
        ast.literal_eval(e) for e in entries if isinstance(e.elts[1], ast.Constant) and e.elts[1].value == "ink_ocr"
    ]
    mine = {c[0]: c for c in crons}
    assert mine["naadi.ink_ocr.sweep"][2:4] == ("*/2 * * * *", {"mode": "sweep"})
    assert mine["naadi.ink_ocr.nightly"][2:4] == ("23 0 * * *", {"mode": "nightly"})
