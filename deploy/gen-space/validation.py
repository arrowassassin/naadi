"""Input validation and GPU-time budgeting for naadi-gen. Pure Python: no torch, gradio or spaces import, so it is
unit-tested on CPU. Anything out of range is clamped or refused here, before a GPU second is requested."""

from __future__ import annotations

MAX_SIDE = 1024
MIN_SIDE = 256
MAX_SECONDS = 5
FPS = 24
VIDEO_SIZE = (768, 512)
MAX_PROMPT_CHARS = 500
MAX_VIDEO_BYTES = 8 * 1024 * 1024
MAX_SEED = 2**31 - 1


class InvalidInput(ValueError):
    """Raised with a stable code; the API maps any Space error to its own generic code."""


def _prompt(prompt: object) -> str:
    if not isinstance(prompt, str) or not prompt.strip():
        raise InvalidInput("prompt_empty")
    text = " ".join(prompt.split())
    if len(text) > MAX_PROMPT_CHARS:
        raise InvalidInput("prompt_too_long")
    return text


def _seed(seed: object) -> int:
    if isinstance(seed, bool) or not isinstance(seed, (int, float)):
        raise InvalidInput("seed_invalid")
    return int(seed) % (MAX_SEED + 1)


def _side(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidInput("size_invalid")
    v = int(value)
    if v < MIN_SIDE or v > MAX_SIDE:
        raise InvalidInput("size_out_of_range")
    return v - v % 16  # FLUX needs multiples of 16


def validate_image(prompt: object, width: object, height: object, seed: object) -> tuple[str, int, int, int]:
    return _prompt(prompt), _side(width), _side(height), _seed(seed)


def validate_video(prompt: object, seconds: object, seed: object) -> tuple[str, int, int]:
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        raise InvalidInput("seconds_invalid")
    s = int(seconds)
    if s < 1 or s > MAX_SECONDS:
        raise InvalidInput("seconds_out_of_range")
    return _prompt(prompt), s, _seed(seed)


def video_frames(seconds: int) -> int:
    """LTX-Video wants 8k + 1 frames."""
    return (seconds * FPS // 8) * 8 + 1


def image_duration(prompt: object = None, width: object = 1024, height: object = 1024, seed: object = 0) -> int:
    """GPU seconds to request (called by `@spaces.GPU(duration=...)` with the endpoint's arguments)."""
    try:
        _, w, h, _ = validate_image(prompt or "x", width, height, seed)
    except InvalidInput:
        return 20
    return 15 + round(15 * (w * h) / (MAX_SIDE * MAX_SIDE))


def video_duration(prompt: object = None, seconds: object = MAX_SECONDS, seed: object = 0) -> int:
    try:
        _, s, _ = validate_video(prompt or "x", seconds, seed)
    except InvalidInput:
        return 60
    return 45 + 15 * s
