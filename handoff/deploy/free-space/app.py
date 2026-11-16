"""Naadi on a FREE Hugging Face Space (Gradio SDK, ZeroGPU flavor, CPU-only work).

gradio.Server gives FastAPI-style routes while satisfying the Gradio runtime the free tier requires.
The built SPA (apps/web/dist) is committed/copied into ./static by CI before upload.
Verify on first deploy that StreamingResponse/SSE routes stream through the Spaces proxy;
if they don't, switch /api/chat to Gradio's streaming @app.api (generator) or use the Docker path.
"""
import os
os.environ.setdefault("HF_HOME", "/tmp/hf")            # ZeroGPU: ~/.cache is read-only
os.environ.setdefault("FASTEMBED_CACHE_PATH", "/tmp/fastembed")

import spaces                                           # must be imported before anything touching torch
from gradio import Server
from fastapi import Request
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from naadi_api.routes import register_api_routes      # same package as the Docker path (apps/api)
from naadi_api.agent.stream import chat_sse            # async generator yielding SSE lines

app = Server(title="naadi")

@spaces.GPU(duration=1)
def _noop():                                            # ZeroGPU requires >=1 decorated function; never called
    pass

register_api_routes(app)                                # /api/health, /api/content/*, /api/attempts, …

@app.post("/api/chat")
async def chat(request: Request):
    body = await request.json()
    user = await request.state.auth_user() if hasattr(request.state, "auth_user") else None
    return StreamingResponse(chat_sse(body, user), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

if os.path.isdir("static/assets"):
    app.mount("/assets", StaticFiles(directory="static/assets"), name="assets")

@app.get("/{path:path}")
async def spa(path: str):                               # SPA fallback (client-side routing)
    f = os.path.join("static", path)
    return FileResponse(f if path and os.path.isfile(f) else "static/index.html")

demo = app                                              # HF runtime expects `demo`
if __name__ == "__main__":
    demo.launch(ssr_mode=False)
