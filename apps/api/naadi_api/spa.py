"""Serve the built SPA from `static_dir` with an SPA fallback.

- `/assets/*` (content-hashed by Vite): long immutable cache; a missing asset is a 404, never HTML.
- other existing files (favicon, robots.txt, manifest): short cache.
- anything else that is not `/api/*`: `index.html` with `no-cache`, so a deploy is picked up at once.
- unknown `/api/*`: JSON 404 (any method); a known `/api` path with the wrong method: JSON 405 + `Allow`.
- `/sandbox/mermaid.html`: the diagram renderer, only ever loaded in `<iframe sandbox="allow-scripts">`. It gets
  its own CSP (see `security.sandbox_content_security_policy`), and because its opaque origin loads the hashed
  module scripts with CORS, `/assets/*` answer `Access-Control-Allow-Origin: *` (public files, no credentials).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.routing import BaseRoute, Match

from .security import (
    SANDBOX_DOCUMENTS,
    YOUTUBE_SANDBOX_DOCUMENT,
    sandbox_content_security_policy,
    youtube_sandbox_content_security_policy,
)

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


METHODS = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE")


def _allowed_methods(app: FastAPI, request: Request, skip: tuple[object, ...]) -> list[str]:
    """Methods another route accepts for this exact path (works for included routers of any kind)."""
    out: list[str] = []
    for method in METHODS:
        probe = {**request.scope, "method": method}
        for route in app.router.routes:
            if getattr(route, "endpoint", None) in skip:
                continue
            if isinstance(route, BaseRoute) and route.matches(probe)[0] is Match.FULL:
                out.append(method)
                break
    return out


def install_spa(app: FastAPI, static_dir: Path) -> None:
    root = static_dir.resolve()

    @app.api_route(
        "/api/{rest:path}",
        methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        include_in_schema=False,
    )
    async def api_not_found(rest: str, request: Request) -> None:
        allowed = _allowed_methods(app, request, skip=(api_not_found, spa))
        if allowed:
            raise HTTPException(
                405, "This API path does not accept that method.", headers={"Allow": ", ".join(allowed)}
            )
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
                headers = {"cache-control": FILE_CACHE}
                if full_path.startswith("assets/"):
                    headers = {"cache-control": ASSET_CACHE, "access-control-allow-origin": "*"}
                elif full_path in SANDBOX_DOCUMENTS:
                    headers["content-security-policy"] = (
                        youtube_sandbox_content_security_policy()
                        if full_path == YOUTUBE_SANDBOX_DOCUMENT
                        else sandbox_content_security_policy()
                    )
                return FileResponse(f, headers=headers)
            last = full_path.rstrip("/").rsplit("/", 1)[-1]
            if full_path.startswith("assets/") or "." in last:
                raise HTTPException(404, "File not found.")
        return FileResponse(index, headers={"cache-control": INDEX_CACHE})
