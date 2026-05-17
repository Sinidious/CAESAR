"""``/metrics`` — Prometheus exposition (ADR: v1.0 observability).

No auth: operators bind Praetor on loopback and front the dashboard
with a reverse proxy. Metric definitions live in
:mod:`caesar.metrics`; the ``CaesarCollector`` for sampled gauges is
registered in :func:`caesar.praetor.app.create_app`.
"""

from __future__ import annotations

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

router = APIRouter(tags=["metrics"])


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    payload = generate_latest()
    return Response(content=payload, media_type=CONTENT_TYPE_LATEST)
