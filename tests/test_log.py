from __future__ import annotations

import json
import logging

import pytest
import structlog

from caesar.config import LogSettings
from caesar.log import bind_decision, configure_logging, get_logger


def test_json_format_emits_json(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(LogSettings(format="json", level="INFO"))
    get_logger("caesar.test").info("hello", subject="world")
    out = capsys.readouterr().out
    for line in out.splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        assert record["event"] == "hello"
        assert record["subject"] == "world"
        assert record["level"] == "info"
        return
    pytest.fail(f"no log line captured; stdout was: {out!r}")


def test_console_format_is_not_json(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(LogSettings(format="console", level="INFO"))
    get_logger("caesar.test").info("hello-console")
    out = capsys.readouterr().out
    assert "hello-console" in out
    # Console renderer produces human-readable lines, not JSON.
    first = next(line for line in out.splitlines() if line.strip())
    with pytest.raises(json.JSONDecodeError):
        json.loads(first)


def test_bind_decision_sets_and_clears(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(LogSettings(format="json", level="INFO"))
    logger = get_logger("caesar.test")
    with bind_decision("dec-123"):
        logger.info("inside")
    logger.info("outside")
    out = capsys.readouterr().out
    records = [json.loads(line) for line in out.splitlines() if line.strip()]
    inside = next(r for r in records if r["event"] == "inside")
    outside = next(r for r in records if r["event"] == "outside")
    assert inside["decision_id"] == "dec-123"
    assert "decision_id" not in outside


def test_drop_color_message_strips_field(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(LogSettings(format="json", level="INFO"))
    # Uvicorn-style: a stdlib log record carrying `color_message`.
    logging.getLogger("uvicorn").info("msg", extra={"color_message": "duplicate"})
    out = capsys.readouterr().out
    for line in out.splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        assert "color_message" not in record


@pytest.fixture(autouse=True)
def _reset_structlog() -> None:
    structlog.reset_defaults()
    structlog.contextvars.clear_contextvars()
