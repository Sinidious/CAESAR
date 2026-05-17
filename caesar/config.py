"""Layered configuration loader (ADR-0017).

Precedence (low to high): defaults → .env file → process env. Nested
fields use a ``__`` delimiter, so ``CAESAR_DB__URL`` sets ``db.url``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
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


class HASettings(BaseModel):
    """Home Assistant bridge configuration (ADR-0007).

    ``url`` and ``token`` are both required at runtime to actually
    talk to HA; if either is missing the bridge isn't constructed and
    the device routes return 503.
    """

    url: str | None = None
    token: SecretStr | None = None
    timeout_seconds: float = 10.0
    verify_ssl: bool = True


class PolicySettings(BaseModel):
    """Policy engine configuration (ADR-0013).

    ``rules_path`` points at a YAML file describing the allow-list.
    When unset, CAESAR loads the deny-all stub policy and refuses every
    service call.
    """

    rules_path: Path | None = None


class BusSettings(BaseModel):
    """Message-bus (NATS) configuration (ADR-0009).

    Disabled by default — Praetor runs fine without NATS for the chat
    and devices paths. Enable it when you want to bring up the Legion
    worker pool (set ``CAESAR_BUS__ENABLED=true`` and point ``url`` at
    your nats-server). v0.3 ships single-node localhost only; auth is
    a later milestone.
    """

    enabled: bool = False
    url: str = "nats://127.0.0.1:4222"
    connect_timeout: float = 5.0
    request_timeout: float = 5.0


class MemorySettings(BaseModel):
    """Episodic-memory retention (ADR-0020).

    The sweep deletes ``audit_log`` rows older than ``retention_days``;
    it runs at startup and then every ``sweep_interval_seconds``.
    """

    retention_days: int = 90
    sweep_interval_seconds: float = 3600.0


class LegionSettings(BaseModel):
    """Legion worker configuration (ADR-0009 + ADR-0010).

    ``inprocess_workers`` lists worker names that Praetor itself should
    instantiate at lifespan start (each still talks to the registry over
    NATS). The default ``["memory_recall"]`` is enough for v0.3's gate;
    operators can add their own out-of-process workers later without
    changing this list.
    """

    inprocess_workers: list[str] = Field(default_factory=lambda: ["memory_recall"])
    recall_default_limit: int = 10
    recall_max_limit: int = 100


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
    ha: HASettings = Field(default_factory=HASettings)
    policy: PolicySettings = Field(default_factory=PolicySettings)
    bus: BusSettings = Field(default_factory=BusSettings)
    legion: LegionSettings = Field(default_factory=LegionSettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)


@lru_cache(maxsize=1)
def get_settings() -> CaesarSettings:
    """Return process-wide settings, instantiated lazily once."""

    return CaesarSettings()


def reset_settings_cache() -> None:
    """Clear the cached settings (used in tests that mutate env)."""

    get_settings.cache_clear()
