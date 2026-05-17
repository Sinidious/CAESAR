"""Server-rendered dashboard (ADR-0021).

HTMX + Jinja, SSE for live audit. Mounted by ``caesar.praetor.app``
only when ``CAESAR_DASHBOARD__TOKEN`` is configured.
"""

from caesar.praetor.dashboard.routes import build_router

__all__ = ["build_router"]
