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


def test_openapi_version_is_the_single_source_and_matches_the_committed_contract(
    client_factory: Callable[..., TestClient],
) -> None:
    """The container installs dependencies only (no package metadata), so the version must not come from
    importlib.metadata; it comes from naadi_api.__version__, which pyproject also reads."""
    import json
    import tomllib
    from importlib.metadata import PackageNotFoundError

    import naadi_api.main as main_mod
    from naadi_api import __version__

    from .conftest import REPO_ROOT

    live = client_factory().get("/api/openapi.json").json()["info"]["version"]
    committed = json.loads((REPO_ROOT / "packages/shared/openapi.json").read_text())["info"]["version"]
    pyproject = tomllib.loads((REPO_ROOT / "apps/api/pyproject.toml").read_text())
    assert live == committed == __version__
    assert pyproject["project"]["dynamic"] == ["version"]
    assert pyproject["tool"]["hatch"]["version"]["path"] == "naadi_api/__init__.py"

    def no_metadata(_: str) -> str:
        raise PackageNotFoundError("naadi-api")

    # Simulate the container: package metadata is absent, the version must still be correct.
    import importlib.metadata as md

    original = md.version
    md.version = no_metadata  # type: ignore[assignment]
    try:
        assert main_mod.package_version() == __version__
    finally:
        md.version = original  # type: ignore[assignment]
