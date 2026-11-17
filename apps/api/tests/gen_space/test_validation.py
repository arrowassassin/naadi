"""naadi-gen input validation on CPU (no torch, gradio or spaces): ranges, clamping, frame counts, GPU budgets."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SPACE = Path(__file__).resolve().parents[4] / "deploy" / "gen-space"
spec = importlib.util.spec_from_file_location("gen_space_validation", SPACE / "validation.py")
assert spec and spec.loader
v = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v)


def test_image_inputs_are_checked_and_snapped_to_16() -> None:
    assert v.validate_image("  a   heart ", 1024, 777, 5) == ("a heart", 1024, 768, 5)
    assert v.validate_image("x", 1024.0, 1024, 2**40)[3] == 2**40 % (v.MAX_SEED + 1)


@pytest.mark.parametrize(
    ("args", "code"),
    [
        (("", 512, 512, 1), "prompt_empty"),
        ((None, 512, 512, 1), "prompt_empty"),
        (("x" * 501, 512, 512, 1), "prompt_too_long"),
        (("x", 2048, 512, 1), "size_out_of_range"),
        (("x", 128, 512, 1), "size_out_of_range"),
        (("x", "big", 512, 1), "size_invalid"),
        (("x", 512, 512, True), "seed_invalid"),
    ],
)
def test_bad_image_inputs_are_refused_with_a_code(args: tuple[object, ...], code: str) -> None:
    with pytest.raises(v.InvalidInput) as e:
        v.validate_image(*args)
    assert str(e.value) == code


def test_video_limits_and_frame_counts() -> None:
    assert v.validate_video("valve", 5, 1) == ("valve", 5, 1)
    for bad in (0, 6, True, "3"):
        with pytest.raises(v.InvalidInput):
            v.validate_video("valve", bad, 1)
    assert [v.video_frames(s) for s in (1, 3, 5)] == [25, 73, 121]  # 8k + 1
    assert v.VIDEO_SIZE == (768, 512) and v.FPS == 24


def test_gpu_budgets_grow_with_the_request_and_never_raise() -> None:
    assert v.image_duration("x", 256, 256, 0) < v.image_duration("x", 1024, 1024, 0)
    assert v.video_duration("x", 1, 0) < v.video_duration("x", 5, 0)
    assert v.image_duration(None, "bad", None, None) == 20 and v.video_duration("x", 99, 0) == 60
