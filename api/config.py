"""Environment configuration. Loads .env then .env.local (Neon-managed) from the repo root.

Every secret is read from the environment; nothing is hardcoded. See .env.example for the full list.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent

APP_VERSION = "0.1.0-phase1"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(ROOT / ".env", ROOT / ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Neon Postgres. DATABASE_URL is the pooled string (runtime); the unpooled one is used by Alembic.
    database_url: str = ""
    database_url_unpooled: str = ""

    # Canonical name is EODHD_API_KEY (what Vercel holds); EODHD_API_TOKEN is accepted as an alias (Radar's name).
    eodhd_api_token: str = Field(default="", validation_alias=AliasChoices("EODHD_API_KEY", "EODHD_API_TOKEN"))
    anthropic_api_key: str = ""

    # Shared write secret (bearer token) and cron secret.
    tt_write_token: str = ""
    cron_secret: str = ""

    public_hide_dollars: bool = True
    # Vercel sets VERCEL_ENV (production | preview | development); APP_ENV overrides it locally.
    app_env: str = Field(default="development", validation_alias=AliasChoices("APP_ENV", "VERCEL_ENV"))
    cors_origins: str = "http://localhost:5174,http://127.0.0.1:5174"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
