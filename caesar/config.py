"""Layered configuration loader (ADR-0017).

Precedence (low to high): defaults → .env file → process env. Nested
fields use a ``__`` delimiter, so ``CAESAR_DB__URL`` sets ``db.url``.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

LogFormat = Literal["json", "console"]


class DatabaseSettings(BaseModel):
    """Persistence configuration (ADR-0019).

    The default lives under ``./var/`` so the working tree stays clean
    and the directory is auto-created at engine init.
    """

    url: str = "sqlite+aiosqlite:///./var/caesar.sqlite3"
    echo: bool = False


class LLMSettings(BaseModel):
    """LLM gateway configuration (ADR-0011)."""

    api_key: SecretStr | None = None
    model: str = "claude-haiku-4-5-20251001"
    system_prompt: str = "You are CAESAR, a self-hosted homelab AI assistant. Be concise."
    max_tokens: int = 1024


class LogSettings(BaseModel):
    """Structured-logging configuration (ADR-0018)."""

    level: str = "INFO"
    format: LogFormat = "json"


class ServerSettings(BaseModel):
    """Praetor HTTP server bind (ADR-0006)."""

    host: str = "0.0.0.0"
    port: int = 8000


class CaesarSettings(BaseSettings):
    """Top-level settings; all subsystems read through this."""

    model_config = SettingsConfigDict(
        env_prefix="CAESAR_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    db: DatabaseSettings = Field(default_factory=DatabaseSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    log: LogSettings = Field(default_factory=LogSettings)
    server: ServerSettings = Field(default_factory=ServerSettings)


@lru_cache(maxsize=1)
def get_settings() -> CaesarSettings:
    """Return process-wide settings, instantiated lazily once."""

    return CaesarSettings()


def reset_settings_cache() -> None:
    """Clear the cached settings (used in tests that mutate env)."""

    get_settings.cache_clear()
