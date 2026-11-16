from __future__ import annotations

from pathlib import Path

import pytest

from naadi_api.settings import Settings


def test_process_env_wins_over_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = tmp_path / ".env"
    env.write_text("APP_ENV=staging\nOPENROUTER_DAILY_CAP=1000\nLOG_LEVEL=debug\n")
    monkeypatch.setenv("APP_ENV", "prod")
    s = Settings(_env_file=env)
    assert s.app_env == "prod"  # process env
    assert s.openrouter_daily_cap == 1000  # .env fallback
    assert s.log_level == "DEBUG"  # normalised


def test_dotenv_used_only_as_fallback(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text('TAVILY_API_KEY="tvly-from-dotenv-123456"\n')
    s = Settings(_env_file=env)
    assert s.tavily_api_key is not None
    assert s.tavily_api_key.get_secret_value() == "tvly-from-dotenv-123456"


def test_vite_alias_and_trailing_slash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VITE_SUPABASE_URL", "https://abcdefghijklmnopqrst.supabase.co/")
    s = Settings(_env_file=None)
    assert s.supabase_url == "https://abcdefghijklmnopqrst.supabase.co"
    assert s.supabase_origin == "https://abcdefghijklmnopqrst.supabase.co"


def test_primary_name_beats_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://primaryprimaryprimary.supabase.co")
    monkeypatch.setenv("VITE_SUPABASE_URL", "https://aliasaliasaliasalias.supabase.co")
    assert Settings(_env_file=None).supabase_url == "https://primaryprimaryprimary.supabase.co"


def test_empty_values_are_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "  ")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    s = Settings(_env_file=None)
    assert s.database_url is None
    assert s.openrouter_api_key is None


def test_secrets_never_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres.ref:hunter2-pass@db.example:5432/postgres")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-role-secret-value")
    monkeypatch.setenv("ADMIN_EMAILS", "someone@example.org")
    s = Settings(_env_file=None)
    text = repr(s) + str(s) + s.model_dump_json()
    for secret in ("hunter2-pass", "service-role-secret-value", "someone@example.org"):
        assert secret not in text
    assert "hunter2-pass" in s.secret_values()


def test_invalid_app_env_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(ValueError, match="app_env"):
        Settings(_env_file=None)


def test_non_https_supabase_has_no_origin() -> None:
    s = Settings(_env_file=None, supabase_url="http://insecure.example")
    assert s.supabase_origin is None
