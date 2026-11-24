from __future__ import annotations

import io
import json
import logging
import os
import re
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient

from naadi_api.db import Database
from naadi_api.health.checks import CheckOutcome
from naadi_api.health.service import COMPONENTS, Check, HealthService
from naadi_api.logs import configure_logging
from naadi_api.main import create_app
from naadi_api.settings import Settings

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = Path(__file__).resolve().parent / "fixtures"

# Every name in docs/SECRETS.md that Settings reads; unit tests start from a clean slate.
ENV_NAMES = [
    "DATABASE_URL",
    "SUPABASE_URL",
    "SUPABASE_ANON_KEY",
    "SUPABASE_SERVICE_ROLE_KEY",
    "SUPABASE_JWT_SECRET",
    "VITE_SUPABASE_URL",
    "VITE_SUPABASE_ANON_KEY",
    "OPENROUTER_API_KEY",
    "OPENROUTER_DAILY_CAP",
    "OPENROUTER_APP_URL",
    "OPENROUTER_APP_TITLE",
    "NVIDIA_API_KEY",
    "TAVILY_API_KEY",
    "HF_GEN_TOKEN",
    "GENERATION_FAKE",
    "ADMIN_EMAILS",
    "GOOGLE_OAUTH_CONFIGURED",
    "APP_ENV",
    "APP_BASE_URL",
    "LOG_LEVEL",
    "APP_VERSION",
    "NAADI_VERSION",
    "NAADI_STATIC_DIR",
    "NAADI_CONFIG_DIR",
    "NAADI_SEED_ON_START",
]


# Secret-bearing names beyond what Settings reads (CLIs, deploy, live checks).
SECRET_ENV_NAMES = ["HF_TOKEN", "SUPABASE_ACCESS_TOKEN", "YOUTUBE_API_KEY", "GOOGLE_API_KEY"]
# Any other variable whose name looks like a credential or a service endpoint is cleared too.
SECRET_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL)S?$|^(SUPABASE|VITE_SUPABASE|R2|AWS)_")
# Local test plumbing a `db` test may keep, and only when it points at this machine.
LOCAL_PLUMBING = ("DATABASE_URL", "NAADI_STORAGE_URL", "NAADI_STORAGE_PORT")
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def is_local_url(url: str | None) -> bool:
    return bool(url) and (urlparse(url).hostname or "") in LOCAL_HOSTS


def _keep_local(name: str, value: str) -> bool:
    return value.isdigit() if name.endswith("_PORT") else is_local_url(value)


@pytest.fixture(autouse=True)
def _clean_env(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test not marked `live` starts from a cleared environment (no `.env` either): no secrets and
    no endpoints of the real project. `db` tests get back only local plumbing (a loopback DATABASE_URL,
    the local storage-api); anything pointing elsewhere is dropped, so a db test can never write to a
    hosted database. Only `live` tests (real external services, budget-aware) see the environment."""
    if request.node.get_closest_marker("live"):
        return
    keep = {n: os.environ[n] for n in LOCAL_PLUMBING if n in os.environ and _keep_local(n, os.environ[n])}
    for name in {*ENV_NAMES, *SECRET_ENV_NAMES, *LOCAL_PLUMBING, *(n for n in os.environ if SECRET_NAME.search(n))}:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    if request.node.get_closest_marker("db"):
        for name, value in keep.items():
            monkeypatch.setenv(name, value)


# Every secret a Settings can carry: None unless a test passes a value explicitly.
_SECRET_SETTINGS = (
    "database_url",
    "supabase_service_role_key",
    "supabase_jwt_secret",
    "openrouter_api_key",
    "nvidia_api_key",
    "tavily_api_key",
    "hf_gen_token",
    "youtube_api_key",
    "admin_emails",
    "hf_backup_token",
)


def make_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {"app_env": "prod", "config_dir": REPO_ROOT / "config", "embeddings_autostart": False}
    base.update(dict.fromkeys(_SECRET_SETTINGS))
    base.update(overrides)
    return Settings(_env_file=None, **base)


def fake_check(status: str = "ok", detail: str = "fine", exc: BaseException | None = None) -> Any:
    async def run() -> CheckOutcome:
        if exc is not None:
            raise exc
        return CheckOutcome(status, detail)  # type: ignore[arg-type]

    return run


def fake_service(**per_component: Any) -> HealthService:
    checks = {n: Check(n, per_component.get(n, fake_check()), ttl_s=60) for n in COMPONENTS}
    return HealthService(checks=checks, version="test", environment="prod")


@pytest.fixture
def static_dir(tmp_path: Path) -> Path:
    d = tmp_path / "static"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text('<!doctype html><html><body><div id="root"></div></body></html>')
    (d / "assets" / "index-abc123.js").write_text("console.log(1)")
    (d / "robots.txt").write_text("User-agent: *\n")
    (tmp_path / "secret.txt").write_text("outside the static root")
    return d


@pytest.fixture
def client_factory(static_dir: Path) -> Iterator[Callable[..., TestClient]]:
    clients: list[TestClient] = []

    def make(settings: Settings | None = None, health: HealthService | None = None, **kw: Any) -> TestClient:
        s = settings or make_settings(static_dir=static_dir, **kw)
        app = create_app(s, health=health or fake_service(), db=Database(None))
        c = TestClient(app, raise_server_exceptions=False)
        c.__enter__()
        clients.append(c)
        return c

    yield make
    for c in clients:
        c.__exit__(None, None, None)


class LogCapture:
    def __init__(self) -> None:
        self.stream = io.StringIO()

    def lines(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.stream.getvalue().splitlines() if line.strip()]

    @property
    def text(self) -> str:
        return self.stream.getvalue()


@pytest.fixture
def logs() -> Iterator[LogCapture]:
    cap = LogCapture()
    handler_before = logging.getLogger().handlers[:]
    configure_logging("INFO", stream=cap.stream)
    original = configure_logging

    import naadi_api.main as main_mod

    # create_app() reconfigures logging; keep it pointed at our capture stream for the test.
    def patched(level: str = "INFO", stream: Any = None) -> None:
        original(level, stream=cap.stream)

    main_mod.configure_logging = patched  # type: ignore[attr-defined]
    try:
        yield cap
    finally:
        main_mod.configure_logging = original  # type: ignore[attr-defined]
        logging.getLogger().handlers = handler_before


def require_env(*names: str) -> dict[str, str]:
    """Integration tests fail loudly (never skip) when their configuration is missing."""
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        pytest.fail(f"missing required environment variable(s): {', '.join(missing)} (see docs/SECRETS.md)")
    return {n: os.environ[n] for n in names}
