from __future__ import annotations

from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from caesar.cli import app


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_root_help_lists_praetor(runner: CliRunner):
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "praetor" in result.stdout.lower()


def test_praetor_serve_invokes_uvicorn(runner: CliRunner, tmp_path):
    with patch("caesar.cli.uvicorn.run") as run_mock:
        result = runner.invoke(
            app,
            ["praetor", "serve", "--host", "127.0.0.1", "--port", "12345"],
        )
    assert result.exit_code == 0, result.stdout
    run_mock.assert_called_once()
    kwargs = run_mock.call_args.kwargs
    assert kwargs["host"] == "127.0.0.1"
    assert kwargs["port"] == 12345
    assert kwargs["factory"] is True


def test_praetor_serve_uses_settings_when_no_flags(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("CAESAR_SERVER__HOST", "10.0.0.1")
    monkeypatch.setenv("CAESAR_SERVER__PORT", "9999")
    from caesar.config import reset_settings_cache

    reset_settings_cache()
    with patch("caesar.cli.uvicorn.run") as run_mock:
        result = runner.invoke(app, ["praetor", "serve"])
    reset_settings_cache()
    assert result.exit_code == 0, result.stdout
    kwargs = run_mock.call_args.kwargs
    assert kwargs["host"] == "10.0.0.1"
    assert kwargs["port"] == 9999


def test_praetor_migrate_invokes_upgrade(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path
):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'cli.sqlite3'}"
    monkeypatch.setenv("CAESAR_DB__URL", db_url)
    from caesar.config import reset_settings_cache

    reset_settings_cache()
    with patch("caesar.db.migrate.upgrade_to_head") as up_mock:
        result = runner.invoke(app, ["praetor", "migrate"])
    reset_settings_cache()
    assert result.exit_code == 0, result.stdout
    up_mock.assert_called_once_with(db_url)


def test_memory_sweep_requires_exactly_one_mode(runner: CliRunner):
    result = runner.invoke(app, ["memory", "sweep"])
    assert result.exit_code != 0


def test_memory_sweep_rejects_both_dry_run_and_apply(runner: CliRunner):
    result = runner.invoke(app, ["memory", "sweep", "--dry-run", "--apply"])
    assert result.exit_code != 0


def test_memory_sweep_dry_run(runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path):
    from caesar.config import reset_settings_cache
    from caesar.db.migrate import upgrade_to_head

    db_url = f"sqlite+aiosqlite:///{tmp_path / 'cli.sqlite3'}"
    upgrade_to_head(db_url)
    monkeypatch.setenv("CAESAR_DB__URL", db_url)
    reset_settings_cache()

    result = runner.invoke(app, ["memory", "sweep", "--dry-run"])
    reset_settings_cache()
    assert result.exit_code == 0, result.stdout
    assert "would delete" in result.stdout


def test_memory_sweep_apply(runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path):
    from caesar.config import reset_settings_cache
    from caesar.db.migrate import upgrade_to_head

    db_url = f"sqlite+aiosqlite:///{tmp_path / 'cli2.sqlite3'}"
    upgrade_to_head(db_url)
    monkeypatch.setenv("CAESAR_DB__URL", db_url)
    reset_settings_cache()

    result = runner.invoke(app, ["memory", "sweep", "--apply", "--days", "1"])
    reset_settings_cache()
    assert result.exit_code == 0, result.stdout
    assert "deleted 0" in result.stdout
