"""Guard: unit tests never see a hosted project together with real-looking secrets (env or `.env`)."""

from __future__ import annotations

from pathlib import Path

import pytest

from naadi_api.settings import Settings
from tests.conftest import is_local_url

_SECRET_FIELDS = (
    "supabase_service_role_key",
    "supabase_jwt_secret",
    "openrouter_api_key",
    "nvidia_api_key",
    "tavily_api_key",
    "admin_emails",
)


def leaks(s: Settings) -> list[str]:
    """Names of secret settings that would reach code under test next to a non-local project."""
    found: list[str] = []
    db = s.database_url.get_secret_value() if s.database_url else None
    if db and not is_local_url(db):
        found.append("database_url")
    hosted = bool(s.supabase_url) and not is_local_url(s.supabase_url)
    if hosted:
        found += [f for f in _SECRET_FIELDS if getattr(s, f) is not None]
    return found


def test_unit_tests_see_no_hosted_project_or_secrets() -> None:
    assert Settings.model_config.get("env_file") is None  # `.env` is not read in unit tests
    assert leaks(Settings()) == []


def test_the_guard_flags_a_hosted_project_with_secrets() -> None:
    s = Settings(
        _env_file=None,
        supabase_url="https://abcdefghijklmnopqrst.supabase.co",
        supabase_service_role_key="eyJhbGciOiJIUzI1NiJ9.e30.x" + "y" * 20,
        database_url="postgresql://u:p@db.example.org:5432/postgres",
    )
    assert leaks(s) == ["database_url", "supabase_service_role_key"]


def test_a_dotenv_file_is_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = tmp_path / ".env"
    env.write_text("SUPABASE_URL=https://abcdefghijklmnopqrst.supabase.co\nTAVILY_API_KEY=tvly-abcdefghijklmnop\n")
    monkeypatch.chdir(tmp_path)
    assert leaks(Settings()) == []
