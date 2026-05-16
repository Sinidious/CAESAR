"""Provider-agnostic LLM abstraction (ADR-0011).

App code calls the :class:`LLMGateway` protocol; provider modules
(currently just :mod:`caesar.llm.anthropic`) implement it.
"""

from caesar.llm.gateway import ChatMessage, ChatResponse, LLMGateway

__all__ = ["ChatMessage", "ChatResponse", "LLMGateway"]
