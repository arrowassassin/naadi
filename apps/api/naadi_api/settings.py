"""Runtime settings for the Naadi API.

Precedence (docs/SECRETS.md): process environment first, then `.env` in the repo root as a local-dev
fallback. Secrets are `SecretStr`, so they never show up in `repr()`, logs or responses.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PACKAGE_DIR = Path(__file__).resolve().parent
APP_DIR = PACKAGE_DIR.parent  # apps/api locally, /home/user/app in the container
REPO_ROOT = APP_DIR.parent.parent  # only meaningful in a checkout


def _first_existing(*candidates: Path) -> Path:
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]


def default_config_dir() -> Path:
    """`./config` next to the app (container), else `<repo>/config` (checkout)."""
    return _first_existing(APP_DIR / "config", REPO_ROOT / "config")


def default_app_version() -> str:
    """The deploy script writes the git commit into ./VERSION; local runs report `dev`."""
    f = APP_DIR / "VERSION"
    try:
        v = f.read_text(encoding="utf-8").strip()
    except OSError:
        return "dev"
    return v[:64] or "dev"


def default_static_dir() -> Path:
    return APP_DIR / "static"


AppEnv = Literal["local", "staging", "prod"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    # ── App ──
    app_env: AppEnv = "local"
    app_base_url: str | None = None
    log_level: LogLevel = "INFO"
    app_version: str = Field(
        default_factory=default_app_version, validation_alias=AliasChoices("APP_VERSION", "NAADI_VERSION")
    )
    static_dir: Path = Field(default_factory=default_static_dir, validation_alias="NAADI_STATIC_DIR")
    config_dir: Path = Field(default_factory=default_config_dir, validation_alias="NAADI_CONFIG_DIR")

    # ── Supabase ──
    database_url: SecretStr | None = None
    supabase_url: str | None = Field(default=None, validation_alias=AliasChoices("SUPABASE_URL", "VITE_SUPABASE_URL"))
    supabase_anon_key: str | None = Field(
        default=None, validation_alias=AliasChoices("SUPABASE_ANON_KEY", "VITE_SUPABASE_ANON_KEY")
    )
    supabase_service_role_key: SecretStr | None = None
    supabase_jwt_secret: SecretStr | None = None
    # A bare storage-api (the local stack and CI) the server-side Storage work (export-all) talks to instead of
    # `supabase_url`. Unset on the real project.
    storage_url: str | None = Field(default=None, validation_alias=AliasChoices("NAADI_STORAGE_URL"))
    # The service key of that bare storage-api when it is not the project's own (its JWT secret is the stack's, not
    # the auth server's). Used with `storage_url` only; unset on the real project.
    storage_service_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("NAADI_STORAGE_SERVICE_KEY")
    )

    # ── AI ──
    openrouter_api_key: SecretStr | None = None
    openrouter_daily_cap: int = 50
    openrouter_app_url: str | None = None
    openrouter_app_title: str = "Naadi"
    # Test hook (e2e only): send OpenRouter calls to a scripted fake on loopback. Honoured only outside prod and
    # only for a loopback http(s) URL (`openrouter_base_url_effective`); production always talks to OpenRouter.
    openrouter_base_url: str | None = Field(default=None, validation_alias="NAADI_OPENROUTER_BASE_URL")
    nvidia_api_key: SecretStr | None = None
    tavily_api_key: SecretStr | None = None
    # Image/video generation (segment 15b): a fine-grained Hugging Face token with access to the private
    # `naadi-gen` Space only. Until it is set, generation reports `not_configured` and its tools and UI stay hidden.
    hf_gen_token: SecretStr | None = None
    # Non-production stub (tests, e2e): generation runs against an in-process fake and an in-memory store, with no
    # Space call. Ignored when APP_ENV=prod.
    generation_fake: bool = False
    # Non-production stub (tests, e2e): the authoring pipeline's model calls (admin runs, the solver re-check) are
    # answered by a scripted fake (naadi_api/authoring/fake.py), with no provider and no quota. Ignored when
    # APP_ENV=prod.
    authoring_fake: bool = False
    # YouTube Data API v3 (segment 20): read only by the weekly `youtube_map` graph; unset = the graph does nothing.
    youtube_api_key: SecretStr | None = None
    # Where the YouTube player page is served (a static Space on its own origin, deploy/player-space). The app's CSP
    # `frame-src`, the Permissions-Policy fullscreen delegation and the web's iframe all use this one value
    # (a Space variable YOUTUBE_PLAYER_ORIGIN overrides it, e.g. for a staging player).
    youtube_player_origin: str = "https://arrowassassin-naadi-player.static.hf.space"
    # Local search (segment 07): bring the embeddings index in line with the content when the API starts
    # (a background task; a no-op when nothing changed or the models are not cached).
    embeddings_autostart: bool = True

    # ── Web Push (S16): owner-provided VAPID pair; reminders stay off until all three are set ──
    vapid_public_key: str | None = None
    vapid_private_key: SecretStr | None = None
    vapid_subject: str | None = None

    # ── Backups (segment 22): owner-provided; until both are set the job reports `not_configured` ──
    # Fine-grained HF write token scoped to the one private backup dataset (Space secret).
    hf_backup_token: SecretStr | None = None
    # The owner's age public key (`age1...`, Space variable). The private key never leaves the owner.
    backup_age_recipient: str | None = None
    backup_repo_id: str = Field(
        default="arrowassassin/naadi-backups", validation_alias=AliasChoices("HF_BACKUP_DATASET", "BACKUP_REPO_ID")
    )

    # ── Auth / admin (PII, so a secret) ──
    admin_emails: SecretStr | None = None
    google_oauth_configured: bool = False

    @field_validator("youtube_player_origin")
    @classmethod
    def _player_origin(cls, v: str) -> str:
        u = urlparse(v.strip())
        loopback = u.hostname in ("localhost", "127.0.0.1")
        if (u.scheme != "https" and not (loopback and u.scheme == "http")) or not u.hostname:
            raise ValueError("YOUTUBE_PLAYER_ORIGIN must be an https origin")
        if u.path not in ("", "/") or u.query or u.fragment or u.username or u.password:
            raise ValueError("YOUTUBE_PLAYER_ORIGIN must be an origin only (no path, query or credentials)")
        return f"{u.scheme}://{u.netloc}"

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper(cls, v: object) -> object:
        return v.upper() if isinstance(v, str) else v

    @field_validator(
        "database_url",
        "supabase_url",
        "supabase_anon_key",
        "supabase_service_role_key",
        "supabase_jwt_secret",
        "openrouter_api_key",
        "openrouter_app_url",
        "nvidia_api_key",
        "tavily_api_key",
        "hf_gen_token",
        "youtube_api_key",
        "vapid_public_key",
        "vapid_private_key",
        "vapid_subject",
        "admin_emails",
        "app_base_url",
        "hf_backup_token",
        "backup_age_recipient",
        mode="before",
    )
    @classmethod
    def _empty_is_none(cls, v: object) -> object:
        return None if isinstance(v, str) and not v.strip() else v

    @field_validator("supabase_url")
    @classmethod
    def _strip_slash(cls, v: str | None) -> str | None:
        return v.rstrip("/") if v else v

    @property
    def supabase_origin(self) -> str | None:
        """`https://<ref>.supabase.co` (scheme + host only), used by the CSP."""
        if not self.supabase_url:
            return None
        u = urlparse(self.supabase_url)
        if not u.hostname:
            return None
        if u.scheme == "https":
            return f"https://{u.netloc}"
        # Plain http only for a local auth server (CI's Supabase auth container, local dev); never remote.
        if u.scheme == "http" and u.hostname in {"localhost", "127.0.0.1", "::1"}:
            return f"http://{u.netloc}"
        return None

    @property
    def generation_configured(self) -> bool:
        """The Space is reachable only with the token; the stub stands in outside production."""
        return self.hf_gen_token is not None or (self.generation_fake and self.app_env != "prod")

    @property
    def push_configured(self) -> bool:
        """True when the owner has provided the whole VAPID triple (public key, private key, contact)."""
        return bool(self.vapid_public_key and self.vapid_private_key and self.vapid_subject)

    @property
    def openrouter_base_url_effective(self) -> str | None:
        if not self.openrouter_base_url or self.app_env == "prod":
            return None
        u = urlparse(self.openrouter_base_url)
        if u.scheme in ("http", "https") and u.hostname in {"127.0.0.1", "localhost", "::1"}:
            return self.openrouter_base_url.rstrip("/")
        return None

    @property
    def is_local(self) -> bool:
        return self.app_env == "local"

    def secret_values(self) -> list[str]:
        """Every secret value (and the DB password), so error details can be scrubbed."""
        out: list[str] = []
        for s in (
            self.database_url,
            self.supabase_service_role_key,
            self.storage_service_key,
            self.supabase_jwt_secret,
            self.openrouter_api_key,
            self.nvidia_api_key,
            self.tavily_api_key,
            self.hf_gen_token,
            self.youtube_api_key,
            self.vapid_private_key,
            self.admin_emails,
            self.hf_backup_token,
        ):
            if s is not None:
                out.append(s.get_secret_value())
        if self.database_url is not None:
            pw = urlparse(self.database_url.get_secret_value()).password
            if pw:
                out.append(pw)
        return [v for v in out if len(v) >= 4]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
