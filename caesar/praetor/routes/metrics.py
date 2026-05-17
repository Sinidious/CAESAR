"""``/metrics`` — Prometheus exposition (ADR: v1.0 observability).

Bind defaults to loopback (SR-001) so a fresh install isn't exposed
to the LAN. Operators who deliberately expose Praetor on the network
should also set ``CAESAR_METRICS__TOKEN`` (SR-003) so scrapes need
``Authorization: Bearer <token>`` and the endpoint's existence
doesn't leak install shape (worker count, audit event types) to
anyone with HTTP access.

Metric definitions live in :mod:`caesar.metrics`; the
``CaesarCollector`` for sampled gauges is registered in
:func:`caesar.praetor.app.create_app`.
"""

from __future__ import annotations

import secrets
from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from caesar.config import CaesarSettings

router = APIRouter(tags=["metrics"])

_BEARER = "bearer "  # case-insensitive prefix per RFC 6750


def _settings_from(request: Request) -> CaesarSettings:
    return cast(CaesarSettings, request.app.state.settings)


SettingsDep = Annotated[CaesarSettings, Depends(_settings_from)]


def _require_metrics_token(request: Request, settings: CaesarSettings) -> None:
    """Reject the request when a token is configured but not presented.

    The check is a no-op when ``settings.metrics.token`` is ``None``
    so existing scrapers (and the SR-001 loopback-default deployment)
    keep working unchanged.
    """

    if settings.metrics.token is None:
        return
    header = request.headers.get("Authorization", "")
    if not header.lower().startswith(_BEARER):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization: Bearer <token> required.",
            headers={"WWW-Authenticate": 'Bearer realm="caesar-metrics"'},
        )
    presented = header[len(_BEARER) :].strip()
    expected = settings.metrics.token.get_secret_value()
    if not secrets.compare_digest(presented, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid metrics token.",
            headers={"WWW-Authenticate": 'Bearer realm="caesar-metrics"'},
        )


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request, settings: SettingsDep) -> Response:
    _require_metrics_token(request, settings)
    payload = generate_latest()
    return Response(content=payload, media_type=CONTENT_TYPE_LATEST)
