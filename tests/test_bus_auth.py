"""Tests for the NATS auth wiring on :class:`Bus` (ADR-0027).

Live multi-process tests (a worker on another machine talking to a
Praetor) live in :mod:`tests.test_legion_multihost`; this module
focuses on the unit-level wiring: BusAuth + Bus._connect_kwargs +
the integration through CaesarSettings → _default_bus.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr

from caesar.bus.client import Bus, BusAuth, BusAuthError
from caesar.config import (
    BusAuthSettings,
    BusSettings,
    CaesarSettings,
    DatabaseSettings,
    LLMSettings,
    LogSettings,
)
from caesar.praetor.app import _default_bus

# A throwaway ed25519 seed in NATS NKEY format. Not signed by any
# server; only used to verify the seed-loading and connect-kwargs
# wiring. NEVER ship a real seed in tests.
_FAKE_SEED = "SUACSSL3UAHUDXKFSNVUZRF5UHPMWZ6BFDTJ7M6USDXIEDNPPQYYYSC2NA"


# --- BusAuth.resolve_seed ---------------------------------------------------


def test_resolve_seed_returns_inline_bytes() -> None:
    auth = BusAuth(nkey_seed=_FAKE_SEED)
    assert auth.resolve_seed() == _FAKE_SEED.encode("utf-8")


def test_resolve_seed_reads_file(tmp_path: Path) -> None:
    path = tmp_path / "praetor.nkey.seed"
    path.write_text(_FAKE_SEED + "\n", encoding="utf-8")
    auth = BusAuth(nkey_seed_path=path)
    assert auth.resolve_seed() == _FAKE_SEED.encode("utf-8")


def test_resolve_seed_strips_trailing_whitespace(tmp_path: Path) -> None:
    path = tmp_path / "seed"
    path.write_text(f"  {_FAKE_SEED}\n", encoding="utf-8")
    auth = BusAuth(nkey_seed_path=path)
    # Leading whitespace is preserved; only trailing whitespace stripped.
    assert auth.resolve_seed().endswith(_FAKE_SEED.encode("utf-8"))


def test_resolve_seed_missing_file_raises(tmp_path: Path) -> None:
    auth = BusAuth(nkey_seed_path=tmp_path / "does-not-exist")
    with pytest.raises(BusAuthError, match="failed to read"):
        auth.resolve_seed()


def test_resolve_seed_without_any_source_raises() -> None:
    auth = BusAuth()
    with pytest.raises(BusAuthError, match="requires either"):
        auth.resolve_seed()


# --- Bus._connect_kwargs and connect() --------------------------------------


def test_bus_without_auth_emits_no_auth_kwargs() -> None:
    bus = Bus("nats://localhost:4222")
    assert bus.authenticated is False
    kwargs = bus._connect_kwargs()
    assert "nkeys_seed_str" not in kwargs
    assert "user" not in kwargs


def test_bus_with_inline_seed_passes_string_to_nats() -> None:
    bus = Bus("nats://localhost:4222", auth=BusAuth(nkey_seed=_FAKE_SEED))
    assert bus.authenticated is True
    kwargs = bus._connect_kwargs()
    assert kwargs["nkeys_seed_str"] == _FAKE_SEED


def test_bus_with_seed_file_passes_string_to_nats(tmp_path: Path) -> None:
    path = tmp_path / "seed"
    path.write_text(_FAKE_SEED, encoding="utf-8")
    bus = Bus("nats://localhost:4222", auth=BusAuth(nkey_seed_path=path))
    kwargs = bus._connect_kwargs()
    assert kwargs["nkeys_seed_str"] == _FAKE_SEED


def test_bus_passes_user_when_configured() -> None:
    bus = Bus(
        "nats://localhost:4222",
        auth=BusAuth(nkey_seed=_FAKE_SEED, user="caesar-praetor"),
    )
    kwargs = bus._connect_kwargs()
    assert kwargs["user"] == "caesar-praetor"


async def test_bus_connect_calls_nats_with_resolved_kwargs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: ``connect()`` hands the kwargs to ``nats.connect``."""

    import nats as nats_module

    fake_nc = AsyncMock()
    fake_nc.is_connected = True
    connect_mock = AsyncMock(return_value=fake_nc)
    monkeypatch.setattr(nats_module, "connect", connect_mock)

    bus = Bus(
        "nats://localhost:4222",
        connect_timeout=2.5,
        auth=BusAuth(nkey_seed=_FAKE_SEED, user="caesar"),
    )
    await bus.connect()

    assert connect_mock.await_args is not None
    args, kwargs = connect_mock.await_args
    assert args == ("nats://localhost:4222",)
    assert kwargs["connect_timeout"] == 2.5
    assert kwargs["nkeys_seed_str"] == _FAKE_SEED
    assert kwargs["user"] == "caesar"


async def test_bus_connect_without_auth_skips_seed_kwargs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import nats as nats_module

    fake_nc = AsyncMock()
    fake_nc.is_connected = True
    connect_mock = AsyncMock(return_value=fake_nc)
    monkeypatch.setattr(nats_module, "connect", connect_mock)

    bus = Bus("nats://localhost:4222")
    await bus.connect()

    assert connect_mock.await_args is not None
    _, kwargs = connect_mock.await_args
    assert "nkeys_seed_str" not in kwargs
    assert "user" not in kwargs


# --- _default_bus integration -----------------------------------------------


def _settings_with_bus(*, auth: BusAuthSettings) -> CaesarSettings:
    return CaesarSettings(
        db=DatabaseSettings(url="sqlite+aiosqlite:///:memory:"),
        llm=LLMSettings(api_key=SecretStr("sk-test")),
        log=LogSettings(format="console", level="DEBUG"),
        bus=BusSettings(enabled=True, auth=auth),
    )


def test_default_bus_disabled_returns_none() -> None:
    settings = CaesarSettings(
        db=DatabaseSettings(url="sqlite+aiosqlite:///:memory:"),
        llm=LLMSettings(api_key=SecretStr("sk-test")),
        log=LogSettings(format="console", level="DEBUG"),
        bus=BusSettings(enabled=False),
    )
    assert _default_bus(settings) is None


def test_default_bus_without_auth_returns_unauthenticated_bus() -> None:
    bus = _default_bus(_settings_with_bus(auth=BusAuthSettings(enabled=False)))
    assert isinstance(bus, Bus)
    assert bus.authenticated is False


def test_default_bus_with_inline_seed_returns_authenticated_bus() -> None:
    bus = _default_bus(
        _settings_with_bus(
            auth=BusAuthSettings(
                enabled=True,
                nkey_seed=SecretStr(_FAKE_SEED),
                user="praetor",
            )
        )
    )
    assert isinstance(bus, Bus)
    assert bus.authenticated is True
    kwargs = bus._connect_kwargs()
    assert kwargs["nkeys_seed_str"] == _FAKE_SEED
    assert kwargs["user"] == "praetor"


def test_default_bus_with_seed_file_returns_authenticated_bus(tmp_path: Path) -> None:
    seed_path = tmp_path / "praetor.nkey"
    seed_path.write_text(_FAKE_SEED, encoding="utf-8")
    bus = _default_bus(
        _settings_with_bus(
            auth=BusAuthSettings(enabled=True, nkey_seed_path=seed_path),
        )
    )
    assert isinstance(bus, Bus)
    assert bus.authenticated is True
