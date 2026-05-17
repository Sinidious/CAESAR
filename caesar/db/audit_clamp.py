"""Audit-payload size clamping (SR-008).

Walks an audit payload dict and replaces any string value longer than
``max_chars`` with a truncated form ending in a marker
(``"… [truncated, N chars total]"``). Numbers, bools, ``None``, and
container shapes are left intact — we only ever shorten strings.

The brain produces audit rows with user content, model replies, and
tool results. Any of those can grow large on adversarial or
pathological LLM output. Without a cap, a single misbehaving response
can write multiple MB into ``audit_log.payload`` and the DB grows
without bound between retention sweeps.

Per-string clamping is simpler than total-payload clamping and gives
the operator a predictable cost ceiling per row. The
:func:`clamp_payload` function is pure and side-effect free; the
caller (``AuditLogger.record``) is what logs a warning when something
got truncated.
"""

from __future__ import annotations

from typing import Any

TRUNCATION_MARKER = "… [truncated, {total} chars total]"


def _clamp_value(value: Any, max_chars: int, truncated: list[bool]) -> Any:
    """Return ``value`` with all overlong strings shortened.

    ``truncated`` is a single-element flag list the caller passes in so
    we can report whether anything was actually clamped without
    threading a return tuple through the recursion.
    """

    if isinstance(value, str):
        if len(value) <= max_chars:
            return value
        truncated[0] = True
        marker = TRUNCATION_MARKER.format(total=len(value))
        keep = max_chars - len(marker)
        if keep < 0:
            keep = 0
        return value[:keep] + marker
    if isinstance(value, dict):
        return {k: _clamp_value(v, max_chars, truncated) for k, v in value.items()}
    if isinstance(value, list):
        return [_clamp_value(item, max_chars, truncated) for item in value]
    if isinstance(value, tuple):
        return tuple(_clamp_value(item, max_chars, truncated) for item in value)
    return value


def clamp_payload(payload: dict[str, Any], *, max_chars: int) -> tuple[dict[str, Any], bool]:
    """Clamp every string value in ``payload`` to at most ``max_chars``.

    Returns ``(clamped_payload, truncated)`` so the caller can decide
    whether to log a warning. ``max_chars <= 0`` disables clamping.
    """

    if max_chars <= 0:
        return payload, False
    flag = [False]
    clamped = _clamp_value(payload, max_chars, flag)
    return clamped, flag[0]
