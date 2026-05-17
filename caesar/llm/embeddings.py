"""Embedding providers (ADR-0010 amendment).

A small Embedder protocol parallel to ADR-0011's LLM gateway.
``StubEmbedder`` is deterministic and used in tests; ``VoyageEmbedder``
wraps Voyage AI's async client for production.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any, Protocol

from caesar.log import get_logger


class EmbedderError(RuntimeError):
    """Generic embedder failure."""


class Embedder(Protocol):
    """Async batch-embedding contract."""

    dimension: int
    model: str

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts; returns one vector per input."""
        ...


class StubEmbedder:
    """Deterministic embedder for tests and offline development.

    Hash the text → seed a tiny PRNG → produce a unit-norm vector.
    Same text always yields the same vector; different texts yield
    different ones (with overwhelmingly high probability), so cosine
    similarity is a meaningful function of identity.
    """

    def __init__(self, dimension: int = 1024, *, model: str = "stub-1024") -> None:
        self.dimension = dimension
        self.model = model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        # Expand the 32-byte digest into `dimension` floats by repeated
        # hashing with a counter. Deterministic; no NumPy dep.
        raw: list[int] = []
        i = 0
        while len(raw) < self.dimension:
            chunk = hashlib.sha256(digest + i.to_bytes(4, "little")).digest()
            raw.extend(chunk)
            i += 1
        # Map bytes [0..255] to [-1, 1].
        floats = [(b - 127.5) / 127.5 for b in raw[: self.dimension]]
        norm = math.sqrt(sum(x * x for x in floats)) or 1.0
        return [x / norm for x in floats]


class VoyageEmbedder:  # pragma: no cover - thin network wrapper; behaviour proven by env-gated live test
    """Async wrapper around the ``voyageai`` client."""

    def __init__(self, api_key: str, *, model: str = "voyage-3.5", dimension: int = 1024) -> None:
        import voyageai

        self._client = voyageai.AsyncClient(api_key=api_key)  # type: ignore[attr-defined]
        self.dimension = dimension
        self.model = model
        self._logger = get_logger("caesar.llm.voyage")

    async def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            response: Any = await self._client.embed(
                texts=texts, model=self.model, input_type="document"
            )
        except Exception as exc:
            raise EmbedderError(f"voyage embed failed: {exc}") from exc
        embeddings = response.embeddings
        self._logger.info(
            "voyage.embed.ok",
            count=len(embeddings),
            model=self.model,
        )
        return [list(map(float, vec)) for vec in embeddings]
