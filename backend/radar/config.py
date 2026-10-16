"""Runtime settings (from environment / .env) and YAML config loading.

Real config files (profile, companies, rules) are gitignored. Resolution order for
``load_yaml_config(name)``: ``config/<name>.yaml`` -> the ``config:<name>`` row in the
settings table -> ``config/<name>.example.yaml``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]

LLMProviderName = Literal["groq", "anthropic", "fake"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    database_url: str = Field(
        default=f"sqlite:///{REPO_ROOT / 'data' / 'radar.db'}",
        validation_alias=AliasChoices("RADAR_DATABASE_URL", "DATABASE_URL"),
    )
    data_dir: Path = Field(default=REPO_ROOT / "data", validation_alias="RADAR_DATA_DIR")
    config_dir: Path = Field(default=REPO_ROOT / "config", validation_alias="RADAR_CONFIG_DIR")

    # LLM
    llm_provider: LLMProviderName = Field(default="groq", validation_alias="RADAR_LLM_PROVIDER")
    scoring_model: str = Field(default="openai/gpt-oss-20b", validation_alias="RADAR_SCORING_MODEL")
    draft_provider: LLMProviderName | None = Field(
        default=None, validation_alias="RADAR_DRAFT_PROVIDER"
    )
    draft_model: str = Field(default="openai/gpt-oss-120b", validation_alias="RADAR_DRAFT_MODEL")
    groq_api_key: str | None = Field(default=None, validation_alias="GROQ_API_KEY")
    anthropic_api_key: str | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")

    # Local pre-ranking (optional `embed` extra)
    embed_model: str = Field(
        default="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        validation_alias="RADAR_EMBED_MODEL",
    )

    # Collection
    user_agent: str = Field(
        default="speculor-radar/0.1 (personal job-search tool; "
        "+https://github.com/DanieleGiovanardi2408/speculor)",
        validation_alias="RADAR_USER_AGENT",
    )
    http_cache_ttl_hours: float = Field(default=12.0, validation_alias="RADAR_HTTP_CACHE_TTL_HOURS")

    # Optional email-alert mailbox (read-only IMAP over SSL). Source "email" is enabled only
    # when host, user and password are all set.
    imap_host: str | None = Field(default=None, validation_alias="RADAR_IMAP_HOST")
    imap_port: int = Field(default=993, validation_alias="RADAR_IMAP_PORT")
    imap_user: str | None = Field(default=None, validation_alias="RADAR_IMAP_USER")
    imap_password: SecretStr | None = Field(default=None, validation_alias="RADAR_IMAP_PASSWORD")
    imap_folder: str = Field(default="radar", validation_alias="RADAR_IMAP_FOLDER")
    imap_since_days: int = Field(default=14, validation_alias="RADAR_IMAP_SINCE_DAYS")

    # Local sending
    linkedin_bin: str = Field(default="linkedin", validation_alias="RADAR_LINKEDIN_BIN")

    # API
    cors_origins: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173", validation_alias="RADAR_CORS_ORIGINS"
    )

    @property
    def effective_draft_provider(self) -> LLMProviderName:
        return self.draft_provider or self.llm_provider

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def load_yaml_config(name: str) -> dict[str, Any]:
    """Load ``config/<name>.yaml`` with DB and example fallbacks (see module docstring)."""
    settings = get_settings()
    real = settings.config_dir / f"{name}.yaml"
    if real.exists():
        return _read_yaml(real)
    from radar.db import get_setting  # local import: avoid cycle at module load

    stored = get_setting(f"config:{name}")
    if isinstance(stored, dict):
        return stored
    example = settings.config_dir / f"{name}.example.yaml"
    if example.exists():
        return _read_yaml(example)
    raise FileNotFoundError(f"No config found for {name!r} (looked in {settings.config_dir})")


def _read_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return data
