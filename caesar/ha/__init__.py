"""Home Assistant bridge (ADR-0007).

Single owner of the REST + WebSocket integration with Home Assistant.
Every real-world side effect from CAESAR flows through here, after a
Policy decision (ADR-0013).
"""

from caesar.ha.client import HAAuthError, HAClient, HAError
from caesar.ha.models import EntityState, ServiceCall

__all__ = ["EntityState", "HAAuthError", "HAClient", "HAError", "ServiceCall"]
