"""Structured logging setup (ADR-0018).

Configures structlog so every log line carries a timestamp, level,
logger name, and any contextual kwargs as fields. In ``json`` mode the
renderer emits one JSON object per line; in ``console`` mode it emits
a coloured human-readable form.

Call :func:`configure_logging` exactly once at startup.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, cast

import structlog
from structlog.types import EventDict, Processor

from caesar.config import LogSettings


def _drop_color_message_key(
    _logger: logging.Logger, _name: str, event_dict: EventDict
) -> EventDict:
    """Uvicorn's ``color_message`` duplicates ``event``; strip it."""

    event_dict.pop("color_message", None)
    return event_dict


def _shared_processors() -> list[Processor]:
    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        _drop_color_message_key,
    ]


def configure_logging(settings: LogSettings) -> None:
    """Initialise structlog and route stdlib logging through it."""

    renderer: Processor
    if settings.format == "json":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=_shared_processors(),
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.level.upper())

    # Funnel uvicorn through the same handler.
    for noisy in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        lg = logging.getLogger(noisy)
        lg.handlers = []
        lg.propagate = True

    structlog.configure(
        processors=[
            *_shared_processors(),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(*args: Any, **kwargs: Any) -> structlog.stdlib.BoundLogger:
    """Return a configured structlog logger."""

    return cast("structlog.stdlib.BoundLogger", structlog.get_logger(*args, **kwargs))


@contextmanager
def bind_decision(decision_id: str) -> Iterator[None]:
    """Bind ``decision_id`` to log context for the duration of the block.

    ADR-0018 says every log line emitted inside a LangGraph node should
    carry the surrounding decision's id so an operator can pivot from
    a log line to the matching audit row (ADR-0012).
    """

    structlog.contextvars.bind_contextvars(decision_id=decision_id)
    try:
        yield
    finally:
        structlog.contextvars.unbind_contextvars("decision_id")
