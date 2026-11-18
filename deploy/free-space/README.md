---
title: Naadi
emoji: 🫀
colorFrom: green
colorTo: purple
sdk: gradio
sdk_version: 6.32.0
python_version: "3.12"
app_file: app.py
short_description: USMLE prep for IMGs — learn, practise, plan
pinned: false
---
# Naadi (FREE hosting path)
Gradio SDK on the free ZeroGPU flavor, used purely as a CPU web server via `gradio.Server`
(FastAPI-style routes). It serves the built SPA at `/` and the JSON/SSE API at `/api/*`.
No GPU is ever requested (a no-op `@spaces.GPU` satisfies ZeroGPU), so no quota is used.
Create with:  hf repos create <ns>/naadi --type space --space-sdk gradio --flavor zero-a10g --private
Then:         hf spaces variables add <ns>/naadi --env GRADIO_SSR_MODE=false
