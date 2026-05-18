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


LLMProvider = Literal["anthropic", "openai", "ollama"]


class AnthropicProviderSettings(BaseModel):
    """Anthropic-specific gateway configuration (ADR-0026)."""

    api_key: SecretStr | None = None


class OpenAIProviderSettings(BaseModel):
    """OpenAI-specific gateway configuration (ADR-0026).

    ``base_url`` lets operators point the client at Azure-OpenAI or
    any other OpenAI-compatible endpoint (vLLM, LiteLLM proxy,
    Together, etc.). When unset the official OpenAI base is used.
    """

    api_key: SecretStr | None = None
    base_url: str | None = None


class OllamaProviderSettings(BaseModel):
    """Ollama-specific gateway configuration (ADR-0026).

    Fully-local operation: no API key required. ``base_url`` points
    at the Ollama HTTP API (default ``http://localhost:11434``).
    """

    base_url: str = "http://localhost:11434"


class LLMTaskConfig(BaseModel):
    """One row of ``LLMSettings.task_routing`` (ADR-0026).

    ``provider`` picks which configured backend handles the task;
    ``model`` overrides the default model for that backend. Provider
    auth/base_url comes from the matching ``LLMSettings.<provider>``
    sub-settings — task routing only chooses; it doesn't reconfigure.
    """

    provider: LLMProvider
    model: str


class LLMSettings(BaseModel):
    """LLM gateway configuration (ADR-0011, extended by ADR-0026)."""

    # ADR-0026: which provider implementation is wired by default.
    provider: LLMProvider = "anthropic"
    # Default model identifier passed to the chosen provider. Operators
    # set both ``provider`` and ``model`` together to switch backends.
    model: str = "claude-haiku-4-5-20251001"
    system_prompt: str = "You are CAESAR, a self-hosted homelab AI assistant. Be concise."
    max_tokens: int = 1024

    # Backward-compat: pre-v1.1 deployments set ``CAESAR_LLM__API_KEY``
    # at the top level. It still works and is treated as the Anthropic
    # key when ``provider == "anthropic"`` and the nested
    # ``anthropic.api_key`` is unset. Slated for removal in v2.x.
    api_key: SecretStr | None = None

    # Per-provider sub-settings (ADR-0026).
    anthropic: AnthropicProviderSettings = Field(default_factory=AnthropicProviderSettings)
    openai: OpenAIProviderSettings = Field(default_factory=OpenAIProviderSettings)
    ollama: OllamaProviderSettings = Field(default_factory=OllamaProviderSettings)

    # ADR-0026: optional per-task routing. Keys are task names emitted
    # by the brain graph (e.g. ``"chat"``); values pick a different
    # provider/model for that task. An empty dict (default) routes
    # every task to the configured default provider/model.
    task_routing: dict[str, LLMTaskConfig] = Field(default_factory=dict)


class LogSettings(BaseModel):
    """Structured-logging configuration (ADR-0018)."""

    level: str = "INFO"
    format: LogFormat = "json"


class ServerSettings(BaseModel):
    """Praetor HTTP server bind (ADR-0006).

    Defaults to loopback so a fresh install doesn't expose ``/v1/chat``,
    the dashboard, or ``/metrics`` to the LAN without the operator
    saying so. To expose Praetor on the network, set
    ``CAESAR_SERVER__HOST=0.0.0.0`` (and front it with auth — see
    SECURITY-REVIEW.md, gap SR-001).
    """

    host: str = "127.0.0.1"
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


class BusAuthSettings(BaseModel):
    """NATS auth (ADR-0027). Opt-in: default is no auth.

    Set ``enabled=true`` and supply *either* ``nkey_seed_path`` (a
    file on disk holding the ed25519 seed) *or* ``nkey_seed`` (the
    seed inline, useful for tests). ``user`` is optional and only
    needed when the operator's ``nats-server.conf`` pairs the NKEY
    with a named user entry rather than listing it directly under
    ``authorization.users``.
    """

    enabled: bool = False
    nkey_seed_path: Path | None = None
    nkey_seed: SecretStr | None = None
    user: str | None = None


class BusSettings(BaseModel):
    """Message-bus (NATS) configuration (ADR-0009).

    Disabled by default — Praetor runs fine without NATS for the chat
    and devices paths. Enable it when you want to bring up the Legion
    worker pool (set ``CAESAR_BUS__ENABLED=true`` and point ``url`` at
    your nats-server).

    v0.3 → v1.1 shipped single-node localhost only. v1.2 (ADR-0027)
    adds NKEY-per-identity auth via :class:`BusAuthSettings` so the
    pool can span hosts; the default stays no-auth so existing
    single-host operators don't see a behaviour change.
    """

    enabled: bool = False
    url: str = "nats://127.0.0.1:4222"
    connect_timeout: float = 5.0
    request_timeout: float = 5.0
    auth: BusAuthSettings = Field(default_factory=BusAuthSettings)


class MemorySettings(BaseModel):
    """Episodic-memory retention (ADR-0020).

    The sweep deletes ``audit_log`` rows older than ``retention_days``;
    it runs at startup and then every ``sweep_interval_seconds``.

    ``audit_max_string_chars`` clamps every string value in an audit
    payload to at most N characters at write time (SR-008). Long
    strings get a ``[truncated, N chars total]`` marker. Set to 0 to
    disable.
    """

    retention_days: int = 90
    sweep_interval_seconds: float = 3600.0
    audit_max_string_chars: int = 16384


class DashboardSettings(BaseModel):
    """Dashboard configuration (ADR-0021).

    The dashboard is opt-in: until ``token`` is set, ``/dashboard/*``
    returns 404. Operators bind to ``127.0.0.1`` and front it with a
    reverse proxy when they want LAN/WAN access.
    """

    token: SecretStr | None = None
    # Optional separate key for signing session cookies (SR-006).
    # When set, the cookie HMAC is decoupled from the auth token:
    # rotating the token still revokes sessions only if the operator
    # *also* rotates this key. When unset, the signing key is derived
    # from the token (HMAC-SHA256) so the legacy "rotate the token to
    # log everyone out" behaviour is preserved.
    signing_key: SecretStr | None = None
    history_limit: int = 100
    cookie_name: str = "caesar_dashboard"
    # 7-day default (SR-007). Long enough to avoid relogging in from
    # the kitchen tablet every morning, short enough that a leaked
    # cookie has a bounded shelf life. Operators can extend via
    # CAESAR_DASHBOARD__COOKIE_MAX_AGE_SECONDS when they want longer
    # sessions.
    cookie_max_age_seconds: int = 60 * 60 * 24 * 7


class SemanticSettings(BaseModel):
    """Semantic memory configuration (ADR-0010 amendment).

    Disabled by default. When enabled, a background indexer embeds
    rows from ``audit_log`` matching ``indexed_event_types`` and a
    ``memory.semantic_recall`` worker can be dispatched to find them
    by similarity.
    """

    enabled: bool = False
    voyage_api_key: SecretStr | None = None
    model: str = "voyage-3.5"
    embedding_dim: int = 1024
    indexer_interval_seconds: float = 60.0
    indexed_event_types: list[str] = Field(default_factory=lambda: ["chat.completed"])
    top_k_default: int = 5
    top_k_max: int = 50


class MetricsSettings(BaseModel):
    """Prometheus ``/metrics`` exposition configuration.

    When ``token`` is set, scrapes must present
    ``Authorization: Bearer <token>``. When unset, the endpoint is
    open — relying on the loopback bind default ([ADR-0023] /
    SR-001) and the operator's reverse-proxy posture to keep it from
    the LAN. See SECURITY-REVIEW.md gap SR-003.
    """

    token: SecretStr | None = None


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


class WebSearchToolSettings(BaseModel):
    """SearXNG-backed ``web_search`` worker configuration (ADR-0028).

    Operator runs the SearXNG instance themselves (CAESAR is a client,
    not a search engine). ``searxng_url`` defaults to localhost so a
    single-host deployment "just works" once the operator launches
    SearXNG with its JSON output format enabled.
    """

    searxng_url: str = "http://localhost:8888"
    result_limit: int = 5
    max_result_limit: int = 25
    timeout_seconds: float = 10.0


class CalendarToolSettings(BaseModel):
    """CalDAV-backed ``calendar_read`` worker configuration (ADR-0028).

    ``calendar_names`` optionally restricts the worker to a subset of
    the operator's calendars (useful when a Nextcloud account hosts
    work + family calendars and only one should reach the brain).
    Empty list = all calendars are visible.
    """

    caldav_url: str = "http://localhost:5232/"
    username: str = ""
    password: SecretStr | None = None
    calendar_names: list[str] = Field(default_factory=list)
    default_range_days: int = 7
    default_event_limit: int = 20
    max_event_limit: int = 200
    max_range_days: int = 365


class NotifyToolSettings(BaseModel):
    """ntfy.sh-backed ``notify`` worker configuration (ADR-0030).

    ``topic`` is required at worker construction time; the worker isn't
    built when it's unset. ``base_url`` defaults to the public ntfy.sh
    server; self-hosters point it at their own instance. ``token`` is
    optional and only used when the operator's ntfy server requires
    bearer auth.
    """

    topic: str = ""
    base_url: str = "https://ntfy.sh"
    token: SecretStr | None = None
    default_priority: int = Field(default=3, ge=1, le=5)
    timeout_seconds: float = 10.0


class ToolsSettings(BaseModel):
    """Per-tool worker configuration (ADR-0028)."""

    web_search: WebSearchToolSettings = Field(default_factory=WebSearchToolSettings)
    calendar: CalendarToolSettings = Field(default_factory=CalendarToolSettings)
    notify: NotifyToolSettings = Field(default_factory=NotifyToolSettings)


class ProactiveSettings(BaseModel):
    """Proactive trigger configuration (ADR-0030, ADR-0031).

    Off by default: when ``triggers_path`` is unset, neither the
    scheduler nor the HA-event driver is constructed and CAESAR
    remains reactive-only. When set, Praetor loads the file at
    lifespan start and arms every ``enabled: true`` trigger inside it.

    v1.6 renames the file from ``schedules.yaml`` to ``triggers.yaml``
    (ADR-0031 §5). ``schedules_path`` remains a deprecated alias for
    one release — operators migrating from v1.5 keep working, but a
    warning lands in the log on startup.
    """

    triggers_path: Path | None = None
    # Deprecated alias for ``triggers_path``. Kept for one release per
    # ADR-0031 §5. Operators should rename ``CAESAR_PROACTIVE__SCHEDULES_PATH``
    # to ``CAESAR_PROACTIVE__TRIGGERS_PATH``.
    schedules_path: Path | None = None

    @property
    def resolved_path(self) -> Path | None:
        """Return whichever path the operator set, preferring the new name.

        Lifespan code reads this and logs a deprecation warning when
        only ``schedules_path`` is populated.
        """

        if self.triggers_path is not None:
            return self.triggers_path
        return self.schedules_path


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
    semantic: SemanticSettings = Field(default_factory=SemanticSettings)
    dashboard: DashboardSettings = Field(default_factory=DashboardSettings)
    metrics: MetricsSettings = Field(default_factory=MetricsSettings)
    tools: ToolsSettings = Field(default_factory=ToolsSettings)
    proactive: ProactiveSettings = Field(default_factory=ProactiveSettings)


@lru_cache(maxsize=1)
def get_settings() -> CaesarSettings:
    """Return process-wide settings, instantiated lazily once."""

    return CaesarSettings()


def reset_settings_cache() -> None:
    """Clear the cached settings (used in tests that mutate env)."""

    get_settings.cache_clear()
