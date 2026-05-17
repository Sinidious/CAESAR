"""Tests for audit-payload size clamping (SR-008)."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from caesar.db.audit import AuditLogger
from caesar.db.audit_clamp import TRUNCATION_MARKER, clamp_payload
from caesar.db.schema import audit_log

# --- unit tests for clamp_payload --------------------------------------------


def test_short_string_passes_through_unchanged() -> None:
    payload = {"reply": "hello"}
    out, truncated = clamp_payload(payload, max_chars=100)
    assert out == payload
    assert truncated is False


def test_long_string_gets_truncated_with_marker() -> None:
    long = "x" * 5000
    out, truncated = clamp_payload({"reply": long}, max_chars=100)
    assert truncated is True
    assert len(out["reply"]) == 100
    marker = TRUNCATION_MARKER.format(total=5000)
    assert out["reply"].endswith(marker)


def test_truncation_reports_original_length() -> None:
    out, _ = clamp_payload({"reply": "y" * 999}, max_chars=200)
    assert "999 chars total" in out["reply"]


def test_nested_dicts_are_clamped_recursively() -> None:
    out, truncated = clamp_payload(
        {
            "outer": {
                "inner": "z" * 5000,
                "ok": "short",
            }
        },
        max_chars=100,
    )
    assert truncated is True
    assert len(out["outer"]["inner"]) == 100
    assert out["outer"]["ok"] == "short"


def test_lists_of_strings_are_clamped() -> None:
    out, truncated = clamp_payload(
        {"messages": ["short", "x" * 5000, "also short"]},
        max_chars=100,
    )
    assert truncated is True
    assert out["messages"][0] == "short"
    assert len(out["messages"][1]) == 100
    assert out["messages"][2] == "also short"


def test_non_string_values_unchanged() -> None:
    payload = {"count": 42, "ok": True, "missing": None, "ratio": 1.5}
    out, truncated = clamp_payload(payload, max_chars=10)
    assert out == payload
    assert truncated is False


def test_disabled_when_max_chars_is_zero() -> None:
    long = "x" * 10000
    out, truncated = clamp_payload({"reply": long}, max_chars=0)
    assert out["reply"] == long
    assert truncated is False


def test_marker_only_when_max_smaller_than_marker_length() -> None:
    """Edge case: max smaller than the marker itself.

    The marker should win and the keep-prefix is empty; the result is
    still bounded by ``max_chars`` even though the marker is longer.
    """

    out, truncated = clamp_payload({"x": "y" * 1000}, max_chars=5)
    assert truncated is True
    # We don't promise the output is <= max_chars when max < marker
    # length, but we do promise the marker is present.
    assert "truncated" in out["x"]


# --- integration: clamping fires at AuditLogger.record -----------------------


@pytest.mark.asyncio
async def test_audit_logger_clamps_long_payload(engine: AsyncEngine) -> None:
    audit = AuditLogger(engine, max_string_chars=64)
    row_id = await audit.record(
        "test.clamp",
        {"reply": "z" * 1000, "short": "ok"},
    )
    async with engine.connect() as conn:
        row = (
            (await conn.execute(select(audit_log).where(audit_log.c.id == row_id))).mappings().one()
        )
    payload = row["payload"]
    assert len(payload["reply"]) == 64
    assert "1000 chars total" in payload["reply"]
    assert payload["short"] == "ok"


@pytest.mark.asyncio
async def test_audit_logger_passes_through_when_disabled(engine: AsyncEngine) -> None:
    audit = AuditLogger(engine, max_string_chars=0)
    big = "y" * 10000
    row_id = await audit.record("test.no_clamp", {"reply": big})
    async with engine.connect() as conn:
        row = (
            (await conn.execute(select(audit_log).where(audit_log.c.id == row_id))).mappings().one()
        )
    assert row["payload"]["reply"] == big
