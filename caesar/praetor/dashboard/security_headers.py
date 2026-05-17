"""Security headers for the dashboard (SR-010).

A small middleware that decorates every ``/dashboard/*`` response with
a conservative ``Content-Security-Policy`` plus the legacy
``X-Frame-Options`` / ``X-Content-Type-Options`` / ``Referrer-Policy``
headers. Defence in depth: a misconfigured reverse proxy or a future
mistake on our own templates can't accidentally let a third-party
page embed the dashboard or load arbitrary scripts.

The CSP is intentionally permissive about ``style-src`` (templates
use a few inline ``style="…"`` attributes) and ``script-src`` (htmx
is loaded from unpkg with an integrity attribute today; tightened by
the planned htmx-vendoring follow-up).

The middleware only fires on dashboard requests so the bare HTTP API
(``/v1/*``) and ``/metrics`` aren't affected.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Request, Response

DASHBOARD_PREFIX = "/dashboard"

_CSP = (
    "default-src 'self'; "
    "script-src 'self' https://unpkg.com; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)

_HEADERS: dict[str, str] = {
    "Content-Security-Policy": _CSP,
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}


async def dashboard_security_headers_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Apply :data:`_HEADERS` to every response under ``/dashboard``.

    Uses ``setdefault`` so route handlers can still override (e.g. for
    a future surface that needs a relaxed CSP).
    """

    response = await call_next(request)
    if request.url.path.startswith(DASHBOARD_PREFIX):
        for header, value in _HEADERS.items():
            response.headers.setdefault(header, value)
    return response
