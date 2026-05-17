"""Memory subsystem (ADR-0010, ADR-0020).

Owns the retention sweep that keeps the audit log bounded. Future
slices add semantic memory (ADR-0010) on top of the same engine.
"""

from caesar.memory.retention import RetentionSweeper, sweep_once

__all__ = ["RetentionSweeper", "sweep_once"]
