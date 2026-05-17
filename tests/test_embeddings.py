from __future__ import annotations

import math

from caesar.llm.embeddings import StubEmbedder


async def test_stub_embedder_is_deterministic() -> None:
    e = StubEmbedder(dimension=64)
    a = (await e.embed(["hello"]))[0]
    b = (await e.embed(["hello"]))[0]
    assert a == b


async def test_stub_embedder_distinguishes_inputs() -> None:
    e = StubEmbedder(dimension=64)
    vectors = await e.embed(["one", "two", "three"])
    # Pairwise different.
    assert vectors[0] != vectors[1]
    assert vectors[1] != vectors[2]


async def test_stub_embedder_returns_unit_norm() -> None:
    e = StubEmbedder(dimension=64)
    vec = (await e.embed(["x"]))[0]
    norm = math.sqrt(sum(x * x for x in vec))
    assert math.isclose(norm, 1.0, rel_tol=1e-6)


async def test_stub_embedder_respects_dimension() -> None:
    e = StubEmbedder(dimension=128)
    vec = (await e.embed(["x"]))[0]
    assert len(vec) == 128
    assert e.dimension == 128
    assert e.model.startswith("stub")
