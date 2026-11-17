"""naadi-gen: two API endpoints (`/image`, `/video`), no UI. See README.md.

Models load on CPU at start; each endpoint moves its pipeline to CUDA inside the `@spaces.GPU` function (ZeroGPU
rules) with a duration computed from the request. Results are returned inline as base64 so the caller needs no file
download. Never requests paid hardware.
"""

from __future__ import annotations

import base64
import io
import os
import tempfile

import gradio as gr
import spaces
import torch
from validation import (
    FPS,
    MAX_VIDEO_BYTES,
    VIDEO_SIZE,
    InvalidInput,
    image_duration,
    validate_image,
    validate_video,
    video_duration,
    video_frames,
)

IMAGE_MODEL = "black-forest-labs/FLUX.1-schnell"  # Apache-2.0
VIDEO_MODEL = "Lightricks/LTX-Video"  # Lightricks open-weights licence (see README)
VIDEO_ENABLED = os.environ.get("NAADI_GEN_VIDEO", "0") == "1"  # off until the owner confirms the licence

image_pipe = None
video_pipe = None


def _load() -> None:
    global image_pipe, video_pipe
    from diffusers import FluxPipeline, LTXPipeline

    image_pipe = FluxPipeline.from_pretrained(IMAGE_MODEL, torch_dtype=torch.bfloat16)
    if VIDEO_ENABLED:
        video_pipe = LTXPipeline.from_pretrained(VIDEO_MODEL, torch_dtype=torch.bfloat16)


_load()


def _refuse(exc: InvalidInput) -> gr.Error:
    return gr.Error(str(exc))


# GPU functions take only inputs that were already validated, so a refused request never asks ZeroGPU for a slot.
@spaces.GPU(duration=lambda text, w, h, s: image_duration(text, w, h, s))
def _render_image(text: str, w: int, h: int, s: int) -> bytes:
    assert image_pipe is not None
    image_pipe.to("cuda")
    out = image_pipe(
        prompt=text,
        width=w,
        height=h,
        num_inference_steps=4,
        guidance_scale=0.0,
        max_sequence_length=256,
        generator=torch.Generator("cuda").manual_seed(s),
    ).images[0]
    buf = io.BytesIO()
    out.save(buf, format="WEBP", quality=85)
    return buf.getvalue()


@spaces.GPU(duration=lambda text, secs, s: video_duration(text, secs, s), size="large")
def _render_video(text: str, secs: int, s: int) -> tuple[bytes, int]:
    from diffusers.utils import export_to_video

    assert video_pipe is not None
    video_pipe.to("cuda")
    width, height = VIDEO_SIZE
    frames = video_pipe(
        prompt=text,
        width=width,
        height=height,
        num_frames=video_frames(secs),
        num_inference_steps=30,
        generator=torch.Generator("cuda").manual_seed(s),
    ).frames[0]
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "clip.mp4")
        export_to_video(frames, path, fps=FPS)  # imageio-ffmpeg, H.264
        with open(path, "rb") as fh:
            return fh.read(), len(frames)


def image(prompt: str, width: int = 1024, height: int = 768, seed: int = 0) -> dict:
    try:
        text, w, h, s = validate_image(prompt, width, height, seed)
    except InvalidInput as exc:
        raise _refuse(exc) from exc
    data = _render_image(text, w, h, s)
    return {
        "mime": "image/webp",
        "data_b64": base64.b64encode(data).decode("ascii"),
        "width": w,
        "height": h,
        "seed": s,
    }


def video(prompt: str, seconds: int = 5, seed: int = 0) -> dict:
    if not VIDEO_ENABLED or video_pipe is None:
        raise gr.Error("video_disabled")  # refused before any GPU request
    try:
        text, secs, s = validate_video(prompt, seconds, seed)
    except InvalidInput as exc:
        raise _refuse(exc) from exc
    data, frames = _render_video(text, secs, s)
    if len(data) > MAX_VIDEO_BYTES:
        raise gr.Error("video_too_large")
    width, height = VIDEO_SIZE
    return {
        "mime": "video/mp4",
        "data_b64": base64.b64encode(data).decode("ascii"),
        "width": width,
        "height": height,
        "duration_s": round(frames / FPS, 2),
        "seed": s,
    }


with gr.Blocks() as demo:
    # No UI: only the two API endpoints below are exposed.
    gr.api(image, api_name="image")
    gr.api(video, api_name="video")

if __name__ == "__main__":
    demo.queue(max_size=8).launch(show_api=True, ssr_mode=False)
