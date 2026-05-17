"""Provider-agnostic LLM abstraction (ADR-0011).

App code calls the :class:`LLMGateway` protocol; provider modules
(currently just :mod:`caesar.llm.anthropic`) implement it.
"""

from caesar.llm.embeddings import Embedder, EmbedderError, StubEmbedder, VoyageEmbedder
from caesar.llm.gateway import (
    ChatMessage,
    ChatResponse,
    LLMGateway,
    ToolDefinition,
    ToolResult,
    ToolUse,
)

__all__ = [
    "ChatMessage",
    "ChatResponse",
    "Embedder",
    "EmbedderError",
    "LLMGateway",
    "StubEmbedder",
    "ToolDefinition",
    "ToolResult",
    "ToolUse",
    "VoyageEmbedder",
]
