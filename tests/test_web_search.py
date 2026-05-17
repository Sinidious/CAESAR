"""Tests for the web-search worker (ADR-0028, v1.3).

Layers:

1. Unit tests on :class:`WebSearchClient` covering the SearXNG JSON
   parsing, error mapping, and result-shape normalisation. The
   underlying httpx call is mocked with :class:`httpx.MockTransport`
   so the test never opens a real socket.
2. Handler-level tests verify the worker's input validation and the
   wire-shape contract.

Brain-graph integration coverage (registration + policy gating +
audit) is added in :mod:`tests.test_praetor_graph` alongside the
calculator tests; the helper there is generic over tool id.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from caesar.legion.protocol import TaskDispatch
from caesar.legion.web_search import (
    CAPABILITY,
    WORKER_ID,
    WebSearchClient,
    WebSearchError,
    WebSearchWorker,
)


def _client(handler: httpx.MockTransport, *, url: str = "http://searx.test") -> WebSearchClient:
    return WebSearchClient(
        searxng_url=url,
        timeout_seconds=2.0,
        http=httpx.AsyncClient(transport=handler, timeout=2.0),
    )


def _ok_response(results: list[dict[str, str]]) -> httpx.Response:
    return httpx.Response(200, content=json.dumps({"results": results}))


# --- WebSearchClient happy path --------------------------------------------


async def test_client_returns_normalised_results() -> None:
    raw = [
        {
            "title": "CAESAR repo",
            "url": "https://github.com/Sinidious/CAESAR",
            "content": "Self-hosted homelab AI assistant.",
        },
        {
            "title": "SearXNG docs",
            "url": "https://docs.searxng.org/",
            "content": "Privacy-respecting meta-search engine.",
        },
    ]
    transport = httpx.MockTransport(lambda request: _ok_response(raw))
    client = _client(transport)
    try:
        results = await client.search("caesar homelab", limit=10)
    finally:
        await client.aclose()
    assert results == [
        {
            "title": "CAESAR repo",
            "url": "https://github.com/Sinidious/CAESAR",
            "snippet": "Self-hosted homelab AI assistant.",
            "domain": "github.com",
        },
        {
            "title": "SearXNG docs",
            "url": "https://docs.searxng.org/",
            "snippet": "Privacy-respecting meta-search engine.",
            "domain": "docs.searxng.org",
        },
    ]


async def test_client_caps_to_limit() -> None:
    raw = [{"title": f"r{i}", "url": f"https://h{i}/", "content": ""} for i in range(20)]
    transport = httpx.MockTransport(lambda request: _ok_response(raw))
    client = _client(transport)
    try:
        results = await client.search("q", limit=3)
    finally:
        await client.aclose()
    assert len(results) == 3
    assert [r["title"] for r in results] == ["r0", "r1", "r2"]


async def test_client_skips_entries_without_url() -> None:
    raw = [
        {"title": "no url", "content": "skip me"},
        {"title": "good", "url": "https://example.com/x", "content": "keep"},
    ]
    transport = httpx.MockTransport(lambda request: _ok_response(raw))
    client = _client(transport)
    try:
        results = await client.search("q", limit=10)
    finally:
        await client.aclose()
    assert [r["url"] for r in results] == ["https://example.com/x"]


async def test_client_sends_format_json_and_safesearch_params() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return _ok_response([])

    client = _client(httpx.MockTransport(handler), url="http://searx.test/")
    try:
        await client.search("kitty", limit=5)
    finally:
        await client.aclose()
    assert len(captured) == 1
    req = captured[0]
    assert str(req.url).startswith("http://searx.test/search")
    assert req.url.params["q"] == "kitty"
    assert req.url.params["format"] == "json"
    assert req.url.params["safesearch"] == "1"


async def test_client_strips_trailing_slash_in_base_url() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return _ok_response([])

    client = _client(httpx.MockTransport(handler), url="http://searx.test/")
    try:
        await client.search("x", limit=1)
    finally:
        await client.aclose()
    # Only one slash between base and /search.
    assert str(captured[0].url).count("//") == 1


# --- WebSearchClient error paths -------------------------------------------


async def test_client_raises_on_http_5xx() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(503, content="upstream timeout"),
    )
    client = _client(transport)
    try:
        with pytest.raises(WebSearchError, match="HTTP 503"):
            await client.search("q", limit=5)
    finally:
        await client.aclose()


async def test_client_raises_on_non_json_body() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content="not json at all"),
    )
    client = _client(transport)
    try:
        with pytest.raises(WebSearchError, match="non-JSON body"):
            await client.search("q", limit=5)
    finally:
        await client.aclose()


async def test_client_raises_when_results_missing() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=json.dumps({"oops": []})),
    )
    client = _client(transport)
    try:
        with pytest.raises(WebSearchError, match="missing 'results' list"):
            await client.search("q", limit=5)
    finally:
        await client.aclose()


async def test_client_maps_httpx_transport_errors() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    transport = httpx.MockTransport(boom)
    client = _client(transport)
    try:
        with pytest.raises(WebSearchError, match="SearXNG request failed"):
            await client.search("q", limit=5)
    finally:
        await client.aclose()


# --- Worker handler contract ----------------------------------------------


def test_worker_metadata() -> None:
    assert WebSearchWorker.worker_id == WORKER_ID == "web_search"
    assert WebSearchWorker.capabilities == [CAPABILITY] == ["tool.web_search"]


class _FakeClient:
    """Captures search arguments and returns canned results."""

    def __init__(self, results: list[dict[str, str]]) -> None:
        self._results = results
        self.calls: list[dict[str, Any]] = []

    async def search(self, query: str, *, limit: int) -> list[dict[str, str]]:
        self.calls.append({"query": query, "limit": limit})
        return self._results

    async def aclose(self) -> None:
        return None


async def test_handle_returns_query_and_results() -> None:
    client = _FakeClient(
        results=[
            {
                "title": "t",
                "url": "https://example.com/",
                "snippet": "s",
                "domain": "example.com",
            }
        ]
    )
    worker = WebSearchWorker(bus=None, client=client)  # type: ignore[arg-type]
    task = TaskDispatch(
        task_id="t1",
        capability=CAPABILITY,
        payload={"query": "kitty cats", "limit": 3},
    )
    out = await worker.handle(task)
    assert out == {
        "query": "kitty cats",
        "results": [
            {
                "title": "t",
                "url": "https://example.com/",
                "snippet": "s",
                "domain": "example.com",
            }
        ],
    }
    assert client.calls == [{"query": "kitty cats", "limit": 3}]


async def test_handle_clamps_limit_to_max() -> None:
    client = _FakeClient(results=[])
    worker = WebSearchWorker(
        bus=None,  # type: ignore[arg-type]
        client=client,  # type: ignore[arg-type]
        default_limit=5,
        max_limit=10,
    )
    task = TaskDispatch(
        task_id="t1",
        capability=CAPABILITY,
        payload={"query": "q", "limit": 9999},
    )
    await worker.handle(task)
    assert client.calls[0]["limit"] == 10


async def test_handle_defaults_limit_when_missing() -> None:
    client = _FakeClient(results=[])
    worker = WebSearchWorker(
        bus=None,  # type: ignore[arg-type]
        client=client,  # type: ignore[arg-type]
        default_limit=7,
    )
    task = TaskDispatch(task_id="t1", capability=CAPABILITY, payload={"query": "q"})
    await worker.handle(task)
    assert client.calls[0]["limit"] == 7


async def test_handle_rejects_missing_query() -> None:
    worker = WebSearchWorker(
        bus=None,  # type: ignore[arg-type]
        client=_FakeClient(results=[]),  # type: ignore[arg-type]
    )
    task = TaskDispatch(task_id="t1", capability=CAPABILITY, payload={})
    with pytest.raises(ValueError, match="non-empty string"):
        await worker.handle(task)


async def test_handle_rejects_non_string_query() -> None:
    worker = WebSearchWorker(
        bus=None,  # type: ignore[arg-type]
        client=_FakeClient(results=[]),  # type: ignore[arg-type]
    )
    task = TaskDispatch(task_id="t1", capability=CAPABILITY, payload={"query": 42})
    with pytest.raises(ValueError, match="non-empty string"):
        await worker.handle(task)


async def test_handle_rejects_bad_limit() -> None:
    worker = WebSearchWorker(
        bus=None,  # type: ignore[arg-type]
        client=_FakeClient(results=[]),  # type: ignore[arg-type]
    )
    task = TaskDispatch(
        task_id="t1",
        capability=CAPABILITY,
        payload={"query": "q", "limit": "huh"},
    )
    with pytest.raises(ValueError, match="must be an integer"):
        await worker.handle(task)


async def test_handle_propagates_client_error_as_value_error() -> None:
    class _ExplodingClient:
        async def search(self, query: str, *, limit: int) -> list[dict[str, str]]:
            raise WebSearchError("upstream choked")

        async def aclose(self) -> None:
            return None

    worker = WebSearchWorker(bus=None, client=_ExplodingClient())  # type: ignore[arg-type]
    task = TaskDispatch(task_id="t1", capability=CAPABILITY, payload={"query": "q"})
    with pytest.raises(ValueError, match="upstream choked"):
        await worker.handle(task)
