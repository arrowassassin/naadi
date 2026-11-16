"""Pydantic mirror of the zod ink schema: the shared fixtures pass/fail identically, and the limits match
packages/shared/ink.schema.json (generated from zod)."""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from pydantic import ValidationError

from naadi_api.ink import models
from naadi_api.ink.models import InkSettings, parse_item

from ..conftest import REPO_ROOT

FIXTURES = REPO_ROOT / "packages" / "shared" / "fixtures" / "ink"
CASES: list[dict[str, Any]] = json.loads((FIXTURES / "items.json").read_text(encoding="utf-8"))["cases"]
SCHEMA: dict[str, Any] = json.loads((REPO_ROOT / "packages" / "shared" / "ink.schema.json").read_text(encoding="utf-8"))


def _expand(item: Any) -> Any:
    if isinstance(item, dict) and isinstance(item.get("points"), str):
        m = re.fullmatch(r"\$points:(\d+)", item["points"])
        if m:
            return {**item, "points": [[0.1, 0.1, 0.5, i] for i in range(int(m.group(1)))]}
    return item


def _valid(item: Any) -> bool:
    try:
        parse_item(item)
    except ValidationError:
        return False
    return True


def test_fixtures_cover_both_outcomes() -> None:
    assert sum(c["valid"] for c in CASES) > 10
    assert sum(not c["valid"] for c in CASES) > 30


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_fixture_parity_with_zod(case: dict[str, Any]) -> None:
    assert _valid(_expand(case["item"])) is case["valid"]


def _variants() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for v in SCHEMA["items"]["oneOf"]:
        tool = v["properties"]["tool"]
        out["text" if tool.get("const") == "text" else "stroke"] = v
    return out


def test_models_mirror_the_generated_json_schema() -> None:
    v = _variants()
    assert SCHEMA["maxItems"] == models.INK_MAX_ITEMS
    stroke, text = v["stroke"]["properties"], v["text"]["properties"]
    assert stroke["id"]["pattern"] == models.UUID_V7_PATTERN
    assert stroke["points"]["maxItems"] == models.INK_MAX_POINTS
    assert stroke["points"]["minItems"] == 1
    assert text["text"]["maxLength"] == models.INK_MAX_TEXT
    assert stroke["color"]["enum"] == ["ink", "violet", "lime", "amber"]
    assert stroke["tool"]["enum"] == ["pen", "marker"]
    assert stroke["shape"]["enum"] == ["line", "arrow", "circle", "ellipse", "rect"]
    # same field sets and required fields as the pydantic models
    assert set(stroke) == set(models.InkStroke.model_fields)
    assert set(text) == set(models.InkTextBox.model_fields)
    assert set(v["stroke"]["required"]) == {n for n, f in models.InkStroke.model_fields.items() if f.is_required()}
    assert set(v["text"]["required"]) == {n for n, f in models.InkTextBox.model_fields.items() if f.is_required()}
    assert set(stroke["anchor"]["properties"]) == set(models.InkAnchor.model_fields)
    assert stroke["size"]["exclusiveMinimum"] == 0 and stroke["size"]["maximum"] == 0.1
    assert text["size"]["maximum"] == 0.2 and text["w"]["maximum"] == 4
    point = stroke["points"]["items"]["prefixItems"]
    assert [p.get("maximum") for p in point] == [10_000, 10_000, 1, 86_400_000]


def test_ink_settings_defaults_and_strictness() -> None:
    assert InkSettings().model_dump() == {"pen_only": True, "predict": True, "tool": None, "color": None}
    InkSettings.model_validate({"pen_only": False, "predict": True, "tool": "marker", "color": "lime"})
    for bad in ({"pen_only": "yes"}, {"tool": "brush"}, {"color": "#000"}, {"extra": 1}, {"predict": 1}):
        with pytest.raises(ValidationError):
            InkSettings.model_validate(bad)
