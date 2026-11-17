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
    image_duration,
    validate_image,
    validate_video,
    video_duration,
    video_frames,
)

IMAGE_MODEL = "black-forest-labs/FLUX.1-schnell"  # Apache-2.0
VIDEO_MODEL = "Lightricks/LTX-Video"  # Lightricks open-weights licence (see README)
VIDEO_ENABLED = os.environ.get("NAADI_GEN_VIDEO", "1") != "0"

image_pipe = None
video_pipe = None


def _load() -> None:
    global image_pipe, video_pipe
    from diffusers import FluxPipeline, LTXPipeline

    image_pipe = FluxPipeline.from_pretrained(IMAGE_MODEL, torch_dtype=torch.bfloat16)
    if VIDEO_ENABLED:
        video_pipe = LTXPipeline.from_pretrained(VIDEO_MODEL, torch_dtype=torch.bfloat16)


_load()


@spaces.GPU(duration=image_duration)
def image(prompt: str, width: int = 1024, height: int = 768, seed: int = 0) -> dict:
    text, w, h, s = validate_image(prompt, width, height, seed)
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
    return {
        "mime": "image/webp",
        "data_b64": base64.b64encode(buf.getvalue()).decode("ascii"),
        "width": w,
        "height": h,
        "seed": s,
    }


@spaces.GPU(duration=video_duration, size="large")
def video(prompt: str, seconds: int = 5, seed: int = 0) -> dict:
    if not VIDEO_ENABLED or video_pipe is None:
        raise gr.Error("video_disabled")
    from diffusers.utils import export_to_video

    text, secs, s = validate_video(prompt, seconds, seed)
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
            data = fh.read()
    if len(data) > MAX_VIDEO_BYTES:
        raise gr.Error("video_too_large")
    return {
        "mime": "video/mp4",
        "data_b64": base64.b64encode(data).decode("ascii"),
        "width": width,
        "height": height,
        "duration_s": round(len(frames) / FPS, 2),
        "seed": s,
    }


with gr.Blocks() as demo:
    # No UI: only the two API endpoints below are exposed.
    gr.api(image, api_name="image")
    gr.api(video, api_name="video")

if __name__ == "__main__":
    demo.queue(max_size=8).launch(show_api=True, ssr_mode=False)
