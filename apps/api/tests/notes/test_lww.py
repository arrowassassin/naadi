"""Unit: the bounded last-writer-wins rule (notes/lww.py) and the own-image filter (notes/text.py)."""

from __future__ import annotations

import datetime as dt
from typing import Any

from naadi_api.notes import lww
from naadi_api.notes.text import own_images, own_path

UID = "6f0d3c1e-1111-4222-8333-944455556666"
NOW = 1_763_380_000_123


def at(ms: int, micro: int = 0) -> dt.datetime:
    return dt.datetime(1970, 1, 1, tzinfo=dt.UTC) + dt.timedelta(milliseconds=ms, microseconds=micro)


def test_edit_time_is_the_client_stamp_clamped_to_server_plus_five_minutes() -> None:
    assert lww.ms(lww.edit_time(None, NOW)) == NOW
    assert lww.ms(lww.edit_time(NOW - 3_600_000, NOW)) == NOW - 3_600_000
    assert lww.ms(lww.edit_time(NOW + lww.FUTURE_SKEW_MS, NOW)) == NOW + lww.FUTURE_SKEW_MS
    assert lww.ms(lww.edit_time(NOW + 10 * lww.FUTURE_SKEW_MS, NOW)) == NOW + lww.FUTURE_SKEW_MS
    assert lww.FUTURE_SKEW_MS == 5 * 60 * 1000
    # Whole milliseconds, exactly (no float rounding of large epochs).
    assert lww.edit_time(NOW, NOW + 1).microsecond == (NOW % 1000) * 1000


def test_is_stale_compares_whole_milliseconds_and_ties_pass() -> None:
    assert lww.is_stale(at(NOW - 1), at(NOW))
    assert not lww.is_stale(at(NOW), at(NOW))
    assert not lww.is_stale(at(NOW + 1), at(NOW))
    # A stored value with microseconds (written without a stamp) is read at ms precision: the client, which sees
    # milliseconds, can repeat it.
    assert not lww.is_stale(at(NOW), at(NOW, 999))
    assert not lww.is_stale(at(NOW), None)


def test_own_path_accepts_only_the_students_notes_folder() -> None:
    assert own_path(f"{UID}/notes/n/a.png", UID)
    for bad in (
        f"{UID}/exports/a.zip",
        "other/notes/n/a.png",
        f"{UID}/notes/",
        f"{UID}/notes/../exports/a.zip",
        f"{UID}/notes//a.png",
        f"{UID}/notes/./a.png",
        f"{UID}/notes\\a.png",
        f"/{UID}/notes/a.png",
        None,
        42,
        f"{UID}/notes/" + "a" * 600,
    ):
        assert not own_path(bad, UID), bad


def img(path: Any) -> dict[str, Any]:
    return {"type": "image", "attrs": {"path": path, "alt": None}}


def test_tiptap_images_outside_the_folder_are_dropped_at_any_depth() -> None:
    mine = img(f"{UID}/notes/n/a.png")
    body: dict[str, Any] = {
        "type": "doc",
        "content": [
            mine,
            img("x/notes/n/b.png"),
            {"type": "bulletList", "content": [{"type": "listItem", "content": [img(None), {"type": "paragraph"}]}]},
            {"type": "image"},
        ],
    }
    out, dropped = own_images(body, "paper", UID)
    assert dropped == 3
    assert out == {
        "type": "doc",
        "content": [
            mine,
            {"type": "bulletList", "content": [{"type": "listItem", "content": [{"type": "paragraph"}]}]},
        ],
    }
    assert body["content"][1]["type"] == "image"  # the input is not changed
    clean = {"type": "doc", "content": [mine]}
    same, none = own_images(clean, "paper", UID)
    assert same is clean and none == 0


def test_whiteboard_files_outside_the_folder_are_dropped() -> None:
    scene = {
        "elements": [],
        "files": {
            "a": {"id": "a", "path": f"{UID}/notes/p/a.png"},
            "b": {"id": "b", "path": "other/notes/p/b.png"},
            "c": {"id": "c", "dataURL": "data:image/png;base64,AAAA"},
        },
    }
    out, dropped = own_images(scene, "whiteboard", UID)
    assert dropped == 1 and sorted(out["files"]) == ["a", "c"]
    assert own_images({"elements": []}, "whiteboard", UID) == ({"elements": []}, 0)
    assert own_images(None, "paper", UID) == (None, 0)
