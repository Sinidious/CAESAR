"""Praetor — the brain (ADR-0006).

FastAPI service + LangGraph state machines. This package exposes the
HTTP surface; the rest of CAESAR (Legion, HA Bridge, Voice Satellite)
talks to it over the wire.
"""

from caesar.praetor.app import create_app

__all__ = ["create_app"]
