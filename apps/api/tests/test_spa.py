from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fastapi.testclient import TestClient

from .conftest import make_settings


def test_index_and_fallback(client_factory: Callable[..., TestClient]) -> None:
    c = client_factory()
    for path in ("/", "/system", "/courses/renal/lesson-6", "/system/"):
        r = c.get(path)
        assert r.status_code == 200, path
        assert '<div id="root">' in r.text
        assert r.headers["cache-control"] == "no-cache"
        assert r.headers["content-type"].startswith("text/html")


def test_hashed_assets_cached_forever(client_factory: Callable[..., TestClient]) -> None:
    r = client_factory().get("/assets/index-abc123.js")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert "javascript" in r.headers["content-type"]


def test_other_root_files(client_factory: Callable[..., TestClient]) -> None:
    r = client_factory().get("/robots.txt")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "public, max-age=3600"


def test_missing_asset_is_404_not_html(client_factory: Callable[..., TestClient]) -> None:
    c = client_factory()
    for path in ("/assets/gone-123.js", "/favicon-missing.ico"):
        r = c.get(path)
        assert r.status_code == 404, path
        assert r.json()["error"]["code"] == "not_found"


def test_path_traversal_blocked(client_factory: Callable[..., TestClient]) -> None:
    c = client_factory()
    for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/assets/..%2f..%2fsecret.txt", "/.env"):
        r = c.get(path)
        assert "outside the static root" not in r.text, path


def test_head_supported(client_factory: Callable[..., TestClient]) -> None:
    r = client_factory().head("/system")
    assert r.status_code == 200


def test_no_build_gives_clear_404(client_factory: Callable[..., TestClient], tmp_path: Path) -> None:
    c = client_factory(settings=make_settings(static_dir=tmp_path / "nope"))
    r = c.get("/")
    assert r.status_code == 404
    assert "not been built" in r.json()["error"]["message"]
    assert c.get("/api/health").status_code == 200


def test_openapi_is_served_but_docs_are_off(client_factory: Callable[..., TestClient]) -> None:
    c = client_factory()
    assert c.get("/api/openapi.json").json()["info"]["title"] == "Naadi API"
    assert c.get("/api/docs").status_code == 404
