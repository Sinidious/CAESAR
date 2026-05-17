"""Memory subsystem (ADR-0010, ADR-0020).

Owns the retention sweep that keeps the audit log bounded and the
semantic indexer that embeds chat replies for similarity recall.
"""

from caesar.memory.retention import RetentionSweeper, sweep_once
from caesar.memory.semantic import (
    IndexResult,
    RecalledChunk,
    SemanticIndexer,
    cosine_top_k,
    index_pending,
)

__all__ = [
    "IndexResult",
    "RecalledChunk",
    "RetentionSweeper",
    "SemanticIndexer",
    "cosine_top_k",
    "index_pending",
    "sweep_once",
]
