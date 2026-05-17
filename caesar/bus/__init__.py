"""Message bus (ADR-0009).

Thin async wrapper around ``nats-py`` so the rest of CAESAR doesn't
import the SDK directly. Owns connection lifecycle.
"""

from caesar.bus.client import Bus, BusError, NotConnectedError

__all__ = ["Bus", "BusError", "NotConnectedError"]
