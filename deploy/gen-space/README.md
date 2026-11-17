---
title: naadi-gen
emoji: 🎨
colorFrom: purple
colorTo: indigo
sdk: gradio
sdk_version: 6.32.0
python_version: "3.12"
app_file: app.py
short_description: Private image and clip generation for Naadi (no UI)
suggested_hardware: zero-a10g
pinned: false
---
# naadi-gen (private ZeroGPU Space)

Two API endpoints, no UI, called only by the Naadi API through `gradio_client` with the fine-grained `HF_GEN_TOKEN`.

| Endpoint | Input | Output |
| --- | --- | --- |
| `/image` | `prompt`, `width` ≤ 1024, `height` ≤ 1024, `seed` | WebP (FLUX.1-schnell, 4 steps, bf16) |
| `/video` | `prompt`, `seconds` ≤ 5, `seed` | H.264 MP4, ≤ 768×512, 24 fps, ≤ 8 MB (LTX-Video) |

Both return `{mime, data_b64, width, height, seed}` (video also `duration_s`). Input is validated in `validation.py`
(CPU-testable); models load on CPU at start and move to CUDA inside the `@spaces.GPU` function with a duration
computed from the request. Nothing here ever requests paid hardware.

## Licences (checked 2026-11-17)
- FLUX.1-schnell: Apache-2.0.
- LTX-Video: Lightricks open-weights licence. Free for organisations under US$10M annual revenue; use restrictions
  apply (no medical advice, no impersonation, no harm to minors) and generated content must be disclosed as machine
  generated, which Naadi does with the AI label (chip, alt text, caption). Set `NAADI_GEN_VIDEO=0` (Space variable)
  to turn video off, and set `generation.video.max_per_user_per_day: 0` in app_config.

## Create (owner, once)
    hf repos create arrowassassin/naadi-gen --type space --space-sdk gradio --flavor zero-a10g --private
    hf spaces variables add arrowassassin/naadi-gen --env GRADIO_SSR_MODE=false
Then run the `deploy-gen` workflow (or `hf upload arrowassassin/naadi-gen deploy/gen-space . --repo-type space`).
Create a fine-grained token with access to this Space only and set it as `HF_GEN_TOKEN` on the main Space.
