"""Video notes (segment 20): `anchor_pos {t}` for `anchor_type 'video'`, the text-range shape for everything else."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from naadi_api.notes.models import AnchorPos, NoteCreate, VideoPos, parse_pos


def test_a_video_note_takes_a_time_position() -> None:
    n = NoteCreate(kind="note", anchor_type="video", anchor_id="yt-abc", anchor_pos={"t": 742})  # type: ignore[arg-type]
    assert isinstance(n.anchor_pos, VideoPos) and n.anchor_pos.t == 742


@pytest.mark.parametrize("t", [-1, 10**9])
def test_the_time_is_bounded(t: int) -> None:
    with pytest.raises(ValidationError):
        NoteCreate(kind="note", anchor_type="video", anchor_id="yt-abc", anchor_pos={"t": t})  # type: ignore[arg-type]


def test_positions_must_fit_their_anchor_type() -> None:
    with pytest.raises(ValidationError):
        NoteCreate(kind="note", anchor_type="section", anchor_id="s1", anchor_pos={"t": 5})  # type: ignore[arg-type]
    text_pos = {"from": 1, "to": 4, "quote": "abc"}
    with pytest.raises(ValidationError):
        NoteCreate(kind="note", anchor_type="video", anchor_id="yt-abc", anchor_pos=text_pos)  # type: ignore[arg-type]
    ok = NoteCreate(kind="note", anchor_type="section", anchor_id="s1", anchor_pos=text_pos)  # type: ignore[arg-type]
    assert isinstance(ok.anchor_pos, AnchorPos)


def test_stored_positions_parse_back_to_the_right_shape() -> None:
    assert parse_pos({"t": 12}) == VideoPos(t=12)
    assert isinstance(parse_pos({"from": 0, "to": 3, "quote": "x"}), AnchorPos)
    assert parse_pos(None) is None
