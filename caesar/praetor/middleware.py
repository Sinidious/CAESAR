"""Request middleware (ADR-0018).

Currently just request-id binding: read or mint an id, bind it to the
structlog context for the duration of the request, and echo it back
in the response header so callers can correlate.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import structlog
from fastapi import Request, Response

REQUEST_ID_HEADER = "X-Request-Id"


async def request_id_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    incoming = request.headers.get(REQUEST_ID_HEADER)
    request_id = incoming if incoming else uuid.uuid4().hex
    structlog.contextvars.bind_contextvars(request_id=request_id)
    try:
        response = await call_next(request)
    finally:
        structlog.contextvars.unbind_contextvars("request_id")
    response.headers[REQUEST_ID_HEADER] = request_id
    return response
