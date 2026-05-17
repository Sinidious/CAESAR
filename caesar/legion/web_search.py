"""Web-search worker backed by SearXNG (ADR-0028).

SearXNG (https://github.com/searxng/searxng) is a self-hosted
meta-search engine. The operator runs an instance themselves;
CAESAR sends queries to its JSON API and normalises results into a
provider-agnostic shape the brain can fold back into context.

The worker doesn't talk to commercial engines directly. That's the
v1.3 privacy posture: searches stay between the operator's homelab
and whatever upstream engines their SearXNG is configured to use
(which they control).

Input payload (dispatched from the brain graph via
``caesar.dispatch.tool.web_search``):

.. code-block:: json

    { "query": "...", "limit": 5 }

Output:

.. code-block:: json

    {
        "query": "...",
        "results": [
            {"title": "...", "url": "...", "snippet": "...", "domain": "..."},
            ...
        ]
    }
"""

from __future__ import annotations

from typing import Any, ClassVar
from urllib.parse import urlparse

import httpx

from caesar.bus.client import Bus
from caesar.legion.protocol import TaskDispatch
from caesar.legion.worker import Worker
from caesar.log import get_logger

CAPABILITY = "tool.web_search"
WORKER_ID = "web_search"
DEFAULT_RESULT_LIMIT = 5
MAX_RESULT_LIMIT = 25
DEFAULT_TIMEOUT_SECONDS = 10.0

logger = get_logger("caesar.legion.web_search")


class WebSearchError(ValueError):
    """The SearXNG backend was unreachable or returned an unusable shape."""


class WebSearchClient:
    """Thin SearXNG HTTP client.

    Wraps :class:`httpx.AsyncClient` so tests can inject a custom
    transport without monkey-patching the worker. Caller owns the
    httpx client's lifecycle via :meth:`aclose`.
    """

    def __init__(
        self,
        *,
        searxng_url: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._searxng_url = searxng_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._http = http or httpx.AsyncClient(timeout=timeout_seconds)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def search(self, query: str, *, limit: int) -> list[dict[str, str]]:
        """Run ``query`` against SearXNG and return at most ``limit`` rows."""

        try:
            resp = await self._http.get(
                f"{self._searxng_url}/search",
                params={"q": query, "format": "json", "safesearch": "1"},
            )
        except httpx.HTTPError as exc:
            raise WebSearchError(f"SearXNG request failed: {exc}") from exc
        if resp.status_code >= 400:
            raise WebSearchError(f"SearXNG returned HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            body = resp.json()
        except ValueError as exc:
            raise WebSearchError(f"SearXNG returned non-JSON body: {exc}") from exc
        raw_results = body.get("results") if isinstance(body, dict) else None
        if not isinstance(raw_results, list):
            raise WebSearchError("SearXNG response missing 'results' list.")

        normalised: list[dict[str, str]] = []
        for entry in raw_results[:limit]:
            if not isinstance(entry, dict):
                continue
            url = str(entry.get("url") or "")
            if not url:
                continue
            normalised.append(
                {
                    "title": str(entry.get("title") or ""),
                    "url": url,
                    "snippet": str(entry.get("content") or ""),
                    "domain": urlparse(url).hostname or "",
                }
            )
        return normalised


class WebSearchWorker(Worker):
    """Web-search Legion worker backed by SearXNG."""

    worker_id: ClassVar[str] = WORKER_ID
    capabilities: ClassVar[list[str]] = [CAPABILITY]
    version: ClassVar[str] = "0.3.0"

    def __init__(
        self,
        bus: Bus,
        client: WebSearchClient,
        *,
        default_limit: int = DEFAULT_RESULT_LIMIT,
        max_limit: int = MAX_RESULT_LIMIT,
    ) -> None:
        super().__init__(bus)
        self._client = client
        self._default_limit = default_limit
        self._max_limit = max_limit

    async def aclose(self) -> None:
        await self._client.aclose()

    async def handle(self, task: TaskDispatch) -> dict[str, Any]:
        query = task.payload.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("'query' must be a non-empty string")
        raw_limit = task.payload.get("limit", self._default_limit)
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"'limit' must be an integer, got {raw_limit!r}") from exc
        if limit < 1:
            raise ValueError("'limit' must be >= 1")
        limit = min(limit, self._max_limit)

        try:
            results = await self._client.search(query, limit=limit)
        except WebSearchError as exc:
            raise ValueError(str(exc)) from exc
        return {"query": query, "results": results}
