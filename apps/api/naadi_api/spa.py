"""Serve the built SPA from `static_dir` with an SPA fallback.

- `/assets/*` (content-hashed by Vite): long immutable cache; a missing asset is a 404, never HTML.
- other existing files (favicon, robots.txt, manifest): short cache.
- anything else that is not `/api/*`: `index.html` with `no-cache`, so a deploy is picked up at once.
- unknown `/api/*`: JSON 404 (any method).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

ASSET_CACHE = "public, max-age=31536000, immutable"
FILE_CACHE = "public, max-age=3600"
INDEX_CACHE = "no-cache"


def _safe_file(root: Path, rel: str) -> Path | None:
    parts = [p for p in rel.split("/") if p]
    if any(p.startswith(".") or "\\" in p or "\x00" in p for p in parts):
        return None
    candidate = root.joinpath(*parts).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return None
    return candidate


def install_spa(app: FastAPI, static_dir: Path) -> None:
    root = static_dir.resolve()

    @app.api_route(
        "/api/{rest:path}",
        methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        include_in_schema=False,
    )
    async def api_not_found(rest: str) -> None:
        raise HTTPException(404, "No API route matches this path.")

    @app.api_route("/{full_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    async def spa(full_path: str) -> FileResponse:
        if full_path == "api":
            raise HTTPException(404, "No API route matches this path.")
        index = root / "index.html"
        if not index.is_file():
            raise HTTPException(404, "The web app has not been built into the static directory.")
        if full_path:
            f = _safe_file(root, full_path)
            if f is not None:
                cache = ASSET_CACHE if full_path.startswith("assets/") else FILE_CACHE
                return FileResponse(f, headers={"cache-control": cache})
            last = full_path.rstrip("/").rsplit("/", 1)[-1]
            if full_path.startswith("assets/") or "." in last:
                raise HTTPException(404, "File not found.")
        return FileResponse(index, headers={"cache-control": INDEX_CACHE})
